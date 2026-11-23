"""A-0d 정렬 실행: dev 표본 정렬 → 검수용 파일 + 통계.

    python scripts/align_sample.py --n 5000 --inspect 200 --seed 0

- 통계(표준출력): 불변식(rebuild_x == x) 통과율, 쌍당 구간 수, 구간 종류, 구간당 형태소 수,
  구간 자모 편집 수 분포, 처리 속도.
- 검수 파일(outputs/a0d/inspect.md, git 제외): 사람이 정렬을 판정할 표본. 각 항목의 판정 칸에
  ok / bad(이유)를 적는다.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.align import Span, align_pair, rebuild_x  # noqa: E402
from hnc.morph import MorphAnalyzer  # noqa: E402


def load(path: Path, cats: set, n: int, seed: int) -> list:
    rng = random.Random(seed)
    sample, seen = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["cat"] not in cats:
                continue
            seen += 1
            if len(sample) < n:
                sample.append(r)
            else:
                k = rng.randint(0, seen - 1)
                if k < n:
                    sample[k] = r
    return sample


def bucket(v: int, edges=(1, 2, 3, 5, 10)) -> str:
    for e in edges:
        if v <= e:
            return f"≤{e}"
    return f">{edges[-1]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("datasets/ec/dev.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("outputs/a0d"))
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--inspect", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--cats", default="content,space_only")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    recs = load(args.data, set(args.cats.split(",")), args.n, args.seed)
    ana = MorphAnalyzer()
    t0 = time.time()
    all_spans = ana.spans_many([r["y"] for r in recs])
    t_kiwi = time.time() - t0

    ok = 0
    n_regions, kinds, morph_n, jamo_n, cats = Counter(), Counter(), Counter(), Counter(), Counter()
    aligned = []
    t1 = time.time()
    for r, sp in zip(recs, all_spans):
        regions = align_pair(r["y"], r["x"], [Span(*s) for s in sp])
        ok += rebuild_x(r["y"], regions) == r["x"]
        cats[r["cat"]] += 1
        n_regions[bucket(len(regions))] += 1
        for g in regions:
            kinds[g.kind] += 1
            if g.kind == "edit":
                morph_n[bucket(len(g.morphs), (0, 1, 2, 3, 5))] += 1
                jamo_n[bucket(sum(op != "=" for op, _, _ in g.jamo_ops))] += 1
        aligned.append((r, regions))
    t_align = time.time() - t1

    n = len(recs)
    print(json.dumps({"pairs": n, "cats": dict(cats), "rebuild_ok": ok, "rebuild_rate": round(ok / n, 4),
                      "kiwi_sec": round(t_kiwi, 1), "align_sec": round(t_align, 1),
                      "align_ms_per_pair": round(1000 * t_align / n, 2)}, ensure_ascii=False))
    print(json.dumps({"regions_per_pair": dict(sorted(n_regions.items())), "region_kind": dict(kinds),
                      "morphs_per_edit_region": dict(sorted(morph_n.items())),
                      "jamo_edits_per_edit_region": dict(sorted(jamo_n.items()))}, ensure_ascii=False))

    rng = random.Random(args.seed + 1)
    picks = rng.sample(aligned, min(args.inspect, len(aligned)))
    with open(args.out / "inspect.md", "w", encoding="utf-8") as f:
        f.write("# A-0d 정렬 검수 표본\n\n각 구간의 `판정:` 칸에 ok 또는 bad(이유)를 적는다. "
                "기준: (1) y↔x 구간 대응이 맞는가 (2) 형태소 묶음이 편집을 빠짐없이 덮는가 "
                "(3) 자모 편집이 그럴듯한가.\n\n")
        for k, (r, regions) in enumerate(picks, 1):
            f.write(f"## {k}. [{r['cat']}] {r['doc_id']} {r['unit_id']} ({r['keyboard'] or '-'})\n\n")
            f.write(f"- y: `{r['y']}`\n- x: `{r['x']}`\n\n")
            for g in regions:
                ops = " ".join(f"{o}:{a}>{b}" if o != "=" else a for o, a, b in g.jamo_ops)
                morphs = " + ".join(f"{m}/{t}" for m, t in g.morphs) or "-"
                f.write(f"  - [{g.kind}] y `{g.y_seg}` → x `{g.x_seg}` | 형태소: {morphs} | 자모: {ops} | 판정: \n")
            f.write("\n")
    print(json.dumps({"inspect_file": str(args.out / "inspect.md"), "items": len(picks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
