"""Тесты слоя brain (M1). Сети нет: используется поддельный транспорт.

    python -m unittest discover -s tests -v
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.budget import Budget  # noqa: E402
from brain.claude_brain import ClaudeBrain  # noqa: E402
from brain.client import MODEL_VOICE, FakeTransport, cost_usd  # noqa: E402
from brain.parse import parse_three_lines  # noqa: E402
from brain.recorder import LIVE, REPLAY, CallLog, ReplayMiss  # noqa: E402
from brain.validate import validate  # noqa: E402
from engine.model import Pet  # noqa: E402
from engine.rng import Rng  # noqa: E402
from sim.run import simulate  # noqa: E402

NOW = 1_800_000_000.0
GOOD = "ДЕЙСТВИЕ: играть\nРЕПЛИКА: Ладно, но недолго.\nТОН: нехотя"
OPTIONS = [("accept", 0.8), ("refuse_sulk", 0.1), ("refuse_lazy", 0.1)]
KIND = "owner_action:play"


def make_brain(texts, **kw):
    transport = FakeTransport(texts)
    brain = ClaudeBrain(transport, kw.pop("calllog", CallLog()),
                        kw.pop("budget", Budget(monthly_limit=100.0)), Rng(1),
                        clock=lambda: NOW, **kw)
    return brain, transport


def pet_stage(stage):
    pet = Pet.new(3)
    pet.speech_stage = stage
    return pet


class ParseTests(unittest.TestCase):
    def test_plain(self):
        p = parse_three_lines(GOOD)
        self.assertEqual(p["action"], "играть")
        self.assertEqual(p["say"], "Ладно, но недолго.")
        self.assertEqual(p["tone"], "нехотя")

    def test_markdown_and_quotes(self):
        p = parse_three_lines("**ДЕЙСТВИЕ:** играть\n**РЕПЛИКА:** «Ну ладно.»\nТОН: ок")
        self.assertEqual(p["action"], "играть")
        self.assertEqual(p["say"], "Ну ладно.")

    def test_missing_fields(self):
        self.assertIsNone(parse_three_lines("просто текст без формата"))
        self.assertIsNone(parse_three_lines("ДЕЙСТВИЕ: играть"))


class ValidateTests(unittest.TestCase):
    menu = {"играть", "сидеть_спиной"}

    def ok(self, say, stage=3, action="играть"):
        return validate({"action": action, "say": say, "tone": ""}, self.menu, stage)

    def test_ok(self):
        self.assertEqual(self.ok("Ладно, но недолго."), [])

    def test_action_not_in_menu(self):
        self.assertTrue(self.ok("Ладно.", action="летать"))

    def test_digits_and_banned_words(self):
        self.assertTrue(self.ok("Голод 14, дай еду."))
        self.assertTrue(self.ok("Ночные пуши разрешены."))
        self.assertTrue(self.ok("Мяу, играем."))

    def test_stage_limits(self):
        long = "Докладываю: лаборант отсутствовал девять часов и ничего мне не принёс."
        self.assertEqual(self.ok(long, stage=4), [])
        self.assertEqual(self.ok(long, stage=3), [])
        self.assertTrue(self.ok(long, stage=2))
        self.assertTrue(self.ok(" ".join(["слово"] * 30), stage=3))
        self.assertTrue(self.ok("играть хочу сейчас же быстро давай уже скорее пока", stage=2))
        self.assertTrue(self.ok("ням ням", stage=1))
        self.assertEqual(self.ok("ууу ~ ммр", stage=1), [])
        self.assertTrue(self.ok("~ ухо ~ ммр", stage=1))

    def test_invented_objects_are_rejected(self):
        self.assertTrue(self.ok("Мячик мой, тапок не трогай."))
        self.assertTrue(self.ok("Небо серое, ладно."))
        self.assertEqual(self.ok("Небольшая игра, ладно."), [])
        self.assertTrue(self.ok("Телефон на столе светится."))
        self.assertEqual(self.ok("Столько мячиков, ладно."), [])
        self.assertEqual(self.ok("Мячик мой, коробку не трогай."), [])

    def test_stage1_rejects_room_object_words(self):
        self.assertTrue(self.ok("Хрум! Мячик! Тсс.", stage=1))
        self.assertTrue(self.ok("Тсс... дверь? Ой!", stage=1))
        self.assertEqual(self.ok("Хрум! Тсс. Ой!", stage=1), [])

    def test_trim_keeps_whole_sentences(self):
        from brain.validate import only_length_problems, trim
        long = "Хм. Играть. Ладно, но сначала ты. Потом я. Или нет."
        cut = trim(long, 2)
        self.assertEqual(cut, "Хм. Играть. Ладно, но сначала ты. Потом я.")
        self.assertEqual(validate({"action": "играть", "say": cut, "tone": ""},
                                  self.menu, 2), [])
        self.assertTrue(only_length_problems(self.ok(long, stage=2)))
        self.assertFalse(only_length_problems(self.ok("Тапок мой.", stage=2)))

    def test_stage3_limit_is_16_words(self):
        sixteen = " ".join(["слово"] * 16)
        self.assertEqual(self.ok(sixteen, stage=3), [])
        self.assertTrue(self.ok(sixteen + " ещё", stage=3))

    def test_empty_say(self):
        self.assertTrue(self.ok("   "))


class BudgetTests(unittest.TestCase):
    def test_limits_and_status(self):
        b = Budget(monthly_limit=3.0, daily_factor=1.0)   # дневной лимит 0.1
        self.assertEqual(b.status(NOW), "ok")
        b.record(NOW, 0.05)
        self.assertTrue(b.allow(NOW, 0.04))
        self.assertFalse(b.allow(NOW, 0.06))
        b.record(NOW, 0.05)
        self.assertEqual(b.status(NOW), "ok")
        self.assertFalse(b.allow(NOW, 0.001))
        # на следующие сутки дневной лимит свободен
        self.assertTrue(b.allow(NOW + 86400, 0.01))

    def test_warn_at_80_percent_of_month(self):
        b = Budget(monthly_limit=1.0, daily_factor=100.0)
        b.record(NOW, 0.81)
        self.assertEqual(b.status(NOW), "warn")
        b.record(NOW, 0.2)
        self.assertEqual(b.status(NOW), "tired")


class RecorderTests(unittest.TestCase):
    def test_key_is_stable_and_sensitive(self):
        k1 = CallLog.key("m", "s", "u")
        self.assertEqual(k1, CallLog.key("m", "s", "u"))
        self.assertNotEqual(k1, CallLog.key("m", "s", "u2"))

    def test_cost(self):
        self.assertAlmostEqual(cost_usd(MODEL_VOICE, 1_000_000, 1_000_000), 0.60)


class ClaudeBrainTests(unittest.TestCase):
    def test_success_path(self):
        brain, tr = make_brain([GOOD])
        pet = pet_stage(3)
        key = brain.choose(KIND, OPTIONS, {"pet": pet})
        self.assertEqual(key, "accept")
        self.assertEqual(brain.last.source, "claude")
        self.assertEqual(brain.last.say, "Ладно, но недолго.")
        self.assertEqual(tr.calls, 1)
        self.assertIn("Допустимые действия: играть, сидеть_спиной, демонстративно_спать",
                      tr.last_user)

    def test_prompt_lists_room_items_and_form_hint(self):
        brain, tr = make_brain([GOOD])
        brain.choose(KIND, OPTIONS, {"pet": pet_stage(2)})
        self.assertIn("Предметы вокруг: миска, подстилка, мячик", tr.last_user)
        self.assertIn("Других предметов нет", tr.last_user)
        self.assertIn("ФОРМА ЭТОЙ РЕПЛИКИ:", tr.last_user)
        brain1, tr1 = make_brain([GOOD])
        brain1.choose(KIND, OPTIONS, {"pet": pet_stage(1)})
        self.assertNotIn("ФОРМА ЭТОЙ РЕПЛИКИ", tr1.last_user)
        self.assertNotIn("Предметы вокруг", tr1.last_user)

    def test_retry_then_success(self):
        bad = "ДЕЙСТВИЕ: играть\nРЕПЛИКА: Голод 14.\nТОН: x"
        brain, tr = make_brain([bad, GOOD])
        brain.choose(KIND, OPTIONS, {"pet": pet_stage(3)})
        self.assertEqual(tr.calls, 2)
        self.assertEqual(brain.last.attempts, 2)
        self.assertEqual(brain.last.source, "claude")
        self.assertIn("нарушил правила", tr.last_user)

    def test_too_long_reply_is_trimmed_not_discarded(self):
        long = ("ДЕЙСТВИЕ: играть\nРЕПЛИКА: Хм. Играть. Ладно, но сначала ты. "
                "Потом я. Или нет.\nТОН: нехотя")
        brain, tr = make_brain([long])
        brain.choose(KIND, OPTIONS, {"pet": pet_stage(2)})
        self.assertEqual(tr.calls, 1)
        self.assertEqual(brain.last.source, "claude:trimmed")
        self.assertEqual(brain.last.say, "Хм. Играть. Ладно, но сначала ты. Потом я.")

    def test_all_attempts_invalid_falls_back(self):
        bad = "ДЕЙСТВИЕ: летать\nРЕПЛИКА: Мяу.\nТОН: x"
        brain, tr = make_brain([bad])
        key = brain.choose(KIND, OPTIONS, {"pet": pet_stage(3)})
        self.assertIn(key, {"accept", "refuse_sulk", "refuse_lazy"})
        self.assertEqual(brain.last.source, "fallback:invalid")
        self.assertTrue(brain.last.say)
        self.assertEqual(tr.calls, 2)

    def test_exhausted_budget_makes_pet_tired_without_calling_api(self):
        budget = Budget(monthly_limit=1.0, daily_factor=1.0)
        budget.record(NOW, 5.0)
        brain, tr = make_brain([GOOD], budget=budget)
        brain.choose(KIND, OPTIONS, {"pet": pet_stage(2)})
        self.assertEqual(brain.last.source, "fallback:tired")
        self.assertEqual(tr.calls, 0)

    def test_costs_are_recorded_in_budget(self):
        budget = Budget(monthly_limit=100.0)
        brain, _ = make_brain([GOOD], budget=budget)
        brain.choose(KIND, OPTIONS, {"pet": pet_stage(3)})
        self.assertGreater(budget.spent_today(NOW), 0.0)

    def test_other_kinds_are_delegated_to_stub(self):
        brain, tr = make_brain([GOOD])
        self.assertEqual(brain.choose("director", [("stash", 1.0)], None), "stash")
        self.assertEqual(tr.calls, 0)

    def test_replay_reproduces_without_calling_api(self):
        log = CallLog(mode=LIVE)
        live, _ = make_brain([GOOD], calllog=log)
        pet = pet_stage(3)
        live.choose(KIND, OPTIONS, {"pet": pet})
        self.assertEqual(log.count(), 1)

        log.mode = REPLAY
        replay, tr = make_brain(["не должно вызываться"], calllog=log)
        key = replay.choose(KIND, OPTIONS, {"pet": pet})
        self.assertEqual(key, "accept")
        self.assertEqual(replay.last.source, "replay")
        self.assertEqual(replay.last.say, "Ладно, но недолго.")
        self.assertEqual(tr.calls, 0)

    def test_replay_miss_raises(self):
        log = CallLog(mode=REPLAY)
        brain, _ = make_brain([GOOD], calllog=log)
        with self.assertRaises(ReplayMiss):
            brain.choose(KIND, OPTIONS, {"pet": pet_stage(3)})


class EchoTransport:
    """Всегда выбирает первое действие из присланного меню."""

    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        from brain.client import Completion
        self.calls += 1
        menu = re.search(r"Допустимые действия: (.+)\.", user).group(1)
        first = menu.split(", ")[0]
        return Completion(f"ДЕЙСТВИЕ: {first}\nРЕПЛИКА: ~ ммр\nТОН: ок", 100, 10)


class EngineIntegrationTests(unittest.TestCase):
    def test_simulation_runs_with_claude_brain_and_fake_transport(self):
        holder = []

        def factory(rng):
            brain = ClaudeBrain(EchoTransport(), CallLog(), Budget(monthly_limit=100.0),
                                rng, clock=lambda: NOW)
            holder.append(brain)
            return brain

        pet, _ = simulate(11, "careful", 3, brain_factory=factory)
        brain = holder[0]
        self.assertGreater(len(brain.reactions), 0)
        self.assertTrue(all(r.source == "claude" for r in brain.reactions))
        self.assertGreaterEqual(pet.health, 25.0)


if __name__ == "__main__":
    unittest.main()
