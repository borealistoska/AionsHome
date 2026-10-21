import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class IntimacySkillTests(unittest.TestCase):
    def setUp(self):
        try:
            import intimacy_skill
        except ModuleNotFoundError:
            self.fail("intimacy_skill module is not installed")
        self.skill = intimacy_skill
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_state_path = intimacy_skill._STATE_PATH
        intimacy_skill._STATE_PATH = Path(self.tempdir.name) / "intimacy_state.json"
        intimacy_skill._ENABLED.clear()
        intimacy_skill._SESSIONS.clear()
        intimacy_skill._RECENT_CARDS.clear()
        intimacy_skill._ACTIVE_GENERATIONS.clear()

    def tearDown(self):
        if hasattr(self, "skill"):
            self.skill._STATE_PATH = self.old_state_path
            self.skill._ENABLED.clear()
            self.skill._SESSIONS.clear()
            self.skill._RECENT_CARDS.clear()
            self.skill._ACTIVE_GENERATIONS.clear()
        if hasattr(self, "tempdir"):
            self.tempdir.cleanup()

    def test_disabled_and_armed_scopes_do_not_inject_a_card(self):
        self.assertEqual(
            self.skill.build_play_prompt("private", "conv-1", "private", actor="aion"),
            "",
        )
        state = self.skill.set_enabled("private", "conv-1", True)
        self.assertEqual(state["state"], "armed")
        self.assertEqual(
            self.skill.build_play_prompt("private", "conv-1", "private", actor="aion"),
            "",
        )

    def test_active_card_uses_configured_names_and_disable_clears_it(self):
        self.skill.set_enabled("room", "room-1", True)
        self.assertEqual(
            self.skill.start_session("room", "room-1", "group")["state"],
            "active",
        )

        prompt = self.skill.build_play_prompt(
            "room",
            "room-1",
            "group",
            actor="aion",
            user_name="Ithil",
            actor_names=("Aion", "Connor"),
        )
        self.assertIn("Ithil", prompt)
        self.assertIn("Aion", prompt)
        self.assertIn("Connor", prompt)
        self.assertNotIn("Mila", prompt)
        self.assertNotIn("Cove", prompt)
        self.assertNotIn("Shep", prompt)
        self.assertEqual(self.skill.get_state("room", "room-1")["seen_actors"], ["aion"])

        state = self.skill.set_enabled("room", "room-1", False)
        self.assertEqual(state["state"], "off")
        self.assertEqual(
            self.skill.build_play_prompt("room", "room-1", "group", actor="connor"),
            "",
        )

    def test_group_reroll_waits_until_current_generation_finishes(self):
        self.skill.set_enabled("room", "room-1", True)
        self.skill.start_session("room", "room-1", "group")
        key = "room:room-1"
        current = self.skill._SESSIONS[key]["card"]

        self.assertTrue(self.skill._begin_generation("room", "room-1", "group"))
        reroll = self.skill.start_session("room", "room-1", "group")
        self.assertTrue(reroll["reroll_queued"])
        self.assertIs(self.skill._SESSIONS[key]["card"], current)
        pending = self.skill._SESSIONS[key]["pending_card"]

        self.skill._end_generation("room", "room-1")
        self.assertIs(self.skill._SESSIONS[key]["card"], pending)

    def test_preview_only_exposes_the_active_card_without_marking_it_read(self):
        preview_card = getattr(self.skill, "get_card_preview", None)
        self.assertIsNotNone(preview_card, "active cards need a read-only preview API")

        self.skill.set_enabled("private", "conv-1", True)
        self.assertIsNone(preview_card("private", "conv-1")["card"])

        self.skill.start_session("private", "conv-1", "private")
        preview = preview_card("private", "conv-1")

        self.assertEqual(preview["state"], "active")
        self.assertEqual(preview["card"]["mode"], "private")
        self.assertTrue(preview["card"]["category"])
        self.assertTrue(preview["card"]["location"])
        self.assertIn("final_finish", preview["card"])
        self.assertNotIn("category_id", preview["card"])
        self.assertNotIn("location_resources", preview["card"])
        self.assertEqual(self.skill.get_state("private", "conv-1")["seen_actors"], [])


class PrivateIntimacyInjectionTests(unittest.TestCase):
    def test_private_context_injects_nothing_when_play_prompt_is_empty(self):
        from routes import chat as chat_routes

        history = [{"role": "user", "content": "hello"}]
        with (
            patch.object(chat_routes, "build_ability_block", new=AsyncMock(return_value="")),
            patch.object(chat_routes, "build_play_prompt", return_value=""),
            patch.object(chat_routes, "with_current_device_context", side_effect=lambda items, **_: items),
        ):
            result = asyncio.run(chat_routes._insert_private_ability_block(
                history,
                0,
                0,
                user_name="Ithil",
                ai_name="Aion",
                model_key="unit-model",
                conv_id="conv-1",
            ))

        self.assertEqual(result, 0)
        self.assertEqual(history, [{"role": "user", "content": "hello"}])

    def test_private_context_adds_active_play_prompt_as_a_message_pair(self):
        from routes import chat as chat_routes

        history = [{"role": "user", "content": "hello"}]
        with (
            patch.object(chat_routes, "build_ability_block", new=AsyncMock(return_value="")),
            patch.object(chat_routes, "build_play_prompt", return_value="hidden-card"),
            patch.object(chat_routes, "with_current_device_context", side_effect=lambda items, **_: items),
        ):
            result = asyncio.run(chat_routes._insert_private_ability_block(
                history,
                0,
                0,
                user_name="Ithil",
                ai_name="Aion",
                model_key="unit-model",
                conv_id="conv-1",
            ))

        self.assertEqual(result, 2)
        self.assertEqual(history[0], {"role": "user", "content": "hidden-card"})
        self.assertEqual(history[1]["role"], "assistant")


class ChatroomIntimacyInjectionTests(unittest.TestCase):
    def setUp(self):
        import intimacy_skill

        self.skill = intimacy_skill
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_state_path = intimacy_skill._STATE_PATH
        intimacy_skill._STATE_PATH = Path(self.tempdir.name) / "intimacy_state.json"
        intimacy_skill._ENABLED.clear()
        intimacy_skill._SESSIONS.clear()
        intimacy_skill._RECENT_CARDS.clear()
        intimacy_skill._ACTIVE_GENERATIONS.clear()

    def tearDown(self):
        self.skill._STATE_PATH = self.old_state_path
        self.skill._ENABLED.clear()
        self.skill._SESSIONS.clear()
        self.skill._RECENT_CARDS.clear()
        self.skill._ACTIVE_GENERATIONS.clear()
        self.tempdir.cleanup()

    def test_chatroom_context_only_appends_an_active_card(self):
        import chatroom

        names = ("Ithil", "Aion", "Connor")
        disabled_history = []
        chatroom.append_intimacy_prompt(
            disabled_history, "room-1", "group", "aion", names
        )
        self.assertEqual(disabled_history, [])

        self.skill.set_enabled("room", "room-1", True)
        self.skill.start_session("room", "room-1", "group")
        active_history = []
        chatroom.append_intimacy_prompt(
            active_history, "room-1", "group", "aion", names
        )
        self.assertEqual(len(active_history), 2)
        self.assertIn("Ithil", active_history[0]["content"])
        self.assertIn("Aion", active_history[0]["content"])
        self.assertIn("Connor", active_history[0]["content"])
        self.assertEqual(active_history[1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
