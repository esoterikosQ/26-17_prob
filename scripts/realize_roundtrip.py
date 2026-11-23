"""A-0b 측정: 표층 실현 왕복률 realize(analyze(y)) == y.

EC 교정문과 MP 문장에서 표본을 뽑아 Kiwi 분석 → (형태, 품사, 띄어쓰기) → Kiwi join 결과가
원문과 같은지 잰다. 수치는 표준출력으로, 문장 예시는 outputs/(git 제외)에 쓴다.

    python scripts/realize_roundtrip.py --n 20000 --seed 0
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

from hnc.corpus import ec_files, has_placeholder, iter_ec_pairs, iter_mp_sentences, mp_files  # noqa: E402
from hnc.morph import MorphAnalyzer, diff_segments, roundtrip_category  # noqa: E402


def reservoir(it, n: int, rng: random.Random) -> list:
    sample: list = []
    for i, x in enumerate(it):
        if i < n:
            sample.append(x)
        else:
            j = rng.randint(0, i)
            if j < n:
                sample[j] = x
    return sample


def measure(name: str, texts: list[str], ana: MorphAnalyzer, out_dir: Path) -> dict:
    t0 = time.time()
    analyses = ana.analyze_many(texts)
    cats: Counter = Counter()
    seg_pairs: Counter = Counter()
    examples = []
    for text, morphs in zip(texts, analyses):
        realized = ana.realize(morphs)
        cat = roundtrip_category(text, realized)
        cats[cat] += 1
        if cat != "exact":
            segs = diff_segments(text, realized)
            for a, b in segs:
                seg_pairs[(a, b)] += 1
            if len(examples) < 300:
                examples.append({"cat": cat, "y": text, "R(m)": realized, "diff": segs})
    n = len(texts)
    res = {
        "set": name, "n": n,
        "exact": cats["exact"], "space_only": cats["space"], "other": cats["other"],
        "exact_rate": round(cats["exact"] / n, 4) if n else None,
        "exact_or_space_rate": round((cats["exact"] + cats["space"]) / n, 4) if n else None,
        "seconds": round(time.time() - t0, 1),
    }
    with open(out_dir / f"realize_roundtrip_{name}.jsonl", "w", encoding="utf-8") as f:
        for e in examples:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(out_dir / f"realize_roundtrip_{name}_segments.tsv", "w", encoding="utf-8") as f:
        for (a, b), c in seg_pairs.most_common(200):
            f.write(f"{c}\t{a!r}\t{b!r}\n")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("outputs/a0b"))
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    ana = MorphAnalyzer(num_workers=args.workers)

    counts = Counter()

    def ec_iter():
        for p in iter_ec_pairs(ec_files(args.data)):
            counts["ec_units"] += 1
            if has_placeholder(p.corrected) or not p.corrected.strip():
                counts["ec_placeholder_or_empty"] += 1
                continue
            yield p.corrected

    ec_sample = reservoir(ec_iter(), args.n, rng)
    mp_sample = reservoir((s for _, s in iter_mp_sentences(mp_files(args.data))), args.n, rng)
    print(json.dumps(dict(counts), ensure_ascii=False))
    for name, texts in [("ec", ec_sample), ("mp", mp_sample)]:
        print(json.dumps(measure(name, texts, ana, args.out), ensure_ascii=False))


if __name__ == "__main__":
    main()
