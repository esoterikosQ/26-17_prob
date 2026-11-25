"""C안의 후보 식별·합집합과 단일 주석 M²의 corpus 오라클."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def surface(text: str) -> str:
    return " ".join(text.split())


def union_pool(hnc: Sequence[str], seq: Sequence[str]) -> list[dict]:
    if not hnc or not seq:
        raise ValueError("두 후보원 모두 적어도 하나의 후보가 필요하다")
    rows: dict[str, dict] = {}
    for field, texts in (("s", seq), ("h", hnc)):
        for rank, raw in enumerate(texts):
            y = surface(raw)
            row = rows.setdefault(y, {"text": y, "s": None, "h": None})
            if row[field] is None:
                row[field] = rank
    return list(rows.values())


@dataclass(frozen=True)
class Counts:
    correct: int
    proposed: int
    gold: int

    @property
    def f(self) -> float:
        den = self.gold + 4 * self.proposed
        return 5 * self.correct / den if den else 1.0

    def metrics(self) -> dict:
        return {"P": self.correct / self.proposed if self.proposed else 1.0,
                "R": self.correct / self.gold if self.gold else 1.0, "F0.5": self.f,
                "correct": self.correct, "proposed": self.proposed, "gold": self.gold}


def aggregate(stats: Sequence[Counts]) -> Counts:
    return Counts(sum(c.correct for c in stats), sum(c.proposed for c in stats), sum(c.gold for c in stats))


def oracle(pools: Sequence[Sequence[Counts]]) -> Counts:
    """후보당 M² 충분통계가 고정된 경우 micro-F0.5의 실제 최대값(문자 유사도 대용 아님)."""
    if not pools or any(not p or any(s.gold != p[0].gold for s in p) for p in pools):
        raise ValueError("빈 후보 집합이거나 후보끼리 정답 편집 수가 다르다")
    # 분수 목적을 정수 계수로 최적화한다. 동점이면 더 적게 고치는 후보.
    num, den = 0, 1
    while True:
        selected = [max(p, key=lambda s: (5 * s.correct * den - num * (4 * s.proposed + s.gold), -s.proposed))
                    for p in pools]
        total = aggregate(selected)
        n, d = 5 * total.correct, 4 * total.proposed + total.gold
        if not d or n * den == num * d:
            return total
        num, den = n, d
