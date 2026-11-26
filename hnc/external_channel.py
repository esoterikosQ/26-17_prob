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
        return self._walk(y, x, None)

    def _walk(self, y: str, x: str, parents: dict | None) -> tuple[float, float]:
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
                        if parents is not None:
                            kind = ("identity" if ny == nx == 1 and aa == bb else "observed" if seen else
                                    "smoothed" if (aa, bb) in self.edges else "other")
                            if seen and good + cost < g0:
                                parents[i + ny, j + nx, 0] = ((i, j, 0), kind, cost)
                            prev = 1 if seen or back < good else 0
                            if (back if prev else good) + cost < b0:
                                parents[i + ny, j + nx, 1] = ((i, j, prev), kind, cost)
                        if seen:
                            cells[i + ny][j + nx] = min(g0, good + cost), min(b0, back + cost)
                        else:
                            cells[i + ny][j + nx] = g0, min(b0, min(good, back) + cost)
        return cells[n][m]

    def traceback(self, y: str, x: str) -> dict:
        """One optimal path in each DP state; offsets are region-local decomposed jamo indices."""
        parents = {}
        costs = self._walk(y, x, parents)
        if costs != self.costs(y, x):
            raise ValueError("traceback changed the jamo DP cost")
        n, m = len(jamo.decompose(y)), len(jamo.decompose(x))
        routes = {}
        for state, name in enumerate(("observed", "backoff")):
            if not math.isfinite(costs[state]):
                routes[name] = None
                continue
            node = (n, m, state)
            steps = []
            while node != (0, 0, 0):
                prev, kind, cost = parents[node]
                steps.append({"y": [prev[0], node[0]], "x": [prev[1], node[1]], "kind": kind, "cost": cost})
                node = prev
            steps.reverse()
            if (not math.isclose(sum(s["cost"] for s in steps), costs[state], abs_tol=1e-10)
                    or any(s["kind"] in ("smoothed", "other") for s in steps) != bool(state)):
                raise ValueError("jamo path support or cost mismatch")
            routes[name] = steps
        return routes


class ExternalChannel:
    def __init__(self, jam: JamoPaths, morph: MorphChannel, floor_cost: float = 12.):
        self.jam, self.morph, self.floor = jam, morph, floor_cost
        self.sub = {(src, _key(dst)): (label, cost) for src, rows in morph.sub.items()
                    for dst, label, cost, _ in rows}
        self.dele = {(lt, _key(dst)): cost for lt, rows in morph.dele.items() for dst, cost, _ in rows}

    def morph_path(self, my, mx, prev: str, *, steps: list | None = None):
        cost, labels, learned = 0., [], True
        for a, b in align_morphs(my, mx):
            if a == b:
                label, c = "IDENT", self.morph.ident_cost(a)
            else:
                if a is None:
                    ins = self.morph.ins.get(f"{_key(b)}|{prev}")
                    label, c = _operation(b, "INS"), ins[0] if ins is not None else None
                elif b is None:
                    label, c = _operation(a, "DEL"), self.dele.get((prev, _key(a)))
                else:
                    label, c = self.sub.get((_key(b), _key(a)), (UNK, None))
                learned &= c is not None
                labels.append(label)
            value = self.floor if c is None else c
            cost += value
            if steps is not None:
                steps.append({"label": label, "left_tag": prev, "y_tag": a[1] if a else None,
                              "x_tag": b[1] if b else None, "floor": c is None, "cost": value})
            if a is not None:
                prev = a[1]
        if not labels:  # m′=m 경로를 자모 경로와 중복 가산하지 않는다.
            return None
        label = (labels[0] if len(set(labels)) == 1 else "MIXED") if learned else UNK
        return label, cost

    def score(self, y: str, x: str, ys, xs, *, trace: dict | None = None) -> tuple[float, list[dict]]:
        regs = align_pair(y, x, [Span(*s) for s in ys])
        if rebuild_x(y, regs) != x:
            raise ValueError("편집 구간으로 관측문을 복원하지 못함")
        yt = [(s[:2], s[2], s[3]) for s in ys if not is_punct(s[:2])]
        xt = [(s[:2], s[2], s[3]) for s in xs if not is_punct(s[:2])]
        j_identity = self.jam.identity(y)
        m_identity = sum(self.morph.ident_cost(m) for m, _, _ in yt)
        cost = j_identity + m_identity
        if trace is not None:
            trace.update(jamo_identity=j_identity, morph_identity=m_identity, regions=[])
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
            mark_ok = marks_y == marks_x
            y_ok = all(g.y_start <= lo and hi <= g.y_end for _, lo, hi in yi)
            x_ok = all(g.x_start <= lo and hi <= g.x_end for _, lo, hi in xi)
            eligible = g.kind == "edit" and mark_ok and y_ok and x_ok
            only_space = (trace is not None and not mark_ok and
                          [c for c in marks_y if not c.isspace()] == [c for c in marks_x if not c.isspace()])
            probe = only_space and g.kind == "edit" and y_ok and x_ok
            steps = [] if trace is not None else None
            morph = None
            if eligible or probe:
                prev = next((m[1] for m, _, hi in reversed(yt) if hi <= g.y_start), "BOS")
                morph = self.morph_path(my, mx, prev, steps=steps)
                if eligible and morph:
                    z, c = morph
                    paths.append((z, c + self.jam.identity(g.x_seg)))
            local, post = marginal(paths)
            increment = local - m_id - self.jam.identity(g.y_seg)
            cost += increment
            spans.append({"x": [g.x_start, g.x_end], "y": [g.y_start, g.y_end],
                          "z": max(post, key=post.__getitem__),
                          "posterior": {k: round(p, 6) for k, p in post.items()},
                          "backoff_only": not any(z != UNK and math.isfinite(c) for z, c in paths)})
            if trace is not None:
                routes = {"jamo_observed": paths[0][1], "jamo_backoff": paths[1][1]}
                if eligible and morph:
                    routes["morph_floor" if morph[0] == UNK else "morph_observed"] = paths[2][1]
                finite = {k: v for k, v in routes.items() if math.isfinite(v)}
                if not math.isclose(marginal(list(finite.items()))[0], local, abs_tol=1e-10):
                    raise ValueError("route marginal differs from the frozen scorer")
                guards = [reason for reason, blocked in (("kind_" + g.kind, g.kind != "edit"),
                          ("whitespace" if only_space else "marks", not mark_ok),
                          ("y_crossing", not y_ok), ("x_crossing", not x_ok)) if blocked]
                trace["regions"].append({"kind": g.kind, "x": spans[-1]["x"], "y": spans[-1]["y"],
                                         "guards": guards, "space_probe": probe,
                                         "morph_label": morph[0] if morph else None, "morph_steps": steps,
                                         "route_costs": finite, "local": local, "increment": increment,
                                         "jamo_paths": self.jam.traceback(g.y_seg, g.x_seg)})
        if not math.isfinite(cost):
            raise ValueError("비유한 합집합 후보 비용")
        return cost, spans


def _operation(m, direction: str) -> str:
    return {"J": "PARTICLE", "E": "ENDING"}.get(mclass(m), "MORPH") + "_" + direction
