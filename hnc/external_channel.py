"""C-2/C-4: 후보 출처에 무관한 외부 교정문 채점. 비용은 항상 생성 방향 y → x.

고정된 A-0d 구간의 자모 격자에서 학습 사건만 쓴 최저비용 경로와 백오프를 한 번이라도
쓴 최저비용 경로를 따로 보존한다. 같은 구간을 실제 학습된 형태소 편집으로 설명할 수도
있다. 이 제한된 경로들의 질량을 합한다. 유한한 점수와 언어적으로 타당한 설명은 다르다.
"""

from __future__ import annotations

import math
from functools import lru_cache

from . import jamo
from .align import Span, align_pair, rebuild_x
from .jamo_channel import ChannelParams
from .morph_channel import MorphChannel, _key, align_morphs, is_punct, mclass

INF = math.inf
UNK = "UNCLASSIFIED"


def marginal(paths: list[tuple[str, float]]) -> tuple[float, dict[str, float]]:
    m = min(c for _, c in paths)
    if not math.isfinite(m):
        raise ValueError("유한한 채널 경로가 없다")
    weights: dict[str, float] = {}
    for z, cost in paths:
        if math.isfinite(cost):
            weights[z] = weights.get(z, 0.) + math.exp(m - cost)
    total = sum(weights.values())
    return m - math.log(total), {z: w / total for z, w in weights.items()}


class JamoPaths:
    def __init__(self, params: ChannelParams, observed: set):
        self.idc = {s: -math.log(p) for s, p in params.identity.items()}
        self.edges = {pair: (-math.log(p), pair in observed) for pair, p in params.edits.items()}
        self.other = -params.other_logp
        self.max_y = max((len(a) for a, _ in self.edges), default=1)
        self.max_x = max((len(b) for _, b in self.edges), default=1)

    @lru_cache(maxsize=16384)
    def identity(self, text: str) -> float:
        return sum(self.idc.get(s, 0.) for s in jamo.decompose(text))

    @lru_cache(maxsize=32768)
    def costs(self, y: str, x: str) -> tuple[float, float]:
        """(관측 사건 전용, 백오프 포함) 경로 비용. 서로 다른 미등록 문자를 동일 기호로 합치지 않는다."""
        a, b = tuple(jamo.decompose(y)), tuple(jamo.decompose(x))
        n, m = len(a), len(b)
        cells = [[(INF, INF) for _ in range(m + 1)] for _ in range(n + 1)]
        cells[0][0] = (0., INF)
        for i in range(n + 1):
            for j in range(m + 1):
                good, back = cells[i][j]
                if not math.isfinite(min(good, back)):
                    continue
                for ny in range(min(max(1, self.max_y), n - i) + 1):
                    aa = a[i:i + ny]
                    for nx in range(min(max(1, self.max_x), m - j) + 1):
                        if ny == nx == 0:
                            continue
                        bb = b[j:j + nx]
                        if ny == nx == 1 and aa == bb:
                            cost, seen = self.idc.get(aa[0], 0.), True
                        else:
                            edge = self.edges.get((aa, bb))
                            if edge is None:
                                # 단일 원문자의 삽입/삭제로 임의 문자열에 유한 경로를 보장.
                                if ny + nx != 1:
                                    continue
                                cost, seen = self.other, False
                            else:
                                cost, seen = edge
                        g0, b0 = cells[i + ny][j + nx]
                        if seen:
                            cells[i + ny][j + nx] = min(g0, good + cost), min(b0, back + cost)
                        else:
                            cells[i + ny][j + nx] = g0, min(b0, min(good, back) + cost)
        return cells[n][m]


class ExternalChannel:
    def __init__(self, jam: JamoPaths, morph: MorphChannel, floor_cost: float = 12.):
        self.jam, self.morph, self.floor = jam, morph, floor_cost
        self.sub = {(src, _key(dst)): (label, cost) for src, rows in morph.sub.items()
                    for dst, label, cost, _ in rows}
        self.dele = {(lt, _key(dst)): cost for lt, rows in morph.dele.items() for dst, cost, _ in rows}

    def morph_path(self, my, mx, prev: str):
        cost, labels, learned = 0., [], True
        for a, b in align_morphs(my, mx):
            if a == b:
                cost += self.morph.ident_cost(a)
            else:
                if a is None:
                    ins = self.morph.ins.get(f"{_key(b)}|{prev}")
                    label, c = _operation(b, "INS"), ins[0] if ins is not None else None
                elif b is None:
                    label, c = _operation(a, "DEL"), self.dele.get((prev, _key(a)))
                else:
                    label, c = self.sub.get((_key(b), _key(a)), (UNK, None))
                learned &= c is not None
                cost += c if c is not None else self.floor
                labels.append(label)
            if a is not None:
                prev = a[1]
        if not labels:  # m′=m 경로를 자모 경로와 중복 가산하지 않는다.
            return None
        label = (labels[0] if len(set(labels)) == 1 else "MIXED") if learned else UNK
        return label, cost

    def score(self, y: str, x: str, ys, xs) -> tuple[float, list[dict]]:
        regs = align_pair(y, x, [Span(*s) for s in ys])
        if rebuild_x(y, regs) != x:
            raise ValueError("편집 구간으로 관측문을 복원하지 못함")
        yt = [(s[:2], s[2], s[3]) for s in ys if not is_punct(s[:2])]
        xt = [(s[:2], s[2], s[3]) for s in xs if not is_punct(s[:2])]
        cost = self.jam.identity(y) + sum(self.morph.ident_cost(m) for m, _, _ in yt)
        spans = []
        for g in regs:
            yi = [(m, lo, hi) for m, lo, hi in yt if g.y_start < g.y_end and lo < g.y_end and hi > g.y_start]
            xi = [(m, lo, hi) for m, lo, hi in xt if g.x_start < g.x_end and lo < g.x_end and hi > g.x_start]
            my, mx = [m for m, _, _ in yi], [m for m, _, _ in xi]
            m_id = sum(self.morph.ident_cost(m) for m in my)
            seen_j, back_j = self.jam.costs(g.y_seg, g.x_seg)
            z_j = "WS" if g.kind == "spacing" else "JAMO"
            paths = [(z_j, m_id + seen_j), (UNK, m_id + back_j)]
            # 분리된 형태소 경로는 인접 부호·공백 변화나 구간을 가로지른 x 분석을 공짜로 설명하지 않는다.
            marks_y = [c for c in g.y_seg if not c.isalnum()]
            marks_x = [c for c in g.x_seg if not c.isalnum()]
            if (g.kind == "edit" and marks_y == marks_x and
                    all(g.y_start <= lo and hi <= g.y_end for _, lo, hi in yi) and
                    all(g.x_start <= lo and hi <= g.x_end for _, lo, hi in xi)):
                prev = next((m[1] for m, _, hi in reversed(yt) if hi <= g.y_start), "BOS")
                morph = self.morph_path(my, mx, prev)
                if morph:
                    z, c = morph
                    paths.append((z, c + self.jam.identity(g.x_seg)))
            local, post = marginal(paths)
            cost += local - m_id - self.jam.identity(g.y_seg)
            spans.append({"x": [g.x_start, g.x_end], "y": [g.y_start, g.y_end],
                          "z": max(post, key=post.__getitem__),
                          "posterior": {k: round(p, 6) for k, p in post.items()},
                          "backoff_only": not any(z != UNK and math.isfinite(c) for z, c in paths)})
        if not math.isfinite(cost):
            raise ValueError("비유한 합집합 후보 비용")
        return cost, spans


def _operation(m, direction: str) -> str:
    return {"J": "PARTICLE", "E": "ENDING"}.get(mclass(m), "MORPH") + "_" + direction
