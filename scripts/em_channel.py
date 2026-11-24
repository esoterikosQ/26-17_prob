"""A-1c 채널 MAP-EM 실행: A-1a 창 → 편집쌍 결합 분포 → 디코더용 채널 확률.

    python scripts/em_channel.py --tag ec2_native --dev dev_ec2_native --iterations 5

입력 datasets/channel/<tag>/{edits,alpha_counts,windows,other_symbols}.jsonl·json,
dev 창 datasets/channel/<dev>/windows.jsonl. 출력 datasets/channel/<tag>_em/
(params.json, other_symbols.json, em_counts.jsonl, history.json).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.channel_em import em, initial_inventory, params_from_counts  # noqa: E402
from hnc.jamo_channel import load_counts  # noqa: E402


def read_windows(path: Path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            out.append((tuple(r["y"]), tuple(r["x"]), r["c"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="ec2_native")
    ap.add_argument("--dev", default="dev_ec2_native")
    ap.add_argument("--iterations", type=int, default=5)
    ap.add_argument("--prior", type=float, default=0.01)
    ap.add_argument("--kappa", type=float, default=1.0)
    ap.add_argument("--min-count", type=float, default=1.0)
    args = ap.parse_args()
    src = Path("datasets/channel") / args.tag
    out = Path("datasets/channel") / f"{args.tag}_em"
    out.mkdir(parents=True, exist_ok=True)

    edits, alphas = load_counts(src)
    windows = read_windows(src / "windows.jsonl")
    dev = read_windows(Path("datasets/channel") / args.dev / "windows.jsonl")
    c_alpha = {tuple(r["a"]): r["c"] for r in alphas}
    inside: Counter = Counter()
    for y, _, c in windows:
        for s in y:
            inside[s] += c
    outside = {a[0]: c - inside[a[0]] for a, c in c_alpha.items() if len(a) == 1 and c - inside[a[0]] > 0}
    init = initial_inventory(edits, alphas)
    print(json.dumps({"windows": len(windows), "window_count": sum(c for *_, c in windows),
                      "dev_windows": len(dev), "inventory": len(init)}), flush=True)

    t0 = time.time()
    counts, hist = em(windows, outside, init, iterations=args.iterations, prior=args.prior, dev_windows=dev,
                      log=lambda r: print(json.dumps(r), flush=True))
    params = params_from_counts(counts, alphas, kappa=args.kappa, min_count=args.min_count)
    (out / "params.json").write_text(params.to_json(), encoding="utf-8")
    shutil.copy(src / "other_symbols.json", out / "other_symbols.json")
    with open(out / "em_counts.jsonl", "w", encoding="utf-8") as f:
        for (a, b), v in sorted(counts.items(), key=lambda kv: -kv[1]):
            if v >= 0.5:
                f.write(json.dumps({"a": list(a), "b": list(b), "c": round(v, 3)}, ensure_ascii=False) + "\n")
    (out / "history.json").write_text(json.dumps(hist, indent=1), encoding="utf-8")
    top = [[" ".join(a) or "∅", " ".join(b) or "∅", round(v, 1)]
           for (a, b), v in sorted(counts.items(), key=lambda kv: -kv[1]) if a != b][:25]
    print(json.dumps({"seconds": round(time.time() - t0, 1), "edits": len(params.edits), "top_edits": top},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
