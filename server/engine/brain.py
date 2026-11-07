"""Интерфейс «мозга». В M0 есть только заглушка, в M1 здесь будет Claude.

Движок формирует варианты (ключ, вес) и просит мозг выбрать один.
Мозг не может вернуть ничего, кроме ключа из переданного списка.
"""


class Brain:
    def choose(self, kind, options, ctx=None):
        raise NotImplementedError


class StubBrain(Brain):
    """Выбор по весам, которые посчитал движок, на детерминированном ГСЧ."""

    def __init__(self, rng):
        self.rng = rng

    def choose(self, kind, options, ctx=None):
        return self.rng.weighted(options)
