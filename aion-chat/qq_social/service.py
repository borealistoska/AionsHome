"""Bounded event handling. Every send rechecks the persisted switch epoch."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime

from .policy import ReplyPolicy


def event_time(value):
    if isinstance(value,(int,float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError):
        return time.time()


class Service:
    def __init__(self, store, transports, generator=None):
        from .context import generate_reply
        self.store, self.transports = store, transports
        self.generator = generator or generate_reply
        self.policy = ReplyPolicy()
        self.tasks = set()
        self.serial = asyncio.Semaphore(1)
        self.diagnostics = {}
        self.last_event = {}
        self.last_error = {}
        self.last_send = {}
        self.digest_attempt = {}
        self.digest_task = None
        self.image_wait_seconds = None  # optional test override; live value comes from settings
        self.image_batches = {}
        self.started_at = time.time()

    def configure(self, patch):
        cfg = self.store.configure(patch)
        self.cancel_pending()
        return cfg

    def cancel_pending(self):
        self.image_batches.clear()
        for task in list(self.tasks):
            task.cancel()
        if self.digest_task:
            self.digest_task.cancel()

    def active(self, actor, group_id, epoch):
        cfg = self.store.settings()
        group = self.store.group(actor, group_id)
        return cfg['enabled'] and cfg['epoch']==epoch and group and group['enabled']

    async def ingest(self, actor, kind, data):
        if kind not in ('GROUP_AT_MESSAGE_CREATE','GROUP_MESSAGE_CREATE','GROUP_ADD_ROBOT'):
            return
        self.last_event[actor] = {'kind':kind, 'at':time.time()}
        cfg = self.store.settings()
        if not cfg['enabled']:
            return
        group_id = str(data.get('group_openid') or '')
        if not group_id:
            return
        group = self.store.discover(actor, group_id)
        if kind == 'GROUP_ADD_ROBOT' or not group['enabled']:
            return
        if kind == 'GROUP_MESSAGE_CREATE':
            self.store.full_messages(actor, group_id)
        created = event_time(data.get('timestamp'))
        # No grace window: delayed messages produced while disabled stay discarded.
        if created < max(self.started_at, cfg['enabled_at']) or time.time()-created > 240:
            return
        author = data.get('author') or {}
        member = str(author.get('member_openid') or author.get('id') or '')
        if not member or member == self.transports[actor].user_id or str(author.get('id','')) == self.transports[actor].user_id:
            return
        from .media import image_attachments, attachment_metadata
        attachments = image_attachments(data.get('attachments'))
        if data.get('attachments'):
            self.diagnostics.setdefault(actor,{}).update(
                attachment_input=attachment_metadata(data['attachments']),
                recognized_images=len(attachments))
        content = str(data.get('content') or '').strip()
        has_text = bool(re.sub(r'<@!?[^>]+>', '', content).strip())
        if not content:
            content = '[发送了图片]' if attachments else ('[发送了暂不支持的附件]' if data.get('attachments') else '')
        if not content or not data.get('id'):
            return
        ext = {}
        for item in (data.get('message_scene') or {}).get('ext', []):
            if isinstance(item,str) and '=' in item:
                k,v = item.split('=',1)
                if k in ('msg_idx','ref_msg_idx'):
                    ext[k] = v
        event = dict(actor=actor, group_id=group_id, message_id=str(data['id']),
                     member_id=member, name=str(author.get('username') or '')[:80],
                     content=content[:6000], is_bot=bool(author.get('bot')), direction='in',
                     created_at=created, ref_id=ext.get('msg_idx',''), attachments=attachments)
        if not self.store.record(event):
            return
        key = (actor,group_id)
        if not event['is_bot']:
            self.policy.human(key)
        direct = kind == 'GROUP_AT_MESSAGE_CREATE'
        # Replies may be explicit mentions in full-message mode as well.
        direct = direct or any(str(m.get('id',''))==self.transports[actor].user_id for m in data.get('mentions',[]) if isinstance(m,dict))
        # Same speaker's text releases the image batch; images alone wait up to 60s.
        batch_key = (actor,group_id,member)
        batch = self.image_batches.get(batch_key)
        if batch and time.monotonic() < batch['deadline']:
            previous = batch['event']
            merged_images = {a['url']:a for a in previous['attachments'] + attachments}
            batch['event'] = {**event,
                'content':(previous['content']+'\n'+event['content'])[-12000:],
                'attachments':list(merged_images.values())[:4], 'image_batch':True}
            batch['direct'] = batch['direct'] or direct
            if has_text:
                batch['ready'].set()
            return
        if attachments:
            if len(self.tasks) >= 8:
                self.last_error[actor] = '群聊较忙，本条未排队；请稍后再试'
                return
            batch = {'event':{**event,'image_batch':True}, 'direct':direct,
                     'deadline':time.monotonic()+(cfg['image_wait_seconds'] if self.image_wait_seconds is None else self.image_wait_seconds),
                     'ready':asyncio.Event()}
            self.image_batches[batch_key] = batch
            task = asyncio.create_task(self.image_reply(batch_key,batch,group,cfg['epoch']))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return
        if not self.policy.allow(key,direct=direct,is_bot=event['is_bot'],settings=cfg,now=time.time()):
            return
        if len(self.tasks) >= 8:
            self.last_error[actor] = '群聊较忙，本条未排队；请稍后再试'
            return
        if not direct:
            self.policy.candidate(key,time.time())
        task = asyncio.create_task(self.reply(actor,group,event,direct,cfg['epoch']))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def image_reply(self, batch_key, batch, group, epoch):
        actor,group_id,_ = batch_key
        try:
            # The timer does not hold the model slot or download any pictures.
            try:
                await asyncio.wait_for(batch['ready'].wait(),max(0,batch['deadline']-time.monotonic()))
            except TimeoutError:
                pass
            if self.image_batches.get(batch_key) is batch:
                self.image_batches.pop(batch_key)
            if not self.active(actor,group_id,epoch):
                return
            event,direct = batch['event'],batch['direct']
            if not self.policy.allow((actor,group_id),direct=direct,is_bot=event['is_bot'],settings=self.store.settings(),now=time.time()):
                return
            if not direct:
                self.policy.candidate((actor,group_id),time.time())
            await self.reply(actor,group,event,direct,epoch)
        finally:
            if self.image_batches.get(batch_key) is batch:
                self.image_batches.pop(batch_key)

    async def reply(self, actor, group, event, direct, epoch):
        key = (actor,group['group_id'])
        try:
            async with self.serial:
                if not self.active(*key,epoch) or time.time()-event['created_at'] > 210:
                    return
                # Recheck global caps after waiting behind another reply.
                if not self.policy.allow(key,direct=direct,is_bot=event['is_bot'],settings=self.store.settings(),now=time.time(),include_candidate=False):
                    return
                diag = self.diagnostics.setdefault(actor,{})
                self.policy.started(key,direct=direct,now=time.time())
                from external_memories import ExternalReply
                generated = await asyncio.wait_for(self.generator(actor,group,event,self.store,direct,diag), 180)
                reply = generated if isinstance(generated, ExternalReply) else ExternalReply(generated)
                text = reply.text
                if not self.active(*key,epoch) or time.time()-event['created_at'] > 270:
                    return
                if text:
                    result = await self.transports[actor].send(group['group_id'],text,event['message_id'])
                    self.policy.sent(key,direct=direct,is_bot=event['is_bot'],now=time.time())
                    self.last_send[actor] = time.time()
                    self.last_error.pop(actor,None)
                    self.store.record(dict(actor=actor,group_id=group['group_id'],message_id=str(result.get('id') or f'local-{time.time_ns()}'),member_id=actor,name=actor,content=text,is_bot=True,direction='out',created_at=time.time(),ref_id=(result.get('ext_info') or {}).get('ref_idx','')))
                if reply.memory and self.active(*key,epoch) and self.store.settings()['bring_home']:
                    await self.bring_memory_home(actor, group, event, reply, diag)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # No raw exceptions: provider errors may contain tokens or request bodies.
            self.last_error[actor] = f'回复未发送：{type(exc).__name__}；可检查模型或QQ权限后重试'

    async def bring_memory_home(self, actor, group, event, reply, diag):
        from external_memories import bring_home, index_memory
        try:
            mem_id = await bring_home(actor, reply.memory, platform='qq', channel=group['group_id'],
                message_id=event['message_id'], occurred_at=event['created_at'], participants=reply.participants,
                evidence=reply.evidence + ('\n本次已发送回复：'+reply.text if reply.text else ''))
            diag.update(memory_status='saved', memory_at=time.time(), memory_id=mem_id)
        except Exception:
            diag.update(memory_status='failed', memory_at=time.time())
            return
        # Text is already durable; embedding failure never changes the saved receipt.
        try:
            await index_memory(actor, mem_id)
        except Exception:
            pass

    async def digest_groups(self):
        while True:
            await asyncio.sleep(60)
            if not self.store.settings()['enabled'] or self.tasks:
                continue
            # Retry missing search vectors without regenerating or duplicating memories.
            from external_memories import retry_pending_index
            for actor in list(self.transports):
                try:
                    await retry_pending_index(actor)
                except Exception:
                    pass
            for group in self.store.groups():
                key = (group['actor'],group['group_id'])
                if not group['enabled'] or not group['memory_enabled'] or time.time()-self.digest_attempt.get(key,0)<600:
                    continue
                rows = self.store.history(*key,limit=60,after=group['digest_cursor'],oldest=True)
                if len(rows)<12 or time.time()-rows[-1]['created_at']<120:
                    continue
                epoch = self.store.settings()['epoch']
                self.digest_attempt[key] = time.time()
                # A cancelled child must not kill the periodic scheduler itself.
                await asyncio.gather(asyncio.create_task(self.digest_group(group,rows,epoch)),return_exceptions=True)

    async def digest_group(self,group,rows,epoch):
        from .context import summarize_group
        key = (group['actor'],group['group_id'])
        self.digest_task = asyncio.current_task()
        try:
            async with self.serial:
                if not self.active(*key,epoch):
                    return
                summary, memories = await asyncio.wait_for(summarize_group(group['actor'],group,rows,self.store),120)
                latest = self.store.group(*key)
                if self.active(*key,epoch) and latest['memory_enabled']:
                    for member,content,sources in memories:
                        self.store.save_memory(*key,member,content,sources)
                    self.store.digest(*key,summary,rows[-1]['id'])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error[group['actor']] = f'群记忆整理稍后重试：{type(exc).__name__}'
        finally:
            self.digest_task = None

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def close(self):
        self.image_batches.clear()
        for task in list(self.tasks):
            task.cancel()
        if self.digest_task:
            self.digest_task.cancel()
            await asyncio.gather(self.digest_task,return_exceptions=True)
        await self.drain()
