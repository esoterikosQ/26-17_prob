"""Experimental y→x channel: reallocate some copy mass to exactly one surplus eojeol."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from .external_channel import marginal

CONTEXTS = ("start", "interior", "end")


def insertion_slots(y: Sequence[str], x: Sequence[str]) -> range:
    """All boundaries in nonempty y where inserting x[b] produces exactly x."""
    n = len(y)
    if n == 0 or len(x) != n + 1:
        return range(0)
    prefix = suffix = 0
    while prefix < n and x[prefix] == y[prefix]:
        prefix += 1
    while suffix < n and x[-1-suffix] == y[-1-suffix]:
        suffix += 1
    return range(n - suffix, prefix + 1)


def boundary(index: int, n: int) -> str:
    return "start" if index == 0 else "end" if index == n else "interior"


@dataclass(frozen=True)
class Adjustment:
    reserve_cost: float
    pilot_cost: float
    kind: str
    slots: tuple[int, ...] = ()
    event_posterior: float = 0.


class ExtraEojeol:
    def __init__(self, opportunities: dict[str, int], counts: dict[str, dict[str, float]]):
        if set(opportunities) != set(CONTEXTS) or set(counts) != set(CONTEXTS):
            raise ValueError("incomplete boundary contexts")
        self.opportunities = dict(opportunities)
        self.counts = {k: dict(counts[k]) for k in CONTEXTS}
        self.log_odds = {}
        global_counts: dict[str, float] = defaultdict(float)
        for k in CONTEXTS:
            total, events = opportunities[k], sum(counts[k].values())
            if (not isinstance(total, int) or total < 0 or events > total + 1e-9
                    or any(not math.isfinite(v) or v <= 0 or w.split() != [w] or not any(c.isalnum() for c in w)
                           for w, v in counts[k].items())):
                raise ValueError(f"{k}: invalid training counts")
            self.log_odds[k] = math.log(events + .5) - math.log(total - events + .5)
            for word, count in counts[k].items():
                global_counts[word] += count
        n = math.fsum(global_counts.values())
        if not n:
            raise ValueError("no observed eligible training events")
        self.log_emission = {}
        for k in CONTEXTS:
            den = math.log1p(sum(counts[k].values()))
            self.log_emission[k] = {w: math.log(counts[k].get(w, 0.) + v/n) - den
                                    for w, v in global_counts.items()}
        for probs in self.log_emission.values():
            if not math.isclose(math.fsum(math.exp(lp) for lp in probs.values()), 1., abs_tol=1e-12):
                raise ValueError("emission distribution is not normalized")

    @classmethod
    def fit(cls, pairs: Iterable[tuple[str, str]]) -> tuple[ExtraEojeol, dict[str, int]]:
        """Count opportunities in all nonempty train references; count isolated x/y events only."""
        opp: dict[str, int] = {k: 0 for k in CONTEXTS}
        counts: dict[str, dict[str, float]] = {k: defaultdict(float) for k in CONTEXTS}
        info: dict[str, int] = {k: 0 for k in
                                ("pairs", "empty_y", "events", "repeated_slots", "punctuation_excluded")}
        for x, y in pairs:
            xs, ys = x.split(), y.split()
            info["pairs"] += 1
            if not ys:
                info["empty_y"] += 1
                continue
            opp["start"] += 1
            opp["end"] += 1
            opp["interior"] += len(ys) - 1
            slots = insertion_slots(ys, xs)
            if not slots:
                continue
            word = xs[slots[0]]
            if not any(c.isalnum() for c in word):
                info["punctuation_excluded"] += 1
                continue
            info["events"] += 1
            info["repeated_slots"] += len(slots) > 1
            for b in slots:
                counts[boundary(b, len(ys))][word] += 1 / len(slots)
        if not math.isclose(sum(sum(c.values()) for c in counts.values()), info["events"], abs_tol=1e-8):
            raise ValueError("duplicated or lost training event mass")
        return cls(opp, counts), info

    def log_normalizer(self, n: int) -> float:
        if n == 0:
            return 0.
        return math.log1p(math.exp(self.log_odds["start"]) + (n-1)*math.exp(self.log_odds["interior"])
                          + math.exp(self.log_odds["end"]))

    def adjust(self, y: str, x: str, base_cost: float, identity_cost: Callable[[str], float]) -> Adjustment:
        if not math.isfinite(base_cost):
            raise ValueError("non-finite original channel score")
        ys, xs = y.split(), x.split()
        if ys == xs:
            cost = base_cost + self.log_normalizer(len(ys))
            return Adjustment(cost, cost, "identity")
        slots = tuple(insertion_slots(ys, xs))
        if not slots:
            return Adjustment(base_cost, base_cost, "other")
        word = xs[slots[0]]
        if word not in self.log_emission["start"]:
            kind = "unseen" if any(c.isalnum() for c in word) else "punctuation"
            return Adjustment(base_cost, base_cost, kind, slots)
        # Distinct latent boundaries can produce the same surface when neighboring words repeat.
        terms = [self.log_odds[k] + self.log_emission[k][word] for k in (boundary(b, len(ys)) for b in slots)]
        best = max(terms)
        lp = best + math.log(math.fsum(math.exp(t-best) for t in terms)) - self.log_normalizer(len(ys))
        copy_cost = identity_cost(" ".join(ys))
        if not math.isfinite(copy_cost) or not math.isfinite(lp) or lp > 1e-12:
            raise ValueError("invalid copy or insertion probability")
        cost, post = marginal([("base", base_cost), ("EXTRA_EOJEOL", copy_cost - lp)])
        return Adjustment(base_cost, cost, "trained", slots, post["EXTRA_EOJEOL"])
