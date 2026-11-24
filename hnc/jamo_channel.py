"""A-1b 자모 채널 P_J(x | y): Brill–Moore 부분열 편집 모형과 그 WFST(Pynini).

추정(A-1a 계수표에서):
- 다자모 편집(|α| ≥ 2 또는 |β| ≥ 2, 문맥 확장 포함): 상대빈도 c(α→β) / c(α), ``min_count`` 이상만.
- 단일 자모 α(알파벳 50개)와 삽입 α = ∅: 출력 β ∈ {∅} ∪ 단일 심볼에 대해 디리클레 평활
  (c(α→β) + κ/|B|) / (c(α) + κ). 관측되지 않은 대치·삭제·삽입에도 양의 확률(전 지지).
- 동일 편집 P(s→s) = 1 − Σ_{α=s의 비동일 편집} P (하한 ``min_identity``).
- 범위 밖 기호(문장부호·숫자·라틴 등): 동일 편집 비용 0, 삭제·삽입은 ``other_logp``.
  → 어떤 (x, y)도 삭제·삽입의 조합으로 만들 수 있어 log P_J(x|y)는 언제나 유한하다(설계서 §4.1).

WFST: 모든 편집 (α:β / −log P)의 합집합의 closure. 점수는 열대 반환(tropical)에서
``y ∘ T_J ∘ x``의 최단 거리 = −log max_{분할} Π P(β_i|α_i) (Brill & Moore의 max 근사).
동일 편집은 단일 심볼 단위로만 둔다(다자모 동일 편집은 두지 않음).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from . import jamo

EPS = "<eps>"
UNK_SYM = "<UNKSYM>"
Sym = Tuple[str, ...]


@dataclass
class ChannelParams:
    edits: Dict[Tuple[Sym, Sym], float] = field(default_factory=dict)   # 비동일 편집 확률
    identity: Dict[str, float] = field(default_factory=dict)            # 단일 심볼 동일 편집 확률
    other_logp: float = math.log(1e-9)

    def to_json(self) -> str:
        return json.dumps({"edits": [[list(a), list(b), p] for (a, b), p in self.edits.items()],
                           "identity": self.identity, "other_logp": self.other_logp}, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "ChannelParams":
        d = json.loads(s)
        return cls({(tuple(a), tuple(b)): p for a, b, p in d["edits"]}, d["identity"], d["other_logp"])


def estimate(edit_rows: Iterable[dict], alpha_rows: Iterable[dict], kappa: float = 1.0,
             min_count: int = 2, min_identity: float = 0.5,
             other_logp: float = math.log(1e-9)) -> ChannelParams:
    c_alpha = {tuple(r["a"]): r["c"] for r in alpha_rows}
    singles = [(s,) for s in jamo.ALPHABET]
    outs1: List[Sym] = [()] + singles       # 단일 α의 단일 출력 공간 (∅ = 삭제)
    counts: Dict[Tuple[Sym, Sym], int] = {}
    for r in edit_rows:
        counts[(tuple(r["a"]), tuple(r["b"]))] = r["c"]

    edits: Dict[Tuple[Sym, Sym], float] = {}
    # 다자모 편집: 상대빈도
    for (a, b), c in counts.items():
        if (len(a) <= 1 and len(b) <= 1) or c < min_count or c_alpha.get(a, 0) <= 0:
            continue
        edits[(a, b)] = c / c_alpha[a]
    # 단일 자모 대치·삭제와 삽입: 디리클레 평활
    for a in singles + [()]:
        n = c_alpha.get(a, 0)
        space = singles if a == () else outs1
        for b in space:
            if b == a:
                continue
            edits[(a, b)] = (counts.get((a, b), 0) + kappa / len(space)) / (n + kappa)
    identity = {}
    out_mass: Dict[Sym, float] = {}
    for (a, _), p in edits.items():
        out_mass[a] = out_mass.get(a, 0.0) + p
    for (s,) in singles:
        identity[s] = max(1.0 - out_mass.get((s,), 0.0), min_identity)
    return ChannelParams(edits, identity, other_logp)


def load_counts(dirpath: Path):
    with open(Path(dirpath) / "edits.jsonl", encoding="utf-8") as f:
        edits = [json.loads(x) for x in f]
    with open(Path(dirpath) / "alpha_counts.jsonl", encoding="utf-8") as f:
        alphas = [json.loads(x) for x in f]
    return edits, alphas


class JamoChannel:
    """P_J(x | y)의 WFST. ``other_symbols``는 범위 밖 기호 목록(자료에서 모은 문자)."""

    def __init__(self, params: ChannelParams, other_symbols: Iterable[str] = ()):
        import pynini

        self.params = params
        self.st = pynini.SymbolTable()
        self.st.add_symbol(EPS)
        others = sorted({s for s in other_symbols if self._usable(s)} - set(jamo.ALPHABET) | {UNK_SYM})
        for s in list(jamo.ALPHABET) + others:
            self.st.add_symbol(s)
        rows = [(" ".join(a), " ".join(b), f"{-math.log(p):.6f}") for (a, b), p in params.edits.items()]
        rows += [(s, s, f"{-math.log(p):.6f}") for s, p in params.identity.items()]
        w_other = f"{-params.other_logp:.6f}"
        for s in others:
            rows += [(s, s, "0"), (s, "", w_other), ("", s, w_other)]
        self.fst = pynini.string_map(rows, input_token_type=self.st, output_token_type=self.st).closure()
        self.fst.arcsort("ilabel")
        self.n_rows = len(rows)

    @staticmethod
    def _usable(s: str) -> bool:
        return bool(s) and not any(ch.isspace() for ch in s)

    def symbols(self, text: str) -> List[str]:
        out = []
        for s in jamo.decompose(text):
            out.append(s if self._usable(s) and self.st.member(s) else UNK_SYM)
        return out

    def _acceptor(self, syms: Sequence[str]):
        import pynini
        return pynini.accep(" ".join(syms), token_type=self.st)

    def neglogprob(self, y: str, x: str) -> float:
        """−log P_J(x | y) (Viterbi 분할). 도달 불가면 inf (전 지지라 원칙상 없음)."""
        import pynini
        lat = self._acceptor(self.symbols(y)) @ self.fst @ self._acceptor(self.symbols(x))
        if lat.num_states() == 0:
            return math.inf
        d = pynini.shortestdistance(lat, reverse=True)[lat.start()]
        return float(str(d))
