"""A-0b 형태소 분석·표층 실현.

- ``analyze(y)``: 교정문 y → 형태소열. 각 형태소는 (형태, 품사, 왼쪽 띄어쓰기 유무)를 갖는다.
  띄어쓰기 유무를 형태소열에 실어야 표층 실현 R(m)이 어절 경계를 재현한다(설계서 §1.1).
- ``realize(m)``: 형태소열 → 표층 문자열. Kiwi ``join``이 축약·이형태·불규칙 활용을 처리한다
  (하+었→했, 덥+어→더워, 을/를 교체). 형태소열은 축약 여부를 담지 않으므로 이 함수는
  자유 변이(되어/돼, 것이/게) 중 하나만 고른다 — 실측 왕복률은 ``log.md`` 2026-11-24 참조.

오류문 x는 분석하지 않는다(설계서 §1.1).
"""

from __future__ import annotations

from typing import Iterable, List, NamedTuple


class Morph(NamedTuple):
    form: str
    tag: str
    space_before: bool


class MorphAnalyzer:
    """Kiwi 래퍼. Kiwi 인스턴스 생성이 무거우므로 한 번 만들어 재사용한다."""

    def __init__(self, kiwi=None, num_workers: int | None = None):
        if kiwi is None:
            from kiwipiepy import Kiwi
            kiwi = Kiwi() if num_workers is None else Kiwi(num_workers=num_workers)
        self.kiwi = kiwi

    @staticmethod
    def _to_morphs(text: str, tokens) -> List[Morph]:
        out: List[Morph] = []
        prev_end = None
        for t in tokens:
            if prev_end is None:
                space = False
            else:
                gap = text[prev_end:t.start] if t.start > prev_end else ""
                space = any(ch.isspace() for ch in gap)
            out.append(Morph(t.form, t.tag, space))
            prev_end = max(prev_end or 0, t.start + t.len)
        return out

    def analyze(self, text: str) -> List[Morph]:
        return self._to_morphs(text, self.kiwi.tokenize(text))

    def analyze_many(self, texts: List[str]) -> List[List[Morph]]:
        results = self.kiwi.tokenize(texts)
        return [self._to_morphs(t, toks) for t, toks in zip(texts, results)]

    def realize(self, morphs: Iterable[Morph], lm_search: bool = True) -> str:
        return self.kiwi.join([(m.form, m.tag, m.space_before) for m in morphs],
                              lm_search=lm_search)

    def spans(self, text: str) -> List[tuple]:
        """[(형태, 품사, 시작, 끝)] — 위치는 text의 문자 인덱스(반열림). 정렬(A-0d)용."""
        return [(t.form, t.tag, t.start, t.start + t.len) for t in self.kiwi.tokenize(text)]

    def spans_many(self, texts: List[str]) -> List[List[tuple]]:
        return [[(t.form, t.tag, t.start, t.start + t.len) for t in toks]
                for toks in self.kiwi.tokenize(texts)]


def normalize_space(text: str) -> str:
    """연속 공백을 하나로, 양끝 공백 제거 (왕복 비교 보조)."""
    return " ".join(text.split())


def roundtrip_category(original: str, realized: str) -> str:
    """'exact' | 'space' (공백만 다름) | 'other'."""
    if realized == original:
        return "exact"
    if normalize_space(realized) == normalize_space(original):
        return "space"
    return "other"


def diff_segments(a: str, b: str) -> List[tuple]:
    """문자 단위 차이 구간 [(a 구간, b 구간), ...]."""
    import difflib
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    return [(a[i1:i2], b[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
