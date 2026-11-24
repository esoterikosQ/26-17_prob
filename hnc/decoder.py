"""A-1d 교정 디코더: 어절 후보 생성(채널 편집의 역적용) + 형태소 LM 빔 탐색.

결정(2026-11-24, 사용자 승인): 설계서 §2.1의 "n-gram WFST 합성" 대신, 형태소 LM이 후보의 형태소
분석을 요구하므로 어절 단위 후보 → Kiwi 분석 → KenLM 상태를 잇는 빔 탐색으로 디코딩한다.

후보 생성(``CandidateGenerator``) — Brill & Moore식:
- 채널 편집 α→β(y→x) 중 확률이 ``min_edit_p`` 이상인 것을 거꾸로(x의 β → y의 α) 적용한다.
  띄어쓰기 편집(``<SP>`` 포함)은 ``min_sp_edit_p`` 이상이면 쓴다.
- 1단계 편집 후 비용 상위 ``expand_top``개에만 2단계 편집을 더한다(``max_edits``=2). 이미 편집된
  자모는 다시 편집하지 않는다(겹치지 않는 분할).
- 유효한 자모열이고 모든 어절이 어휘에 있는 것 중 비용 상위 ``k_cand``개를 남긴다. 비용은 후보를
  만든 분할의 채널 비용(바뀌지 않은 자모의 동일 비용 + 편집 비용)이다 — WFST 최단 거리(모든 분할의
  max)의 상한이며, ``exact_channel``이면 WFST로 다시 채점한다(느림). 원문 그대로(동일) 후보는
  어휘와 무관하게 항상 넣는다.

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
    expand_top: int = 20
    scan_top: int = 3000
    exact_channel: bool = False
    max_pure_ins: int = 8      # 문맥 없는 순수 삽입(β = ∅)은 비용 상위 몇 개만 (문맥 붙은 형태는 따로 있음)
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
        # 동일 편집 비용(단일 심볼). 범위 밖 기호는 0 (JamoChannel과 같음).
        self.idc: Dict[str, float] = {s: -math.log(p) for s, p in params.identity.items()}
        # 역편집: x 쪽 β → (y 쪽 α, 비용 증분). 증분 = −log P(α→β) − Σ_{s∈β} 동일 비용(s)
        # (β 자리의 x 심볼이 원래 y=x 동일 편집으로 계산돼 있던 것을 대체하므로).
        self.rev: Dict[Sym, List[Tuple[Sym, float]]] = {}
        for (a, b), p in params.edits.items():
            if not all(_in_scope(s) for s in a + b):
                continue
            floor = self.cfg.min_sp_edit_p if SP in a + b else self.cfg.min_edit_p
            if p >= floor:
                delta = -math.log(p) - sum(self.idc.get(s, 0.0) for s in b)
                self.rev.setdefault(b, []).append((a, delta))
        self.max_b = max((len(b) for b in self.rev), default=0)
        self._ins = sorted(self.rev.get((), []), key=lambda ad: ad[1])[:self.cfg.max_pure_ins]
        self._cache: Dict[str, List[Tuple[str, float]]] = {}

    def _apply_once(self, xs: Sym, mask: Tuple[bool, ...]):
        """편집되지 않은(원래 x) 자리에만 역편집 하나를 적용: [(새 열, 새 표시, 비용 증분)].

        삽입(β = ∅)은 어느 경계에나 넣을 수 있다. 같은 자모를 두 번 편집하지 않는다(겹치지 않는 분할).
        """
        out = []
        n = len(xs)
        for i in range(n + 1):
            for a, d in self._ins:
                out.append((xs[:i] + a + xs[i:], mask[:i] + (False,) * len(a) + mask[i:], d))
            for L in range(1, self.max_b + 1):
                if i + L > n or not mask[i + L - 1]:
                    break
                for a, d in self.rev.get(xs[i:i + L], ()):
                    out.append((xs[:i] + a + xs[i + L:], mask[:i] + (False,) * len(a) + mask[i + L:], d))
        return out

    def _in_vocab(self, text: str) -> bool:
        parts = text.split(" ")
        return all(p and p in self.vocab for p in parts)

    def generate(self, core: str, merged_only: bool = False) -> List[Tuple[str, float]]:
        """x 조각(핵심, 공백으로 어절 구분 가능) → [(y 조각, 채널 비용)], 동일 후보 포함.

        채널 비용 = 후보를 만든 분할의 비용(바뀌지 않은 y 자모의 동일 비용 + 적용한 편집 비용).
        Brill–Moore의 max 근사와 같은 성격이며, ``exact_channel``이면 WFST 최단 거리로 다시 채점한다.
        ``merged_only``: 공백이 없는 y만(두 x 어절을 합치는 구간용), 동일 후보는 넣지 않는다.
        """
        key = ("M" if merged_only else "S") + core
        if key in self._cache:
            return self._cache[key]
        xs = tuple(jamo.decompose(core))
        base = sum(self.idc.get(s, 0.0) for s in xs)
        best: Dict[Sym, float] = {}
        level = [(xs, (True,) * len(xs), 0.0)]
        for _ in range(self.cfg.max_edits):
            nxt: Dict[Sym, Tuple[float, Tuple[bool, ...]]] = {}
            for ys, mk, c0 in level:
                for ys2, mk2, d in self._apply_once(ys, mk):
                    cc = c0 + d
                    old = nxt.get(ys2)
                    if old is None or cc < old[0]:
                        nxt[ys2] = (cc, mk2)
            for ys2, (cc, _) in nxt.items():
                if cc < best.get(ys2, math.inf):
                    best[ys2] = cc
            level = [(ys2, mk2, cc) for ys2, (cc, mk2) in
                     heapq.nsmallest(self.cfg.expand_top, nxt.items(), key=lambda kv: kv[1][0])]
        best.pop(xs, None)
        scored = []
        for ys, cc in heapq.nsmallest(self.cfg.scan_top, best.items(), key=lambda kv: kv[1]):
            if len(scored) >= self.cfg.k_cand:
                break
            try:
                y = jamo.compose(ys)
            except ValueError:
                continue
            if (merged_only and " " in y) or not self._in_vocab(y):
                continue
            scored.append((y, base + cc))
        out = list({y: c for y, c in reversed(scored)}.items())
        if not merged_only:
            out.append((core, base))
        if self.cfg.exact_channel:
            out = [(y, self.channel.neglogprob(y, core)) for y, _ in out]
        self._cache[key] = out
        return out


@dataclass
class _Hyp:
    score: float
    state: object
    out: Tuple[str, ...]
    lm: float = 0.0       # log P_LM (자연로그)
    ch: float = 0.0       # 채널 비용(구간 사이 공백 포함)
    nm: int = 0           # 형태소 수


@dataclass
class NBest:
    text: str
    score: float
    lm: float
    ch: float
    n_morph: int


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
        nb = self.search_nbest(x, prep, cfg, 1)
        return nb[0].text if nb else x

    def search_nbest(self, x: str, prep, cfg: Optional[DecoderConfig] = None, n_best: int = 8) -> List[NBest]:
        """최종 빔의 상위 ``n_best`` 가설(문장 끝 점수 포함, 서로 다른 출력만)과 성분 점수."""
        cfg = cfg or self.cfg
        n = len(x.split())
        if n == 0:
            return [NBest(x, 0.0, 0.0, 0.0, 0)]
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
                        beams[i + L].append(_Hyp(s, st, h.out + (y,), h.lm + lm, h.ch + ch + sp,
                                                 h.nm + len(toks_y)))
        finals = {}
        for h in beams[n]:
            end = self._kenlm.State()
            e = self.lm.model.BaseScore(h.state, "</s>", end) * LN10
            text = " ".join(h.out)
            cand = NBest(text, h.score + cfg.lam_lm * e, h.lm + e, h.ch, h.nm)
            if text not in finals or cand.score > finals[text].score:
                finals[text] = cand
        return sorted(finals.values(), key=lambda c: -c.score)[:n_best]
