"""Deterministic randomness, bit-compatible with the JavaScript reference engine (mulberry32)."""
import math

MASK = 0xFFFFFFFF


class Mulberry32:
    """Small, fast, seedable PRNG. Same sequence as the JS reference for the same seed."""

    def __init__(self, seed: int):
        self.a = int(seed) & MASK

    def __call__(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & MASK
        a = self.a
        t = ((a ^ (a >> 15)) * (1 | a)) & MASK
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & MASK)) & MASK) ^ t
        return ((t ^ (t >> 14)) & MASK) / 4294967296.0


def gauss(r: Mulberry32) -> float:
    """Box-Muller standard normal using two draws (same draw order as the reference)."""
    u = 0.0
    while not u:
        u = r()
    v = r()
    return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v)


def pick(r: Mulberry32, arr):
    return arr[int(math.floor(r() * len(arr)))]
