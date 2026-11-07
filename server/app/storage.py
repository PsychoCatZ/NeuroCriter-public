"""Хранилище состояния зверька в SQLite.

Зверёк хранится одним JSON-документом (один пользователь, мало записей),
рядом настройки и журнал последних реплик для экрана.
"""
import sqlite3
import threading

from engine import persist


class Storage:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS pet_state (id INTEGER PRIMARY KEY CHECK (id=1),
                    json TEXT NOT NULL, updated_t REAL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY,
                    p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_t REAL);
                CREATE TABLE IF NOT EXISTS pings (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    t INTEGER, category TEXT, title TEXT, body TEXT, status TEXT);
                CREATE TABLE IF NOT EXISTS speech (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    t INTEGER, kind TEXT, action TEXT, say TEXT, tone TEXT, source TEXT);
            """)
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    # --- зверёк
    def load_pet(self):
        with self.lock:
            row = self.db.execute("SELECT json FROM pet_state WHERE id=1").fetchone()
        return persist.loads(row[0]) if row else None

    def save_pet(self, pet, wall_time=0.0):
        with self.lock:
            self.db.execute(
                "INSERT INTO pet_state (id, json, updated_t) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET json=excluded.json, updated_t=excluded.updated_t",
                (persist.dumps(pet), wall_time))
            self.db.commit()

    # --- настройки
    def get_setting(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.lock:
            self.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, str(value)))
            self.db.commit()

    # --- реплики для экрана
    def add_speech(self, t, kind, action, say, tone, source):
        with self.lock:
            self.db.execute("INSERT INTO speech (t, kind, action, say, tone, source) "
                            "VALUES (?,?,?,?,?,?)", (t, kind, action, say, tone, source))
            self.db.execute("DELETE FROM speech WHERE id NOT IN "
                            "(SELECT id FROM speech ORDER BY id DESC LIMIT 100)")
            self.db.commit()

    def recent_speech(self, n=8):
        with self.lock:
            rows = self.db.execute(
                "SELECT t, kind, action, say, tone, source FROM speech "
                "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        keys = ("t", "kind", "action", "say", "tone", "source")
        return [dict(zip(keys, r)) for r in reversed(rows)]

    # --- push-подписки и журнал пингов
    def add_sub(self, endpoint, p256dh, auth, created_t=0.0):
        with self.lock:
            self.db.execute(
                "INSERT INTO push_subs (endpoint, p256dh, auth, created_t) VALUES (?,?,?,?) "
                "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth",
                (endpoint, p256dh, auth, created_t))
            self.db.commit()

    def remove_sub(self, endpoint):
        with self.lock:
            cur = self.db.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))
            self.db.commit()
            return cur.rowcount > 0

    def list_subs(self):
        with self.lock:
            rows = self.db.execute("SELECT endpoint, p256dh, auth FROM push_subs").fetchall()
        return [{"endpoint": r[0], "keys": {"p256dh": r[1], "auth": r[2]}} for r in rows]

    def add_ping(self, t, category, title, body, status):
        with self.lock:
            self.db.execute("INSERT INTO pings (t, category, title, body, status) VALUES (?,?,?,?,?)",
                            (t, category, title, body, status))
            self.db.execute("DELETE FROM pings WHERE id NOT IN "
                            "(SELECT id FROM pings ORDER BY id DESC LIMIT 200)")
            self.db.commit()

    def count_pings_since(self, t):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM pings WHERE t>=? AND status='sent'",
                                   (t,)).fetchone()[0]

    def last_ping_t(self):
        with self.lock:
            row = self.db.execute("SELECT MAX(t) FROM pings WHERE status='sent'").fetchone()
        return row[0]

    def recent_pings(self, n=10):
        with self.lock:
            rows = self.db.execute("SELECT t, category, title, body, status FROM pings "
                                   "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        keys = ("t", "category", "title", "body", "status")
        return [dict(zip(keys, r)) for r in rows]

    def backup_to(self, path):
        """Консистентная копия базы (для ежедневного бэкапа)."""
        with self.lock:
            dest = sqlite3.connect(path)
            self.db.backup(dest)
            dest.close()
