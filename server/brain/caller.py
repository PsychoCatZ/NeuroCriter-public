"""Общий механизм вызова: запись и воспроизведение, учёт расходов."""
from .client import TransportError, cost_usd
from .recorder import REPLAY, CallLog, ReplayMiss


class Caller:
    def __init__(self, transport, calllog, budget, model, clock):
        self.transport = transport
        self.calllog = calllog
        self.budget = budget
        self.model = model
        self.clock = clock

    def allowed(self, est_cost):
        if self.calllog.mode == REPLAY:
            return True
        return self.budget.allow(self.clock(), est_cost)

    def call(self, kind, system, user):
        """Вернуть (текст, источник, стоимость). В replay без записи ReplayMiss."""
        key = CallLog.key(self.model, system, user)
        if self.calllog.mode == REPLAY:
            rec = self.calllog.get(key)
            if rec is None:
                raise ReplayMiss(f"нет записи для запроса {key[:12]}")
            return rec["text"], "replay", 0.0
        if hasattr(self.transport, "estimate_cost"):
            if not self.allowed(self.transport.estimate_cost(system, user)):
                return "", "fallback:tired", 0.0
        try:
            comp = self.transport.complete(system, user)
        except TransportError:
            return "", "fallback:api", 0.0
        cost = cost_usd(self.model, comp.in_tokens, comp.out_tokens)
        self.budget.record(self.clock(), cost)
        self.calllog.put(key, kind, self.model, user, comp.text, comp.in_tokens,
                         comp.out_tokens, cost, "ok")
        return comp.text, getattr(self.transport, "provider", "claude"), cost
