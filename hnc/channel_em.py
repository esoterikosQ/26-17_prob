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
       dev_windows: Sequence[Tuple[Sym, Sym, int]] = (), log=print, max_len: int = MAX_LEN):
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
            Z, ec = forward_backward(y, x, delta, max_len)
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
            Z, _ = forward_backward(y, x, delta, max_len)
            if Z > 0:
                dev_ll += c * math.log(Z)
                dev_n += c
        rec = {"iter": it, "train_ll": ll, "objective": ll + log_prior, "unreachable": unreachable,
               "dev_ll_per_window": dev_ll / dev_n if dev_n else None}
        history.append(rec)
        log(rec)
        delta = normalize(counts, prior)
    return counts, history


# ---------------------------------------------------------------------------
# A-6 베이즈화: 희소 디리클레 사전분포 + 평균장 변분 EM (Beal 2003, Johnson 2007)
# ---------------------------------------------------------------------------
def digamma(x: float) -> float:
    r = 0.0
    while x < 6.0:
        r -= 1.0 / x
        x += 1.0
    f = 1.0 / (x * x)
    return r + math.log(x) - 0.5 / x - f * (1 / 12 - f * (1 / 120 - f * (1 / 252 - f * (1 / 240 - f / 132))))


def dirichlet_kl(post: Dict[Pair, float], alpha: float) -> float:
    """KL(Dir(post) ‖ Dir(alpha·1)) (같은 목록 위)."""
    K = len(post)
    a0 = sum(post.values())
    dg0 = digamma(a0)
    kl = math.lgamma(a0) - math.lgamma(alpha * K) + K * math.lgamma(alpha)
    for a in post.values():
        kl += -math.lgamma(a) + (a - alpha) * (digamma(a) - dg0)
    return kl


def vb_weights(post: Dict[Pair, float]) -> Dict[Pair, float]:
    """exp(E_q[log δ]) = exp(ψ(a_k) − ψ(Σa)) — 합이 1보다 작은 부분 정규화 가중치."""
    dg0 = digamma(sum(post.values()))
    return {k: math.exp(digamma(a) - dg0) for k, a in post.items()}


def vb_em(windows: Sequence[Tuple[Sym, Sym, int]], outside_identity: Dict[str, float],
          init_counts: Dict[Pair, float], iterations: int = 5, alpha: float = 0.1,
          dev_windows: Sequence[Tuple[Sym, Sym, int]] = (), log=print, max_len: int = MAX_LEN):
    """편집쌍 결합 분포 δ ~ Dir(α·1)의 평균장 VB-EM. q(δ) = Dir(α + 기대 계수).

    E단계는 δ̃ = exp(E_q log δ)로 전·후진 계산을 하고, ELBO = Σ log Z̃ − KL(q(δ) ‖ p(δ))는 반복마다
    단조 증가한다(구현 정합성 점검용). α < 1이면 쓰이지 않는 편집쌍의 사후 가중치가 크게 줄어든다
    (ψ(α + c) ≈ log(c − 1/2)라 작은 계수일수록 상대적으로 더 깎임).
    반환: (마지막 기대 계수, 사후 매개변수 a, 반복별 기록).
    """
    inventory = list(init_counts)
    post = {k: alpha + init_counts[k] for k in inventory}
    history = []
    counts: Dict[Pair, float] = {}
    for it in range(1, iterations + 1):
        w = vb_weights(post)
        counts = {k: 0.0 for k in inventory}
        ll = 0.0
        unreachable = 0
        for y, x, c in windows:
            Z, ec = forward_backward(y, x, w, max_len)
            if Z == 0.0:
                unreachable += c
                continue
            ll += c * math.log(Z)
            for k, v in ec.items():
                counts[k] += c * v
        for s, c in outside_identity.items():
            counts[((s,), (s,))] += c
            ll += c * math.log(w[((s,), (s,))])
        kl = dirichlet_kl(post, alpha)
        a0 = sum(post.values())
        mean = {k: a / a0 for k, a in post.items()}
        dev_ll = 0.0
        dev_n = 0
        for y, x, c in dev_windows:
            Z, _ = forward_backward(y, x, mean, max_len)
            if Z > 0:
                dev_ll += c * math.log(Z)
                dev_n += c
        rec = {"iter": it, "expected_ll": ll, "kl": kl, "elbo": ll - kl, "unreachable": unreachable,
               "dev_ll_per_window": dev_ll / dev_n if dev_n else None,
               "active_pairs": sum(1 for v in counts.values() if v >= 0.5)}
        history.append(rec)
        log(rec)
        post = {k: alpha + counts[k] for k in inventory}
    return counts, post, history


def conditional_posterior(post: Dict[Pair, float], keys: Sequence[Pair]) -> Dict[Pair, Tuple[float, float]]:
    """q(δ) = Dir(a)에서 조건부 δ(β|α) = δ(α,β)/Σ_β' δ(α,β')의 사후 평균·표준편차.

    디리클레의 부분 벡터를 정규화하면 다시 디리클레(Dir(a_{α,·}))이므로 닫힌 꼴로 계산된다.
    """
    row: Dict[Sym, float] = {}
    for (a, b), v in post.items():
        row[a] = row.get(a, 0.0) + v
    out = {}
    for k in keys:
        A = row[k[0]]
        m = post[k] / A
        out[k] = (m, math.sqrt(m * (1 - m) / (A + 1)))
    return out


def subsample(windows: Sequence[Tuple[Sym, Sym, int]], outside: Dict[str, float], frac: float, seed: int = 0):
    """창 토큰을 비율 ``frac``로 이항 표집(창 종류별 계수 c → Binomial(c, frac)), 창 밖 계수는 비례 축소."""
    import random
    rng = random.Random(seed)
    if frac >= 1.0:
        return list(windows), dict(outside)
    out = []
    for y, x, c in windows:
        k = sum(rng.random() < frac for _ in range(c)) if c < 64 else max(0, round(rng.gauss(c * frac, math.sqrt(c * frac * (1 - frac)))))
        if k:
            out.append((y, x, k))
    return out, {s: c * frac for s, c in outside.items()}


def params_from_counts(counts: Dict[Pair, float], alpha_rows: List[dict], kappa: float = 1.0,
                       min_count: float = 1.0) -> ChannelParams:
    """EM 기대 계수 → 디코더용 조건부 채널 확률(A-1b의 ``estimate``와 같은 정규화·평활)."""
    rows = [{"a": list(a), "b": list(b), "c": v} for (a, b), v in counts.items() if a != b and v > 0]
    return estimate(rows, alpha_rows, kappa=kappa, min_count=min_count)
