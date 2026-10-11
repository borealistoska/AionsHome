import asyncio
import tempfile
import unittest
from pathlib import Path


class QQStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from qq_social.store import Store
        self.store = Store(Path(self.tmp.name) / 'qq.db')

    def test_switch_epoch_prevents_old_reply_after_reopen(self):
        self.assertFalse(self.store.settings()['enabled'])
        self.store.configure({'enabled': True})
        old = self.store.settings()['epoch']
        self.store.configure({'enabled': False})
        self.store.configure({'enabled': True})
        self.assertNotEqual(old, self.store.settings()['epoch'])

    def test_group_identity_is_bot_scoped_and_new_groups_are_closed(self):
        a = self.store.discover('aion', 'g1')
        b = self.store.discover('connor', 'g1')
        self.assertNotEqual(a['id'], b['id'])
        self.assertFalse(a['enabled'])
        self.store.configure({'bind_first_group': True})
        self.assertTrue(self.store.discover('aion', 'g2')['enabled'])
        self.assertFalse(self.store.discover('aion', 'g3')['enabled'])

    def test_event_dedup_and_member_memories_never_cross_groups(self):
        self.store.discover('aion', 'g1')
        row = dict(actor='aion', group_id='g1', message_id='m1', member_id='u1', name='朋友', content='喜欢星空', is_bot=False, direction='in', created_at=100)
        self.assertTrue(self.store.record(row))
        self.assertFalse(self.store.record(row))
        self.store.save_memory('aion', 'g1', 'u1', '喜欢星空', ['m1'])
        self.assertEqual(len(self.store.memories('aion', 'g1')), 1)
        self.assertEqual(self.store.memories('connor', 'g1'), [])
        self.assertEqual(self.store.memories('aion', 'g2'), [])

    def test_digest_reads_oldest_unprocessed_messages(self):
        for n in range(75):
            self.store.record(dict(actor='aion',group_id='g1',message_id=str(n),member_id='u',name='朋友',content='话题',is_bot=False,direction='in',created_at=n))
        rows=self.store.history('aion','g1',60,after=0,oldest=True)
        self.assertEqual(rows[0]['message_id'],'0')
        self.assertEqual(rows[-1]['message_id'],'59')

    def test_tunable_context_image_wait_and_interval_are_persisted(self):
        self.store.configure({'interval_seconds':15,'context_messages':70,'image_wait_seconds':90,'hourly_limit':45})
        cfg=self.store.settings()
        self.assertEqual((cfg['interval_seconds'],cfg['context_messages'],cfg['image_wait_seconds'],cfg['hourly_limit']),(15,70,90,45))
        for patch in ({'context_messages':101},{'image_wait_seconds':-1},{'interval_seconds':9}):
            with self.assertRaises(ValueError):
                self.store.configure(patch)


class QQPolicyTests(unittest.TestCase):
    def test_queued_natural_calls_keep_interval_even_if_previous_call_was_silent(self):
        from qq_social.policy import ReplyPolicy
        p=ReplyPolicy()
        cfg={'mode':'natural','interval_seconds':120}
        p.started('a',direct=False,now=1000)
        self.assertFalse(p.allow('a',direct=False,is_bot=False,settings=cfg,now=1050,include_candidate=False))
        self.assertTrue(p.allow('a',direct=False,is_bot=False,settings=cfg,now=1120,include_candidate=False))
        self.assertTrue(p.allow('a',direct=True,is_bot=False,settings=cfg,now=1050))

    def test_rate_and_bot_loop_limits(self):
        from qq_social.policy import ReplyPolicy
        p = ReplyPolicy()
        cfg = {'mode': 'natural', 'interval_seconds': 120, 'hourly_limit': 6, 'total_hourly_limit': 60}
        self.assertTrue(p.allow('a', direct=False, is_bot=False, settings=cfg, now=1000))
        p.sent('a', direct=False, is_bot=False, now=1000)
        self.assertFalse(p.allow('a', direct=False, is_bot=False, settings=cfg, now=1010))
        self.assertTrue(p.allow('a', direct=True, is_bot=False, settings=cfg, now=1010))
        for n in range(3):
            p.sent('b', direct=True, is_bot=True, now=1000+n*10)
        self.assertFalse(p.allow('b', direct=True, is_bot=True, settings=cfg, now=1100))
        p.human('b')
        self.assertTrue(p.allow('b', direct=True, is_bot=True, settings=cfg, now=1100))

    def test_respond_mode_never_initiates(self):
        from qq_social.policy import ReplyPolicy
        self.assertFalse(ReplyPolicy().allow('a', direct=False, is_bot=False, settings={'mode':'respond'}, now=1000))

    def test_queued_natural_reply_rechecks_spontaneous_budget_without_own_candidate(self):
        from qq_social.policy import ReplyPolicy
        p=ReplyPolicy()
        cfg={'mode':'natural','interval_seconds':120,'hourly_limit':1,'total_hourly_limit':60}
        p.candidate('a',1000)
        self.assertTrue(p.allow('a',direct=False,is_bot=False,settings=cfg,now=1001,include_candidate=False))
        p.sent('a',direct=False,is_bot=False,now=1001)
        self.assertFalse(p.allow('a',direct=False,is_bot=False,settings=cfg,now=1300,include_candidate=False))


class QQContextTests(unittest.TestCase):
    def test_model_is_exact_current_chatroom_selection(self):
        from qq_social.context import select_model
        cfg = {'aion_model':'chosen-a', 'connor_model':'chosen-c'}
        models = {'chosen-a':{}, 'chosen-c':{}}
        self.assertEqual(select_model('aion', cfg, models), 'chosen-a')
        self.assertEqual(select_model('connor', cfg, models), 'chosen-c')
        with self.assertRaises(ValueError):
            select_model('connor', {'connor_model':'missing'}, models)

    def test_public_output_removes_hidden_content_and_rejects_error(self):
        from qq_social.context import clean_reply
        self.assertEqual(clean_reply('<think>secret</think>你好<meta>internal</meta>[HOME:turn_on]'), '你好')
        self.assertEqual(clean_reply('[保持安静]'), '')
        with self.assertRaises(ValueError):
            clean_reply('[CodexCLI错误] timeout')

    def test_private_qq_image_reference_cannot_escape_media_directory(self):
        from ai_providers import _resolve_attachment_path
        path=_resolve_attachment_path('/qq-media/test.jpg')
        self.assertEqual(path.parent.name,'media')
        self.assertEqual(path.parent.parent.name,'qq_social')
        self.assertIsNone(_resolve_attachment_path('/qq-media/../../secrets.jpg'))


class QQImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_qq_cdn_domain_is_accepted_and_http_upgraded(self):
        from qq_social.media import image_attachments
        for scheme in ('https','http'):
            refs=image_attachments([{'content_type':'image/jpeg','url':scheme+'://multimedia.nt.qq.com.cn/download?token=test'}])
            self.assertEqual(len(refs),1)
            self.assertEqual(refs[0]['url'],'https://multimedia.nt.qq.com.cn/download?token=test')
        self.assertEqual(image_attachments([{'content_type':'image/jpeg','url':'https://multimedia.nt.qq.com.cn.evil.example/a.jpg'}]),[])

    async def test_download_validate_attach_and_cleanup(self):
        import io, httpx
        from PIL import Image
        from qq_social.media import image_attachments, prepare_images
        raw=io.BytesIO()
        Image.new('RGB',(40,30),'red').save(raw,format='PNG')
        refs=image_attachments([{'content_type':'image/png','url':'https://gchat.qpic.cn/test.png'}])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=raw.getvalue()))) as client:
            with tempfile.TemporaryDirectory() as tmp:
                async with prepare_images(refs,directory=Path(tmp),client=client) as (attachments,notes):
                    self.assertEqual(len(attachments),1)
                    self.assertEqual(notes,[])
                    self.assertTrue((Path(tmp)/attachments[0].split('/')[-1]).exists())
                self.assertEqual(list(Path(tmp).iterdir()),[])

    async def test_untrusted_url_and_nonimage_body_are_not_forwarded(self):
        import httpx
        from qq_social.media import image_attachments, prepare_images
        self.assertEqual(image_attachments([{'content_type':'image/png','url':'http://127.0.0.1/secret'}]),[])
        refs=image_attachments([{'content_type':'image/png','url':'https://gchat.qpic.cn/a'}])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=b'not an image'))) as client:
            with tempfile.TemporaryDirectory() as tmp:
                async with prepare_images(refs,directory=Path(tmp),client=client) as (attachments,notes):
                    self.assertEqual(attachments,[])
                    self.assertTrue(notes)
                    self.assertNotIn('https:',str(notes))

    async def test_image_history_is_scoped_to_current_speaker(self):
        import time
        from qq_social.media import recent_images
        refs=[{'url':'https://gchat.qpic.cn/a','content_type':'image/png'}]
        event={'member_id':'u1','attachments':[],'created_at':time.time()}
        history=[{'member_id':'u2','direction':'in','created_at':event['created_at']-1,'attachments':refs}]
        self.assertEqual(recent_images(event,history),[])
        history[0]['member_id']='u1'
        self.assertEqual(len(recent_images(event,history)),1)


class QQServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import time
        from qq_social.store import Store
        from qq_social.service import Service
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'qq.db')
        self.sent = []
        class Transport:
            user_id = 'bot-id'
            async def send(inner, group_id, content, message_id):
                self.sent.append(content)
                return {'id':'out1'}
        self.calls = 0
        async def generate(*args):
            self.calls += 1
            return '你好'
        self.service = Service(self.store, {'aion':Transport()}, generator=generate)
        self.event = {'id':'m1','group_openid':'g1','content':'你好', 'author':{'member_openid':'u1','username':'姐妹'}, 'timestamp':time.time()}

    async def asyncTearDown(self):
        await self.service.close()

    async def test_off_drops_body_and_duplicate_generates_once(self):
        await self.service.ingest('aion', 'GROUP_AT_MESSAGE_CREATE', self.event)
        self.assertEqual(self.store.history('aion','g1'), [])
        self.service.configure({'enabled':True,'bind_first_group':True})
        self.event['timestamp']=__import__('time').time()
        await self.service.ingest('aion', 'GROUP_AT_MESSAGE_CREATE', self.event)
        await self.service.ingest('aion', 'GROUP_AT_MESSAGE_CREATE', self.event)
        await self.service.drain()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.sent, ['你好'])

    async def test_close_reopen_discards_inflight_reply(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass  # even a provider that swallows cancellation cannot send
            return '旧回复'
        self.service.generator = slow
        self.service.configure({'enabled':True,'bind_first_group':True})
        self.event['timestamp']=__import__('time').time()
        await self.service.ingest('aion', 'GROUP_AT_MESSAGE_CREATE', self.event)
        await asyncio.wait_for(entered.wait(), 2)
        self.service.configure({'enabled':False})
        self.service.configure({'enabled':True})
        release.set()
        await self.service.drain()
        self.assertEqual(self.sent, [])

    async def test_stale_and_other_bot_echo_are_ignored(self):
        self.service.configure({'enabled':True,'bind_first_group':True})
        old = {**self.event, 'timestamp':1}
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',old)
        own = {**self.event, 'author':{'id':'bot-id','bot':True}}
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',own)
        await self.service.drain()
        self.assertEqual(self.calls, 0)

    async def test_delayed_event_from_disabled_period_is_not_stored(self):
        import time
        self.service.configure({'enabled':True,'bind_first_group':True})
        event={**self.event,'timestamp':time.time()-1}
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',event)
        self.assertEqual(self.store.history('aion','g1'),[])
        self.assertEqual(self.calls,0)

    async def test_disabling_cancels_memory_model_and_releases_slot(self):
        from unittest.mock import patch
        entered,cancelled=asyncio.Event(),asyncio.Event()
        async def summary(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.service.configure({'enabled':True,'bind_first_group':True})
        group=self.store.discover('aion','g1')
        with patch('qq_social.context.summarize_group',summary):
            task=asyncio.create_task(self.service.digest_group(group,[{'id':1}],self.store.settings()['epoch']))
            await asyncio.wait_for(entered.wait(),2)
            self.service.configure({'enabled':False})
            await asyncio.wait_for(cancelled.wait(),2)
            await asyncio.gather(task,return_exceptions=True)
        self.assertFalse(self.service.serial.locked())

    async def test_natural_reply_rechecks_total_budget_after_queue(self):
        import time
        self.service.configure({'enabled':True,'bind_first_group':True,'mode':'natural','total_hourly_limit':5})
        self.event['timestamp']=time.time()
        await self.service.serial.acquire()
        await self.service.ingest('aion','GROUP_MESSAGE_CREATE',self.event)
        for n in range(5):
            self.service.policy.sent(('aion','g1'),direct=True,is_bot=False,now=time.time()-10+n)
        self.service.serial.release()
        await self.service.drain()
        self.assertEqual(self.calls,0)
        self.assertEqual(self.sent,[])

    async def test_same_human_followup_does_not_bypass_natural_cooldown(self):
        import time
        self.service.configure({'enabled':True,'bind_first_group':True,'mode':'natural'})
        self.event['timestamp']=time.time()
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',self.event)
        await self.service.drain()
        self.sent.clear()
        self.calls=0
        self.service.policy.history[('aion','g1')].clear()
        self.service.policy.sent(('aion','g1'),direct=False,is_bot=False,now=time.time()-5)
        self.event['timestamp']=time.time()
        self.event['id']='followup'
        await self.service.ingest('aion','GROUP_MESSAGE_CREATE',self.event)
        await self.service.drain()
        self.assertEqual(self.sent,[])
        self.assertEqual(self.calls,0)

    async def test_image_wait_uses_saved_setting(self):
        import time
        self.service.configure({'enabled':True,'bind_first_group':True,'image_wait_seconds':90})
        picture={**self.event,'timestamp':time.time(),'content':'','attachments':[{'content_type':'image/jpeg','url':'https://multimedia.nt.qq.com.cn/a'}]}
        before=time.monotonic()
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',picture)
        remaining=self.service.image_batches[('aion','g1','u1')]['deadline']-before
        self.assertGreaterEqual(remaining,90)
        self.assertLess(remaining,91)

    async def test_group_context_uses_saved_message_count(self):
        from unittest.mock import AsyncMock, patch
        import time, json
        from qq_social.context import generate_reply
        self.store.configure({'context_messages':5,'bind_first_group':True})
        group=self.store.discover('aion','g1')
        for n in range(9):
            self.store.record(dict(actor='aion',group_id='g1',message_id=str(n),member_id='u1',name='朋友',content='消息'+str(n),is_bot=False,direction='in',created_at=time.time()-10+n))
        event={'message_id':'original','member_id':'u2','content':'排队前的问题','created_at':time.time(),'attachments':[]}
        diag={}
        with patch('qq_social.context.private_context',new=AsyncMock(return_value=('model',{'aion':'AI','user':'用户'},'private',{}))), patch('qq_social.context.call_model',new=AsyncMock(return_value='回复\n［带回小家记忆：一次有趣的讨论］')) as model:
            reply = await generate_reply('aion',group,event,self.store,True,diag)
        self.assertEqual((reply.text,reply.memory),('回复','一次有趣的讨论'))
        self.assertEqual(diag['group_messages'],5)
        content=model.call_args.args[0][-1]['content']
        transcript=json.loads(content.split('\n')[1])
        self.assertEqual([row['text'] for row in transcript],['消息4','消息5','消息6','消息7','消息8'])
        trigger=json.loads(content.split('[本次触发消息]\n')[1].split('\n')[0])
        self.assertEqual(trigger,{'id':'original','member':'u2','text':'排队前的问题'})

    async def test_mention_only_ignores_plain_and_quoted_messages(self):
        import time
        self.service.configure({'enabled':True,'bind_first_group':True,'mode':'respond'})
        self.store.record(dict(actor='aion',group_id='g1',message_id='previous',member_id='aion',name='AI',content='先前回复',is_bot=True,direction='out',created_at=time.time()-10,ref_id='ref-1'))
        for message_id, scene in [('plain',{}),('quoted',{'ext':['ref_msg_idx=ref-1']})]:
            event={**self.event,'id':message_id,'timestamp':time.time(),'message_scene':scene}
            await self.service.ingest('aion','GROUP_MESSAGE_CREATE',event)
            await self.service.drain()
        self.assertEqual(self.calls,0)
        self.event['timestamp']=time.time()
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',self.event)
        await self.service.drain()
        self.assertEqual(self.sent,['你好'])

    async def test_image_only_event_reaches_generator_and_history(self):
        import time
        received=[]
        async def capture(actor,group,event,*args):
            received.extend(event.get('attachments',[]))
            return '看到图片了'
        self.service.generator=capture
        self.service.image_wait_seconds=0
        self.service.configure({'enabled':True,'bind_first_group':True})
        self.event.update(timestamp=time.time(),content='',attachments=[{'content_type':'image/png','url':'https://gchat.qpic.cn/a.png'}])
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',self.event)
        await self.service.drain()
        self.assertEqual(len(received),1)
        import json
        self.assertEqual(len(json.loads(self.store.history('aion','g1')[0]['attachments'])),1)
        self.assertNotIn('只读取文字',self.store.history('aion','g1')[0]['content'])

    async def test_image_wait_combines_followup_once_without_resetting_deadline(self):
        import time
        received=[]
        async def capture(actor,group,event,*args):
            received.append(dict(event))
            return '合并回复'
        self.service.generator=capture
        self.service.image_wait_seconds=.08
        self.service.configure({'enabled':True,'bind_first_group':True,'mode':'natural'})
        picture={**self.event,'timestamp':time.time(),'content':'','attachments':[{'content_type':'image/png','url':'https://gchat.qpic.cn/one.png'}]}
        await self.service.ingest('aion','GROUP_MESSAGE_CREATE',picture)
        await asyncio.sleep(0)
        self.assertEqual(received,[])
        self.assertFalse(self.service.serial.locked())
        batch=self.service.image_batches[('aion','g1','u1')]
        deadline=batch['deadline']
        followup={**self.event,'id':'m2','timestamp':time.time(),'content':'帮我看看图里的颜色'}
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',followup)
        second={**picture,'id':'m3','timestamp':time.time(),'attachments':[{'content_type':'image/png','url':'https://gchat.qpic.cn/two.png'}]}
        await self.service.ingest('aion','GROUP_MESSAGE_CREATE',second)
        self.assertEqual(batch['deadline'],deadline)
        self.assertEqual(received,[])
        await self.service.drain()
        self.assertEqual(len(received),1)
        self.assertIn('帮我看看图里的颜色',received[0]['content'])
        self.assertEqual(len(received[0]['attachments']),2)
        self.assertEqual(self.sent,['合并回复'])

    async def test_image_wait_cancelled_by_close_and_other_speaker_not_merged(self):
        import time
        self.service.configure({'enabled':True,'bind_first_group':True})
        picture={**self.event,'timestamp':time.time(),'content':'','attachments':[{'content_type':'image/png','url':'https://gchat.qpic.cn/a.png'}]}
        await self.service.ingest('aion','GROUP_MESSAGE_CREATE',picture)
        other={**self.event,'id':'other','timestamp':time.time(),'author':{'member_openid':'u2'},'content':'别人的话'}
        await self.service.ingest('aion','GROUP_MESSAGE_CREATE',other)
        self.assertEqual(len(self.service.image_batches),1)
        self.assertNotIn('别人的话',self.service.image_batches[('aion','g1','u1')]['event']['content'])
        self.service.configure({'enabled':False})
        self.service.configure({'enabled':True})
        await self.service.drain()
        self.assertEqual(self.calls,0)
        self.assertEqual(self.sent,[])
        self.assertEqual(self.service.image_batches,{})

    async def test_followup_text_releases_image_wait_immediately(self):
        import time
        self.service.configure({'enabled':True,'bind_first_group':True,'mode':'natural'})
        picture={**self.event,'timestamp':time.time(),'content':'','attachments':[{'content_type':'image/jpeg','url':'https://multimedia.nt.qq.com.cn/a'}]}
        await self.service.ingest('aion','GROUP_MESSAGE_CREATE',picture)
        await asyncio.sleep(0)
        self.assertEqual(self.calls,0)
        followup={**self.event,'id':'caption','timestamp':time.time(),'content':'看看我这碗面'}
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',followup)
        await asyncio.wait_for(self.service.drain(),.5)
        self.assertEqual(self.calls,1)
        self.assertEqual(self.sent,['你好'])

    async def test_image_without_followup_keeps_timeout(self):
        import time
        self.service.image_wait_seconds=.04
        self.service.configure({'enabled':True,'bind_first_group':True,'mode':'natural'})
        picture={**self.event,'timestamp':time.time(),'content':'','attachments':[{'content_type':'image/jpeg','url':'https://multimedia.nt.qq.com.cn/a'}]}
        await self.service.ingest('aion','GROUP_AT_MESSAGE_CREATE',picture)
        await asyncio.sleep(0)
        self.assertEqual(self.calls,0)
        self.assertFalse(self.service.image_batches[('aion','g1','u1')]['ready'].is_set())
        await self.service.drain()
        self.assertEqual(self.calls,1)


class QQTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_token_and_group_reply_never_expose_secret(self):
        import httpx
        from qq_social.transport import BotTransport
        requests=[]
        def handler(req):
            requests.append(req)
            if req.url.path=='/app/getAppAccessToken':
                return httpx.Response(200,json={'access_token':'private-token','expires_in':7200})
            return httpx.Response(200,json={'id':'reply-id'})
        bot=BotTransport({'app_id':'123','secret':'private-secret'},lambda *args:None)
        await bot.client.aclose()
        bot.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            self.assertEqual(await bot.send('group1','你好','original'),{'id':'reply-id'})
            self.assertEqual(requests[-1].url.path,'/v2/groups/group1/messages')
            self.assertEqual(__import__('json').loads(requests[-1].content)['msg_id'],'original')
            self.assertNotIn('private',str(bot.status))
        finally:
            await bot.close()

    async def test_http_200_token_error_is_a_failure(self):
        import httpx
        from qq_social.transport import BotTransport,QQAPIError
        bot=BotTransport({'app_id':'123','secret':'private-secret'},lambda *args:None)
        await bot.client.aclose()
        bot.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'code':100016,'message':'private-secret'})))
        try:
            with self.assertRaises(QQAPIError) as caught:
                await bot.authorize()
            self.assertNotIn('private-secret',str(caught.exception))
        finally:
            await bot.close()


class QQManagementTests(unittest.TestCase):
    def test_worker_rejects_unauthorized_requests(self):
        from fastapi.testclient import TestClient
        from qq_social.worker import create_app
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            (directory/'management.key').write_text('private-management-key')
            client=TestClient(create_app(directory))
            self.assertEqual(client.get('/status').status_code,401)
            self.assertEqual(client.patch('/settings',json={'enabled':True}).status_code,401)
            self.assertNotIn('private-management-key',client.get('/status').text)


class QQCredentialSetupTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch, AsyncMock
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes import qq_social
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        for target, value in (
            ('routes.qq_social.DIRECTORY', self.directory),
            ('qq_social.context.home_config', lambda: ({}, {}, {}, {'aion':'角色甲','connor':'角色乙'})),
            ('qq_social.transport.BotTransport.authorize', AsyncMock()),
        ):
            mocked = patch(target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        app = FastAPI()
        app.include_router(qq_social.router)
        self.client = TestClient(app)

    def save(self, **body):
        return self.client.patch('/api/qq-social/credentials', json=body,
                                 headers={'origin':'http://testserver'})

    def test_first_setup_supports_one_bot_and_never_returns_secrets(self):
        import json
        from unittest.mock import patch, AsyncMock
        from qq_social.store import Store
        with patch('routes.qq_social.connect_saved_credentials', AsyncMock(return_value='connecting'), create=True):
            before = self.client.get('/api/qq-social/credentials')
            self.assertEqual(before.status_code, 200)
            self.assertEqual(before.json()['actors']['aion']['name'], '角色甲')
            response = self.save(actor='aion', app_id='123456', secret='private-setup-secret')
            self.assertEqual(response.status_code, 200)
        saved = json.loads((self.directory/'credentials.json').read_text())
        self.assertEqual(saved, {'aion':{'app_id':'123456','secret':'private-setup-secret'}})
        self.assertTrue((self.directory/'management.key').is_file())
        self.assertFalse(Store(self.directory/'social.db').settings()['enabled'])
        self.assertTrue(Store(self.directory/'social.db').settings()['bind_first_group'])
        public = self.client.get('/api/qq-social/credentials')
        self.assertTrue(public.json()['actors']['aion']['configured'])
        self.assertFalse(public.json()['actors']['connor']['configured'])
        self.assertNotIn('private-setup-secret', public.text + response.text)

    def test_invalid_secret_or_cross_origin_does_not_replace_working_config(self):
        import json
        from unittest.mock import patch, AsyncMock
        existing = {'aion':{'app_id':'123456','secret':'working-secret'}}
        (self.directory/'credentials.json').write_text(json.dumps(existing))
        response = self.client.patch('/api/qq-social/credentials', json={'actor':'aion','app_id':'999','secret':'bad'},
                                     headers={'origin':'https://elsewhere.invalid'})
        self.assertEqual(response.status_code,403)
        with patch('qq_social.transport.BotTransport.authorize', AsyncMock(side_effect=ValueError('sensitive provider response'))):
            response = self.save(actor='aion', app_id='123456', secret='wrong-secret')
        self.assertEqual(response.status_code,400)
        self.assertNotIn('sensitive',response.text)
        self.assertEqual(json.loads((self.directory/'credentials.json').read_text()),existing)
        self.assertEqual(self.save(actor='aion', app_id='different-id', secret='').status_code,400)

    def test_blank_secret_keeps_existing_bot_and_settings(self):
        import json
        from unittest.mock import patch, AsyncMock
        from qq_social.store import Store
        existing = {'aion':{'app_id':'123456','secret':'working-secret'},
                    'connor':{'app_id':'654321','secret':'other-secret'}}
        (self.directory/'credentials.json').write_text(json.dumps(existing))
        Store(self.directory/'social.db').configure({'enabled':False,'interval_seconds':345})
        with patch('routes.qq_social.connect_saved_credentials', AsyncMock(return_value='connecting'), create=True):
            response = self.save(actor='aion', app_id='123456', secret='')
        self.assertEqual(response.status_code,200)
        self.assertEqual(json.loads((self.directory/'credentials.json').read_text()),existing)
        self.assertEqual(Store(self.directory/'social.db').settings()['interval_seconds'],345)
        self.assertFalse(Store(self.directory/'social.db').settings()['enabled'])

    def test_worker_reloads_changed_credentials_without_restarting_home(self):
        import json
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from qq_social.worker import create_app
        from qq_social.service import Service
        created = []
        services, pending, drained_at_close = [], [], []
        def capture_service(*args):
            service = Service(*args)
            services.append(service)
            return service
        async def pending_reply():
            await asyncio.Future()
        async def queue_reply():
            task = asyncio.create_task(pending_reply())
            pending.append(task)
            services[0].tasks.add(task)
            task.add_done_callback(services[0].tasks.discard)
            await asyncio.sleep(0)
        class FakeBot:
            def __init__(self, credentials, event):
                self.credentials = credentials
                self.closed = False
                created.append(self)
            async def run(self):
                await asyncio.Future()
            async def close(self):
                self.closed = True
                drained_at_close.append(all(task.done() for task in pending))
        (self.directory/'management.key').write_text('test-management')
        path = self.directory/'credentials.json'
        path.write_text(json.dumps({'aion':{'app_id':'123','secret':'first'}}))
        with patch('qq_social.worker.BotTransport', FakeBot), patch('qq_social.worker.Service',capture_service), TestClient(create_app(self.directory)) as client:
            self.assertEqual(len(created),1)
            client.portal.call(queue_reply)
            path.write_text(json.dumps({'aion':{'app_id':'123','secret':'changed'}}))
            self.assertEqual(client.post('/reload-credentials').status_code,401)
            response = client.post('/reload-credentials', headers={'authorization':'Bearer test-management'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(created),2)
            self.assertTrue(created[0].closed)
            self.assertTrue(drained_at_close[0], 'Pending replies must stop before old connection closes')
            self.assertEqual(created[1].credentials['secret'],'changed')
            client.post('/reload-credentials', headers={'authorization':'Bearer test-management'})
            self.assertEqual(len(created),2)

    def test_adding_bot_during_memory_retry_keeps_digest_loop_alive(self):
        from unittest.mock import patch
        from qq_social.store import Store
        from qq_social.service import Service
        async def exercise():
            store = Store(self.directory/'digest.db')
            store.configure({'enabled':True})
            transports = {'aion':object()}
            service = Service(store,transports)
            sleeps = 0
            async def sleep(_):
                nonlocal sleeps
                sleeps += 1
                if sleeps > 1:
                    raise asyncio.CancelledError()
            async def retry(actor):
                transports['connor'] = object()
            with patch('qq_social.service.asyncio.sleep',sleep), patch('external_memories.retry_pending_index',retry):
                with self.assertRaises(asyncio.CancelledError):
                    await service.digest_groups()
        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
