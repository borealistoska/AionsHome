import copy
import json
import unittest
import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

from routes import doudizhu as game


def table(hand, *, landlord="aion", user=None, teammate=None, last=None):
    return {
        "id": "test-table", "phase": "playing",
        "players": ["user", "aion", "connor"],
        "hands": {
            "aion": hand,
            "user": user if user is not None else ["10H", "JH", "QH", "KH", "AH", "2H"],
            "connor": teammate if teammate is not None else ["10C", "JC", "QC", "KC", "AC", "2C"],
        },
        "landlord": landlord, "current_player": "aion", "last_play": last,
        "bottom": ["10H", "JH", "QH"], "bottom_revealed": True,
        "passes": [], "played_cards": last["cards"][:] if last else [],
        "history": [], "updated_at": 0,
    }


def last_play(player, cards):
    return {"player": player, "cards": cards, **game.classify_cards(cards)}


def choose(state):
    return game._strategic_play_decision(state, "aion", game.legal_moves(state, "aion"))


class DoudizhuStrategyTests(unittest.TestCase):
    def test_each_view_and_model_prompt_only_contains_own_hand(self):
        state = table(["3S", "AS", "2S"])
        for viewer in game.PLAYERS:
            with self.subTest(viewer=viewer):
                view = game._public_state(state, viewer)
                for player in game.PLAYERS:
                    self.assertEqual("hand" in view["players"][player], player == viewer)
                prompt = game._game_prompt(state, viewer, game.legal_moves(state, viewer))
                private = json.loads(prompt.split("【你的私有牌局信息】\n", 1)[1])
                self.assertEqual(private["yourHand"], state["hands"][viewer])
                for player in game.PLAYERS:
                    self.assertEqual("hand" in private["publicState"]["players"][player], player == viewer)
        self.assertEqual(game._public_state(state)["players"]["user"]["hand"], state["hands"]["user"])
        state["bottom_revealed"] = False
        self.assertEqual(game._public_state(state, "aion")["bottomCards"], [])

    def test_can_finish_airplanes_and_four_with_wings(self):
        for hand in [
            ["3S", "3H", "3C", "4S", "4H", "4C", "5S", "6S"],
            ["3S", "3H", "3C", "4S", "4H", "4C", "5S", "5H", "6S", "6H"],
            ["3S", "3H", "3C", "3D", "4S", "5S"],
            ["3S", "3H", "3C", "3D", "4S", "4H", "5S", "5H"],
        ]:
            with self.subTest(hand=hand):
                self.assertCountEqual(choose(table(hand))["cards"], hand)

    def test_attachment_choices_include_preserving_low_pair(self):
        hand = ["3S", "3H", "4S", "4H", "4C", "9S"]
        candidates = game._generate_candidate_moves(hand)
        self.assertIn(["4S", "4H", "4C", "9S"], candidates)

    def test_reported_single_or_pair_is_not_fed_low_matching_cards(self):
        single = choose(table(["3S", "AS", "2S"], user=["KS"]))
        self.assertEqual(single["cards"], ["2S"])
        pair = choose(table(["3S", "3H", "AS", "AH", "2S", "2H"], user=["KS", "KH"]))
        self.assertEqual(pair["cards"], ["2S", "2H"])

    def test_following_preserves_bomb_when_a_small_single_will_do(self):
        state = table(["4S", "4H", "4C", "4D", "6S", "AS"], last=last_play("user", ["3S"]))
        self.assertEqual(choose(state)["cards"], ["6S"])

    def test_keeps_two_straights_instead_of_one_long_straight_and_scraps(self):
        hand = ["3S", "4S", "5S", "6S", "6H", "7S", "7H", "8S", "9S", "10S"]
        move = choose(table(hand))
        self.assertEqual(len(move["cards"]), 5)
        remaining = [c for c in hand if c not in move["cards"]]
        self.assertEqual(game.classify_cards(remaining)["type"], "straight")

    def test_uses_unbeatable_bomb_to_regain_lead_and_finish(self):
        state = table(["3S", "3H", "3C", "3D", "6S", "6H"], landlord="user",
                      user=["RJ"], teammate=["KS", "KH"], last=last_play("connor", ["5S"]))
        state["players"] = ["connor", "aion", "user"]
        move = choose(state)
        self.assertCountEqual(move["cards"], ["3S", "3H", "3C", "3D"])
        game.apply_play(state, "aion", move["action"], move["cards"])
        game.apply_play(state, "user", "pass", [])
        game.apply_play(state, "connor", "pass", [])
        move = choose(state)
        game.apply_play(state, "aion", move["action"], move["cards"])
        self.assertEqual(state["winner"], "aion")

    def test_farmer_yields_to_teammate_and_feeds_a_pair_on_lead(self):
        state = table(["4S", "7S", "9S", "AS"], landlord="user", last=last_play("connor", ["3S"]))
        state["players"] = ["user", "connor", "aion"]
        self.assertEqual(choose(state)["action"], "pass")
        state = table(["3S", "4S", "4H", "7S", "9S", "AS"], landlord="user", teammate=["KS", "KH"])
        self.assertEqual(choose(state)["cards"], ["4S", "4H"])

    def test_decision_does_not_depend_on_opponents_hidden_ranks(self):
        state = table(["3S", "AS", "2S"], user=["KS"])
        changed = copy.deepcopy(state)
        changed["hands"]["user"] = ["4D"]
        changed["hands"]["connor"] = ["5D", "6D", "7D", "8D", "9D", "10D"]
        self.assertEqual(choose(state)["cards"], choose(changed)["cards"])


class DoudizhuTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_announcement_is_a_model_visible_system_card(self):
        from context_builder import render_merged_timeline

        state = table([], landlord="user")
        state.update(phase="game_over", winner="aion", wallet_settlement={
            "lines": ["测试用户 -6 元", "测试同伴 +3 元", "测试伙伴 +3 元"],
        })
        labels = {"user": "测试用户", "aion": "测试同伴", "connor": "测试伙伴"}
        routes = ModuleType("routes.chatroom")
        async def saved(room_id, sender, content, msg_id, attachments=None, **kwargs):
            return dict(id=msg_id, room_id=room_id, sender=sender, content=content,
                        created_at=1, attachments=attachments or [])
        routes._save_msg = AsyncMock(side_effect=saved)
        with patch.dict(sys.modules, {"routes.chatroom": routes}), \
                patch.object(game, "_load_state", return_value=state), \
                patch.object(game, "_player_names", return_value=labels), \
                patch.object(game, "_latest_group_room_id", AsyncMock(return_value="family")), \
                patch.object(game, "_trigger_group_replies", AsyncMock()) as replies:
            result = await game.announce_result()
            await asyncio.sleep(0)
        self.assertTrue(result["ok"])
        message = result["message"]
        self.assertEqual(message["sender"], "system")
        self.assertEqual(message["attachments"][0]["type"], "doudizhu_result")
        self.assertIn({"type": "system_model_context"}, message["attachments"])
        self.assertIn("农民方获胜", message["content"])
        self.assertIn("测试伙伴", message["content"])
        self.assertIn("测试用户 -6 元", message["content"])
        for who in ("aion", "connor"):
            rendered = render_merged_timeline([{**message, "source": "group"}], who)
            self.assertTrue(any("斗地主战报" in item["content"] for item in rendered))
        replies.assert_awaited_once_with("family")

    async def test_model_cannot_override_team_pass_but_speech_is_kept(self):
        state = table(["4S", "7S", "9S", "AS"], landlord="user", last=last_play("connor", ["3S"]))
        state["players"] = ["user", "connor", "aion"]
        with patch.object(game, "_ask_ai", AsyncMock(return_value={
            "action": "play", "cards": ["AS"], "speech": "这手让你走。",
        })):
            decision = await game._run_ai_step(state)
        self.assertEqual(decision["action"], "pass")
        self.assertEqual(state["hands"]["aion"], ["4S", "7S", "9S", "AS"])
        self.assertEqual(state["history"][-1]["speech"], "这手让你走。")

    async def test_model_failure_still_finishes_legal_hand(self):
        state = table(["3S", "3H", "3C", "4S", "4H", "4C", "5S", "6S"])
        with patch.object(game, "_ask_ai", AsyncMock(side_effect=TimeoutError)):
            await game._run_ai_step(state)
        self.assertEqual(state["winner"], "aion")
        self.assertEqual(state["phase"], "game_over")


if __name__ == "__main__":
    unittest.main()
