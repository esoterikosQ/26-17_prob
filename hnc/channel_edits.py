"""A-1a 자모 채널 학습 편집쌍 추출 (Brill & Moore 2000 방식).

입력은 A-0d 정렬(``hnc.align``)이다. 문장 전체의 자모 정렬을 복원한 뒤(구간 밖은 일치 ``=``),
연속된 비일치 연산 덩어리(atom)마다 좌우 문맥을 0~``context``개 붙여 길이 ≤ ``max_len`` 자모의
부분열 편집 α→β를 모두 센다. 이 계수는 A-1b의 초기 편집 확률이 되고, A-1c의 EM이 분할을 다시
배분한다.

범위(설계서 §1.2(b), plan.md A-1):
- 문맥 없이도 α나 β가 ``max_len``을 넘는 덩어리는 자모 채널 범위 밖(형태소 채널 P_M 몫)으로 보고
  ``long``으로만 센다.
- 한글 자모·``<SP>`` 외의 기호(문장부호·숫자·라틴)가 낀 덩어리는 A-1 범위(맞춤법·띄어쓰기) 밖으로
  ``other``로만 센다.

편집 방향은 생성 방향(P(x|y)): α는 y(정문) 쪽 자모열, β는 x(오류문) 쪽 자모열.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterator, List, Sequence, Tuple

from . import jamo
from .align import Region

Op = Tuple[str, str, str]
Sym = Tuple[str, ...]


def sentence_ops(y: str, regions: Sequence[Region]) -> List[Op]:
    """문장 전체의 자모 정렬 연산열. 구간 밖은 ``("=", s, s)``."""
    ops: List[Op] = []
    pos = 0
    for r in regions:
        ops.extend(("=", s, s) for s in jamo.decompose(y[pos:r.y_start]))
        ops.extend(r.jamo_ops)
        pos = r.y_end
    ops.extend(("=", s, s) for s in jamo.decompose(y[pos:]))
    return ops


def atoms(ops: Sequence[Op]) -> List[Tuple[int, int]]:
    """연속된 비일치 연산 구간 [a, b)."""
    out, start = [], None
    for k, (op, _, _) in enumerate(ops):
        if op != "=" and start is None:
            start = k
        elif op == "=" and start is not None:
            out.append((start, k))
            start = None
    if start is not None:
        out.append((start, len(ops)))
    return out


def _sides(ops: Sequence[Op], lo: int, hi: int) -> Tuple[Sym, Sym]:
    return (tuple(y for _, y, _ in ops[lo:hi] if y), tuple(x for _, _, x in ops[lo:hi] if x))


def _in_scope(sym: str) -> bool:
    return jamo.is_jamo(sym) or sym == jamo.SP


def substring_edits(ops: Sequence[Op], max_len: int = 3, context: int = 1,
                    stats: Counter | None = None) -> Iterator[Tuple[Sym, Sym, bool]]:
    """덩어리마다 문맥 확장 편집 (α, β, 문맥 없는 핵심 편집인가)를 낸다. ``stats``에 덩어리 분류를 센다."""
    for a, b in atoms(ops):
        core_a, core_b = _sides(ops, a, b)
        if not all(_in_scope(s) for s in core_a + core_b):
            if stats is not None:
                stats["atom_other"] += 1
            continue
        if len(core_a) > max_len or len(core_b) > max_len:
            if stats is not None:
                stats["atom_long"] += 1
            continue
        if stats is not None:
            stats["atom_ok"] += 1
        for left in range(context + 1):
            for right in range(context + 1):
                lo, hi = a - left, b + right
                if lo < 0 or hi > len(ops):
                    continue
                alpha, beta = _sides(ops, lo, hi)
                # 문맥으로 붙는 자모도 범위 안(한글 자모·<SP>)이어야 한다 (줄바꿈·부호 문맥 제외).
                if len(alpha) <= max_len and len(beta) <= max_len and \
                        all(_in_scope(s) for s in alpha + beta):
                    yield alpha, beta, left == 0 and right == 0


def substring_counts(syms: Sequence[str], max_len: int = 3) -> Counter:
    """y 자모열의 길이 1..max_len 부분열 빈도 (편집 확률의 분모). 빈 부분열 ()은 삽입 자리 수."""
    c: Counter = Counter()
    n = len(syms)
    c[()] += n + 1
    for i in range(n):
        for k in range(1, max_len + 1):
            if i + k <= n:
                c[tuple(syms[i:i + k])] += 1
    return c
