import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class ExternalActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import external_activity as activity
        self.activity = activity
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = patch.object(activity, 'PATH', Path(self.tmp.name)/'activity.db')
        self.path.start()
        self.addCleanup(self.path.stop)

    def save(self, actor='aion', channel='group', now=None, **kw):
        now = now or time.time()
        return self.activity.save_summary(actor, 'qq', channel, '朋友群', '一起聊兔子。',
            source_start=now-30, updated_at=now, generation=self.activity.settings()['generation'], **kw)

    async def test_role_isolation_expiry_and_switch_generation(self):
        now=time.time()
        self.save(now=now)
        self.assertIn('兔子',await self.activity.recent_context('aion',now=now))
        self.assertEqual(await self.activity.recent_context('connor',now=now),'')
        self.assertEqual(await self.activity.recent_context('aion',now=now+3601),'')
        old=self.activity.settings()['generation']
        self.activity.configure(False)
        self.assertEqual(await self.activity.recent_context('aion'),'')
        self.activity.configure(True)
        self.assertFalse(self.activity.save_summary('aion','qq','group','群','迟到的摘要',
            source_start=now,updated_at=now,generation=old))
        self.assertFalse(self.activity.save_summary('aion','qq','old','群','过期素材',
            source_start=now-7201,updated_at=now,generation=self.activity.settings()['generation']))

    async def test_context_is_bounded_and_owner_presence_is_not_direct_interaction(self):
        now=time.time()
        for i in range(6):
            self.save(channel=str(i),now=now-i)
        self.activity.touch_owner('aion','qq','0',direct=False,at=now)
        self.assertEqual(self.activity.last_interaction('aion',now=now),0)
        self.activity.touch_owner('aion','qq','0',direct=True,at=now)
        self.assertEqual(self.activity.last_interaction('aion',now=now),now)
        self.assertEqual(self.activity.last_interaction('aion',now=now+3610),now)
        self.assertEqual(await self.activity.recent_context('aion',now=now+3610),'')
        self.assertEqual(self.activity.last_interaction('connor',now=now),0)
        context=await self.activity.recent_context('aion',now=now)
        self.assertLessEqual(len(context),700)
        self.assertEqual(context.count('朋友群'),3)
        self.assertIn('直接交流',context)

    async def test_qq_output_is_separated_and_invalid_output_never_sent(self):
        from qq_social.context import generate_reply
        from qq_social.service import Service
        from qq_social.store import Store
        store=Store(Path(self.tmp.name)/'qq.db')
        store.configure({'enabled':True,'bind_first_group':True})
        sent=[]
        class Bot:
            user_id='bot'
            async def send(self,group,text,message_id,**kw):
                sent.append(text)
                return {'id':str(len(sent))}
        service=Service(store,{'aion':Bot()})
        payload=json.dumps({'reply':'收到啦。','memory':'','activity_summary':'内部摘要：刚和用户聊兔子。'},ensure_ascii=False)
        try:
            for i,raw in enumerate([payload,'收到啦。\n内部摘要：泄漏', '{"reply":"半截',
                                    json.dumps({'reply':{'activity_summary':'秘密'}})]):
                service.policy.history.clear()
                with patch('qq_social.context.private_context',new=AsyncMock(return_value=('model',{'aion':'AI','user':'用户'},'private',{}))), patch('qq_social.context.call_model',new=AsyncMock(return_value=raw)) as model:
                    await service.ingest('aion','GROUP_AT_MESSAGE_CREATE',dict(id=str(i),group_openid='g',content='兔子真可爱',author={'id':'user'},timestamp=time.time()))
                    await service.drain()
                    self.assertEqual(model.await_count,1)
            self.assertEqual(sent,['收到啦。'])
            self.assertIn('内部摘要',await self.activity.recent_context('aion'))
        finally:
            await service.close()

    async def test_home_context_and_settings_api_switch_without_worker(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.qq_social import router
        import context_builder as cb
        self.save()
        app=FastAPI()
        app.include_router(router)
        with TestClient(app) as client, patch.object(cb,'build_capability_prompt_items',new=AsyncMock(return_value=[])), patch.object(cb,'is_capability_enabled',return_value=False), patch.object(cb,'get_device_context_for_prompt',return_value=''), patch.object(cb,'build_cli_file_storage_text',return_value=''), patch('external_memories.recent_context',new=AsyncMock(return_value='')):
            self.assertTrue(client.get('/api/qq-social/activity').json()['enabled'])
            body=await cb.build_ability_block('用户',who='aion',excluded_capabilities={'post_sentinel'})
            self.assertIn('兔子',body)
            self.assertEqual(client.patch('/api/qq-social/activity',json={'enabled':'no'}).status_code,400)
            self.assertEqual(client.patch('/api/qq-social/activity',json={'enabled':False},headers={'origin':'https://foreign.invalid'}).status_code,403)
            self.assertEqual(client.patch('/api/qq-social/activity',json={'enabled':False}).status_code,200)
            body=await cb.build_ability_block('用户',who='aion',excluded_capabilities={'post_sentinel'})
            self.assertNotIn('兔子',body)

    async def test_unavailable_cache_does_not_block_context(self):
        import sqlite3
        self.save()
        with patch.object(self.activity,'connection',side_effect=sqlite3.OperationalError('locked')):
            self.assertEqual(await self.activity.recent_context('aion'),'')
            self.assertEqual(self.activity.last_interaction('aion'),0)

    async def test_summary_view_lists_only_current_role_scoped_cache(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.qq_social import router
        now=time.time()
        self.save(actor='aion',channel='current',now=now)
        self.save(actor='aion',channel='expired',now=now-3601)
        self.save(actor='connor',channel='other',now=now)
        app=FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            result=client.get('/api/qq-social/activity/summaries')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.headers['cache-control'],'no-store')
            data=result.json()
            self.assertEqual(len(data['actors']['aion']),1)
            self.assertEqual(len(data['actors']['connor']),1)
            row=data['actors']['aion'][0]
            self.assertEqual(row['summary'],'一起聊兔子。')
            self.assertEqual(row['expires_at'],row['updated_at']+3600)
            self.assertNotIn('channel',row)
            self.activity.configure(False)
            data=client.get('/api/qq-social/activity/summaries').json()
            self.assertFalse(data['enabled'])
            self.assertEqual(data['actors'],{'aion':[],'connor':[]})

    async def test_natural_reply_counts_owner_contact_even_without_summary(self):
        from qq_social.context import QQReply
        from qq_social.service import Service
        from qq_social.store import Store
        store=Store(Path(self.tmp.name)/'qq.db')
        store.configure({'enabled':True,'bind_first_group':True,'mode':'natural'})
        at=time.time()
        reply=QQReply('公开回复',activity_owner_at=at,activity_generation=0)
        bot=type('Bot',(),{'user_id':'bot','send':AsyncMock(return_value={'id':'out'})})()
        service=Service(store,{'aion':bot},generator=AsyncMock(return_value=reply))
        try:
            await service.ingest('aion','GROUP_MESSAGE_CREATE',dict(id='in',group_openid='g',content='你好',author={'id':'user'},timestamp=time.time()))
            await service.drain()
            self.assertEqual(self.activity.last_interaction('aion'),at)
            self.activity.configure(False)
            self.activity.configure(True)
            self.activity.touch_owner('aion','qq','g',direct=True,at=at,generation=0)
            self.assertEqual(self.activity.last_interaction('aion'),0)
        finally:
            await service.close()
