"""Persistent, concealed rock-paper-scissors rounds for private and group chats."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
from typing import Literal
from uuid import uuid4

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from database import get_db
from ws import manager

router = APIRouter(prefix='/api/rps', tags=['rps'])
RPS_COMMAND_PATTERN = re.compile(r'\[\s*猜拳\s*[：:]\s*(石头|剪刀|布)\s*(?:[|｜]\s*([^\]\r\n]*))?\]')
reply_round_id = contextvars.ContextVar('rps_reply_round_id', default='')
_notification_tasks: set[asyncio.Task] = set()
Hand = Literal['石头', '剪刀', '布']


class RoundCreate(BaseModel):
    scope: Literal['private', 'chatroom']
    source_id: str = Field(min_length=1, max_length=200)
    actor: Literal['aion', 'connor']
    hand: Hand
    topic: str = Field(min_length=1, max_length=200)
    reply_options: dict = Field(default_factory=dict)

    @field_validator('topic')
    @classmethod
    def clean_topic(cls, value):
        value = value.strip()
        if not value or any(c in value for c in '\r\n[]'):
            raise ValueError('请填写一句本局约定（不含方括号或换行）')
        return value


class HandRequest(BaseModel):
    hand: Hand
    reply_options: dict = Field(default_factory=dict)


async def ensure_rps_tables(db):
    await db.execute('''CREATE TABLE IF NOT EXISTS rps_rounds (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_id TEXT NOT NULL,
        actor TEXT NOT NULL, initiator TEXT NOT NULL, status TEXT NOT NULL,
        user_hand TEXT, ai_hand TEXT, source_msg_id TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    )''')
    await db.execute('CREATE INDEX IF NOT EXISTS idx_rps_source ON rps_rounds(scope, source_id, created_at)')
    columns = {r[1] for r in await (await db.execute('PRAGMA table_info(rps_rounds)')).fetchall()}
    for name, definition in (('topic', "TEXT NOT NULL DEFAULT '随便玩一局'"),
                             ('result_pending', 'INTEGER NOT NULL DEFAULT 0')):
        if name not in columns:
            await db.execute(f'ALTER TABLE rps_rounds ADD COLUMN {name} {definition}')
    await db.execute('UPDATE rps_rounds SET result_pending=0 WHERE result_pending=1')
    for table in ('messages', 'chatroom_messages'):
        if 'attachments' not in {r[1] for r in await (await db.execute(f'PRAGMA table_info({table})')).fetchall()}:
            continue
        await db.execute(f"UPDATE {table} SET attachments=replace(attachments, ?, ?) WHERE attachments LIKE ?",
                         ('"rps_pending_context"', '"system_model_context"', '%"rps_pending_context"%'))



def _names():
    from chatroom import get_chatroom_names
    return dict(zip(('user', 'aion', 'connor'), get_chatroom_names()))


def _winner(row):
    if row['status'] != 'done':
        return None
    if row['user_hand'] == row['ai_hand']:
        return 'draw'
    return 'user' if {'石头': '剪刀', '剪刀': '布', '布': '石头'}[row['user_hand']] == row['ai_hand'] else row['actor']


def _public(row):
    names = _names()
    return {key: row[key] for key in ('id', 'scope', 'source_id', 'actor', 'initiator', 'status', 'source_msg_id', 'created_at', 'updated_at')} | {
        'user_hand': row['user_hand'],
        'ai_hand': row['ai_hand'] if row['status'] == 'done' else None,
        'winner': _winner(row), 'user_name': names['user'], 'actor_name': names[row['actor']],
        'topic': row['topic'], 'result_pending': bool(row['result_pending']),
    }


async def _validate_source(db, scope, source_id, actor=None):
    private = scope == 'private'
    table = 'conversations' if private else 'chatroom_rooms'
    cursor = await db.execute(f"SELECT {'id' if private else 'type'} FROM {table} WHERE id=?", (source_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(404, '聊天不存在')
    allowed = {'aion'} if private else {'connor'} if row[0] == 'connor_1v1' else {'aion', 'connor'}
    if actor and actor not in allowed:
        raise HTTPException(400, '这个人不在当前聊天里')


async def _row(db, round_id):
    db.row_factory = aiosqlite.Row
    cursor = await db.execute('SELECT * FROM rps_rounds WHERE id=?', (round_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(404, '这局猜拳不存在')
    return dict(row)


async def _event(db, row, content, *, kind, notify=False, after_msg_id=''):
    private = row['scope'] == 'private'
    table, key, role = ('messages', 'conv_id', 'role') if private else ('chatroom_messages', 'room_id', 'sender')
    attachments = [{'type': 'rps_' + kind, 'round_id': row['id'], 'actor': row['actor'], 'initiator': row['initiator'], 'topic': row['topic']},
                   {'type': 'system_model_context'}]
    if notify:
        attachments.append({'type': 'rps_notification', 'round_id': row['id'], 'actor': row['actor']})
    if after_msg_id:
        attachments.append({'type': 'system_notice_order', 'after_msg_id': after_msg_id})
    message = {'id': 'rps_msg_' + uuid4().hex, key: row['source_id'], role: 'system',
               'content': content, 'created_at': time.time(), 'attachments': attachments}
    await db.execute(f'INSERT INTO {table} (id,{key},{role},content,created_at,attachments) VALUES (?,?,?,?,?,?)',
                     (message['id'], row['source_id'], 'system', content, message['created_at'], json.dumps(attachments, ensure_ascii=False)))
    parent = 'conversations' if private else 'chatroom_rooms'
    await db.execute(f'UPDATE {parent} SET updated_at=? WHERE id=?', (message['created_at'], row['source_id']))
    return message


async def _publish(row, message):
    await manager.broadcast({'type': 'rps_updated', 'data': _public(row)})
    if message:
        await manager.broadcast({'type': 'msg_created' if row['scope'] == 'private' else 'chatroom_msg_created', 'data': message})


async def notification_message(scope, source_id, event_id):
    """Reuse a persisted result/invitation when generating the targeted response."""
    table, key, role = ('messages', 'conv_id', 'role') if scope == 'private' else ('chatroom_messages', 'room_id', 'sender')
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(f'SELECT * FROM {table} WHERE id=? AND {key}=?', (event_id, source_id))
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(404, '猜拳通知不存在')
    message = dict(row)
    message['attachments'] = json.loads(message.get('attachments') or '[]')
    marker = next((a for a in message['attachments'] if a.get('type') == 'rps_notification'), None)
    if not marker or message[role] != 'system':
        raise HTTPException(400, '不是猜拳通知')
    return message, marker['actor']


async def notify_ai(row, message, options=None):
    """Finish the normal chat request on the server, even if the dialog closes."""
    options = options or {}

    async def reply():
        token = reply_round_id.set(row['id'])
        try:
            if row['scope'] == 'private':
                from routes.chat import send_message, MsgCreate
                fields = {k: v for k, v in options.items() if k in MsgCreate.model_fields}
                fields.update(content=message['content'], rps_event_id=message['id'])
                response = await send_message(row['source_id'], MsgCreate(**fields))
            else:
                from chatroom import load_chatroom_config
                from config import DEFAULT_MODEL, SETTINGS
                from routes.chatroom import send_message, MsgSend
                cfg = load_chatroom_config()
                fields = {'model': cfg.get('aion_model') or SETTINGS.get('model') or DEFAULT_MODEL,
                          'connor_model': cfg.get('connor_model') or 'Codex'}
                fields.update({k: v for k, v in options.items() if k in MsgSend.model_fields})
                fields.update(content=message['content'], rps_event_id=message['id'])
                response = await send_message(row['source_id'], MsgSend(**fields))
            if hasattr(response, 'body_iterator'):
                async for _ in response.body_iterator:
                    pass
        except Exception:
            logging.getLogger(__name__).exception('猜拳通知回复失败：%s', row['id'])
        finally:
            reply_round_id.reset(token)

    task = asyncio.create_task(reply(), context=contextvars.Context())
    _notification_tasks.add(task)
    task.add_done_callback(_notification_tasks.discard)


async def _finish(db, row, *, after_msg_id=''):
    row.update(status='done', result_pending=0, updated_at=time.time())
    await db.execute('UPDATE rps_rounds SET status=?,user_hand=?,ai_hand=?,updated_at=?,result_pending=0 WHERE id=?',
                     ('done', row['user_hand'], row['ai_hand'], row['updated_at'], row['id']))
    return await _event(db, row, _result_content(row), kind='result', after_msg_id=after_msg_id)


def _result_content(row):
    names = _names()
    winner = _winner(row)
    result = '平局' if winner == 'draw' else names[winner] + '获胜'
    return f"猜拳结果：本局约定「{row['topic']}」。{names['user']}出了{row['user_hand']}，{names[row['actor']]}出了{row['ai_hand']}，{result}。"


async def validate_send(scope, source_id, round_id):
    if not round_id:
        return
    async with get_db() as db:
        row = await _row(db, round_id)
        if (row['scope'], row['source_id']) != (scope, source_id) or row['status'] not in ('draft', 'waiting_ai'):
            raise HTTPException(409, '这局猜拳不能在当前聊天发送，请刷新后重试')


async def attach_to_message(scope, source_id, message, round_id=''):
    """Attach a draft invitation to a saved user turn without calling a model."""
    if not round_id:
        return message, None
    table, key = ('messages', 'conv_id') if scope == 'private' else ('chatroom_messages', 'room_id')
    updates, invitation, actor = [], None, None
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        db.row_factory = aiosqlite.Row
        saved = await (await db.execute(f'SELECT attachments FROM {table} WHERE id=? AND {key}=?', (message['id'], source_id))).fetchone()
        if not saved:
            raise HTTPException(404, '聊天消息不存在')
        attachments = json.loads(saved['attachments'] or '[]')
        if round_id:
            row = await _row(db, round_id)
            if (row['scope'], row['source_id']) != (scope, source_id) or row['status'] not in ('draft', 'waiting_ai'):
                raise HTTPException(409, '这局猜拳已结束或已撤回')
            actor = row['actor']
            if row['status'] == 'draft':
                row.update(status='waiting_ai', source_msg_id=message['id'], updated_at=time.time())
                await db.execute('UPDATE rps_rounds SET status=?,source_msg_id=?,updated_at=? WHERE id=?',
                                 ('waiting_ai', message['id'], row['updated_at'], round_id))
                names = _names()
                content = f"{names['user']}邀请{names[actor]}猜拳。本局约定：「{row['topic']}」。已出拳且未公开，请用[猜拳:石头]、[猜拳:剪刀]或[猜拳:布]接招，不要修改约定。"
                invitation = await _event(db, row, content, kind='invite', after_msg_id=message['id'])
                attachments.append({'type': 'rps_context', 'round_id': round_id, 'content': content})
                updates.append(row)
        await db.execute(f'UPDATE {table} SET attachments=? WHERE id=?', (json.dumps(attachments, ensure_ascii=False), message['id']))
        await db.commit()
    message = {**message, 'attachments': attachments}
    for row in updates:
        await _publish(row, invitation if invitation and row['id'] == round_id else None)
    return message, actor


@router.get('')
async def list_rounds(scope: Literal['private', 'chatroom'], source_id: str):
    async with get_db() as db:
        await _validate_source(db, scope, source_id)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('SELECT * FROM (SELECT * FROM rps_rounds WHERE scope=? AND source_id=? ORDER BY created_at DESC LIMIT 100) ORDER BY created_at', (scope, source_id))
        return [_public(dict(row)) for row in await cursor.fetchall()]


@router.get('/{round_id}')
async def get_round(round_id: str):
    async with get_db() as db:
        return _public(await _row(db, round_id))


@router.post('')
async def create_round(body: RoundCreate):
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        await _validate_source(db, body.scope, body.source_id, body.actor)
        cursor = await db.execute("SELECT id FROM rps_rounds WHERE scope=? AND source_id=? AND (status='draft' OR (actor=? AND status='waiting_ai'))", (body.scope, body.source_id, body.actor))
        if await cursor.fetchone():
            raise HTTPException(409, '还有一局在等对方出拳，请先完成或放弃那一局')
        now = time.time()
        row = dict(id='rps_' + uuid4().hex, scope=body.scope, source_id=body.source_id,
                   actor=body.actor, initiator='user', status='draft', user_hand=body.hand,
                   ai_hand=None, source_msg_id='', created_at=now, updated_at=now, topic=body.topic, result_pending=0)
        await db.execute('INSERT INTO rps_rounds (id,scope,source_id,actor,initiator,status,user_hand,ai_hand,source_msg_id,created_at,updated_at,topic,result_pending) VALUES (:id,:scope,:source_id,:actor,:initiator,:status,:user_hand,:ai_hand,:source_msg_id,:created_at,:updated_at,:topic,:result_pending)', row)
        await db.commit()
    await _publish(row, None)
    return _public(row)


@router.post('/{round_id}/play')
async def play_round(round_id: str, body: HandRequest):
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        row = await _row(db, round_id)
        if row['status'] == 'done':
            return _public(row)
        if row['status'] != 'waiting_user':
            raise HTTPException(409, '这一局现在不能出拳')
        row['user_hand'] = body.hand
        message = await _finish(db, row)
        await db.commit()
    await _publish(row, message)
    return _public(row)


@router.post('/{round_id}/cancel')
async def cancel_round(round_id: str):
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        row = await _row(db, round_id)
        if row['status'] in ('done', 'cancelled'):
            return _public(row)
        was_draft = row['status'] == 'draft'
        row.update(status='cancelled', updated_at=time.time())
        await db.execute("UPDATE rps_rounds SET status='cancelled',updated_at=? WHERE id=?", (row['updated_at'], round_id))
        names = _names()
        message = None if was_draft else await _event(db, row, f"猜拳取消：{names['user']}放弃了与{names[row['actor']]}的本局猜拳，不计输赢。", kind='cancel')
        await db.commit()
    await _publish(row, message)
    return _public(row)


async def process_rps_commands(text, *, source_type, source_id, sender, source_msg_id='', on_saved=None):
    matches = list(RPS_COMMAND_PATTERN.finditer(text or ''))
    cleaned = RPS_COMMAND_PATTERN.sub('', text or '').strip()
    if not matches or sender not in ('aion', 'connor'):
        return cleaned
    from capabilities import is_capability_enabled
    hand = matches[0].group(1)
    async with get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        await _validate_source(db, source_type, source_id, sender)
        db.row_factory = aiosqlite.Row
        pinned = reply_round_id.get()
        if pinned:
            row = await _row(db, pinned)
            if row['scope'] != source_type or row['source_id'] != source_id or row['actor'] != sender or row['status'] != 'waiting_ai':
                return cleaned
        else:
            cursor = await db.execute("SELECT * FROM rps_rounds WHERE scope=? AND source_id=? AND actor=? AND status='waiting_ai' ORDER BY created_at LIMIT 1", (source_type, source_id, sender))
            pending = await cursor.fetchone()
            row = dict(pending) if pending else None
        if row:
            row['ai_hand'] = hand
            message = await _finish(db, row, after_msg_id=source_msg_id)
        else:
            if not is_capability_enabled('rps'):
                return cleaned
            topic = (matches[0].group(2) or '').strip()
            if not topic or len(topic) > 200 or '[' in topic:
                return cleaned
            if source_msg_id:
                cursor = await db.execute('SELECT id FROM rps_rounds WHERE scope=? AND source_id=? AND actor=? AND source_msg_id=?', (source_type, source_id, sender, source_msg_id))
                if await cursor.fetchone():
                    return cleaned
            now = time.time()
            row = dict(id='rps_' + uuid4().hex, scope=source_type, source_id=source_id,
                       actor=sender, initiator=sender, status='waiting_user', user_hand=None,
                       ai_hand=hand, source_msg_id=source_msg_id, created_at=now, updated_at=now, topic=topic, result_pending=0)
            await db.execute('INSERT INTO rps_rounds (id,scope,source_id,actor,initiator,status,user_hand,ai_hand,source_msg_id,created_at,updated_at,topic,result_pending) VALUES (:id,:scope,:source_id,:actor,:initiator,:status,:user_hand,:ai_hand,:source_msg_id,:created_at,:updated_at,:topic,:result_pending)', row)
            message = await _event(db, row, _names()[sender] + f'邀请你猜拳。本局约定：「{topic}」', kind='invite', after_msg_id=source_msg_id)
        await db.commit()
    await _publish(row, message)
    if on_saved:
        await on_saved(message)
    return cleaned
