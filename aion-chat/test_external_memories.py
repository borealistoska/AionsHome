import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite


class ExternalMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'home.db'
        self.db_patch = patch.object(database, 'DB_PATH', self.path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        await database.init_db()
        self.embedding = patch('memory.get_embedding', new=AsyncMock(return_value=[.1, .2]))
        self.embedding.start()
        self.addCleanup(self.embedding.stop)

    def test_only_own_standalone_command_is_executed_and_never_leaks(self):
        from external_memories import split_memory_command
        text, memory = split_memory_command('好呀！\n［带回小家记忆：和朋友约好下次去看兔子。］')
        self.assertEqual((text, memory), ('好呀！', '和朋友约好下次去看兔子。'))
        self.assertEqual(split_memory_command('你好\n［带回小家记忆：朋友发了[晚安]表情］'),
                         ('你好','朋友发了[晚安]表情'))
        self.assertEqual(split_memory_command('你好\n［带回小家记忆：朋友邀请我\n明天一起玩］'), ('你好',''))
        for raw in ('朋友说［带回小家记忆：转述］', '```\n[带回小家记忆：示例]\n```',
                    '> [带回小家记忆：引用]', '<think>[带回小家记忆：思考]</think>你好',
                    '你好\n[带回小家记忆：未完成', '[带回小家记忆：不是末行]\n```\n后续代码\n```'):
            visible, selected = split_memory_command(raw)
            self.assertEqual(selected, '')
            self.assertNotIn('带回小家', visible)

    async def test_native_stores_are_separate_idempotent_and_recent_context_tracks_edits(self):
        from external_memories import bring_home, recent_context, index_memory
        source = dict(platform='qq', channel='friends', message_id='m1', occurred_at=time.time(),
                      participants=[{'id':'u1','name':'小雨'}], evidence='小雨：下次来我家看兔子吧。')
        first = await bring_home('aion', '和小雨约好下次去看兔子。', **source)
        self.assertEqual(await bring_home('aion', '同一事件重试不重复保存。', **source), first)
        second = await bring_home('connor', '朋友讲了有趣的养花经历。', **source)
        await index_memory('aion', first)
        await index_memory('connor', second)
        from memory import recall_memories
        from chatroom import recall_chatroom_memories
        self.assertEqual((await recall_memories('兔子'))[0][0]['id'], first)
        self.assertEqual((await recall_chatroom_memories('养花',scope='connor'))[0]['id'], second)
        async with aiosqlite.connect(self.path) as db:
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM memories')).fetchone())[0], 1)
            self.assertEqual((await (await db.execute("SELECT COUNT(*) FROM chatroom_memories WHERE scope='connor'")).fetchone())[0], 1)
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM messages')).fetchone())[0], 0)
            self.assertEqual((await (await db.execute('SELECT type FROM memories')).fetchone())[0], 'friend')
            await db.execute('UPDATE memories SET content=? WHERE id=?', ('修正后的兔子约定', first))
            await db.commit()
        self.assertIn('修正后的兔子约定', await recent_context('aion'))
        self.assertNotIn('养花', await recent_context('aion'))
        self.assertIn('养花', await recent_context('connor'))
        from context_builder import build_ability_block
        with patch('context_builder.build_capability_prompt_items',new=AsyncMock(return_value=[])), patch('context_builder.is_capability_enabled',return_value=False), patch('context_builder.get_device_context_for_prompt',return_value=''), patch('context_builder.build_cli_file_storage_text',return_value=''):
            context = await build_ability_block('用户',who='aion',excluded_capabilities={'post_sentinel'})
        self.assertIn('修正后的兔子约定', context)
        self.assertNotIn('养花', context)
        async with aiosqlite.connect(self.path) as db:
            await db.execute('DELETE FROM chatroom_memories WHERE id=?', (second,))
            await db.commit()
        self.assertEqual(await recent_context('connor'), '')

    async def test_external_evidence_does_not_fall_back_to_home_conversation(self):
        from external_memories import bring_home
        from memory import fetch_source_details
        from chatroom import fetch_chatroom_source_details
        for actor, table, fetch in [('aion','memories',fetch_source_details), ('connor','chatroom_memories',fetch_chatroom_source_details)]:
            mem_id = await bring_home(actor, '计划去看兔子。', platform='qq', channel='friends', message_id='m2',
                                      occurred_at=time.time(), participants=[], evidence='群友：下次来看兔子。')
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                row = dict(await (await db.execute(f'SELECT * FROM {table} WHERE id=?', (mem_id,))).fetchone())
            evidence = await fetch([row], ['兔子'])
            self.assertIn('群友：下次来看兔子。', evidence)
            self.assertIn('qq', evidence.lower())

    async def test_qq_saves_after_send_and_off_or_failed_send_never_writes(self):
        from external_memories import ExternalReply, recent_context
        from qq_social.service import Service
        from qq_social.store import Store
        store = Store(Path(self.tmp.name) / 'qq.db')
        sent = []
        class Transport:
            user_id = 'bot'
            fail = False
            async def send(inner, group, text, message_id):
                if inner.fail:
                    raise OSError('send failed')
                sent.append(text)
                return {'id': 'out-' + message_id}
        transport = Transport()
        async def generate(*args):
            return ExternalReply('好呀', '和朋友约好看兔子。', [{'id':'u1','name':'小雨'}], '小雨：去看兔子吧。')
        service = Service(store, {'aion':transport}, generator=generate)
        try:
            for number, enabled, failed in [(1,False,False),(2,True,True),(3,True,False)]:
                service.configure({'enabled':True,'bind_first_group':True,'bring_home':enabled})
                service.policy.history.clear()
                transport.fail = failed
                await service.ingest('aion','GROUP_AT_MESSAGE_CREATE',dict(id=str(number),group_openid='g1',content='去看兔子吧',author={'id':'u1'},timestamp=time.time()))
                await service.drain()
                self.assertEqual(bool(await recent_context('aion')), number == 3)
            self.assertEqual(sent, ['好呀','好呀'])
            self.assertEqual(service.diagnostics['aion']['memory_status'], 'saved')
        finally:
            await service.close()

    async def test_write_failure_keeps_reply_and_reports_failure(self):
        from external_memories import ExternalReply
        from qq_social.service import Service
        from qq_social.store import Store
        store = Store(Path(self.tmp.name) / 'qq.db')
        sent = []
        class Transport:
            user_id = 'bot'
            async def send(inner, group, text, message_id):
                sent.append(text)
                return {'id':'out'}
        async def generate(*args):
            return ExternalReply('好呀', '值得记住的事', [], '原消息')
        service = Service(store, {'aion':Transport()}, generator=generate)
        service.configure({'enabled':True,'bind_first_group':True,'bring_home':True})
        try:
            with patch('external_memories.bring_home',new=AsyncMock(side_effect=OSError('database unavailable'))):
                await service.ingest('aion','GROUP_AT_MESSAGE_CREATE',dict(id='m',group_openid='g',content='你好',author={'id':'u'},timestamp=time.time()))
                await service.drain()
            self.assertEqual(sent,['好呀'])
            self.assertEqual(service.diagnostics['aion']['memory_status'],'failed')
        finally:
            await service.close()

    async def test_unavailable_embedding_keeps_text_and_retry_indexes_same_memory(self):
        from external_memories import bring_home, index_memory, retry_pending_index, recent_context
        mem_id = await bring_home('aion','一次有趣的见闻',platform='another-platform',channel='friends',
                                  message_id='event',occurred_at=time.time(),participants=[],evidence='原文'*4000)
        with patch('memory.get_embedding',new=AsyncMock(return_value=None)):
            await index_memory('aion',mem_id)
        self.assertIn('一次有趣的见闻', await recent_context('aion'))
        await retry_pending_index('aion')
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute('SELECT embedding,evidence_summary FROM memories')).fetchall()
        self.assertEqual(len(rows),1)
        self.assertIsNotNone(rows[0][0])
        self.assertLessEqual(len(rows[0][1]),5000)
