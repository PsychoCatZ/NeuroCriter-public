"""Тесты M4: ключи VAPID, подписки, политика пингов, интеграция с сервисом и API.

Реальных запросов к push-сервисам нет: отправитель подменяется. Тесты, которым
нужны py_vapid/fastapi, пропускаются без них (окружение server/.venv).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.push import (DEFAULT_PER_DAY, VIBRATION, PushService, host_allowed,  # noqa: E402
                      load_or_create_vapid)
from app.service import PetService  # noqa: E402
from app.storage import Storage  # noqa: E402
from engine.constants import DAY, HOUR  # noqa: E402

try:
    import py_vapid  # noqa: F401
    HAVE_VAPID = True
except Exception:
    HAVE_VAPID = False
try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE_WEB = True
except Exception:
    HAVE_WEB = False

START = 1_800_000_000
GOOD_EP = "https://fcm.googleapis.com/fcm/send/abc123"


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeSender:
    def __init__(self, status=201):
        self.status = status
        self.sent = []

    def __call__(self, subscription, payload):
        self.sent.append((subscription["endpoint"], json.loads(payload)))
        return self.status


def make(tmp, status=201):
    storage = Storage(os.path.join(tmp, "pet.sqlite"))
    sender = FakeSender(status)
    push = PushService(storage, sender, public_key="PUBKEY")
    return storage, sender, push


class HostTests(unittest.TestCase):
    def test_only_known_push_services(self):
        for ok in ("https://fcm.googleapis.com/x", "https://updates.push.services.mozilla.com/y",
                   "https://web.push.apple.com/z", "https://wns2.notify.windows.com/w"):
            self.assertTrue(host_allowed(ok), ok)
        for bad in ("http://fcm.googleapis.com/x", "https://evil.example/x",
                    "https://127.0.0.1/x", "https://fcm.googleapis.com.evil.example/x",
                    "https://localhost/x", "ftp://fcm.googleapis.com/x", "", "not a url"):
            self.assertFalse(host_allowed(bad), bad)


@unittest.skipUnless(HAVE_VAPID, "нужен py_vapid")
class VapidTests(unittest.TestCase):
    def test_key_is_created_once_and_reloaded(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = os.path.join(d, "vapid.pem")
            _, key1 = load_or_create_vapid(path)
            _, key2 = load_or_create_vapid(path)
            self.assertEqual(key1, key2)
            self.assertEqual(len(key1), 87)          # 65 байт в base64url без «=»
            self.assertNotIn("=", key1)


class PushPolicyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.storage, self.sender, self.push = make(self._tmp.name)
        self.push.subscribe(GOOD_EP, "p256", "authkey", 1.0)
        # день D, 15:00 локального времени
        self.noon = 100 * DAY + 15 * HOUR

    def tearDown(self):
        self.storage.close()
        self._tmp.cleanup()

    def test_subscribe_validation_and_idempotence(self):
        with self.assertRaises(ValueError):
            self.push.subscribe("https://evil.example/x", "p", "a")
        with self.assertRaises(ValueError):
            self.push.subscribe(GOOD_EP, "", "a")
        self.push.subscribe(GOOD_EP, "p256-new", "auth-new")
        self.assertEqual(self.push.count_subs(), 1)
        self.assertEqual(self.storage.list_subs()[0]["keys"]["p256dh"], "p256-new")
        self.assertTrue(self.push.unsubscribe(GOOD_EP))
        self.assertFalse(self.push.unsubscribe(GOOD_EP))

    def test_sends_payload_with_vibration(self):
        res = self.push.notify("need", "Недоделок", "хочет есть", self.noon)
        self.assertEqual(res["sent"], 1)
        payload = self.sender.sent[0][1]
        self.assertEqual(payload["body"], "хочет есть")
        self.assertEqual(payload["vibrate"], VIBRATION["need"])

    def test_no_subscribers_means_no_send(self):
        self.push.unsubscribe(GOOD_EP)
        res = self.push.notify("need", "t", "b", self.noon)
        self.assertEqual(res["skipped"], "no_subscribers")
        self.assertEqual(self.sender.sent, [])

    def test_quiet_hours_including_wrap_around(self):
        self.storage.set_setting("quiet_start", 0)
        self.storage.set_setting("quiet_end", 11)
        night = 100 * DAY + 4 * HOUR
        self.assertEqual(self.push.notify("need", "t", "b", night)["skipped"], "quiet")
        self.assertEqual(self.push.notify("need", "t", "b", self.noon)["sent"], 1)
        self.storage.set_setting("quiet_start", 22)        # 22:00-07:00 через полночь
        self.storage.set_setting("quiet_end", 7)
        for hour, quiet in ((23, True), (3, True), (6, True), (7, False), (21, False)):
            self.assertEqual(self.push.in_quiet(hour), quiet, hour)

    def test_equal_start_and_end_disables_quiet_hours(self):
        self.storage.set_setting("quiet_start", 5)
        self.storage.set_setting("quiet_end", 5)
        self.assertFalse(self.push.in_quiet(5))

    def test_urgent_ignores_gap_but_not_quiet_hours_or_limit(self):
        self.assertEqual(self.push.notify("need", "t", "b", self.noon)["sent"], 1)
        self.assertEqual(self.push.notify("need", "t", "b", self.noon + HOUR)["skipped"], "gap")
        self.assertEqual(self.push.notify("health", "t", "b", self.noon + HOUR, urgent=True)["sent"], 1)
        night = 100 * DAY + 4 * HOUR + 2 * DAY
        self.assertEqual(self.push.notify("health", "t", "b", night, urgent=True)["skipped"], "quiet")

    def test_daily_limit(self):
        t = self.noon
        sent = 0
        for _ in range(DEFAULT_PER_DAY + 3):
            sent += self.push.notify("health", "t", "b", t, urgent=True)["sent"]
            t += 600
        self.assertEqual(sent, DEFAULT_PER_DAY)
        next_day = self.noon + DAY
        self.assertEqual(self.push.notify("health", "t", "b", next_day, urgent=True)["sent"], 1)

    def test_dead_subscription_is_removed_and_server_errors_are_kept(self):
        self.sender.status = 410
        res = self.push.notify("need", "t", "b", self.noon)
        self.assertEqual((res["sent"], res["removed"]), (0, 1))
        self.assertEqual(self.push.count_subs(), 0)
        self.push.subscribe(GOOD_EP, "p", "a")
        self.sender.status = 500
        res = self.push.notify("need", "t", "b", self.noon)
        self.assertEqual((res["sent"], res["removed"]), (0, 0))
        self.assertEqual(self.push.count_subs(), 1)
        self.assertEqual(self.storage.recent_pings(1)[0]["status"], "failed")

    def test_force_ignores_policy(self):
        self.storage.set_setting("quiet_start", 0)
        self.storage.set_setting("quiet_end", 23)
        night = 100 * DAY + 4 * HOUR
        res = self.push.notify("test", "t", "b", night, force=True)
        self.assertEqual(res["sent"], 1)


class PetPingTests(unittest.TestCase):
    """Пинги от зверька: фоновый цикл, отсутствие хозяина, тихие часы."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.storage, self.sender, self.push = make(self._tmp.name)
        self.push.subscribe(GOOD_EP, "p", "a")
        self.clock = Clock(START - START % DAY + 9 * HOUR)       # 09:00 локального
        self.service = PetService(self.storage, clock=self.clock, push=self.push)
        self.service.hatch(tz_offset_min=0)
        self.storage.set_setting("quiet_start", 0)
        self.storage.set_setting("quiet_end", 8)

    def tearDown(self):
        self.storage.close()
        self._tmp.cleanup()

    def run_ticks(self, hours, step_min=30):
        for _ in range(int(hours * 60 / step_min)):
            self.clock.advance(step_min * 60)
            self.service.background_tick()

    def test_hungry_pet_pings_an_absent_owner(self):
        self.run_ticks(14)
        bodies = [p["body"] for _, p in self.sender.sent]
        self.assertTrue(any("голод" in b or "хочет есть" in b for b in bodies), bodies)

    def test_no_pings_during_quiet_hours(self):
        self.run_ticks(60)
        for ep, payload in self.sender.sent:
            pass
        times = [p["t"] for p in self.storage.recent_pings(50)]
        self.assertTrue(times)
        for t in times:
            self.assertGreaterEqual((t % DAY) // HOUR, 8)

    def test_daily_cap_is_respected_over_many_days(self):
        self.run_ticks(24 * 5)
        by_day = {}
        for p in self.storage.recent_pings(200):
            by_day[p["t"] // DAY] = by_day.get(p["t"] // DAY, 0) + 1
        self.assertTrue(by_day)
        self.assertLessEqual(max(by_day.values()), DEFAULT_PER_DAY)

    def test_request_path_never_pings(self):
        self.clock.advance(20 * HOUR)
        self.service.state()
        self.service.act("pet")
        self.assertEqual(self.sender.sent, [])

    def test_active_owner_gets_no_need_pings(self):
        for _ in range(48):                          # заботится каждые 30 минут
            self.clock.advance(1800)
            self.service.act("say", text="привет")
            self.service.background_tick()
        self.assertEqual(self.sender.sent, [])

    def test_miss_ping_only_once_per_absence(self):
        self.storage.set_setting("quiet_start", 3)
        self.storage.set_setting("quiet_end", 3)     # без тихих часов
        self.storage.set_setting("pings_per_day", 12)
        self.storage.set_setting("ping_gap_h", 0)
        self.run_ticks(20)
        miss = [p for _, p in self.sender.sent if p["category"] == "miss"]
        self.assertLessEqual(len(miss), 1)

    def test_push_texts_have_no_emoji_because_watches_cannot_draw_them(self):
        import re
        from engine.model import Moment
        pet = self.service.storage.load_pet()
        pet.last_owner_t = pet.t - 9 * HOUR
        moments = [Moment("starving", pet.t), Moment("hungry", pet.t), Moment("ill", pet.t, {"kind": "простуда"}),
                   Moment("hibernate", pet.t), Moment("event:stash", pet.t, {"intro": "Недоделок прячет вещь."})]
        bodies = [body for _, body, _ in self.service._plan_pings(pet, moments, pet.t)]
        bodies += [body for _, body, _ in self.service._plan_pings(pet, [], pet.t)]      # «скучает»
        self.assertGreaterEqual(len(bodies), 5)
        emoji = re.compile("[←-⯿🀀-🫿️]")
        for body in bodies:
            self.assertIsNone(emoji.search(body), body)

    def test_service_without_push_still_works(self):
        service = PetService(self.storage, clock=self.clock)
        self.clock.advance(5 * HOUR)
        self.assertTrue(service.background_tick())
        self.assertEqual(service.get_settings()["push"], {"configured": False, "subscriptions": 0})


@unittest.skipUnless(HAVE_WEB, "нужны fastapi и httpx")
class PushApiTests(unittest.TestCase):
    TOKEN = "test-token-0123456789"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.storage, self.sender, self.push = make(self._tmp.name)
        self.clock = Clock(START - START % DAY + 15 * HOUR)
        self.service = PetService(self.storage, clock=self.clock, push=self.push)
        self.client = TestClient(create_app(self.service, self.TOKEN, static_dir=None))
        self.auth = {"Authorization": "Bearer " + self.TOKEN}
        self.client.post("/api/hatch", headers=self.auth, json={})

    def tearDown(self):
        self.storage.close()
        self._tmp.cleanup()

    def sub_body(self, endpoint=GOOD_EP):
        return {"endpoint": endpoint, "keys": {"p256dh": "p256", "auth": "auth"}}

    def test_requires_token(self):
        self.assertEqual(self.client.get("/api/push/key").status_code, 401)
        self.assertEqual(self.client.post("/api/push/subscribe", json=self.sub_body()).status_code, 401)

    def test_key_subscribe_test_unsubscribe(self):
        r = self.client.get("/api/push/key", headers=self.auth)
        self.assertEqual(r.json(), {"key": "PUBKEY", "subscriptions": 0})
        r = self.client.post("/api/push/subscribe", headers=self.auth, json=self.sub_body())
        self.assertEqual(r.json()["subscriptions"], 1)
        r = self.client.post("/api/push/test", headers=self.auth)
        self.assertEqual(r.json()["sent"], 1)
        self.assertEqual(self.sender.sent[0][1]["category"], "test")
        r = self.client.post("/api/push/unsubscribe", headers=self.auth, json={"endpoint": GOOD_EP})
        self.assertEqual(r.json()["subscriptions"], 0)

    def test_bad_endpoint_is_rejected(self):
        r = self.client.post("/api/push/subscribe", headers=self.auth,
                             json=self.sub_body("https://169.254.169.254/latest"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.push.count_subs(), 0)

    def test_settings_pings_per_day(self):
        r = self.client.post("/api/settings", headers=self.auth, json={"pings_per_day": 6})
        self.assertEqual(r.json()["pings_per_day"], 6)
        r = self.client.post("/api/settings", headers=self.auth, json={"pings_per_day": 99})
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/api/settings", headers=self.auth)
        self.assertEqual(r.json()["push"], {"configured": True, "subscriptions": 0})

    def test_push_not_configured(self):
        service = PetService(self.storage, clock=self.clock)
        client = TestClient(create_app(service, self.TOKEN, static_dir=None))
        r = client.get("/api/push/key", headers=self.auth)
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
