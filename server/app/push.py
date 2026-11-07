"""Web Push (M4): ключи VAPID, подписки, политика пингов, отправка.

Пинг сообщает о зверьке, а не упрекает хозяина. Ограничения: тихие часы,
не более N пингов в сутки, минимальный интервал между пингами. Срочный пинг
(сильный голод, болезнь) обходит только интервал, но не тихие часы и не лимит.
"""
import base64
import json
import os
from urllib.parse import urlparse

from engine.constants import DAY, HOUR

# только известные push-сервисы: сервер сам ходит по адресу подписки (защита от SSRF)
ALLOWED_PUSH_HOSTS = ("googleapis.com", "mozilla.com", "apple.com", "windows.com")
# вибрация по категориям (Chrome на Android; на часы приходит системное уведомление)
VIBRATION = {
    "need": [200, 100, 200],
    "health": [300, 100, 300, 100, 300],
    "event": [100, 60, 100],
    "miss": [450],
    "test": [120],
}
DEFAULT_PER_DAY = 4
DEFAULT_GAP_H = 3
MAX_FIELD = 600


def host_allowed(endpoint):
    try:
        u = urlparse(endpoint)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and bool(host) and len(endpoint) <= 1000 and
            any(host == h or host.endswith("." + h) for h in ALLOWED_PUSH_HOSTS))


def load_or_create_vapid(path):
    """Вернуть (Vapid, публичный ключ base64url для браузера). Ключ создаётся один раз."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    if os.path.exists(path):
        vapid = Vapid.from_file(path)
    else:
        vapid = Vapid()
        vapid.generate_keys()
        vapid.save_key(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    raw = vapid.public_key.public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    return vapid, base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_webpush_sender(vapid_path, subject):
    """Настоящая отправка. Возвращает HTTP-статус (0 при сетевой ошибке)."""
    def send(subscription, payload):
        from pywebpush import WebPushException, webpush
        try:
            webpush(subscription_info=subscription, data=payload,
                    vapid_private_key=vapid_path, vapid_claims={"sub": subject},
                    ttl=3600, timeout=10)
            return 201
        except WebPushException as e:
            resp = getattr(e, "response", None)
            return resp.status_code if resp is not None else 0
        except Exception:
            return 0
    return send


class PushService:
    def __init__(self, storage, sender, public_key=""):
        self.storage = storage
        self.sender = sender
        self.public_key = public_key

    # --- настройки
    def per_day(self):
        return int(self.storage.get_setting("pings_per_day", str(DEFAULT_PER_DAY)))

    def gap_h(self):
        return int(self.storage.get_setting("ping_gap_h", str(DEFAULT_GAP_H)))

    def in_quiet(self, hour):
        start = int(self.storage.get_setting("quiet_start", "0"))
        end = int(self.storage.get_setting("quiet_end", "11"))
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end          # через полночь

    # --- подписки
    def subscribe(self, endpoint, p256dh, auth, now=0.0):
        if not host_allowed(endpoint):
            raise ValueError("адрес push-сервиса не разрешён")
        for value in (p256dh, auth):
            if not isinstance(value, str) or not value or len(value) > MAX_FIELD:
                raise ValueError("неверные ключи подписки")
        self.storage.add_sub(endpoint, p256dh, auth, now)

    def unsubscribe(self, endpoint):
        return self.storage.remove_sub(endpoint)

    def count_subs(self):
        return len(self.storage.list_subs())

    # --- политика
    def check(self, local_now, urgent=False):
        """None, если можно слать, иначе причина отказа."""
        if not self.storage.list_subs():
            return "no_subscribers"
        hour = (local_now % DAY) // HOUR
        if self.in_quiet(hour):
            return "quiet"
        day_start = local_now - local_now % DAY
        if self.storage.count_pings_since(day_start) >= self.per_day():
            return "limit"
        last = self.storage.last_ping_t()
        if not urgent and last is not None and local_now - last < self.gap_h() * HOUR:
            return "gap"
        return None

    # --- отправка
    def notify(self, category, title, body, local_now, urgent=False, force=False):
        """Отправить пинг всем подписчикам. force=True только для проверки из настроек."""
        if not self.storage.list_subs():
            return {"sent": 0, "removed": 0, "skipped": "no_subscribers"}
        reason = None if force else self.check(local_now, urgent)
        if reason:
            return {"sent": 0, "removed": 0, "skipped": reason}
        payload = json.dumps({"title": title, "body": body, "category": category,
                              "vibrate": VIBRATION.get(category, [150])}, ensure_ascii=False)
        sent = removed = 0
        for sub in self.storage.list_subs():
            status = self.sender(sub, payload)
            if status in (404, 410):                 # подписка мертва
                self.storage.remove_sub(sub["endpoint"])
                removed += 1
            elif 200 <= status < 300:
                sent += 1
        if sent:
            self.storage.add_ping(local_now, category, title, body, "sent")
        elif removed == 0:
            self.storage.add_ping(local_now, category, title, body, "failed")
        return {"sent": sent, "removed": removed, "skipped": None}
