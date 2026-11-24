"""사전확률 판별력 점검용 형태소 오류 주입.

`phdq_pcfg2/scripts/eval_scorer.py`(K5)와 같은 네 종류를 형태소열 ``(형태, 품사, 왼쪽 띄어쓰기)``에
적용한다. 위치는 ``rng``로 하나 고른다. 적용할 수 없으면 None.

- case_swap   : 격조사 대치 JKS ↔ JKO (이형태는 앞 형태소의 받침에 맞춤: 이/가, 을/를)
- particle_del: 어절 안의 조사(JK*, JX) 삭제
- eojeol_swap : 인접한 두 어절의 순서 바꿈
- final_ec    : 마지막 종결어미(EF)를 연결어미 '고/EC'로 바꿈
"""

from __future__ import annotations

import random
from typing import List, Optional, Sequence, Tuple

M = Tuple[str, str, bool]
KINDS = ("case_swap", "particle_del", "eojeol_swap", "final_ec")


def _has_coda(form: str) -> Optional[bool]:
    ch = form[-1] if form else ""
    if not ("가" <= ch <= "힣"):
        return None
    return (ord(ch) - 0xAC00) % 28 != 0


def eojeols(m: Sequence[M]) -> List[List[M]]:
    groups: List[List[M]] = []
    for k, x in enumerate(m):
        if not groups or (x[2] and k > 0):
            groups.append([])
        groups[-1].append(tuple(x))
    return groups


def corrupt(m: Sequence[M], kind: str, rng: random.Random) -> Optional[List[M]]:
    m = [tuple(x) for x in m]
    if kind == "case_swap":
        pos = [i for i, x in enumerate(m) if x[1] in ("JKS", "JKO") and i > 0 and _has_coda(m[i - 1][0]) is not None]
        if not pos:
            return None
        i = rng.choice(pos)
        coda = _has_coda(m[i - 1][0])
        form, tag = (("을" if coda else "를"), "JKO") if m[i][1] == "JKS" else (("이" if coda else "가"), "JKS")
        return m[:i] + [(form, tag, m[i][2])] + m[i + 1:]
    if kind == "particle_del":
        pos = [i for i, x in enumerate(m) if (x[1].startswith("JK") or x[1] == "JX") and not x[2] and i > 0]
        if not pos:
            return None
        i = rng.choice(pos)
        return m[:i] + m[i + 1:]
    if kind == "eojeol_swap":
        g = eojeols(m)
        if len(g) < 2:
            return None
        i = rng.randrange(len(g) - 1)
        g[i], g[i + 1] = g[i + 1], g[i]
        return [(f, t, j == 0 and k > 0) for k, e in enumerate(g) for j, (f, t, _) in enumerate(e)]
    if kind == "final_ec":
        pos = [i for i, x in enumerate(m) if x[1] == "EF"]
        if not pos:
            return None
        i = pos[-1]
        return m[:i] + [("고", "EC", m[i][2])] + m[i + 1:]
    raise ValueError(kind)
