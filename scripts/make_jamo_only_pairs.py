"""A-3d 계층 분리: 자모 채널을 자모 층위 오류만으로 학습하기 위한 (x′, y) 쌍.

    python scripts/make_jamo_only_pairs.py --sets korean_learner,lang8

설계서 §1.2의 캐스케이드 P(x|y) = Σ_{m′} P_M(m′|m) P_J(x|m′)에서 P_J는 m′ = m(형태소는 그대로)인
자모 잡음만 설명해야 한다. 모든 편집으로 자모 채널을 학습하면 조사·어미 오류(는 → ∅, 를 → 을)도 자모
편집으로 흡수되어, 설명 사후분포에서 형태소 경로가 자모 경로에 확률을 빼앗긴다(2026-11-25 실측:
KAGAS PARTICLE인 참 양성의 53%가 JAMO로 설명됨).

x′ = y에서 A-3a 자동 z가 SPELL인 편집 구간과 띄어쓰기·부호 구간만 x 쪽으로 바꾼 문장(형태소 층위 편집
구간은 y 그대로). 구간 순서는 ``datasets/morph/<set>_train/regions.jsonl``(편집 구간만, 쌍 순서대로)과
같은 ``align_pair``로 다시 맞춘다.
출력: datasets/kor_gec/<set>_jamo_train.jsonl ({unit_id, x: x′, y, x_orig}).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.align import Span, align_pair  # noqa: E402
from hnc.morph import MorphAnalyzer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="korean_learner,lang8")
    ap.add_argument("--chunk", type=int, default=20000)
    args = ap.parse_args()
    ana = MorphAnalyzer()
    for name in args.sets.split(","):
        zs = defaultdict(list)
        with open(f"datasets/morph/{name}_train/regions.jsonl", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                zs[r["unit"]].append((r["y_seg"], r["x_seg"], r["z"]))
        recs = [json.loads(line) for line in open(f"datasets/kor_gec/{name}_train.jsonl", encoding="utf-8")]
        recs = [r for r in recs if r["x"].strip() and r["y"].strip()]
        stat = Counter()
        out = Path(f"datasets/kor_gec/{name}_jamo_train.jsonl")
        with open(out, "w", encoding="utf-8") as fo:
            for s in range(0, len(recs), args.chunk):
                chunk = recs[s:s + args.chunk]
                for r, ys in zip(chunk, ana.spans_many([r["y"] for r in chunk])):
                    y, x = r["y"], r["x"]
                    if x == y:
                        fo.write(json.dumps({"unit_id": r["unit_id"], "x": x, "y": y, "x_orig": x}, ensure_ascii=False) + "\n")
                        stat["identical"] += 1
                        continue
                    regs = align_pair(y, x, [Span(*q) for q in ys])
                    edits = [g for g in regs if g.kind == "edit"]
                    lab = zs.get(r["unit_id"], [])
                    if len(lab) != len(edits) or any(a != g.y_seg or b != g.x_seg for (a, b, _), g in zip(lab, edits)):
                        stat["mismatch"] += 1
                        continue
                    zit = iter(z for _, _, z in lab)
                    parts, pos = [], 0
                    for g in regs:
                        keep_x = g.kind != "edit" or next(zit) == "SPELL"
                        stat["kept_x_" + g.kind if keep_x else "restored_y"] += 1
                        parts.append(y[pos:g.y_start])
                        parts.append(g.x_seg if keep_x else g.y_seg)
                        pos = g.y_end
                    parts.append(y[pos:])
                    xp = "".join(parts)
                    stat["changed_pairs" if xp != y else "now_identical"] += 1
                    fo.write(json.dumps({"unit_id": r["unit_id"], "x": xp, "y": y, "x_orig": x}, ensure_ascii=False) + "\n")
        print(json.dumps({"set": name, "pairs": len(recs), **stat, "out": str(out)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
