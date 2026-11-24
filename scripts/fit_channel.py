"""A-1b 자모 채널 추정 + WFST 구성 + dev 우도 평가.

    python scripts/fit_channel.py --tag ec2_native --workers 12

- datasets/channel/<tag>/{edits,alpha_counts}.jsonl → params.json, other_symbols.json
- 평가(dev): 쌍마다 −log P_J(x | y_정답)과 −log P_J(x | x)(교정 안 함 가설)를 계산.
  유한 비율, 쌍·자모당 평균, 정답 교정의 채널 비용 D = NLL(x|y) − NLL(x|x) 분포, 속도.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc import jamo  # noqa: E402
from hnc.jamo_channel import JamoChannel, estimate, load_counts  # noqa: E402

DEV = {
    "ec2_dev": (Path("datasets/ec/dev.jsonl"), lambda r: r["keyboard"] == "2벌식(쿼티)"),
    "native_dev": (Path("datasets/kor_gec/native_dev.jsonl"), lambda r: True),
    "learner_dev": (Path("datasets/kor_gec/korean_learner_dev.jsonl"), lambda r: True),
}
SYMBOL_SOURCES = [Path("datasets/ec/train.jsonl"), Path("datasets/ec/dev.jsonl"), Path("datasets/ec/test.jsonl")] + \
    sorted(Path("datasets/kor_gec").glob("*.jsonl"))
_CH = None


def _work(item):
    name, y, x, cat = item
    t = time.time()
    a = _CH.neglogprob(y, x)
    b = _CH.neglogprob(x, x)
    return name, cat, a, b, len(_CH.symbols(x)), time.time() - t


def main() -> None:
    global _CH
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="ec2_native")
    ap.add_argument("--kappa", type=float, default=1.0)
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--n", type=int, default=3000, help="dev 집합당 표본 수")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    d = Path("datasets/channel") / args.tag

    t0 = time.time()
    edits, alphas = load_counts(d)
    params = estimate(edits, alphas, kappa=args.kappa, min_count=args.min_count)
    (d / "params.json").write_text(params.to_json(), encoding="utf-8")
    others = Counter()
    for p in SYMBOL_SOURCES:
        with open(p, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                for s in jamo.decompose(r["x"] + r["y"]):
                    if s not in jamo.ALPHABET:
                        others[s] += 1
    (d / "other_symbols.json").write_text(json.dumps(sorted(others), ensure_ascii=False), encoding="utf-8")
    t_est = time.time() - t0
    t0 = time.time()
    _CH = JamoChannel(params, others)
    t_fst = time.time() - t0
    print(json.dumps({"edits": len(params.edits), "other_symbols": len(others), "fst_rows": _CH.n_rows,
                      "fst_states": _CH.fst.num_states(), "estimate_sec": round(t_est, 1),
                      "fst_sec": round(t_fst, 1)}, ensure_ascii=False), flush=True)

    rng = random.Random(args.seed)
    items = []
    for name, (path, keep) in DEV.items():
        recs = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if keep(r):
                    recs.append(r)
        for r in rng.sample(recs, min(args.n, len(recs))):
            items.append((name, r["y"], r["x"], r["cat"]))

    t0 = time.time()
    with mp.get_context("fork").Pool(args.workers) as pool:
        res = list(pool.imap_unordered(_work, items, chunksize=16))
    wall = time.time() - t0
    out = {}
    for name in DEV:
        rows = [r for r in res if r[0] == name]
        fin = [r for r in rows if math.isfinite(r[2])]
        diffs = sorted(r[2] - r[3] for r in fin)
        out[name] = {
            "n": len(rows), "finite": len(fin),
            "nll_per_pair": round(statistics.mean(r[2] for r in fin), 3),
            "nll_per_jamo": round(sum(r[2] for r in fin) / sum(r[4] for r in fin), 4),
            "D_median": round(diffs[len(diffs) // 2], 3), "D_p90": round(diffs[int(len(diffs) * 0.9)], 3),
            "sec_per_pair_mean": round(statistics.mean(r[5] for r in rows), 4),
            "sec_per_pair_max": round(max(r[5] for r in rows), 3),
        }
    print(json.dumps({"wall_sec": round(wall, 1), "dev": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
