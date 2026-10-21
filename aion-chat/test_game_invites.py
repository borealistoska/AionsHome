import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import capabilities


class GameInvitePromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_uses_user_name_and_respects_capability_switch(self):
        item = capabilities.get_capability_def('mini_game')
        self.assertIsNotNone(item)
        self.assertTrue(item.default_enabled)
        with patch.object(capabilities, 'is_capability_enabled', side_effect=lambda key: key == 'mini_game'):
            for who in ('aion', 'connor'):
                prompt = '\n'.join(await capabilities.build_capability_prompt_items('测试月亮', who=who))
                self.assertIn('测试月亮', prompt)
                self.assertIn('[小游戏：桌球]', prompt)
                self.assertIn('[小游戏：斗地主]', prompt)
            excluded = await capabilities.build_capability_prompt_items('测试月亮', excluded_capabilities={'mini_game'})
            self.assertFalse(any('小游戏' in text for text in excluded))
        with patch.object(capabilities, 'is_capability_enabled', return_value=False):
            self.assertFalse(any('小游戏' in text for text in await capabilities.build_capability_prompt_items('测试月亮')))


class GameInviteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import game_invites
        self.invites = game_invites
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'chat.db'
        async with aiosqlite.connect(self.path) as db:
            await db.executescript('''
                CREATE TABLE conversations(id TEXT PRIMARY KEY, updated_at REAL);
                CREATE TABLE chatroom_rooms(id TEXT PRIMARY KEY, type TEXT, updated_at REAL);
                CREATE TABLE messages(id TEXT PRIMARY KEY,conv_id TEXT,role TEXT,content TEXT,created_at REAL,attachments TEXT);
                CREATE TABLE chatroom_messages(id TEXT PRIMARY KEY,room_id TEXT,sender TEXT,content TEXT,created_at REAL,attachments TEXT);
                INSERT INTO conversations VALUES('private',0);
                INSERT INTO chatroom_rooms VALUES('group','group',0),('companion','connor_1v1',0);
            ''')
        self.broadcast = AsyncMock()
        self.enabled = self.enterContext(patch.object(capabilities, 'is_capability_enabled', return_value=True))
        self.enterContext(patch.object(game_invites, 'get_db', lambda: aiosqlite.connect(self.path)))
        self.enterContext(patch.object(game_invites, 'append_sync_event', AsyncMock(return_value=1)))
        self.enterContext(patch.object(game_invites, 'broadcast_synced', self.broadcast))
        self.enterContext(patch('chatroom.get_chatroom_names', return_value=('测试月亮', '测试星星', '测试小熊')))

    async def test_both_private_chats_and_group_save_ordered_invites_once(self):
        from context_builder import render_merged_timeline
        cases = [('private','private','aion','桌球'), ('chatroom','companion','connor','斗地主'),
                 ('chatroom','group','aion','斗地主'), ('chatroom','group','connor','台球')]
        for index, (scope, source, actor, game) in enumerate(cases):
            saved = AsyncMock()
            kwargs = dict(source_type=scope, source_id=source, sender=actor,
                          source_msg_id=f'reply-{index}', on_saved=saved)
            text = f'陪你玩一局。[小游戏：{game}][小游戏:桌球]'
            self.assertEqual(await self.invites.process_game_invites(text, **kwargs), '陪你玩一局。')
            await self.invites.process_game_invites(text, **kwargs)
            saved.assert_awaited_once()
            message = saved.call_args.args[0]
            self.assertIn({'type': 'system_notice_order', 'after_msg_id': f'reply-{index}'}, message['attachments'])
            self.assertIn({'type': 'system_model_context'}, message['attachments'])
            self.assertEqual(message['attachments'][0]['game'], '桌球' if game == '台球' else game)
            history = render_merged_timeline([{**message, 'sender':'system', 'source':'group'}], actor)
            self.assertIn('邀请测试月亮', json.dumps(history, ensure_ascii=False))
        async with aiosqlite.connect(self.path) as db:
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM messages')).fetchone())[0], 1)
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM chatroom_messages')).fetchone())[0], 3)
        self.assertEqual(self.broadcast.await_count, 4)

    async def test_disabled_unknown_and_user_commands_do_not_create_invites(self):
        kwargs = dict(source_type='private', source_id='private', sender='aion', source_msg_id='reply')
        await self.invites.process_game_invites('[小游戏:象棋]', **kwargs)
        await self.invites.process_game_invites('[小游戏:桌球]', **{**kwargs, 'sender':'user'})
        self.enabled.return_value = False
        self.assertEqual(await self.invites.process_game_invites('好呀[小游戏:斗地主]', **kwargs), '好呀')
        self.broadcast.assert_not_awaited()

    async def test_command_is_hidden_from_stream_and_speech_but_retained_for_processing(self):
        from context_builder import strip_tool_commands
        from safe_live_stream import consume_safe_live_stream
        from web_search import WebCommandStreamFilter
        from tts import _strip_tags
        text = '一起来玩。[小游戏：桌球]好不好？'
        filtered = WebCommandStreamFilter()
        self.assertEqual(''.join(filtered.feed(char) for char in text) + filtered.flush(), '一起来玩。好不好？')
        self.assertEqual(strip_tool_commands(text), '一起来玩。好不好？')
        self.assertNotIn('小游戏', _strip_tags(text))
        async def source():
            for char in text:
                yield char
        visible = []
        result = await consume_safe_live_stream(source(), visible.append)
        self.assertEqual(result.committed_text, text)
        self.assertEqual(''.join(visible), '一起来玩。好不好？')


if __name__ == '__main__':
    unittest.main()
