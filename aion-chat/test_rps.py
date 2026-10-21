import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite


class RpsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('rps'), '猜拳功能尚未实现')
        self.rps = importlib.import_module('rps')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'chat.db'
        async with aiosqlite.connect(self.path) as db:
            await db.executescript('''
                CREATE TABLE conversations (id TEXT PRIMARY KEY, updated_at REAL);
                CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, updated_at REAL);
                CREATE TABLE messages (id TEXT PRIMARY KEY, conv_id TEXT, role TEXT, content TEXT, created_at REAL, attachments TEXT);
                CREATE TABLE chatroom_messages (id TEXT PRIMARY KEY, room_id TEXT, sender TEXT, content TEXT, created_at REAL, attachments TEXT);
                INSERT INTO conversations VALUES ('private', 0);
                INSERT INTO chatroom_rooms VALUES ('group', 'group', 0), ('companion', 'connor_1v1', 0);
            ''')
            await self.rps.ensure_rps_tables(db)
            await db.commit()
        self.notify = AsyncMock()
        self.broadcast = AsyncMock()
        for replacement in (
            patch.object(self.rps, 'get_db', lambda: aiosqlite.connect(self.path)),
            patch.object(self.rps, '_names', return_value={'user': '小月', 'aion': '星星', 'connor': '小熊'}),
            patch.object(self.rps, 'notify_ai', self.notify),
            patch.object(self.rps.manager, 'broadcast', self.broadcast),
            patch('capabilities.is_capability_enabled', return_value=True),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    async def invite(self, actor='connor', source_id='group', source_type='chatroom', command='石头', msg='ai-1'):
        return await self.rps.process_rps_commands('来一局。[猜拳:' + command + '|谁输谁是小狗]', source_type=source_type,
            source_id=source_id, sender=actor, source_msg_id=msg)

    async def saved_user_message(self, content='我赢了，山竹拿来', source_id='group', scope='chatroom'):
        from uuid import uuid4
        message = dict(id=uuid4().hex, content=content, attachments=[], created_at=100)
        table, key, role = ('messages', 'conv_id', 'role') if scope == 'private' else ('chatroom_messages', 'room_id', 'sender')
        message.update({key: source_id, role: 'user'})
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?)', (message['id'], source_id, 'user', content, 100, '[]'))
            await db.commit()
        return message

    async def send_turn(self, round_id='', source_id='group', scope='chatroom'):
        message = await self.saved_user_message(source_id=source_id, scope=scope)
        return await self.rps.attach_to_message(scope, source_id, message, round_id)

    async def test_result_finishes_in_history_without_pending_delivery(self):
        await self.invite()
        first = (await self.rps.list_rounds('chatroom', 'group'))[0]
        result = await self.rps.play_round(first['id'], self.rps.HandRequest(hand='布'))
        self.assertFalse(result['result_pending'])
        self.notify.assert_not_awaited()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            saved = dict(await (await db.execute("SELECT * FROM chatroom_messages WHERE content LIKE '猜拳结果%'")).fetchone())
        from context_builder import render_merged_timeline
        for actor in ('aion', 'connor'):
            history = render_merged_timeline([{**saved, 'source':'group'}], actor)
            self.assertIn('小月获胜', json.dumps(history, ensure_ascii=False))
            self.assertIn('谁输谁是小狗', json.dumps(history, ensure_ascii=False))
        message, actor = await self.send_turn()
        self.assertIsNone(actor)
        self.assertEqual(message['attachments'], [])

    async def test_previous_pending_results_migrate_into_normal_history(self):
        async with aiosqlite.connect(':memory:') as empty:
            await self.rps.ensure_rps_tables(empty)
        await self.invite()
        first = (await self.rps.list_rounds('chatroom', 'group'))[0]
        await self.rps.play_round(first['id'], self.rps.HandRequest(hand='布'))
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE rps_rounds SET result_pending=1')
            await db.execute("UPDATE chatroom_messages SET attachments=replace(attachments, 'system_model_context', 'rps_pending_context') WHERE content LIKE '猜拳结果%'")
            await self.rps.ensure_rps_tables(db)
            await self.rps.ensure_rps_tables(db)
            row = await (await db.execute("SELECT attachments FROM chatroom_messages WHERE content LIKE '猜拳结果%'")).fetchone()
            self.assertIn('system_model_context', row[0])
            self.assertNotIn('rps_pending_context', row[0])
            await db.commit()
        self.assertFalse((await self.rps.get_round(first['id']))['result_pending'])

    async def test_topic_required_for_new_rounds_but_legacy_reply_is_accepted(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.rps.RoundCreate(scope='private', source_id='private', actor='aion', hand='布', topic='  ')
        await self.rps.process_rps_commands('来玩。[猜拳:石头]', source_type='chatroom', source_id='group', sender='connor')
        self.assertEqual(await self.rps.list_rounds('chatroom', 'group'), [])
        first = await self.rps.create_round(self.rps.RoundCreate(scope='private', source_id='private', actor='aion', hand='布', topic='随便玩一局'))
        await self.send_turn(first['id'], 'private', 'private')
        await self.rps.process_rps_commands('[猜拳:石头]', source_type='private', source_id='private', sender='aion')
        self.assertEqual((await self.rps.get_round(first['id']))['winner'], 'user')
        self.notify.assert_not_awaited()

    async def test_ai_choices_stay_hidden_and_two_invitations_survive_reload(self):
        self.assertEqual(await self.invite(), '来一局。')
        await self.invite('aion', command='剪刀', msg='ai-2')
        rounds = await self.rps.list_rounds('chatroom', 'group')
        self.assertEqual([r['actor'] for r in rounds], ['connor', 'aion'])
        self.assertTrue(all(r['ai_hand'] is None for r in rounds))
        self.assertNotIn('石头', json.dumps(self.broadcast.call_args_list, ensure_ascii=False, default=str))
        result = await self.rps.play_round(rounds[0]['id'], self.rps.HandRequest(hand='布'))
        self.assertEqual(result['winner'], 'user')
        self.assertEqual(result['ai_hand'], '石头')
        await self.rps.play_round(rounds[0]['id'], self.rps.HandRequest(hand='剪刀'))
        self.notify.assert_not_awaited()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT content, attachments FROM chatroom_messages WHERE content LIKE '猜拳结果%'")
            rows = await cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn('小月获胜', rows[0][0])
        self.assertIn('system_model_context', rows[0][1])

    async def test_user_invitation_keeps_choice_out_of_messages_and_accepts_only_target(self):
        first = await self.rps.create_round(self.rps.RoundCreate(scope='chatroom', source_id='group', actor='connor', hand='布', topic='我赢了就吃三颗山竹'))
        self.assertEqual(first['status'], 'draft')
        self.notify.assert_not_awaited()
        async with aiosqlite.connect(self.path) as db:
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM chatroom_messages')).fetchone())[0], 0)
        await self.send_turn(first['id'])
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute('SELECT content, attachments FROM chatroom_messages')
            content, attachments = (await cur.fetchall())[0]
        self.assertNotIn('出了布', content)
        self.assertNotIn('user_hand', attachments)
        await self.invite('aion', command='剪刀', msg='unrelated')
        self.assertEqual((await self.rps.get_round(first['id']))['status'], 'waiting_ai')
        await self.invite(command='石头', msg='reply')
        result = await self.rps.get_round(first['id'])
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['winner'], 'user')

    async def test_cancelled_round_cannot_be_revived_by_late_reply(self):
        first = await self.rps.create_round(self.rps.RoundCreate(scope='chatroom', source_id='group', actor='connor', hand='布', topic='我赢了就吃三颗山竹'))
        await self.rps.cancel_round(first['id'])
        token = self.rps.reply_round_id.set(first['id'])
        try:
            await self.invite(command='石头', msg='late')
        finally:
            self.rps.reply_round_id.reset(token)
        rounds = await self.rps.list_rounds('chatroom', 'group')
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]['status'], 'cancelled')
        self.assertIsNone(rounds[0]['ai_hand'])

    async def test_toggle_stops_initiations_but_keeps_results_visible_in_context(self):
        await self.invite()
        first = (await self.rps.list_rounds('chatroom', 'group'))[0]
        with patch('capabilities.is_capability_enabled', return_value=False):
            await self.invite('aion', msg='disabled')
            result = await self.rps.play_round(first['id'], self.rps.HandRequest(hand='布'))
        self.assertEqual(len(await self.rps.list_rounds('chatroom', 'group')), 1)
        self.assertEqual(result['winner'], 'user')
        from context_builder import _is_model_visible_timeline_message, strip_tool_commands
        self.assertTrue(_is_model_visible_timeline_message({'sender': 'system', 'content': '猜拳结果', 'attachments': [{'type': 'system_model_context'}]}))
        self.assertEqual(strip_tool_commands('好呀。[猜拳:剪刀]'), '好呀。')
        from safe_live_stream import KnownCommandStreamFilter
        stream = KnownCommandStreamFilter()
        visible = ''.join(stream.feed(c) for c in '好呀。[猜拳:剪刀]') + stream.finish()[0]
        self.assertEqual(visible, '好呀。')
        from tts import _strip_tags
        self.assertEqual(_strip_tags('好呀。[猜拳:剪刀]'), '好呀。')
        import capabilities
        with patch('capabilities.is_capability_enabled', side_effect=lambda key: key == 'rps'):
            prompt = '\n'.join(await capabilities.build_capability_prompt_items('小月'))
        self.assertIn('通过猜拳和小月决胜', prompt)
        with patch('capabilities.is_capability_enabled', return_value=False):
            prompt = '\n'.join(await capabilities.build_capability_prompt_items('小月'))
        self.assertNotIn('[猜拳:', prompt)

    async def test_bundled_invitation_only_selected_group_actor_replies(self):
        from routes import chatroom
        first = await self.rps.create_round(self.rps.RoundCreate(scope='chatroom', source_id='group', actor='connor', hand='布', topic='我赢了就吃三颗山竹'))
        user_msg = await self.saved_user_message('敢不敢来嘛')
        aion, connor = AsyncMock(), AsyncMock()
        with patch.object(chatroom, '_load_room_and_messages', AsyncMock(return_value=({'id': 'group', 'type': 'group'}, []))), \
             patch.object(chatroom, 'record_chatroom_active', AsyncMock()), \
             patch.object(chatroom, '_reply_aion', aion), patch.object(chatroom, '_reply_connor', connor), \
             patch.object(chatroom, '_save_msg', AsyncMock(return_value=user_msg)), \
             patch.object(chatroom.cam, 'reset_patrol_timer'):
            response = await chatroom.send_message('group', chatroom.MsgSend(content='敢不敢来嘛', rps_round_id=first['id']))
            async for _ in response.body_iterator:
                pass
        aion.assert_not_awaited()
        connor.assert_awaited_once()
        self.assertEqual('敢不敢来嘛', connor.call_args.args[3])
        self.notify.assert_not_awaited()
        await self.invite(command='石头', msg='answer')
        next_msg = await self.saved_user_message('我赢啦')
        group_reply = AsyncMock()
        with patch.object(chatroom, '_load_room_and_messages', AsyncMock(return_value=({'id': 'group', 'type': 'group'}, []))), \
             patch.object(chatroom, 'record_chatroom_active', AsyncMock()), \
             patch.object(chatroom, '_is_manual_group_reply_mode', return_value=False), \
             patch.object(chatroom, '_generate_group_replies', group_reply), \
             patch.object(chatroom, '_save_msg', AsyncMock(return_value=next_msg)), \
             patch.object(chatroom.cam, 'reset_patrol_timer'):
            response = await chatroom.send_message('group', chatroom.MsgSend(content='我赢啦'))
            async for _ in response.body_iterator:
                pass
        group_reply.assert_awaited_once()


    async def test_private_chat_and_companion_validate_participants(self):
        await self.invite('aion', source_id='private', source_type='private')
        first = (await self.rps.list_rounds('private', 'private'))[0]
        self.assertEqual((await self.rps.play_round(first['id'], self.rps.HandRequest(hand='石头')))['winner'], 'draw')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            await self.rps.create_round(self.rps.RoundCreate(scope='chatroom', source_id='companion', actor='aion', hand='石头', topic='随便玩一局'))


if __name__ == '__main__':
    unittest.main()
