"""Small, persistent lobby invitations attached below an AI reply."""
import json
import re
import time
from uuid import uuid4

from database import get_db
from sync_events import append_sync_event, broadcast_synced
from ws import manager


GAME_COMMAND_PATTERN = re.compile(r'\[\s*小游戏\s*[：:]\s*([^\]]*)\]')
GAME_NAMES = {'桌球': '桌球', '台球': '桌球', '斗地主': '斗地主'}


async def process_game_invites(text, *, source_type, source_id, sender, source_msg_id='', on_saved=None):
    from capabilities import is_capability_enabled
    from chatroom import get_chatroom_names

    matches = GAME_COMMAND_PATTERN.findall(text or '')
    cleaned = GAME_COMMAND_PATTERN.sub('', text or '').strip()
    if not matches or sender not in ('aion', 'connor') or not is_capability_enabled('mini_game'):
        return cleaned
    game = next((GAME_NAMES[value.strip()] for value in matches if value.strip() in GAME_NAMES), None)
    if not game:
        return cleaned

    names = dict(zip(('user', 'aion', 'connor'), get_chatroom_names()))
    private = source_type == 'private'
    table, key, role = ('messages', 'conv_id', 'role') if private else ('chatroom_messages', 'room_id', 'sender')
    parent = 'conversations' if private else 'chatroom_rooms'
    attachments = [
        {'type': 'mini_game_invite', 'game': game, 'actor': sender, 'actor_name': names[sender]},
        {'type': 'system_model_context'},
    ]
    if source_msg_id:
        attachments.append({'type': 'system_notice_order', 'after_msg_id': source_msg_id})
    message = {
        'id': 'game_invite_' + (source_msg_id or uuid4().hex), key: source_id, role: 'system',
        'content': f'{names[sender]}邀请{names["user"]}玩一局{game}。',
        'created_at': time.time(), 'attachments': attachments,
    }
    event = {'type': 'msg_created' if private else 'chatroom_msg_created', 'data': message}
    async with get_db() as db:
        if not await (await db.execute(f'SELECT id FROM {parent} WHERE id=?', (source_id,))).fetchone():
            return cleaned
        cursor = await db.execute(
            f'INSERT OR IGNORE INTO {table} (id,{key},{role},content,created_at,attachments) VALUES (?,?,?,?,?,?)',
            (message['id'], source_id, 'system', message['content'], message['created_at'], json.dumps(attachments, ensure_ascii=False)),
        )
        if not cursor.rowcount:
            return cleaned
        await db.execute(f'UPDATE {parent} SET updated_at=? WHERE id=?', (message['created_at'], source_id))
        seq = await append_sync_event(db, event)
        await db.commit()
    await broadcast_synced(manager, event, seq)
    if on_saved:
        await on_saved(message)
    return cleaned
