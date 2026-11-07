"""Запись и воспроизведение вызовов LLM.

LIVE: вызов идёт в API, ответ записывается.
REPLAY: ответ берётся из записи по хэшу запроса, при отсутствии записи ошибка.
Так движок отлаживается без трат и без недетерминизма модели.
"""
import hashlib
import json
import sqlite3
import threading

LIVE = "live"
REPLAY = "replay"


class ReplayMiss(Exception):
    pass


class CallLog:
    def __init__(self, path=":memory:", mode=LIVE):
        self.mode = mode
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS llm_calls (key TEXT PRIMARY KEY, kind TEXT, "
            "model TEXT, request TEXT, response TEXT, in_tokens INT, out_tokens INT, "
            "cost_usd REAL, status TEXT)")

    @staticmethod
    def key(model, system, user):
        blob = json.dumps({"m": model, "s": system, "u": user}, ensure_ascii=False,
                          sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key):
        with self.lock:
            row = self.db.execute(
                "SELECT response, in_tokens, out_tokens, cost_usd FROM llm_calls WHERE key=?",
                (key,)).fetchone()
        if row is None:
            return None
        return {"text": row[0], "in_tokens": row[1], "out_tokens": row[2], "cost": row[3]}

    def put(self, key, kind, model, user, text, in_tokens, out_tokens, cost, status):
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?)",
                (key, kind, model, user, text, in_tokens, out_tokens, cost, status))
            self.db.commit()

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
