"""A-2 형태소 n-gram LM: 토큰화와 채점.

- LM 토큰 = ``▁형태/품사`` (어절 첫 형태소에 ``▁``). PCFG 말단(``phdq_pcfg2``의 ``pcfg.korean``)과
  같은 경계 표시를 써서 LM도 띄어쓰기를 판별한다. KenLM은 공백으로 토큰을 나누므로 형태 안의
  공백 문자는 ``_``로 바꾼다.
- 점수는 자연로그(KenLM의 log10 × ln 10), 문장 시작·끝 기호 포함.
- 디코더용 증분 인터페이스: ``begin() → 상태``, ``extend(상태, 토큰열) → (log P, 상태)``,
  ``end(상태) → log P(</s>)``. ``InterpLM``은 여러 LM의 토큰 단위 선형 보간
  P(w|h) = Σ_k w_k P_k(w|h)(상태 = 성분 상태 튜플).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .morph import Morph, MorphAnalyzer

LM_MARK = "▁"
LN10 = math.log(10.0)


def _escape(form: str) -> str:
    return "".join("_" if ch.isspace() else ch for ch in form)


def lm_tokens(morphs: Sequence) -> List[str]:
    """``(형태, 품사[, 왼쪽 띄어쓰기])`` 열 → LM 토큰열. 첫 형태소와 띄어쓰기 뒤 형태소에 ``▁``."""
    out = []
    for k, m in enumerate(morphs):
        space = bool(m[2]) if len(m) > 2 else False
        out.append((LM_MARK if (k == 0 or space) else "") + f"{_escape(m[0])}/{m[1]}")
    return out


def kiwi_morphs(text: str, tokens) -> List[Morph]:
    """Kiwi 토큰(위치는 ``text`` 기준) → 형태소열(띄어쓰기 표시 포함)."""
    return MorphAnalyzer._to_morphs(text, tokens)


class MorphLM:
    def __init__(self, path):
        import kenlm
        self._kenlm = kenlm
        self.model = kenlm.Model(str(path))
        self.order = self.model.order

    def score_tokens(self, tokens: Sequence[str]) -> float:
        """자연로그 log P(문장), <s>·</s> 포함."""
        return self.model.score(" ".join(tokens), bos=True, eos=True) * LN10

    def score(self, morphs: Sequence) -> float:
        return self.score_tokens(lm_tokens(morphs))

    def oov(self, tokens: Sequence[str]) -> int:
        return sum(1 for t in tokens if t not in self.model)

    def token_log10(self, tokens: Sequence[str]) -> List[float]:
        """토큰별 log10 P(</s> 포함, 길이 = len(tokens) + 1)."""
        return [s for s, _, _ in self.model.full_scores(" ".join(tokens), bos=True, eos=True)]

    def begin(self):
        st = self._kenlm.State()
        self.model.BeginSentenceWrite(st)
        return st

    def extend(self, state, tokens: Sequence[str]):
        total, st = 0.0, state
        for t in tokens:
            nxt = self._kenlm.State()
            total += self.model.BaseScore(st, t, nxt)
            st = nxt
        return total * LN10, st

    def end(self, state) -> float:
        return self.model.BaseScore(state, "</s>", self._kenlm.State()) * LN10


def mix_log10(lps: Sequence[float], logw: Sequence[float]) -> float:
    """성분 log10 확률 → 보간 자연로그 확률 log Σ_k w_k 10^{lp_k}."""
    a = [lw + lp * LN10 for lw, lp in zip(logw, lps)]
    m = max(a)
    return m + math.log(sum(math.exp(x - m) for x in a))


class InterpLM:
    """여러 KenLM의 토큰 단위 선형 보간. 가중치 0인 성분은 싣지 않는다."""

    def __init__(self, paths: Sequence, weights: Sequence[float]):
        if len(paths) != len(weights):
            raise ValueError("paths와 weights 길이가 다르다")
        kept = [(p, w) for p, w in zip(paths, weights) if w > 0]
        z = sum(w for _, w in kept)
        self.paths = [p for p, _ in kept]
        self.weights = [w / z for _, w in kept]
        self.logw = [math.log(w) for w in self.weights]
        self.parts = [MorphLM(p) for p in self.paths]
        self._kenlm = self.parts[0]._kenlm
        self.order = max(p.order for p in self.parts)

    def begin(self):
        return tuple(p.begin() for p in self.parts)

    def extend(self, state, tokens: Sequence[str]):
        st = list(state)
        total = 0.0
        for t in tokens:
            lps = []
            for k, p in enumerate(self.parts):
                nxt = self._kenlm.State()
                lps.append(p.model.BaseScore(st[k], t, nxt))
                st[k] = nxt
            total += mix_log10(lps, self.logw)
        return total, tuple(st)

    def end(self, state) -> float:
        lps = [p.model.BaseScore(s, "</s>", self._kenlm.State()) for p, s in zip(self.parts, state)]
        return mix_log10(lps, self.logw)

    def score_tokens(self, tokens: Sequence[str]) -> float:
        cols = [p.token_log10(tokens) for p in self.parts]
        return sum(mix_log10(lps, self.logw) for lps in zip(*cols))

    def score(self, morphs: Sequence) -> float:
        return self.score_tokens(lm_tokens(morphs))

    def oov(self, tokens: Sequence[str]) -> int:
        return sum(1 for t in tokens if all(t not in p.model for p in self.parts))


def load_lm(paths: str, weights: Optional[str] = None):
    """``a.bin`` → MorphLM, ``a.bin,b.bin`` + ``0.3,0.7`` → InterpLM(가중치 생략 시 균등)."""
    ps = [x for x in str(paths).split(",") if x]
    if len(ps) == 1:
        return MorphLM(ps[0])
    ws = [float(x) for x in weights.split(",")] if weights else [1.0] * len(ps)
    return InterpLM(ps, ws)
