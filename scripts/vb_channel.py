"""A-6 채널 베이즈화 실행: 희소 디리클레 VB-EM + 사후 집중 곡선 + 디코더용 채널.

    python scripts/vb_channel.py --tag ec2_native --dev dev_ec2_native --alpha 0.1 --fracs 0.1,0.25,0.5,1

- 학습 창(A-1a ``windows.jsonl``)을 비율별로 이항 표집해 VB-EM(``hnc.channel_em.vb_em``)을 돌리고,
  주요 편집의 조건부 확률 δ(β|α)의 사후 평균·표준편차를 기록한다(설계서 §6.3 사후분포 집중 곡선).
  편집 목록은 전체 자료의 Brill–Moore 계수 상위 ``--top-k``개(동일쌍 제외)로 고정한다.
- 비율 1.0의 결과는 디코더용 채널로 저장: datasets/channel/<tag>_vb_a<α>/ (params.json 등, A-1c와 같은 형식).
- 출력: outputs/a6/<tag>_vb_a<α>/curve.json, 표준출력(JSON 줄).
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

from hnc.channel_em import (  # noqa: E402
    conditional_posterior, initial_inventory, params_from_counts, subsample, vb_em,
)
from hnc.jamo_channel import load_counts  # noqa: E402
from scripts.em_channel import read_windows  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="ec2_native")
    ap.add_argument("--dev", default="dev_ec2_native")
    ap.add_argument("--alpha", type=float, default=0.1)
    ap.add_argument("--iterations", type=int, default=5)
    ap.add_argument("--fracs", default="0.1,0.25,0.5,1")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--max-len", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--kappa", type=float, default=1.0)
    ap.add_argument("--min-count", type=float, default=1.0)
    args = ap.parse_args()
    name = f"{args.tag}_vb_a{args.alpha:g}" + (f"_L{args.max_len}" if args.max_len != 3 else "")
    out = Path("outputs/a6") / name
    out.mkdir(parents=True, exist_ok=True)

    src = Path("datasets/channel") / args.tag
    edits, alphas = load_counts(src)
    windows = read_windows(src / "windows.jsonl")
    dev = read_windows(Path("datasets/channel") / args.dev / "windows.jsonl")
    c_alpha = {tuple(r["a"]): r["c"] for r in alphas}
    inside: Counter = Counter()
    for y, _, c in windows:
        for s in y:
            inside[s] += c
    outside = {a[0]: c - inside[a[0]] for a, c in c_alpha.items() if len(a) == 1 and c - inside[a[0]] > 0}
    full_init = initial_inventory(edits, alphas)
    keys = [k for k, _ in sorted(((k, v) for k, v in full_init.items() if k[0] != k[1] and len(k[0]) <= 3
                                  and len(k[1]) <= 3), key=lambda kv: -kv[1])][:args.top_k]

    curve = []
    for frac in [float(f) for f in args.fracs.split(",")]:
        w_sub, out_sub = subsample(windows, outside, frac, args.seed)
        # 초기 계수도 같은 비율로 줄인다(Brill–Moore 계수 ∝ 자료량); 목록(지지)은 전체와 같게 유지.
        init = {k: v * frac for k, v in full_init.items()}
        t0 = time.time()
        counts, post, hist = vb_em(w_sub, out_sub, init, iterations=args.iterations, alpha=args.alpha,
                                   dev_windows=dev, log=lambda r: None, max_len=args.max_len)
        cp = conditional_posterior(post, keys)
        row = {"frac": frac, "windows": sum(c for *_, c in w_sub), "seconds": round(time.time() - t0, 1),
               "history": hist,
               "edits": [{"a": " ".join(k[0]) or "∅", "b": " ".join(k[1]) or "∅", "mean": m, "sd": s,
                          "cv": s / m if m else None} for k, (m, s) in cp.items()]}
        curve.append(row)
        last = hist[-1]
        print(json.dumps({"frac": frac, "windows": row["windows"], "elbo": [round(h["elbo"]) for h in hist],
                          "dev_ll_per_window": [round(h["dev_ll_per_window"], 4) for h in hist],
                          "active_pairs": last["active_pairs"],
                          "median_cv": sorted(e["cv"] for e in row["edits"])[len(row["edits"]) // 2],
                          "seconds": row["seconds"]}, ensure_ascii=False), flush=True)
        if frac >= 1.0:
            ch = Path("datasets/channel") / name
            ch.mkdir(parents=True, exist_ok=True)
            params = params_from_counts(counts, alphas, kappa=args.kappa, min_count=args.min_count)
            (ch / "params.json").write_text(params.to_json(), encoding="utf-8")
            shutil.copy(src / "other_symbols.json", ch / "other_symbols.json")
            (ch / "history.json").write_text(json.dumps(hist, indent=1), encoding="utf-8")
            with open(ch / "vb_counts.jsonl", "w", encoding="utf-8") as f:
                for (a, b), v in sorted(counts.items(), key=lambda kv: -kv[1]):
                    if v >= 0.5:
                        f.write(json.dumps({"a": list(a), "b": list(b), "c": round(v, 3)}, ensure_ascii=False) + "\n")
            print(json.dumps({"channel": str(ch), "edits": len(params.edits)}), flush=True)
    (out / "curve.json").write_text(json.dumps({"args": vars(args), "curve": curve}, ensure_ascii=False, indent=1),
                                    encoding="utf-8")


if __name__ == "__main__":
    main()
