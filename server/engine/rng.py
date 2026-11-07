"""Детерминированный генератор случайных чисел с учётом числа обращений."""
import random


class Rng:
    def __init__(self, seed):
        self._r = random.Random(seed)
        self.draws = 0

    def random(self):
        self.draws += 1
        return self._r.random()

    def uniform(self, a, b):
        return a + (b - a) * self.random()

    def gauss(self, mu, sigma):
        self.draws += 1
        return self._r.gauss(mu, sigma)

    def chance(self, p):
        return self.random() < p

    def choice(self, seq):
        return seq[int(self.random() * len(seq)) % len(seq)]

    def weighted(self, options):
        """options: список (ключ, вес). Возвращает ключ."""
        total = sum(max(0.0, w) for _, w in options)
        if total <= 0:
            return options[0][0]
        x = self.random() * total
        for key, w in options:
            x -= max(0.0, w)
            if x <= 0:
                return key
        return options[-1][0]
