"""A-0d 정렬 실행: dev 표본 정렬 → 검수용 파일 + 통계.

    python scripts/align_sample.py --n 5000 --inspect 100 --seed 0

- 통계(표준출력): 불변식(rebuild_x == x) 통과율, 쌍당 구간 수, 구간 종류, 구간당 형태소 수,
  구간 자모 편집 수 분포, 처리 속도.
- 검수 파일(outputs/a0d/inspect_v2.md, git 제외): 내용 편집이 있는 쌍에서 무작위로 뽑아 사람이
  정렬을 판정할 표본. 교정 방향(원문 x ⇒ 교정 y)으로 보이고, 각 편집 구간의 판정 칸에
  ok / bad(이유)를 적는다. (v1 `inspect.md`는 채널 방향 y → x 표기가 혼동을 일으켜 대체.)
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
    ap.add_argument("--inspect", type=int, default=100)
    ap.add_argument("--inspect-name", default="inspect_v2.md")
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

    # 검수 표본: 내용 편집 구간이 있는 쌍만(띄어쓰기·부호만 다른 쌍은 정렬이 자명).
    pool = [(r, gs) for r, gs in aligned if any(g.kind == "edit" for g in gs)]
    rng = random.Random(args.seed + 1)
    picks = rng.sample(pool, min(args.inspect, len(pool)))
    path = args.out / args.inspect_name
    write_inspect(path, picks)
    print(json.dumps({"inspect_file": str(path), "items": len(picks), "pool": len(pool),
                      "edit_regions": sum(g.kind == "edit" for _, gs in picks for g in gs)},
                     ensure_ascii=False))


INSPECT_HEADER = """# A-0d 정렬 검수 표본

## 읽는 법

- **원문 x**: 사람이 메신저에 실제로 친 문장(말뭉치 `form`). 오류가 있는 쪽.
- **교정 y**: 국립국어원 교정자가 고친 문장(말뭉치 `corrected_form`). 모형이 복원할 정답.
- 잡음 채널은 "y를 쓰려다 x를 쳤다"(P(x|y))를 모형화하므로 **y가 정문, x가 비문인 것이 정상**이다.
- 이 검수는 교정이 옳은지가 아니라, 프로그램이 **원문과 교정문을 맞게 짝지었는지**를 본다.

## 판정

- 대상: 내용 편집 구간 `[E#]`만. 띄어쓰기·부호 차이는 개수만 적었다(판정 불필요).
- 셋 다 맞으면 `ok`, 아니면 `bad(번호: 이유)`.
  1. **구간**: 원문 조각 ⇒ 교정 조각이 서로 대응하는 부분을 과부족 없이 잘랐는가.
  2. **형태소**: 교정 조각에 붙은 형태소 분석이 맞고, 바뀐 글자를 빠짐없이 덮는가.
  3. **자모**: `[교정→원문]` 자모 편집이 실제로 그렇게 잘못 칠 법한 설명인가
     (자모는 타건 단위, 초성 ᄀ·중성 ᅡ·종성 ᆨ 구별, ∅ = 없음).
- 교정 자체가 이상하면(말뭉치 정답 오류) 판정 뒤에 `gold?`를 붙인다.

"""


def _jamo_str(ops) -> str:
    out = []
    for o, a, b in ops:
        if o == "=":
            out.append(a)
        else:
            out.append(f"[{a or '∅'}→{b or '∅'}]")
    return " ".join(out)


def write_inspect(path: Path, picks) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(INSPECT_HEADER)
        for k, (r, regions) in enumerate(picks, 1):
            f.write(f"## {k}. {r['unit_id']} ({r['keyboard'] or '-'})\n\n")
            f.write(f"- 원문 x: `{r['x']}`\n- 교정 y: `{r['y']}`\n")
            n_sp = sum(g.kind == "spacing" for g in regions)
            n_pu = sum(g.kind == "punct" for g in regions)
            if n_sp or n_pu:
                f.write(f"- (띄어쓰기 {n_sp}곳, 부호 {n_pu}곳 차이 — 판정 불필요)\n")
            f.write("\n")
            e = 0
            for g in regions:
                if g.kind != "edit":
                    continue
                e += 1
                morphs = " + ".join(f"{m}/{t}" for m, t in g.morphs) or "-"
                f.write(f"  - [E{e}] 원문 `{g.x_seg}` ⇒ 교정 `{g.y_seg}` | 형태소: {morphs} | "
                        f"자모: {_jamo_str(g.jamo_ops)} | 판정: \n")
            f.write("\n")


if __name__ == "__main__":
    main()
