"""A-3b 형태소 채널 추정: 추출한 형태소 편집(A-3a) → ``MorphChannel`` (자동 z 지도).

    python scripts/fit_morph_channel.py --sets korean_learner,lang8 --name learner

입력 datasets/morph/<set>_train/{regions.jsonl, morph_counts.json}. 출력 datasets/morph_channel/<name>/params.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.morph_channel import MorphChannel  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="korean_learner,lang8")
    ap.add_argument("--split", default="train")
    ap.add_argument("--name", default="learner")
    ap.add_argument("--beta", type=float, default=5.0)
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--max-alt", type=int, default=20)
    args = ap.parse_args()
    regions, uni, bi = [], Counter(), Counter()
    for s in args.sets.split(","):
        d = Path("datasets/morph") / f"{s}_{args.split}"
        with open(d / "regions.jsonl", encoding="utf-8") as f:
            regions.extend(json.loads(line) for line in f)
        mc = json.loads((d / "morph_counts.json").read_text(encoding="utf-8"))
        for f_, t, n in mc["uni"]:
            uni[(f_, t)] += n
        for lt, f_, t, n in mc["bi"]:
            bi[(lt, f_, t)] += n
    ch = MorphChannel.fit(regions, uni, bi, beta=args.beta, min_count=args.min_count, max_alt=args.max_alt)
    out = Path("datasets/morph_channel") / args.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "params.json").write_text(ch.to_json(), encoding="utf-8")
    top_sub = sorted(((k, m, z, n) for k, v in ch.sub.items() for m, z, _, n in v), key=lambda a: -a[3])[:15]
    top_del = sorted(((lt, m, n) for lt, v in ch.dele.items() for m, _, n in v), key=lambda a: -a[2])[:12]
    top_ins = sorted(ch.ins.items(), key=lambda a: -a[1][1])[:10]
    print(json.dumps({"name": args.name, "sets": args.sets, "regions": len(regions), "morph_types": len(uni),
                      "sub_keys": len(ch.sub), "sub_pairs": sum(len(v) for v in ch.sub.values()),
                      "ins": len(ch.ins), "del_contexts": len(ch.dele),
                      "top_sub(x→y, 빈도)": [[k, "/".join(m), z, n] for k, m, z, n in top_sub],
                      "top_del(lt+y, 빈도)": [[lt, "/".join(m), n] for lt, m, n in top_del],
                      "top_ins(빈도)": [[k, cn[1]] for k, cn in top_ins]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
