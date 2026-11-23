"""A-0d 정렬: (x, y) → 형태소에 투영된 편집 구간 + 구간 내부 자모 편집.

설계서 §1.4 1단계(거친 정렬 → 편집쌍 내부 자모 재정렬)를 구현한다.

1. 문자 정렬: x와 y의 공통 접두·접미를 떼고 가운데를 가중 레벤슈타인으로 정렬한다.
   한글 음절끼리의 치환 비용은 자모 거리로 매기고(비슷한 음절일수록 쌈), 공백 삽입·삭제는 싸게,
   공백과 문자의 치환은 금지에 가깝게 둔다.
2. 형태소 투영: 연속된 비일치 정렬 항목을 편집 구간으로 묶고, y 쪽 구간을 y의 Kiwi 형태소
   위치(span)에 맞게 넓힌다. 한 음절에 겹친 형태소(갔 = 가 + 었)도 함께 묶인다. 넓힌 뒤 겹치는
   구간은 합친다.
3. 자모 재정렬: 각 구간의 y 조각과 x 조각을 위치 구분 자모열(``hnc.jamo``)로 바꿔 단위 비용
   레벤슈타인으로 정렬한다.

편집 방향은 생성 방향(채널 P(x|y))이다: ``sub`` = y 자모가 x 자모로 바뀜, ``del`` = y 자모가
x에서 빠짐, ``ins`` = x에 자모가 더해짐.

불변식: y에서 각 구간의 y 조각을 x 조각으로 바꾸면 x가 된다(``rebuild_x``).
y의 표층과 형태소 위치를 직접 쓰므로 표층 실현 R(m)은 필요 없다(plan.md §4-3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from . import jamo

INDEL = 1.0
SPACE_INDEL = 0.5
SPACE_SUB = 9.0
OTHER_SUB = 1.0


@dataclass
class Span:
    form: str
    tag: str
    start: int
    end: int


@dataclass
class Region:
    y_start: int
    y_end: int
    x_start: int
    x_end: int
    y_seg: str
    x_seg: str
    kind: str                     # 'spacing' (공백만 다름) | 'edit'
    morphs: List[Tuple[str, str]] = field(default_factory=list)
    jamo_ops: List[Tuple[str, str, str]] = field(default_factory=list)


# --- 1. 문자 정렬 ---------------------------------------------------------------
def _lev(a: Sequence[str], b: Sequence[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


@lru_cache(maxsize=200_000)
def sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if a == " " or b == " ":
        return SPACE_SUB
    if jamo.is_syllable(a) and jamo.is_syllable(b):
        ja, jb = jamo.decompose(a), jamo.decompose(b)
        return 0.4 + 0.6 * _lev(ja, jb) / max(len(ja), len(jb))
    return OTHER_SUB


def _indel(c: str) -> float:
    return SPACE_INDEL if c == " " else INDEL


def char_align(y: str, x: str) -> List[Tuple[Optional[int], Optional[int]]]:
    """정렬 항목 목록 [(y 위치|None, x 위치|None)]. 동점이면 대각(일치·치환)을 우선한다."""
    p = 0
    while p < len(y) and p < len(x) and y[p] == x[p]:
        p += 1
    s = 0
    while s < len(y) - p and s < len(x) - p and y[len(y) - 1 - s] == x[len(x) - 1 - s]:
        s += 1
    ym, xm = y[p:len(y) - s], x[p:len(x) - s]
    n, m = len(ym), len(xm)
    inf = float("inf")
    D = [[inf] * (m + 1) for _ in range(n + 1)]
    B = [[0] * (m + 1) for _ in range(n + 1)]  # 0 대각, 1 y 삭제(위), 2 x 삽입(왼쪽)
    D[0][0] = 0.0
    for i in range(1, n + 1):
        D[i][0] = D[i - 1][0] + _indel(ym[i - 1])
        B[i][0] = 1
    for j in range(1, m + 1):
        D[0][j] = D[0][j - 1] + _indel(xm[j - 1])
        B[0][j] = 2
    for i in range(1, n + 1):
        yi = ym[i - 1]
        row, prow = D[i], D[i - 1]
        for j in range(1, m + 1):
            best, arg = prow[j - 1] + sub_cost(yi, xm[j - 1]), 0
            c = prow[j] + _indel(yi)
            if c < best:
                best, arg = c, 1
            c = row[j - 1] + _indel(xm[j - 1])
            if c < best:
                best, arg = c, 2
            row[j], B[i][j] = best, arg
    mid: List[Tuple[Optional[int], Optional[int]]] = []
    i, j = n, m
    while i > 0 or j > 0:
        arg = B[i][j]
        if arg == 0:
            mid.append((p + i - 1, p + j - 1))
            i, j = i - 1, j - 1
        elif arg == 1:
            mid.append((p + i - 1, None))
            i -= 1
        else:
            mid.append((None, p + j - 1))
            j -= 1
    mid.reverse()
    head = [(k, k) for k in range(p)]
    tail = [(len(y) - s + k, len(x) - s + k) for k in range(s)]
    return head + mid + tail


# --- 3. 자모 재정렬 -------------------------------------------------------------
def jamo_align(ys: Sequence[str], xs: Sequence[str]) -> List[Tuple[str, str, str]]:
    """단위 비용 레벤슈타인 자모 정렬 [(op, y 자모, x 자모)], op ∈ {'=', 'sub', 'del', 'ins'}."""
    n, m = len(ys), len(xs)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        D[i][0] = i
    for j in range(m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j - 1] + (ys[i - 1] != xs[j - 1]), D[i - 1][j] + 1, D[i][j - 1] + 1)
    ops: List[Tuple[str, str, str]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + (ys[i - 1] != xs[j - 1]):
            ops.append(("=" if ys[i - 1] == xs[j - 1] else "sub", ys[i - 1], xs[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and D[i][j] == D[i - 1][j] + 1:
            ops.append(("del", ys[i - 1], ""))
            i -= 1
        else:
            ops.append(("ins", "", xs[j - 1]))
            j -= 1
    ops.reverse()
    return ops


# --- 2. 편집 구간 + 형태소 투영 -------------------------------------------------
def _raw_regions(y: str, x: str, A) -> List[Tuple[int, int]]:
    """연속된 비일치 정렬 항목의 정렬 인덱스 구간 [a0, a1)."""
    out, start = [], None
    for k, (i, j) in enumerate(A):
        match = i is not None and j is not None and y[i] == x[j]
        if not match and start is None:
            start = k
        elif match and start is not None:
            out.append((start, k))
            start = None
    if start is not None:
        out.append((start, len(A)))
    return out


def align_pair(y: str, x: str, spans: Sequence[Span]) -> List[Region]:
    A = char_align(y, x)
    a_of_y = {i: k for k, (i, _) in enumerate(A) if i is not None}
    ranges: List[Tuple[int, int]] = []
    for a0, a1 in _raw_regions(y, x, A):
        ys = [A[k][0] for k in range(a0, a1) if A[k][0] is not None]
        xs_only = "".join(x[A[k][1]] for k in range(a0, a1) if A[k][1] is not None)
        if ys:
            lo, hi = min(ys), max(ys) + 1
        else:
            # x 쪽에만 문자가 더해진 구간: 공백만이면 그대로 두고, 아니면 왼쪽(없으면 오른쪽) y 문자에 붙인다.
            if xs_only.strip() == "":
                ranges.append((a0, a1))
                continue
            left = next((A[k][0] for k in range(a0 - 1, -1, -1) if A[k][0] is not None), None)
            right = next((A[k][0] for k in range(a1, len(A)) if A[k][0] is not None), None)
            anchor = left if left is not None and y[left] != " " else right
            if anchor is None:
                anchor = left
            if anchor is None:
                ranges.append((a0, a1))
                continue
            lo, hi = anchor, anchor + 1
        # 형태소 위치로 넓히기 (겹친 형태소까지 고정점 반복). 공백 문자에만 걸친 경우는 넓히지 않는다.
        if y[lo:hi].strip():
            changed = True
            while changed:
                changed = False
                for s in spans:
                    if s.start < hi and s.end > lo and (s.start < lo or s.end > hi):
                        lo, hi = min(lo, s.start), max(hi, s.end)
                        changed = True
            lo, hi = max(lo, 0), min(hi, len(y))
        ranges.append((min(a0, a_of_y[lo]), max(a1, a_of_y[hi - 1] + 1)))
    ranges.sort()
    merged: List[List[int]] = []
    for a0, a1 in ranges:
        if merged and a0 < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], a1)
        else:
            merged.append([a0, a1])

    regions: List[Region] = []
    for a0, a1 in merged:
        yi = [A[k][0] for k in range(a0, a1) if A[k][0] is not None]
        xj = [A[k][1] for k in range(a0, a1) if A[k][1] is not None]
        y_start = yi[0] if yi else _boundary(A, a0, 0, len(y))
        x_start = xj[0] if xj else _boundary(A, a0, 1, len(x))
        y_seg = "".join(y[i] for i in yi)
        x_seg = "".join(x[j] for j in xj)
        kind = "spacing" if y_seg.replace(" ", "") == x_seg.replace(" ", "") else "edit"
        y_end = y_start + len(y_seg)
        morphs = [(s.form, s.tag) for s in spans if s.start < y_end and s.end > y_start] if y_seg.strip() else []
        regions.append(Region(y_start, y_end, x_start, x_start + len(x_seg), y_seg, x_seg, kind, morphs,
                              jamo_align(jamo.decompose(y_seg), jamo.decompose(x_seg))))
    return regions


def _boundary(A, a0: int, side: int, length: int) -> int:
    """정렬 인덱스 a0 앞에서 해당 쪽(0=y, 1=x)에 남은 다음 문자 위치."""
    for k in range(a0, len(A)):
        if A[k][side] is not None:
            return A[k][side]
    return length


def rebuild_x(y: str, regions: Sequence[Region]) -> str:
    out, pos = [], 0
    for r in regions:
        out.append(y[pos:r.y_start])
        out.append(r.x_seg)
        pos = r.y_end
    out.append(y[pos:])
    return "".join(out)
