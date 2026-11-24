"""A-1d 교정 디코더: 어절 후보 생성(채널 편집의 역적용) + 형태소 LM 빔 탐색.

결정(2026-11-24, 사용자 승인): 설계서 §2.1의 "n-gram WFST 합성" 대신, 형태소 LM이 후보의 형태소
분석을 요구하므로 어절 단위 후보 → Kiwi 분석 → KenLM 상태를 잇는 빔 탐색으로 디코딩한다.

후보 생성(``CandidateGenerator``) — Brill & Moore식:
- 채널 편집 α→β(y→x) 중 확률이 ``min_edit_p`` 이상인 것을 거꾸로(x의 β → y의 α) 적용한다.
  띄어쓰기 편집(``<SP>`` 포함)은 ``min_sp_edit_p`` 이상이면 쓴다.
- 1단계 편집 후 근사 비용 상위 ``expand_top``개에만 2단계 편집을 더한다(``max_edits``=2).
- 유효한 자모열이고 모든 어절이 어휘에 있는 것만 남긴 뒤, 근사 비용 상위 ``k_cand``개를 채널
  WFST(``JamoChannel``)로 정확히 채점한다. 원문 그대로(동일) 후보는 어휘와 무관하게 항상 넣는다.

탐색(``Decoder``):
- x 어절 위치를 따라 빔 탐색. 구간은 x 어절 1개(분리·철자) 또는 2개(공백 삭제로 합치기).
- 점수 = λ_LM·log P_LM(형태소열) − λ_ch·(채널 비용 + 구간 사이 공백의 동일 비용) + λ_len·형태소 수.
- 앞뒤 부호는 x 그대로 둔다(A-1 범위 밖). 형태소열은 후보 조각을 Kiwi로 따로 분석해 얻는다(캐시).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import jamo
from .jamo_channel import ChannelParams, JamoChannel
from .morph_lm import LN10, lm_tokens
from .surface import has_hangul, split_punct

SP = jamo.SP
Sym = Tuple[str, ...]


@dataclass
class DecoderConfig:
    min_edit_p: float = 1e-3
    min_sp_edit_p: float = 1e-5
    max_edits: int = 2
    expand_top: int = 40
    scan_top: int = 3000
    k_cand: int = 8
    beam: int = 8
    lam_lm: float = 1.0
    lam_ch: float = 1.0
    lam_len: float = 0.0
    merge_spans: bool = True


def _in_scope(sym: str) -> bool:
    return jamo.is_jamo(sym) or sym == SP


class CandidateGenerator:
    def __init__(self, params: ChannelParams, vocab: Set[str], channel: JamoChannel,
                 cfg: Optional[DecoderConfig] = None):
        self.cfg = cfg or DecoderConfig()
        self.vocab = vocab
        self.channel = channel
        self.rev: Dict[Sym, List[Tuple[Sym, float]]] = {}
        for (a, b), p in params.edits.items():
            if not all(_in_scope(s) for s in a + b):
                continue
            floor = self.cfg.min_sp_edit_p if SP in a + b else self.cfg.min_edit_p
            if p >= floor:
                self.rev.setdefault(b, []).append((a, -math.log(p)))
        self.max_b = max((len(b) for b in self.rev), default=0)
        self._cache: Dict[str, List[Tuple[str, float]]] = {}

    def _apply_once(self, xs: Sym) -> List[Tuple[Sym, float]]:
        out = []
        n = len(xs)
        for i in range(n + 1):
            for L in range(0, self.max_b + 1):
                if i + L > n:
                    break
                for a, cost in self.rev.get(xs[i:i + L], ()):
                    out.append((xs[:i] + a + xs[i + L:], cost))
        return out

    def _in_vocab(self, text: str) -> bool:
        parts = text.split(" ")
        return all(p and p in self.vocab for p in parts)

    def generate(self, core: str, merged_only: bool = False) -> List[Tuple[str, float]]:
        """x 조각(핵심, 공백으로 어절 구분 가능) → [(y 조각, 정확한 채널 비용)], 동일 후보 포함.

        ``merged_only``: 공백이 없는 y만(두 x 어절을 합치는 구간용), 동일 후보는 넣지 않는다.
        """
        key = ("M" if merged_only else "S") + core
        if key in self._cache:
            return self._cache[key]
        xs = tuple(jamo.decompose(core))
        approx: Dict[Sym, float] = {}
        level = [(xs, 0.0)]
        for _ in range(self.cfg.max_edits):
            nxt: Dict[Sym, float] = {}
            for ys, c0 in level:
                for ys2, c in self._apply_once(ys):
                    cc = c0 + c
                    if cc < nxt.get(ys2, math.inf):
                        nxt[ys2] = cc
            for ys2, cc in nxt.items():
                if cc < approx.get(ys2, math.inf):
                    approx[ys2] = cc
            level = heapq.nsmallest(self.cfg.expand_top, nxt.items(), key=lambda kv: kv[1])
        approx.pop(xs, None)
        scored = []
        for ys, cc in heapq.nsmallest(self.cfg.scan_top, approx.items(), key=lambda kv: kv[1]):
            if len(scored) >= self.cfg.k_cand:
                break
            try:
                y = jamo.compose(ys)
            except ValueError:
                continue
            if (merged_only and " " in y) or not self._in_vocab(y):
                continue
            scored.append(y)
        out = [(y, self.channel.neglogprob(y, core)) for y in dict.fromkeys(scored)]
        if not merged_only:
            out.append((core, self.channel.neglogprob(core, core)))
        self._cache[key] = out
        return out


@dataclass
class _Hyp:
    score: float
    state: object
    out: Tuple[str, ...]


class Decoder:
    def __init__(self, gen: CandidateGenerator, lm, analyzer, cfg: Optional[DecoderConfig] = None):
        import kenlm
        self._kenlm = kenlm
        self.gen = gen
        self.lm = lm
        self.ana = analyzer
        self.cfg = cfg or gen.cfg
        self.sp_cost = -math.log(gen.channel.params.identity[SP])
        self._morph_cache: Dict[str, list] = {}

    def _tokens(self, text: str) -> List[str]:
        m = self._morph_cache.get(text)
        if m is None:
            m = lm_tokens(self.ana.analyze(text))
            self._morph_cache[text] = m
        return m

    def _lm_extend(self, state, tokens: Sequence[str]):
        total = 0.0
        st = state
        for t in tokens:
            nxt = self._kenlm.State()
            total += self.lm.model.BaseScore(st, t, nxt)
            st = nxt
        return total * LN10, st

    def _span_candidates(self, toks: Sequence[str], i: int, L: int) -> List[Tuple[str, float]]:
        if L == 1:
            lead, core, trail = split_punct(toks[i])
            if core and has_hangul(core):
                return [(lead + y + trail, c) for y, c in self.gen.generate(core)]
            return [(toks[i], self.gen.channel.neglogprob(toks[i], toks[i]) if has_hangul(toks[i]) else 0.0)]
        l1, c1, t1 = split_punct(toks[i])
        l2, c2, t2 = split_punct(toks[i + 1])
        if t1 or l2 or not (c1 and c2 and has_hangul(c1) and has_hangul(c2)):
            return []
        return [(l1 + y + t2, c) for y, c in self.gen.generate(c1 + " " + c2, merged_only=True)]

    def prepare(self, x: str) -> Dict[Tuple[int, int], List[Tuple[str, float, List[str]]]]:
        """λ와 무관한 준비: 구간 (i, L)별 후보 [(y 조각, 채널 비용, LM 토큰)]."""
        toks = x.split()
        spans = (1, 2) if self.cfg.merge_spans else (1,)
        prep = {}
        for i in range(len(toks)):
            for L in spans:
                if i + L <= len(toks):
                    prep[(i, L)] = [(y, ch, self._tokens(y)) for y, ch in self._span_candidates(toks, i, L)]
        return prep

    def decode(self, x: str, cfg: Optional[DecoderConfig] = None) -> str:
        return self.search(x, self.prepare(x), cfg)

    def search(self, x: str, prep, cfg: Optional[DecoderConfig] = None) -> str:
        cfg = cfg or self.cfg
        n = len(x.split())
        if n == 0:
            return x
        start = self._kenlm.State()
        self.lm.model.BeginSentenceWrite(start)
        beams: List[List[_Hyp]] = [[] for _ in range(n + 1)]
        beams[0] = [_Hyp(0.0, start, ())]
        for i in range(n):
            if not beams[i]:
                continue
            beams[i] = heapq.nlargest(cfg.beam, beams[i], key=lambda h: h.score)
            for L in (1, 2):
                for y, ch, toks_y in prep.get((i, L), ()):
                    for h in beams[i]:
                        lm, st = self._lm_extend(h.state, toks_y)
                        sp = self.sp_cost if i > 0 else 0.0
                        s = h.score + cfg.lam_lm * lm - cfg.lam_ch * (ch + sp) + cfg.lam_len * len(toks_y)
                        beams[i + L].append(_Hyp(s, st, h.out + (y,)))
        best = None
        for h in beams[n]:
            end = self._kenlm.State()
            s = h.score + cfg.lam_lm * self.lm.model.BaseScore(h.state, "</s>", end) * LN10
            if best is None or s > best[0]:
                best = (s, h)
        return " ".join(best[1].out) if best else x
