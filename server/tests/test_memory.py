"""Тесты M2: память, журнал, консолидация, ночной мозг, режиссёр. Сети нет."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.budget import Budget  # noqa: E402
from brain.claude_brain import ClaudeBrain  # noqa: E402
from brain.client import Completion, FakeTransport  # noqa: E402
from brain.night import NightBrain, parse_json  # noqa: E402
from brain.recorder import LIVE, REPLAY, CallLog  # noqa: E402
from engine import actions, consolidation, events, journal, needs  # noqa: E402
from engine.brain import StubBrain  # noqa: E402
from engine.constants import DAY, HOUR  # noqa: E402
from engine.memory import (COOLDOWN_DAYS, FORGET_BELOW, MAX_FACTS,  # noqa: E402
                           MAX_MEMORIES, MemoryStore)
from engine.model import Moment, Pet  # noqa: E402
from engine.rng import Rng  # noqa: E402
from sim.run import simulate  # noqa: E402

NOW = 1_800_000_000.0

GOOD_PLAN = json.dumps({
    "dream": "Снилось, что миска разговаривает.",
    "diary": "Сегодня лаборант пришёл поздно. Я запомнил.",
    "self_narrative": "Я Недоделок. Лаборант приходит ночью. Я не злюсь, почти.",
    "memories_add": [{"summary": "Лаборант разбудил ночью", "emotion": "обида",
                      "weight": 0.8, "tags": ["ночь", "сон", "выдуманный_тег"]}],
    "memories_merge": [], "memories_forget": [],
    "owner_facts": [{"statement": "Лаборант ложится поздно", "confidence": 0.7}],
    "trait_nudges": [{"trait": "anxiety", "delta": 1}],
}, ensure_ascii=False)


def night(texts, **kw):
    tr = FakeTransport(texts)
    brain = NightBrain(tr, kw.pop("calllog", CallLog()),
                       kw.pop("budget", Budget(monthly_limit=100.0)),
                       clock=lambda: NOW, **kw)
    return brain, tr


def pet_with_log():
    pet = Pet.new(3)
    pet.day_index = 5
    journal._log(pet, "woken_by_owner", 0.5, ["сон", "ночь", "хозяин"])
    journal._log(pet, "starving", 0.8, ["еда", "голод"])
    journal._log(pet, "done:play", 0.1, ["игра"])
    return pet


class MemoryStoreTests(unittest.TestCase):
    def test_add_dedupes_and_caps(self):
        s = MemoryStore()
        a = s.add(1, "Миска была пустой", "обида", 0.5, ["еда"])
        s.add(1, "миска была пустой", "обида", 0.9, ["еда"])
        self.assertEqual(len(s.memories), 1)
        self.assertEqual(a.weight, 0.9)
        for i in range(MAX_MEMORIES + 10):
            s.add(2, f"событие {i}", "", 0.1 + i / 1000, ["событие"])
        self.assertLessEqual(len(s.memories), MAX_MEMORIES)

    def test_merge_and_forget(self):
        s = MemoryStore()
        a = s.add(1, "раз", "", 0.5, ["еда"])
        b = s.add(2, "два", "", 0.6, ["сон"])
        m = s.merge([a.id, b.id], "раз и два")
        self.assertEqual(len(s.memories), 1)
        self.assertEqual(set(m.tags), {"еда", "сон"})
        self.assertTrue(s.forget(m.id))
        self.assertFalse(s.forget(m.id))

    def test_decay_forgets_weak_memories(self):
        s = MemoryStore()
        s.add(1, "слабое", "", FORGET_BELOW + 0.001, ["еда"])
        s.add(1, "сильное", "", 0.9, ["еда"])
        s.decay()
        self.assertEqual([m.summary for m in s.memories], ["сильное"])

    def test_facts_upsert_and_cap(self):
        s = MemoryStore()
        s.upsert_fact("ложится поздно", 0.4, 1)
        s.upsert_fact("Ложится поздно", 0.8, 2)
        self.assertEqual(len(s.facts), 1)
        for i in range(MAX_FACTS + 5):
            s.upsert_fact(f"факт {i}", 0.1 + i / 100, 3)
        self.assertLessEqual(len(s.facts), MAX_FACTS)

    def test_retrieve_by_tags_with_cooldown(self):
        s = MemoryStore()
        m = s.add(1, "про еду", "", 0.9, ["еда"])
        s.add(1, "про сон", "", 0.9, ["сон"])
        now = 3 * DAY
        self.assertEqual(s.retrieve(["еда"], now).id, m.id)
        self.assertIsNone(s.retrieve(["игра"], now))
        s.mark_used(m, now)
        self.assertIsNone(s.retrieve(["еда"], now + HOUR))
        self.assertIsNotNone(s.retrieve(["еда"], now + (COOLDOWN_DAYS + 1) * DAY))


class SimilarityAndLeakTests(unittest.TestCase):
    def test_near_duplicate_fact_updates_instead_of_adding(self):
        s = MemoryStore()
        s.upsert_fact("Лаборант не давит и не торопит меня", 0.5, 1)
        s.upsert_fact("Лаборант меня не торопит и не давит", 0.7, 2)
        self.assertEqual(len(s.facts), 1)
        s.upsert_fact("Лаборант ложится спать поздно ночью", 0.6, 3)
        self.assertEqual(len(s.facts), 2)

    def test_near_duplicate_memory_strengthens_existing(self):
        s = MemoryStore()
        a = s.add(1, "Миска была пустой до самого вечера", "обида", 0.5, ["еда"])
        b = s.add(2, "Миска была пустой до вечера", "обида", 0.8, ["еда"])
        self.assertEqual(len(s.memories), 1)
        self.assertIs(a, b)
        self.assertEqual(a.weight, 0.8)

    def test_digest_does_not_carry_director_fiction(self):
        pet = Pet.new(3)
        events.run_director(pet, type("B", (), {
            "last_intro": "Недоделок выглянул из угла с бумажкой.",
            "choose": lambda self, k, o, c=None: o[0][0]})(), Rng(1))
        text = " ".join(journal.digest(pet))
        self.assertNotIn("угла", text)
        self.assertIn("событие", text)

    def test_sleep_prompt_asks_for_facts_only_while_dossier_is_thin(self):
        from brain.night import build_sleep_user
        pet = pet_with_log()
        self.assertIn("мало фактов", build_sleep_user(pet))
        for fact in ("Приходит под утро", "Кормит супом", "Играет мячиком",
                     "Боится чайника", "Любит тишину"):
            pet.memory.upsert_fact(fact, 0.6, 1)
        self.assertNotIn("мало фактов", build_sleep_user(pet))

    def test_director_prompt_shows_previous_intros(self):
        brain, tr = night(["СОБЫТИЕ: report\nВСТУПЛЕНИЕ: Первая сцена с миской.",
                           "СОБЫТИЕ: stash\nВСТУПЛЕНИЕ: Вторая сцена у двери."])
        pet = Pet.new(3)
        opts = [("stash", 0.5), ("report", 0.9)]
        brain.choose_event(pet, opts)
        self.assertNotIn("ПРОШЛЫЕ ВСТУПЛЕНИЯ", tr.last_user)
        brain.choose_event(pet, opts)
        self.assertIn("ПРОШЛЫЕ ВСТУПЛЕНИЯ", tr.last_user)
        self.assertIn("Первая сцена с миской.", tr.last_user)


class JournalTests(unittest.TestCase):
    def test_neglect_and_return_are_logged(self):
        pet = Pet.new(4)
        needs.advance(pet, pet.t + 80 * HOUR)
        kinds = [e["kind"] for e in pet.event_log]
        self.assertIn("hibernate", kinds)
        actions.act(pet, pet.t + HOUR, "say", StubBrain(Rng(1)), Rng(2), text="я дома")
        kinds = [e["kind"] for e in pet.event_log]
        self.assertIn("return_from_quarantine", kinds)
        text = " ".join(journal.digest(pet))
        self.assertIn("карантин", text)

    def test_refusals_and_small_things(self):
        pet = Pet.new(4)
        journal.owner_action(pet, "play", "refuse_sulk")
        journal.owner_action(pet, "play", "accept")
        journal.owner_action(pet, "feed", "accept", food="суп")
        lines = journal.digest(pet)
        self.assertTrue(any("отказался" in l for l in lines))
        self.assertTrue(any("обычные дела" in l for l in lines))

    def test_director_event_goes_to_journal_with_intro(self):
        pet = Pet.new(4)
        pet.rel["grudge"] = 80.0

        class B:
            last_intro = "В гнезде стало тихо."

            def choose(self, kind, options, ctx=None):
                return options[0][0]

        m = events.run_director(pet, B(), Rng(1))
        self.assertTrue(m.kind.startswith("event:"))
        self.assertEqual(m.data["intro"], "В гнезде стало тихо.")
        self.assertTrue(any(e["kind"] == m.kind for e in pet.event_log))


class SanitizePlanTests(unittest.TestCase):
    def test_good_plan_is_cleaned_and_applied(self):
        pet = pet_with_log()
        plan, notes = consolidation.sanitize_plan(json.loads(GOOD_PLAN), pet)
        self.assertEqual(plan["add"][0]["tags"], ["ночь", "сон"])   # чужой тег убран
        before = pet.traits["anxiety"]
        report = consolidation.apply_plan(pet, plan)
        self.assertEqual(report["added"], 1)
        self.assertEqual(len(pet.dreams), 1)
        self.assertTrue(pet.memory.self_narrative.startswith("Я Недоделок"))
        self.assertEqual(pet.memory.facts[0]["statement"], "Лаборант ложится поздно")
        self.assertGreater(pet.traits["anxiety"], before)

    def test_banned_text_and_bad_ids_are_dropped(self):
        pet = pet_with_log()
        raw = {"diary": "Пришло уведомление о лимите", "self_narrative": "Я зверёк.",
               "memories_add": [{"summary": "Включили настройки", "weight": 1}],
               "memories_merge": [{"ids": [99, 100], "summary": "x"}],
               "memories_forget": [42]}
        plan, notes = consolidation.sanitize_plan(raw, pet)
        self.assertIsNone(plan["diary"])
        self.assertEqual(plan["add"], [])
        self.assertEqual(plan["merge"], [])
        self.assertEqual(plan["forget"], [])
        self.assertEqual(plan["self_narrative"], "Я зверёк.")

    def test_limits_are_enforced(self):
        pet = pet_with_log()
        raw = {"memories_add": [{"summary": f"воспоминание {i}", "tags": ["еда"]}
                                for i in range(10)],
               "trait_nudges": [{"trait": "anxiety", "delta": 50},
                                {"trait": "greed", "delta": -50},
                                {"trait": "laziness", "delta": 2},
                                {"trait": "нет такой", "delta": 1}]}
        plan, _ = consolidation.sanitize_plan(raw, pet)
        self.assertEqual(len(plan["add"]), 4)
        self.assertTrue(all(abs(n["delta"]) <= 2.0 for n in plan["nudges"]))
        self.assertLessEqual(sum(abs(n["delta"]) for n in plan["nudges"]), 4.0)
        self.assertNotIn("нет такой", [n["trait"] for n in plan["nudges"]])

    def test_not_a_dict(self):
        plan, notes = consolidation.sanitize_plan([1, 2], pet_with_log())
        self.assertEqual(plan["add"], [])
        self.assertTrue(notes)

    def test_rule_based_plan_takes_top_events(self):
        pet = pet_with_log()
        plan = consolidation.rule_based_plan(pet)
        self.assertEqual(len(plan["add"]), 2)
        self.assertGreaterEqual(plan["add"][0]["weight"], 0.5)


class NightBrainTests(unittest.TestCase):
    def test_parse_json_tolerates_wrapping(self):
        self.assertEqual(parse_json('вот:\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertIsNone(parse_json("нет json"))

    def test_successful_consolidation(self):
        pet = pet_with_log()
        brain, tr = night([GOOD_PLAN])
        report = brain.consolidate(pet)
        self.assertEqual(report.source, "claude")
        self.assertEqual(tr.calls, 1)
        self.assertEqual(len(pet.memory.memories), 1)
        self.assertEqual(pet.event_log, [])                     # журнал очищен
        self.assertEqual(pet.last_consolidation_day, pet.day_index)
        self.assertLess(pet.memory.memories[0].weight, 0.8)      # затухание применено

    def test_retry_on_garbage_then_success(self):
        brain, tr = night(["ой, не знаю", GOOD_PLAN])
        report = brain.consolidate(pet_with_log())
        self.assertEqual(tr.calls, 2)
        self.assertEqual(report.source, "claude")

    def test_all_garbage_falls_back_to_rules(self):
        pet = pet_with_log()
        brain, tr = night(["мусор"])
        report = brain.consolidate(pet)
        self.assertEqual(report.source, "fallback:invalid")
        self.assertGreaterEqual(len(pet.memory.memories), 1)

    def test_tired_budget_uses_rules_without_calling_api(self):
        budget = Budget(monthly_limit=1.0, daily_factor=1.0)
        budget.record(NOW, 5.0)
        pet = pet_with_log()
        brain, tr = night([GOOD_PLAN], budget=budget)
        report = brain.consolidate(pet)
        self.assertEqual(report.source, "fallback:tired")
        self.assertEqual(tr.calls, 0)
        self.assertGreaterEqual(len(pet.memory.memories), 1)

    def test_empty_day_costs_nothing(self):
        pet = Pet.new(3)
        brain, tr = night([GOOD_PLAN])
        report = brain.consolidate(pet)
        self.assertEqual(report.source, "skip:no_events")
        self.assertEqual(tr.calls, 0)

    def test_replay(self):
        log = CallLog(mode=LIVE)
        brain, _ = night([GOOD_PLAN], calllog=log)
        brain.consolidate(pet_with_log())
        log.mode = REPLAY
        pet = pet_with_log()
        again, tr = night(["не вызывать"], calllog=log)
        report = again.consolidate(pet)
        self.assertEqual(report.source, "replay")
        self.assertEqual(tr.calls, 0)
        self.assertEqual(len(pet.memory.memories), 1)


class DirectorTests(unittest.TestCase):
    OPTIONS = [("stash", 0.5), ("report", 0.9)]

    def test_choose_event(self):
        brain, tr = night(["СОБЫТИЕ: report\nВСТУПЛЕНИЕ: Недоделок пишет жалобу на суп."])
        self.assertEqual(brain.choose_event(Pet.new(3), self.OPTIONS), "report")
        self.assertEqual(brain.last_intro, "Недоделок пишет жалобу на суп.")

    def test_unknown_event_returns_none(self):
        brain, tr = night(["СОБЫТИЕ: полёт\nВСТУПЛЕНИЕ: Летит."])
        self.assertIsNone(brain.choose_event(Pet.new(3), self.OPTIONS))
        self.assertEqual(tr.calls, 2)

    def test_single_candidate_needs_no_call(self):
        brain, tr = night(["x"])
        self.assertEqual(brain.choose_event(Pet.new(3), [("stash", 1.0)]), "stash")
        self.assertEqual(tr.calls, 0)

    def test_claude_brain_delegates_director_and_falls_back(self):
        nb, _ = night(["СОБЫТИЕ: report\nВСТУПЛЕНИЕ: Пишет жалобу."])
        cb = ClaudeBrain(FakeTransport(["x"]), CallLog(), Budget(monthly_limit=100.0),
                         Rng(1), clock=lambda: NOW, night=nb)
        pet = Pet.new(3)
        self.assertEqual(cb.choose("director", self.OPTIONS, {"pet": pet}), "report")
        self.assertEqual(cb.last_intro, "Пишет жалобу.")
        nb2, _ = night(["чушь"])
        cb2 = ClaudeBrain(FakeTransport(["x"]), CallLog(), Budget(monthly_limit=100.0),
                          Rng(1), clock=lambda: NOW, night=nb2)
        self.assertIn(cb2.choose("director", self.OPTIONS, {"pet": pet}),
                      {"stash", "report"})


class VoiceMemoryTests(unittest.TestCase):
    OPTIONS = [("accept", 0.8), ("refuse_sulk", 0.1), ("refuse_lazy", 0.1)]
    GOOD = "ДЕЙСТВИЕ: играть\nРЕПЛИКА: Помню, как миска пустовала.\nТОН: ворчливо"

    def brain(self):
        tr = FakeTransport([self.GOOD])
        return ClaudeBrain(tr, CallLog(), Budget(monthly_limit=100.0), Rng(1),
                           clock=lambda: NOW, memory_chance=1.0), tr

    def pet(self, stage):
        pet = Pet.new(3)
        pet.speech_stage = stage
        pet.memory.add(2, "Миска была пустой до вечера", "обида", 0.9, ["еда", "голод", "игра"])
        pet.memory.self_narrative = "Я Недоделок, и я злопамятный."
        pet.memory.upsert_fact("Лаборант ложится поздно", 0.8, 3)
        return pet

    def test_memory_narrative_and_facts_reach_prompt_at_stage_3(self):
        brain, tr = self.brain()
        pet = self.pet(3)
        brain.choose("owner_action:play", self.OPTIONS, {"pet": pet})
        self.assertIn("ВОСПОМИНАНИЕ СЕЙЧАС: Миска была пустой до вечера", tr.last_user)
        self.assertIn("КТО Я: Я Недоделок", tr.last_user)
        self.assertIn("ЧТО Я ЗНАЮ О ЛАБОРАНТЕ: Лаборант ложится поздно", tr.last_user)
        self.assertEqual(brain.last.memory, "Миска была пустой до вечера")

    def test_no_memory_at_stage_1(self):
        brain, tr = self.brain()
        brain.choose("owner_action:play", self.OPTIONS, {"pet": self.pet(1)})
        self.assertNotIn("ВОСПОМИНАНИЕ", tr.last_user)
        self.assertNotIn("КТО Я", tr.last_user)

    def test_memory_has_cooldown(self):
        brain, tr = self.brain()
        pet = self.pet(3)
        brain.choose("owner_action:play", self.OPTIONS, {"pet": pet})
        brain.choose("owner_action:play", self.OPTIONS, {"pet": pet})
        self.assertNotIn("ВОСПОМИНАНИЕ", tr.last_user)


class NightEcho:
    """Для интеграционного теста: возвращает воспоминание с номером дня."""

    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        day = int(re.search(r"День (\d+)", user).group(1))
        plan = {"diary": f"Запись за день {day}.",
                "memories_add": [{"summary": f"Что-то случилось в день {day}",
                                  "emotion": "тревога", "weight": 0.7,
                                  "tags": ["событие"]}],
                "self_narrative": f"Я Недоделок, мне {day} дней."}
        return Completion(json.dumps(plan, ensure_ascii=False), 500, 200)


class SimulationMemoryTests(unittest.TestCase):
    def test_rule_based_memory_makes_pets_different(self):
        careful, _ = simulate(11, "careful", 21)
        sloppy, _ = simulate(11, "sloppy", 21)
        tags_c = {t for m in careful.memory.memories for t in m.tags}
        tags_s = {t for m in sloppy.memory.memories for t in m.tags}
        self.assertTrue(sloppy.memory.memories)
        self.assertIn("пропал", tags_s)
        self.assertNotEqual(tags_c, tags_s)
        self.assertLessEqual(len(sloppy.memory.memories), MAX_MEMORIES)

    def test_simulation_with_night_brain_builds_memory_and_diary(self):
        nb = NightBrain(NightEcho(), CallLog(), Budget(monthly_limit=100.0),
                        clock=lambda: NOW)
        pet, _ = simulate(11, "sloppy", 10, night=nb)
        self.assertGreater(len(pet.diary), 0)
        self.assertTrue(pet.memory.self_narrative.startswith("Я Недоделок"))
        self.assertLessEqual(len(pet.memory.memories), MAX_MEMORIES)
        self.assertGreater(len(nb.reports), 0)


class DemoNightSmokeTest(unittest.TestCase):
    """Сквозной прогон скрипта живого запуска на поддельных транспортах."""

    def test_run_demo_offline(self):
        from brain.demo_night import run_demo

        class Voice:
            def complete(self, system, user):
                menu = re.search(r"Допустимые действия: (.+)\.", user).group(1)
                first = menu.split(", ")[0]
                text = "ДЕЙСТВИЕ: " + first + "\nРЕПЛИКА: ~ ммр\nТОН: ок"
                return Completion(text, 100, 10)

        class Night:
            def complete(self, system, user):
                if "режиссёр" in system:
                    eid = re.search(r"^- (\w+):", user, re.MULTILINE).group(1)
                    text = "СОБЫТИЕ: " + eid + "\nВСТУПЛЕНИЕ: Что-то происходит."
                    return Completion(text, 300, 30)
                return NightEcho().complete(system, user)

        out = run_demo(Voice(), Night(), CallLog(), 8, clock=lambda: NOW)
        self.assertIn("## Режиссёр", out)
        self.assertIn("## Память в конце", out)
        self.assertIn("sleep:", out)


if __name__ == "__main__":
    unittest.main()
