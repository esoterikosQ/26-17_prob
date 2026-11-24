"""A-2 형태소 n-gram LM: 토큰화와 채점.

- LM 토큰 = ``▁형태/품사`` (어절 첫 형태소에 ``▁``). PCFG 말단(``phdq_pcfg2``의 ``pcfg.korean``)과
  같은 경계 표시를 써서 LM도 띄어쓰기를 판별한다. KenLM은 공백으로 토큰을 나누므로 형태 안의
  공백 문자는 ``_``로 바꾼다.
- 점수는 자연로그(KenLM의 log10 × ln 10), 문장 시작·끝 기호 포함.
"""

from __future__ import annotations

import math
from typing import List, Sequence

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
        self.model = kenlm.Model(str(path))
        self.order = self.model.order

    def score_tokens(self, tokens: Sequence[str]) -> float:
        """자연로그 log P(문장), <s>·</s> 포함."""
        return self.model.score(" ".join(tokens), bos=True, eos=True) * LN10

    def score(self, morphs: Sequence) -> float:
        return self.score_tokens(lm_tokens(morphs))

    def oov(self, tokens: Sequence[str]) -> int:
        return sum(1 for t in tokens if t not in self.model)
