"""Тесты движка M0. Запуск из папки server:

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import actions, mutations, needs  # noqa: E402
from engine.brain import StubBrain  # noqa: E402
from engine.constants import (DAY, HEALTH_FLOOR, HOUR, REFUSAL_BUDGET,  # noqa: E402
                              STAGE_THRESHOLDS)
from engine.model import Pet  # noqa: E402
from engine.rng import Rng  # noqa: E402
from engine.speech import stage_for  # noqa: E402
from sim.run import describe, mutation_distance, simulate, trait_distance  # noqa: E402


def snapshot(pet):
    return (dict(pet.needs), pet.health, pet.mood, dict(pet.rel), pet.sleeping,
            pet.mode, pet.illness, dict(pet.counters), dict(pet.traits),
            dict(pet.mutations), pet.day_index)


class ForcedAcceptTests(unittest.TestCase):
    def test_brain_still_voices_accept_when_refusal_budget_is_spent(self):
        class Spy(StubBrain):
            def __init__(self, rng):
                super().__init__(rng)
                self.kinds = []

            def choose(self, kind, options, ctx=None):
                self.kinds.append((kind, list(options)))
                return super().choose(kind, options, ctx)

        pet = Pet.new(9)
        pet.refusals_today = REFUSAL_BUDGET
        pet.needs["tiredness"] = 10
        brain = Spy(Rng(1))
        outcome = actions._ask(pet, brain, "play", 0.5)
        self.assertEqual(outcome, "accept")
        self.assertEqual(brain.kinds, [("owner_action:play", [("accept", 1.0)])])


class LazyTimeTests(unittest.TestCase):
    def test_split_advance_equals_single_advance(self):
        a, b = Pet.new(5), Pet.new(5)
        target = a.t + 2 * DAY + 1234
        needs.advance(a, target)
        needs.advance(b, b.t + 11 * HOUR + 77)
        needs.advance(b, target)
        self.assertEqual(snapshot(a), snapshot(b))

    def test_split_advance_equals_through_hibernation(self):
        a, b = Pet.new(6), Pet.new(6)
        target = a.t + 10 * DAY + 999
        needs.advance(a, target)
        needs.advance(b, b.t + 3 * DAY + 5)
        needs.advance(b, target)
        self.assertEqual(snapshot(a), snapshot(b))

    def test_no_death_even_after_months_of_neglect(self):
        pet = Pet.new(7)
        needs.advance(pet, pet.t + 90 * DAY)
        self.assertGreaterEqual(pet.health, HEALTH_FLOOR)
        self.assertEqual(pet.mode, "hibernating")

    def test_health_floor_while_owner_is_present_but_negligent(self):
        pet = Pet.new(8)
        brain = StubBrain(Rng(1))
        rng = Rng(2)
        t = pet.t
        for _ in range(20):          # раз в 2.5 суток просто «привет»
            t += 60 * HOUR
            actions.act(pet, t, "say", brain, rng, text="привет")
        self.assertGreaterEqual(pet.health, HEALTH_FLOOR)


class HibernationTests(unittest.TestCase):
    def test_hibernates_after_72h_and_returns_softly(self):
        pet = Pet.new(9)
        pet.add_care(100)
        needs.advance(pet, pet.t + 80 * HOUR)
        self.assertEqual(pet.mode, "hibernating")
        self.assertTrue(all(v <= 80.0 for v in pet.needs.values()))
        care_before = pet.care_points
        res = actions.act(pet, pet.t + HOUR, "say", StubBrain(Rng(1)), Rng(2), text="я дома")
        self.assertEqual(pet.mode, "normal")
        self.assertIn("return_from_quarantine", [m.kind for m in res.moments])
        self.assertLess(pet.care_points, care_before)
        self.assertGreater(pet.care_points, 0)


class ActionTests(unittest.TestCase):
    def test_refusal_budget_is_enforced(self):
        pet = Pet.new(3)
        pet.rel["grudge"] = 100.0
        brain, rng = StubBrain(Rng(4)), Rng(5)
        t = pet.t
        for i in range(30):
            t += 60
            actions.act(pet, t, "pet", brain, rng)
            pet.rel["grudge"] = 100.0
            pet.needs["tiredness"] = 0.0
            pet.rel["attachment"] = 0.0
        self.assertLessEqual(pet.counters.get("refusals", 0), REFUSAL_BUDGET)

    def test_hungry_pet_always_eats_even_disliked_food(self):
        pet = Pet.new(3)
        food = min(pet.taste, key=pet.taste.get)
        pet.taste[food] = -1.0
        pet.needs["hunger"] = 90.0
        res = actions.act(pet, pet.t + 60, "feed", StubBrain(Rng(1)), Rng(2), food=food)
        self.assertTrue(res.accepted)

    def test_acting_on_sleeping_pet_wakes_it_with_a_cost(self):
        pet = Pet.new(3)
        pet.sleeping = True
        pet.sleep_started_t = pet.t
        grudge = pet.rel["grudge"]
        res = actions.act(pet, pet.t + 60, "say", StubBrain(Rng(1)), Rng(2), text="эй")
        self.assertFalse(pet.sleeping)
        self.assertIn("woken_by_owner", [m.kind for m in res.moments])
        self.assertGreater(pet.rel["grudge"], grudge)

    def test_ill_pet_cannot_play_or_walk(self):
        pet = Pet.new(3)
        pet.illness = "простуда"
        pet.health = 30.0
        brain, rng = StubBrain(Rng(1)), Rng(2)
        self.assertEqual(actions.act(pet, pet.t + 60, "play", brain, rng).outcome,
                         "unavailable_ill")
        self.assertEqual(actions.act(pet, pet.t + 120, "walk", brain, rng).outcome,
                         "unavailable")


class SpeechAndMutationTests(unittest.TestCase):
    def test_stage_thresholds(self):
        self.assertEqual(stage_for(0), 1)
        self.assertEqual(stage_for(STAGE_THRESHOLDS[1] - 0.1), 1)
        self.assertEqual(stage_for(STAGE_THRESHOLDS[1]), 2)
        self.assertEqual(stage_for(STAGE_THRESHOLDS[2]), 3)
        self.assertEqual(stage_for(STAGE_THRESHOLDS[3]), 4)

    def test_mutations_fire_once(self):
        pet = Pet.new(3)
        pet.counters["affection"] = 100
        out = []
        mutations.check(pet, out)
        first_day = pet.mutations["big_ear"]
        pet.day_index = 9
        mutations.check(pet, out)
        self.assertEqual(pet.mutations["big_ear"], first_day)
        self.assertEqual([m.data["name"] for m in out].count("big_ear"), 1)


class SimulationTests(unittest.TestCase):
    def test_same_inputs_give_identical_result(self):
        a, sa = simulate(11, "careful", 14)
        b, sb = simulate(11, "careful", 14)
        self.assertEqual(describe(a, sa, "x"), describe(b, sb, "x"))
        self.assertEqual(snapshot(a), snapshot(b))

    def test_same_pet_diverges_under_different_owners(self):
        careful, _ = simulate(11, "careful", 21)
        sloppy, _ = simulate(11, "sloppy", 21)
        self.assertEqual(careful.base_traits, sloppy.base_traits)
        self.assertGreaterEqual(trait_distance(careful, sloppy), 30.0)
        self.assertGreater(mutation_distance(careful, sloppy), 0.5)
        self.assertGreater(careful.speech_stage, sloppy.speech_stage)

    def test_neglected_pet_never_dies_in_simulation(self):
        for seed in (1, 2, 3, 4, 5):
            pet, _ = simulate(seed, "sloppy", 30)
            self.assertGreaterEqual(pet.health, HEALTH_FLOOR)

    def test_neglected_pet_recovers_with_good_care(self):
        pets = [simulate(seed, "sloppy", 35, switch=("careful", 21))[0]
                for seed in range(1, 7)]
        for pet in pets:
            self.assertLess(pet.rel["grudge"], 30.0)
            self.assertIsNone(pet.illness)
            self.assertGreater(pet.rel["trust"], 40.0)
        self.assertGreaterEqual(sum(p.health for p in pets) / len(pets), 80.0)

    def test_neglect_has_floors_not_despair(self):
        from engine.constants import MOOD_FLOOR, TRUST_FLOOR
        pets = [simulate(seed, "sloppy", 21)[0] for seed in range(1, 7)]
        for pet in pets:
            self.assertGreaterEqual(pet.mood, MOOD_FLOOR)
            self.assertGreaterEqual(pet.rel["trust"], TRUST_FLOOR)
            self.assertLess(pet.rel["grudge"], 80.0)

    def test_different_seeds_have_different_temperaments(self):
        a = Pet.new(1).base_traits
        b = Pet.new(2).base_traits
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
