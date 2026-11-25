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

형태소 채널(A-3c, ``morph_channel``을 주면): x를 Kiwi로 한 번 분석해 어절별 형태소(m′의 근사)를 얻고,
``MorphChannel.candidates``의 편집(조사·어미·어휘 대치, 더 쓴 형태소 제거, 빠뜨린 형태소 복원)을 하나씩
적용한 형태소열을 Kiwi ``join``으로 실현해 어절 후보에 더한다(이형태·축약·불규칙 활용은 join이 처리).
비용 = 원문 어절의 자모 동일 비용 + Δ_M. 같은 표층이 자모 경로와 형태소 경로에서 모두 나오면
두 경로의 확률을 더한다(P(x|y) = Σ_{m′} P_M(m′|m) P_J(x|m′)의 두 항).

설명(설계서 §1.5, ``explain``): 고른 출력의 바뀐 어절마다 그 후보를 만든 경로들(JAMO = 자모 채널:
오타·표기·띄어쓰기, PARTICLE·ENDING = 조사·어미 대치, INS = 더 쓴 형태소, DEL = 빠뜨린 형태소)의
채널 확률을 정규화한 사후분포 P(z | x, ŷ)를 낸다(사전확률 항은 같은 ŷ에서 공통이라 약분됨).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import jamo
from .jamo_channel import ChannelParams, JamoChannel
from .morph_lm import lm_tokens
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
    morph_max_cands: int = 12     # 어절당 형태소 편집 후보 수(편집 1개)
    morph_two_edits: bool = False  # 상위 형태소 후보에 편집 하나를 더(어절당 2개)
    morph_combine: int = 0        # 형태소 편집을 얹을 자모 후보 수(오타 + 조사 오류가 한 어절에 겹친 경우)


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


def _punct_tag(tag: str) -> bool:
    return tag in ("SF", "SP", "SS", "SSO", "SSC", "SE", "SO", "SW")


def _neglog_add(a: float, b: float) -> float:
    """−log(e^{−a} + e^{−b})."""
    m = min(a, b)
    return m - math.log1p(math.exp(-abs(a - b)))


@dataclass
class _Hyp:
    score: float
    state: object
    out: Tuple[str, ...]
    lm: float = 0.0       # log P_LM (자연로그)
    ch: float = 0.0       # 채널 비용(구간 사이 공백 포함)
    nm: int = 0           # 형태소 수
    segs: Tuple[Tuple[int, int], ...] = ()   # 출력 조각마다 x 구간 (시작 어절, 어절 수)


@dataclass
class NBest:
    text: str
    score: float
    lm: float
    ch: float
    n_morph: int
    segs: Tuple[Tuple[int, int], ...] = ()
    outs: Tuple[str, ...] = ()


class Decoder:
    def __init__(self, gen: CandidateGenerator, lm, analyzer, cfg: Optional[DecoderConfig] = None,
                 morph_channel=None):
        self.gen = gen
        self.mc = morph_channel
        self.lm = lm
        self.ana = analyzer
        self.cfg = cfg or gen.cfg
        self.sp_cost = -math.log(gen.channel.params.identity[SP])
        self._morph_cache: Dict[str, list] = {}
        self._paths: Dict[Tuple[int, str], Dict[str, float]] = {}

    def _tokens(self, text: str) -> List[str]:
        m = self._morph_cache.get(text)
        if m is None:
            m = lm_tokens(self.ana.analyze(text))
            self._morph_cache[text] = m
        return m

    def _x_morphs(self, x: str, toks: Sequence[str]) -> List[List[Tuple[str, str, int]]]:
        """x 어절별 Kiwi 형태소 [(형태, 품사, 어절 안 시작 위치)] — 위치로 배정."""
        offs, pos = [], 0
        for t in toks:
            a = x.index(t, pos)
            offs.append((a, a + len(t)))
            pos = a + len(t)
        out: List[List[Tuple[str, str, int]]] = [[] for _ in toks]
        k = 0
        for t in self.ana.kiwi.tokenize(x):
            while k < len(offs) and t.start >= offs[k][1]:
                k += 1
            if k < len(offs) and t.start >= offs[k][0]:
                out[k].append((t.form, t.tag, t.start - offs[k][0]))
        return out

    def _realize(self, ms2) -> Optional[str]:
        if not ms2:
            return None
        try:
            return self.ana.kiwi.join(ms2)
        except Exception:
            return None

    def _morph_edits(self, inside, lt0: str) -> List[Tuple[list, str, float]]:
        cfg = self.cfg
        first = self.mc.candidates(inside, lt0, max_cands=cfg.morph_max_cands)
        if not cfg.morph_two_edits:
            return first
        out = list(first)
        for ms1, z1, d1 in first[:5]:
            for ms2, z2, d2 in self.mc.candidates(ms1, lt0, max_cands=5):
                out.append((ms2, f"{z1}+{z2}", d1 + d2))
        return out

    def _morph_candidates(self, core: str, lead: str, ms, lt0: str, jamo_cands=()) -> List[Tuple[str, float, str]]:
        """[(y 핵심, 원문 동일 비용 대비 비용 증분, z)]. ``jamo_cands``: [(자모 후보, 그 후보의 비용 증분)] —
        오타를 고친 자모 후보 위에 형태소 편집을 얹는다(z = JAMO+…)."""
        from .morph_channel import is_punct
        inside = [(f, t) for f, t, a in ms if len(lead) <= a < len(lead) + len(core) and not is_punct((f, t))]
        bases = [(inside, 0.0, "")] if inside else []
        for yj, dj in jamo_cands:
            mj = [(t.form, t.tag) for t in self.ana.kiwi.tokenize(yj) if not is_punct((t.form, t.tag))]
            if mj:
                bases.append((mj, dj, "JAMO+"))
        out = []
        for base_ms, d0, zp in bases:
            for ms2, z, d in self._morph_edits(base_ms, lt0):
                y = self._realize(ms2)
                if y and y != core and " " not in y and has_hangul(y) and self.gen._in_vocab(y):
                    out.append((y, d0 + d, zp + z))
        return out

    def _add_path(self, i: int, y: str, z: str, c: float) -> None:
        d = self._paths.setdefault((i, y), {})
        d[z] = _neglog_add(d[z], c) if z in d else c

    def _span_candidates(self, toks: Sequence[str], i: int, L: int, xm=None) -> List[Tuple[str, float]]:
        if L == 1:
            lead, core, trail = split_punct(toks[i])
            if core and has_hangul(core):
                cands = self.gen.generate(core)
                for y, c in cands:
                    self._add_path(i, lead + y + trail, "IDENT" if y == core else "JAMO", c)
                if xm is not None:
                    base = cands[-1][1] if cands and cands[-1][0] == core else 0.0
                    prev = [m for m in xm[i - 1] if not _punct_tag(m[1])] if i > 0 else []
                    lt0 = prev[-1][1] if prev else "BOS"
                    merged: Dict[str, float] = dict(cands)
                    jc = sorted(((y, c - base) for y, c in cands if y != core and " " not in y),
                                key=lambda yc: yc[1])[:self.cfg.morph_combine]
                    for y, d, z in self._morph_candidates(core, lead, xm[i], lt0, jc):
                        c = base + d
                        merged[y] = _neglog_add(merged[y], c) if y in merged else c
                        self._add_path(i, lead + y + trail, z, c)
                    cands = list(merged.items())
                return [(lead + y + trail, c) for y, c in cands]
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
        xm = self._x_morphs(x, toks) if self.mc is not None and toks else None
        self._paths = {}
        prep = {}
        for i in range(len(toks)):
            for L in spans:
                if i + L <= len(toks):
                    prep[(i, L)] = [(y, ch, self._tokens(y)) for y, ch in self._span_candidates(toks, i, L, xm)]
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
        start = self.lm.begin()
        beams: List[List[_Hyp]] = [[] for _ in range(n + 1)]
        beams[0] = [_Hyp(0.0, start, ())]
        for i in range(n):
            if not beams[i]:
                continue
            beams[i] = heapq.nlargest(cfg.beam, beams[i], key=lambda h: h.score)
            for L in (1, 2):
                for y, ch, toks_y in prep.get((i, L), ()):
                    for h in beams[i]:
                        lm, st = self.lm.extend(h.state, toks_y)
                        sp = self.sp_cost if i > 0 else 0.0
                        s = h.score + cfg.lam_lm * lm - cfg.lam_ch * (ch + sp) + cfg.lam_len * len(toks_y)
                        beams[i + L].append(_Hyp(s, st, h.out + (y,), h.lm + lm, h.ch + ch + sp,
                                                 h.nm + len(toks_y), h.segs + ((i, L),)))
        finals = {}
        for h in beams[n]:
            e = self.lm.end(h.state)
            text = " ".join(h.out)
            cand = NBest(text, h.score + cfg.lam_lm * e, h.lm + e, h.ch, h.nm, h.segs, h.out)
            if text not in finals or cand.score > finals[text].score:
                finals[text] = cand
        return sorted(finals.values(), key=lambda c: -c.score)[:n_best]

    def explain(self, x: str, hyp: "NBest") -> List[dict]:
        """``prepare(x)`` 직후 고른 가설의 바뀐 구간마다 {i, L, x, y, posterior{z: p}}.

        두 어절을 합친 구간(L = 2)은 자모 채널(띄어쓰기)만 만들 수 있으므로 JAMO = 1.
        """
        toks = x.split()
        out = []
        for (i, L), y in zip(hyp.segs, hyp.outs):
            xs = " ".join(toks[i:i + L])
            if y == xs:
                continue
            if L == 2:
                post = {"JAMO": 1.0}
            else:
                paths = self._paths.get((i, y), {"JAMO": 0.0})
                m = min(paths.values())
                w = {z: math.exp(m - c) for z, c in paths.items()}
                tot = sum(w.values())
                post = {z: v / tot for z, v in sorted(w.items(), key=lambda kv: -kv[1])}
            out.append({"i": i, "L": L, "x": xs, "y": y, "posterior": post})
        return out
