"""A-1c 자모 채널 EM — 편집쌍 결합 분포 δ(α, β)의 MAP-EM.

Ristad & Yianilos(1998)의 확률적 편집거리를 부분열 편집(|α|, |β| ≤ 3)으로 확장한 결합 모형:
  P(x, y) = Σ_{분할} Π_i δ(α_i, β_i)
분할(y·x를 같은 수의 조각으로 나누는 방식)이 잠재변수다. E단계는 (i, j) 격자 위의 전·후진
계산으로 각 편집쌍의 기대 계수를, M단계는 δ = (기대 계수 + λ₀) / (합 + λ₀·|목록|)를 구한다
(디리클레 사전분포 MAP — 목적함수 Σ log P + log 사전 이 단조 증가하고, 목록의 모든 쌍이 양의
확률을 유지한다).

학습 자료는 A-1a의 덩어리 창(``windows.jsonl``)이다. 창 밖의 바뀌지 않은 자모는 길이 1 동일쌍
(s, s)으로 분할이 확정되므로 그 계수를 고정값으로 더한다.

디코더용 조건부 확률은 EM 기대 계수를 A-1b와 같은 방식(``jamo_channel.estimate``: 부분열 빈도로
정규화, 단일 자모는 디리클레 평활)으로 바꿔 만든다 — Brill–Moore의 문맥 확장 중복 계수를 분할
사후확률로 다시 배분한 것에 해당한다.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple

from . import jamo
from .jamo_channel import ChannelParams, estimate

Sym = Tuple[str, ...]
Pair = Tuple[Sym, Sym]
MAX_LEN = 3


def forward_backward(y: Sym, x: Sym, delta: Dict[Pair, float], max_len: int = MAX_LEN):
    """창 하나의 Z = Σ_분할 Π δ 와 편집쌍별 기대 계수(사후 기대 빈도). Z = 0이면 (0, {})."""
    n, m = len(y), len(x)
    edges = []
    for i in range(n + 1):
        for j in range(m + 1):
            for a in range(0, min(max_len, n - i) + 1):
                ya = y[i:i + a]
                for b in range(0, min(max_len, m - j) + 1):
                    if a == 0 and b == 0:
                        continue
                    p = delta.get((ya, x[j:j + b]))
                    if p:
                        edges.append((i, j, i + a, j + b, (ya, x[j:j + b]), p))
    F = [[0.0] * (m + 1) for _ in range(n + 1)]
    F[0][0] = 1.0
    for i, j, i2, j2, _, p in edges:            # (i, j) 사전식 순서 = 위상 순서
        if F[i][j]:
            F[i2][j2] += F[i][j] * p
    Z = F[n][m]
    if Z <= 0.0:
        return 0.0, {}
    B = [[0.0] * (m + 1) for _ in range(n + 1)]
    B[n][m] = 1.0
    for i, j, i2, j2, _, p in reversed(edges):
        if B[i2][j2]:
            B[i][j] += p * B[i2][j2]
    counts: Dict[Pair, float] = {}
    for i, j, i2, j2, key, p in edges:
        post = F[i][j] * p * B[i2][j2] / Z
        if post > 0.0:
            counts[key] = counts.get(key, 0.0) + post
    return Z, counts


def initial_inventory(edit_rows: Iterable[dict], alpha_rows: Iterable[dict],
                      backoff_count: float = 0.01) -> Dict[Pair, float]:
    """편집쌍 목록과 초기 계수: Brill–Moore 계수 + 단일 동일쌍 + 단일 대치·삭제·삽입 백오프."""
    c: Dict[Pair, float] = {}
    for r in edit_rows:
        c[(tuple(r["a"]), tuple(r["b"]))] = float(r["c"])
    c_alpha = {tuple(r["a"]): r["c"] for r in alpha_rows}
    out_mass: Counter = Counter()
    for (a, b), v in c.items():
        if len(a) == 1 and len(b) <= 1:
            out_mass[a] += v
    singles = [(s,) for s in jamo.ALPHABET]
    for a in singles:
        c[(a, a)] = max(float(c_alpha.get(a, 0)) - out_mass[a], 1.0)
        for b in [()] + singles:
            if b != a:
                c.setdefault((a, b), backoff_count)
    for b in singles:
        c.setdefault(((), b), backoff_count)
    return c


def normalize(counts: Dict[Pair, float], prior: float) -> Dict[Pair, float]:
    total = sum(counts.values()) + prior * len(counts)
    return {k: (v + prior) / total for k, v in counts.items()}


def em(windows: Sequence[Tuple[Sym, Sym, int]], outside_identity: Dict[str, float],
       init_counts: Dict[Pair, float], iterations: int = 5, prior: float = 0.01,
       dev_windows: Sequence[Tuple[Sym, Sym, int]] = (), log=print):
    """MAP-EM. 반환: (마지막 기대 계수, 반복별 기록)."""
    inventory = list(init_counts)
    delta = normalize(init_counts, prior)
    history = []
    counts: Dict[Pair, float] = {}
    for it in range(1, iterations + 1):
        counts = {k: 0.0 for k in inventory}
        ll = 0.0
        unreachable = 0
        for y, x, c in windows:
            Z, ec = forward_backward(y, x, delta)
            if Z == 0.0:
                unreachable += c
                continue
            ll += c * math.log(Z)
            for k, v in ec.items():
                counts[k] += c * v
        for s, c in outside_identity.items():
            counts[((s,), (s,))] += c
            ll += c * math.log(delta[((s,), (s,))])
        log_prior = prior * sum(math.log(v) for v in delta.values())
        dev_ll = 0.0
        dev_n = 0
        for y, x, c in dev_windows:
            Z, _ = forward_backward(y, x, delta)
            if Z > 0:
                dev_ll += c * math.log(Z)
                dev_n += c
        rec = {"iter": it, "train_ll": ll, "objective": ll + log_prior, "unreachable": unreachable,
               "dev_ll_per_window": dev_ll / dev_n if dev_n else None}
        history.append(rec)
        log(rec)
        delta = normalize(counts, prior)
    return counts, history


def params_from_counts(counts: Dict[Pair, float], alpha_rows: List[dict], kappa: float = 1.0,
                       min_count: float = 1.0) -> ChannelParams:
    """EM 기대 계수 → 디코더용 조건부 채널 확률(A-1b의 ``estimate``와 같은 정규화·평활)."""
    rows = [{"a": list(a), "b": list(b), "c": v} for (a, b), v in counts.items() if a != b and v > 0]
    return estimate(rows, alpha_rows, kappa=kappa, min_count=min_count)
