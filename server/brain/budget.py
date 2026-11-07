"""Учёт расходов на API и «ворота»: когда потолок исчерпан, зверёк «устал».

Время здесь реальное (деньги тратятся в реальном времени), а не игровое.
"""
import sqlite3
import threading
import time


def _day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _month(ts):
    return time.strftime("%Y-%m", time.gmtime(ts))


class Budget:
    def __init__(self, path=":memory:", monthly_limit=5.0, daily_factor=1.5,
                 warn_at=0.8):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.execute("CREATE TABLE IF NOT EXISTS spend "
                        "(ts REAL, day TEXT, month TEXT, cost REAL)")
        self.monthly_limit = monthly_limit
        # дневная доля: 1/30 месяца с запасом на всплески
        self.daily_limit = monthly_limit / 30 * daily_factor
        self.warn_at = warn_at

    def spent_today(self, now):
        with self.lock:
            row = self.db.execute("SELECT COALESCE(SUM(cost),0) FROM spend WHERE day=?",
                                  (_day(now),)).fetchone()
        return row[0]

    def spent_month(self, now):
        with self.lock:
            row = self.db.execute("SELECT COALESCE(SUM(cost),0) FROM spend WHERE month=?",
                                  (_month(now),)).fetchone()
        return row[0]

    def allow(self, now, est_cost=0.0):
        return (self.spent_today(now) + est_cost <= self.daily_limit
                and self.spent_month(now) + est_cost <= self.monthly_limit)

    def status(self, now):
        """ok | warn (дошли до warn_at месячного потолка) | tired (нельзя звать Claude)."""
        if not self.allow(now):
            return "tired"
        if self.spent_month(now) >= self.warn_at * self.monthly_limit:
            return "warn"
        return "ok"

    def record(self, now, cost):
        with self.lock:
            self.db.execute("INSERT INTO spend VALUES (?,?,?,?)",
                            (now, _day(now), _month(now), cost))
            self.db.commit()
