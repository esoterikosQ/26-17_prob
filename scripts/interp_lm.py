"""A-2 LM 보간 가중치: v0(영역 내)·v1(신문 포함)의 토큰 단위 선형 보간 P = w·P_v0 + (1−w)·P_v1을
dev 퍼플렉시티로 고른다.

    python scripts/interp_lm.py --lms datasets/lm/v0/lm5.bin,datasets/lm/v1/lm5.bin \
        --dev-dir datasets/lm/v0 --grid 0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1

- 토큰별 log10 P를 성분마다 한 번만 구하고(KenLM full_scores) 격자는 그 위에서 계산한다.
- 퍼플렉시티 분모 = 토큰 수 + 문장 수(</s>), eval_lm.py와 같은 정의.
- 출력: 표준출력(JSON), outputs/interp_lm/summary.json.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.morph_lm import MorphLM  # noqa: E402


def token_scores(lm: MorphLM, path: Path) -> np.ndarray:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            toks = line.split()
            if toks:
                out.extend(lm.token_log10(toks))
    return np.asarray(out, dtype=np.float64)


def mix_ppl(a: np.ndarray, b: np.ndarray, w: float) -> float:
    ln10 = math.log(10.0)
    if w >= 1.0:
        lp = a * ln10
    elif w <= 0.0:
        lp = b * ln10
    else:
        lp = np.logaddexp(math.log(w) + a * ln10, math.log(1 - w) + b * ln10)
    return float(math.exp(-lp.mean()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lms", default="datasets/lm/v0/lm5.bin,datasets/lm/v1/lm5.bin")
    ap.add_argument("--dev-dir", type=Path, default=Path("datasets/lm/v0"))
    ap.add_argument("--grid", default="0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1")
    ap.add_argument("--out", type=Path, default=Path("outputs/interp_lm"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    p0, p1 = args.lms.split(",")
    grid = [float(x) for x in args.grid.split(",")]

    t0 = time.time()
    lm0, lm1 = MorphLM(p0), MorphLM(p1)
    t_load = time.time() - t0
    files = sorted(args.dev_dir.glob("dev_*.txt"))
    scores = {}
    t0 = time.time()
    for f in files:
        a, b = token_scores(lm0, f), token_scores(lm1, f)
        assert a.shape == b.shape
        scores[f.stem[4:]] = (a, b)
    t_score = time.time() - t0

    table = {}
    for name, (a, b) in scores.items():
        table[name] = {"events": int(a.size), **{f"w={w:g}": round(mix_ppl(a, b, w), 2) for w in grid}}
    # 묶음(전체 dev 사건을 이어 붙임)과 집합별 퍼플렉시티의 기하평균(집합 크기 차이 무시)
    A = np.concatenate([a for a, _ in scores.values()])
    B = np.concatenate([b for _, b in scores.values()])
    pooled = {f"w={w:g}": round(mix_ppl(A, B, w), 2) for w in grid}
    geo = {f"w={w:g}": round(float(np.exp(np.mean([math.log(mix_ppl(a, b, w)) for a, b in scores.values()]))), 2)
           for w in grid}
    best_pooled = min(grid, key=lambda w: pooled[f"w={w:g}"])
    best_geo = min(grid, key=lambda w: geo[f"w={w:g}"])
    best_each = {name: min(grid, key=lambda w: row[f"w={w:g}"]) for name, row in table.items()}
    summary = {"lms": [p0, p1], "weight_is_for": p0, "load_sec": round(t_load, 1), "score_sec": round(t_score, 1),
               "ppl": table, "pooled": pooled, "geomean": geo,
               "best": {"pooled": best_pooled, "geomean": best_geo, "per_set": best_each}}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
