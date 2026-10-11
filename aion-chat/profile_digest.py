"""Reuse memory digest calls; persist profile results before memory anchors can advance."""
import hashlib
import copy
import json
import logging
import time

import post_sentinel as profile
from profile_prompts import digest_prompt
from user_profile_policy import maintain

log = logging.getLogger(__name__)


async def _source(db, source_id):
    if source_id == 'config:user_persona':
        text = profile.load_worldbook().get('user_persona') or ''
        return {'content': text, 'created_at': 0, 'speaker': 'user'} if text else None
    scope, _, message_id = source_id.partition(':')
    if scope not in ('private', 'chatroom') or not message_id:
        return None
    table, speaker = ('messages', 'role') if scope == 'private' else ('chatroom_messages', 'sender')
    row = await (await db.execute(f'SELECT content,created_at,{speaker} AS speaker FROM {table} WHERE id=?',
                                 (message_id,))).fetchone()
    return dict(row) if row else None


async def prepare(group, actor_name, user_name):
    """Return prompt plus evidence/snapshot; no model call, no memory-anchor changes."""
    if not profile.enabled():
        return None
    await profile.ensure_schema()
    async with profile.get_db() as db:
        row, document = await profile._read(db)
        evidence = {}
        for msg in group:
            if msg.get('sender', msg.get('role')) != 'user':
                continue
            source_id = msg.get('_source_id', '')
            actual = await _source(db, source_id)
            if actual and actual['speaker'] == 'user':
                evidence[source_id] = {**actual, 'text': msg.get('content', '')}
        # A malformed profile output must remain recoverable after its memory batch succeeded.
        pending = await (await db.execute(
            "SELECT batch_key,payload,error FROM post_sentinel_digest_jobs WHERE status='pending' "
            'ORDER BY created_at LIMIT 4')).fetchall()
        retries, repair_material = [], []
        for pending_row in pending:
            old = json.loads(pending_row['payload'])
            if old.get('epoch') != row['epoch']:
                continue
            retries.append(pending_row['batch_key'])
            for source_id, source in old.get('evidence', {}).items():
                current = await _source(db, source_id)
                if not current or current['speaker'] != 'user':
                    continue  # A deleted source must not poison unrelated new evidence.
                if any(current[k] != source[k] for k in ('content', 'created_at', 'speaker')):
                    source = {**current, 'text': current['content']}
                # New explicit evidence wins over an old pending snapshot of the same message.
                if source_id not in evidence:
                    evidence[source_id] = source
                    repair_material.append({'id': source_id, 'time': source['created_at'], 'text': source['text']})
    maintain(document)
    prompt = digest_prompt(document, actor_name, user_name)
    if repair_material:
        prompt += ('\n【之前未完成的画像维护，请一并补处理；这些原文只供画像，不重复生成聊天记忆】\n'
                   + json.dumps(repair_material, ensure_ascii=False))
    identity = json.dumps([row['epoch'], row['revision'], sorted(evidence.items()), retries], ensure_ascii=False, sort_keys=True)
    return {'batch_key': hashlib.sha256(identity.encode()).hexdigest(), 'prompt': prompt,
            'epoch': row['epoch'], 'evidence': evidence, 'retry_keys': retries,
            'refs': profile._profile_refs(document), 'prepared_at': time.time()}


async def store_result(context, result):
    """Durability is required before the caller advances the existing memory anchor."""
    if context is None:
        return
    payload = {k: v for k, v in context.items() if k != 'prompt'}
    payload['updates'] = result.get('profile_updates')
    async with profile.get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        row, _ = await profile._read(db)
        if not profile.enabled() or row['epoch'] != context['epoch']:
            return
        await db.execute('INSERT OR IGNORE INTO post_sentinel_digest_jobs(batch_key,payload,created_at) VALUES(?,?,?)',
                         (context['batch_key'], json.dumps(payload, ensure_ascii=False), time.time()))
        await db.commit()
    try:
        await apply_pending()
    except Exception:
        # The result is already durable and will be retried by the existing worker.
        log.exception('Profile result saved; application deferred')


async def apply_pending():
    if not profile.enabled():
        return False
    async with profile.get_db() as db:
        await db.execute('BEGIN IMMEDIATE')
        row, document = await profile._read(db)
        jobs = await (await db.execute("SELECT * FROM post_sentinel_digest_jobs WHERE status='pending' AND error='' "
                                       'ORDER BY created_at LIMIT 20')).fetchall()
        changed = False
        for record in jobs:
            job = json.loads(record['payload'])
            error, status = '', 'done'
            if job['epoch'] != row['epoch']:
                status = 'skipped'
            elif not isinstance(job.get('updates'), (list, dict)):
                status, error = 'pending', '总结未返回有效profile_updates，下次总结时补处理'
            else:
                valid = True
                for source_id, saved in job['evidence'].items():
                    current = await _source(db, source_id)
                    if not current or any(current[k] != saved[k] for k in ('content', 'created_at', 'speaker')):
                        valid = False
                        break
                if not valid:
                    status = 'skipped'
                else:
                    sources = {source_id: source_id for source_id in job['evidence']}
                    context = {'digest': True, 'consolidation': True, 'source_ts': 0,
                               'defer_budget': True,
                               '_source_times': {key: val['created_at'] for key, val in job['evidence'].items()},
                               '_refs': job['refs'], '_base_entries': {e['key']: e for e in job['refs'].values()}}
                    updates = job['updates']
                    changed |= maintain(document)
                    candidate = copy.deepcopy(document)
                    applied = profile._apply_analysis(candidate, {'updates': updates, 'state': None}, sources, context)
                    errors = candidate.pop('_analysis_errors', [])
                    try:
                        profile._check_budget(candidate)
                    except ValueError as exc:
                        errors.append(str(exc))
                    else:
                        document = candidate
                        changed |= applied
                    if errors:
                        status, error = 'pending', '；'.join(errors)
                    else:
                        document['last_digest_at'] = time.time()
                        changed = True
                        for retry_key in job.get('retry_keys', []):
                            await db.execute("UPDATE post_sentinel_digest_jobs SET status='done',payload='{}',error='' WHERE batch_key=?",
                                             (retry_key,))
            await db.execute('UPDATE post_sentinel_digest_jobs SET status=?,error=?,payload=? WHERE batch_key=?',
                             (status, error, record['payload'] if status == 'pending' else '{}', record['batch_key']))
        if changed:
            maintain(document)
            await db.execute('UPDATE post_sentinel_document SET payload=?,revision=revision+1,updated_at=? WHERE id=1',
                             (json.dumps(document, ensure_ascii=False), time.time()))
        await db.commit()
    if jobs:
        await profile._notify_changed()
    return bool(jobs)
