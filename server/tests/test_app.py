"""Тесты веб-слоя M3: сервис, хранилище, API. Сеть и Claude не нужны.

Тесты API требуют fastapi и httpx (окружение server/.venv); без них пропускаются:

    .venv\\Scripts\\python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.service import PetService, ServiceError  # noqa: E402
from app.storage import Storage  # noqa: E402
from engine.constants import DAY, HOUR  # noqa: E402

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_WEB = True
except Exception:          # нет fastapi или httpx
    HAVE_WEB = False

START = 1_800_000_000      # любой момент, UTC


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_service(tmp, clock=None):
    clock = clock or Clock()
    return PetService(Storage(os.path.join(tmp, "pet.sqlite")), clock=clock), clock


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = self._tmp.name
        self.service, self.clock = make_service(self.tmp)

    def tearDown(self):
        self.service.storage.close()
        self._tmp.cleanup()

    def test_no_pet_before_hatch(self):
        self.assertFalse(self.service.has_pet())
        with self.assertRaises(ServiceError) as cm:
            self.service.state()
        self.assertEqual(cm.exception.status, 409)

    def test_hatch_once_with_timezone(self):
        view = self.service.hatch(tz_offset_min=420)
        self.assertTrue(view["id"].startswith("НП-"))
        self.assertIsNone(view["name"])
        self.assertEqual(self.service.get_settings()["tz_offset_min"], 420)
        with self.assertRaises(ServiceError):
            self.service.hatch()
        with self.assertRaises(ServiceError):
            make_service(self.tmp)[0].hatch(tz_offset_min=99999)

    def test_local_hour_follows_timezone(self):
        self.service.hatch(tz_offset_min=180)
        expected = ((START + 180 * 60 + 600) // HOUR) % 24
        hour = self.service.state()["clock"]["hour"]
        self.assertIn(hour, (expected, (expected + 23) % 24))

    def test_view_tells_whether_the_brain_is_on(self):
        from engine.brain import StubBrain
        self.service.hatch()
        self.assertEqual(self.service.state()["brain"], "stub")
        with_brain = PetService(self.service.storage, brain_factory=lambda rng: StubBrain(rng),
                                clock=self.clock)
        self.assertEqual(with_brain.state()["brain"], "claude")

    def test_view_has_bands_and_only_the_five_main_meters(self):
        self.service.hatch()
        self.clock.advance(10 * HOUR)
        view = self.service.state()
        for key in ("hunger", "tiredness", "dirt", "boredom", "loneliness", "walk"):
            self.assertIn(view["state"][key], (0, 1, 2, 3))
        self.assertEqual(set(view["meters"]), {"hunger", "mood", "tiredness", "health", "boredom"})
        for value in view["meters"].values():
            self.assertIsInstance(value, int)
            self.assertTrue(0 <= value <= 100)
        text = json.dumps(view, ensure_ascii=False)
        for hidden in ("loneliness_value", "trust", "grudge", "traits"):
            self.assertNotIn(hidden, text)             # остальное по-прежнему только в «Осмотре»

    def test_meters_follow_care(self):
        self.service.hatch()
        self.clock.advance(14 * HOUR)
        hungry = self.service.state()["meters"]["hunger"]
        fed = self.service.act("feed", food="суп")["state"]["meters"]["hunger"]
        self.assertLess(fed, hungry)
        self.assertGreater(hungry, 60)

    def test_feeding_a_hungry_pet_works_and_speaks(self):
        self.service.hatch()
        self.clock.advance(14 * HOUR)
        before = self.service.state()["state"]["hunger"]
        res = self.service.act("feed", food="суп")
        self.assertEqual(res["outcome"], "accept")
        self.assertIsNotNone(res["speech"])
        self.assertLess(res["state"]["state"]["hunger"], before)
        self.assertTrue(res["state"]["speech"])

    def test_bad_input_is_rejected(self):
        self.service.hatch()
        for bad in (dict(action="fly"), dict(action="feed", food="гвозди"),
                    dict(action="feed")):
            with self.assertRaises(ServiceError):
                self.service.act(**bad)

    def test_say_is_stored_without_crashing(self):
        self.service.hatch()
        self.clock.advance(HOUR)
        res = self.service.act("say", text="привет, как дела")
        self.assertIn(res["outcome"], ("accept", "demand", "snub"))
        self.assertEqual(self.service.inspect()["counters"].get("said"), 1.0)

    def test_name_is_trimmed_and_required(self):
        self.service.hatch()
        self.assertEqual(self.service.set_name("  Кусь   Второй ")["name"], "Кусь Второй")
        with self.assertRaises(ServiceError):
            self.service.set_name("   ")

    def test_state_survives_restart(self):
        self.service.hatch()
        self.clock.advance(14 * HOUR)
        self.service.act("feed", food="рыба")
        self.service.set_name("Кусь")
        view1 = self.service.state()
        service2, _ = make_service(self.tmp, self.clock)
        view2 = service2.state()
        self.assertEqual(view1["name"], view2["name"])
        self.assertEqual(view1["state"], view2["state"])
        self.assertEqual(view1["day"], view2["day"])

    def test_week_of_neglect_never_kills_and_leaves_memories(self):
        self.service.hatch()
        for _ in range(7 * 4):                         # раз в 6 часов только смотрят
            self.clock.advance(6 * HOUR)
            view = self.service.state()
        self.assertEqual(view["state"]["mode"], "hibernating")
        self.assertGreaterEqual(self.service.inspect()["health"], 25)
        self.assertTrue(self.service.dossier()["memories"])

    def test_dossier_lists_only_grown_mutations(self):
        self.service.hatch()
        self.assertEqual(self.service.dossier()["mutations"], [])
        with self.service.lock:
            pet = self.service._load()
            pet.mutations["big_ear"] = 3
            pet.mutations["color_печенье"] = 5
            self.service._save(pet)
        got = self.service.dossier()["mutations"]
        self.assertEqual([m["day"] for m in got], [3, 5])
        self.assertEqual(got[0]["text"], "ухо-антенна выросла")
        self.assertIn("печенье", got[1]["text"])

    def test_director_posts_events_once_a_day(self):
        self.service.hatch()
        for _ in range(3 * 6):                         # три дня, шаг 4 часа, активный хозяин
            self.clock.advance(4 * HOUR)
            self.service.act("say", text="привет")
        events = [s for s in self.service.storage.recent_speech(500) if s["kind"] == "event"]
        self.assertGreaterEqual(len(events), 1)

    def test_quiet_hours_validation(self):
        self.assertEqual(self.service.set_settings(quiet_start=1, quiet_end=9)["quiet_end"], 9)
        with self.assertRaises(ServiceError):
            self.service.set_settings(quiet_start=24)

    def test_backup_is_a_valid_database(self):
        self.service.hatch()
        dest = os.path.join(self.tmp, "backup.sqlite")
        self.service.storage.backup_to(dest)
        self.assertTrue(Storage(dest).load_pet() is not None)


@unittest.skipUnless(HAVE_WEB, "нужны fastapi и httpx")
class ApiTests(unittest.TestCase):
    TOKEN = "test-token-0123456789"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.service, self.clock = make_service(self._tmp.name)
        self.static = Path(self._tmp.name) / "client"
        self.static.mkdir()
        (self.static / "index.html").write_text("<h1>зверёк</h1>", encoding="utf-8")
        self.app = create_app(self.service, self.TOKEN, static_dir=self.static)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": "Bearer " + self.TOKEN}

    def tearDown(self):
        self.service.storage.close()
        self._tmp.cleanup()

    def test_health_is_public_data_is_not(self):
        self.assertEqual(self.client.get("/api/health").json(), {"ok": True})
        self.assertEqual(self.client.get("/api/state").status_code, 401)
        bad = self.client.get("/api/state", headers={"Authorization": "Bearer wrong-token"})
        self.assertEqual(bad.status_code, 401)

    def test_full_flow(self):
        r = self.client.get("/api/state", headers=self.auth)
        self.assertEqual(r.json(), {"hatched": False})
        r = self.client.post("/api/hatch", headers=self.auth, json={"tz_offset_min": 60})
        self.assertTrue(r.json()["hatched"])
        self.clock.advance(14 * HOUR)
        r = self.client.post("/api/act", headers=self.auth,
                             json={"action": "feed", "food": "суп"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["outcome"], "accept")
        r = self.client.post("/api/name", headers=self.auth, json={"name": "Кусь"})
        self.assertEqual(r.json()["name"], "Кусь")
        for path in ("/api/inspect", "/api/dossier", "/api/diary", "/api/settings"):
            self.assertEqual(self.client.get(path, headers=self.auth).status_code, 200)

    def test_service_errors_become_json(self):
        r = self.client.post("/api/act", headers=self.auth, json={"action": "pet"})
        self.assertEqual(r.status_code, 409)          # зверька ещё нет
        self.client.post("/api/hatch", headers=self.auth, json={})
        r = self.client.post("/api/act", headers=self.auth, json={"action": "летать"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    def test_too_long_text_is_rejected_by_validation(self):
        self.client.post("/api/hatch", headers=self.auth, json={})
        r = self.client.post("/api/act", headers=self.auth,
                             json={"action": "say", "text": "а" * 1000})
        self.assertEqual(r.status_code, 422)

    def test_rate_limit(self):
        codes = [self.client.get("/api/state", headers=self.auth).status_code
                 for _ in range(125)]
        self.assertEqual(codes[0], 200)
        self.assertEqual(codes[-1], 429)

    def test_failed_logins_lock_out_even_the_right_token(self):
        for _ in range(11):
            self.client.get("/api/state", headers={"Authorization": "Bearer wrong-token"})
        r = self.client.get("/api/state", headers=self.auth)
        self.assertEqual(r.status_code, 429)

    def test_static_client_is_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("зверёк", r.text)

    def test_dev_advance_exists_only_in_dev_mode(self):
        self.client.post("/api/hatch", headers=self.auth, json={})
        code = self.client.post("/api/dev/advance", headers=self.auth,
                                json={"hours": 5}).status_code
        self.assertIn(code, (404, 405))
        app = create_app(self.service, "", dev=True, static_dir=self.static)
        c = TestClient(app)
        r = c.post("/api/dev/advance", json={"hours": 10})
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["state"]["hunger"], 1)

    def test_dev_mode_without_token(self):
        app = create_app(self.service, "", dev=True, static_dir=self.static)
        c = TestClient(app)
        self.assertEqual(c.get("/api/state").status_code, 200)

    def test_empty_token_in_production_mode_refuses_everything(self):
        app = create_app(self.service, "", dev=False, static_dir=self.static)
        c = TestClient(app)
        self.assertEqual(c.get("/api/state").status_code, 401)
        self.assertEqual(c.get("/api/state", headers={"Authorization": "Bearer "}).status_code, 401)


class ThreadSafetyTests(unittest.TestCase):
    """Регрессия: на сервере Budget и CallLog создавались в одном потоке, а использовались
    в рабочих потоках FastAPI, и любой запрос в режиме Claude падал с ошибкой 500."""

    def test_budget_and_calllog_work_from_other_threads(self):
        import threading
        from brain.budget import Budget
        from brain.recorder import CallLog
        budget, log = Budget(monthly_limit=5.0), CallLog()
        errors = []

        def work():
            try:
                budget.record(START, 0.01)
                self.assertTrue(budget.allow(START, 0.0))
                budget.status(START)
                log.put("k", "play", "m", "u", "t", 1, 1, 0.0, "ok")
                self.assertEqual(log.get("k")["text"], "t")
                self.assertEqual(log.count(), 1)
            except Exception as e:      # noqa: BLE001
                errors.append(e)

        th = threading.Thread(target=work)
        th.start()
        th.join()
        self.assertEqual(errors, [])

    @unittest.skipUnless(HAVE_WEB, "нужны fastapi и httpx")
    def test_api_works_when_the_service_has_a_budget(self):
        from brain.budget import Budget
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            storage = Storage(os.path.join(tmp, "pet.sqlite"))
            service = PetService(storage, budget=Budget(monthly_limit=5.0), clock=Clock())
            client = TestClient(create_app(service, "test-token-0123456789", static_dir=None))
            h = {"Authorization": "Bearer test-token-0123456789"}
            self.assertEqual(client.post("/api/hatch", headers=h, json={}).status_code, 200)
            r = client.get("/api/state", headers=h)
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["state"]["tired_budget"])
            self.assertEqual(client.post("/api/act", headers=h,
                                         json={"action": "pet"}).status_code, 200)
            storage.close()


if __name__ == "__main__":
    unittest.main()
