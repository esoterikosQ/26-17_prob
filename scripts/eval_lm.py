"""A-2 평가: (1) dev 퍼플렉시티·미등록 토큰 비율 (2) 판별력 — 형태소 LM vs PCFG vs 합.

    python scripts/eval_lm.py --tag v1 --order 5 --n 2000 --workers 12

판별력: dev 교정문(Kiwi 분석)에 형태소 오류 하나를 넣고(hnc.corrupt) 원문 점수 > 오류 문장 점수인
비율. 점수: LM(자연로그, <s>·</s> 포함), PCFG(``phdq_pcfg2``의 ``PCFGScorer``, 비터비 log P(t*, y)),
합(LM + PCFG). 원점수와 형태소당 점수(점수 / 형태소 수) 두 가지로 비교한다.
PCFG 채점기는 itcerdo의 ``~/projects/phdq_pcfg2``(코드·``data/models/pcfg_best.pkl``)에서 읽는다.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.corrupt import KINDS, corrupt  # noqa: E402
from hnc.morph import MorphAnalyzer  # noqa: E402
from hnc.morph_lm import LN10, MorphLM, load_lm  # noqa: E402

DEV = {
    "ec2": (Path("datasets/ec/dev.jsonl"), lambda r: r["keyboard"] == "2벌식(쿼티)"),
    "native": (Path("datasets/kor_gec/native_dev.jsonl"), lambda r: True),
    "korean_learner": (Path("datasets/kor_gec/korean_learner_dev.jsonl"), lambda r: True),
    "lang8": (Path("datasets/kor_gec/lang8_dev.jsonl"), lambda r: True),
}
_LM = None
_PCFG = None


def perplexity(lm, path: Path) -> dict:
    lp10 = 0.0
    n_tok = n_sent = n_oov = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            toks = line.split()
            if not toks:
                continue
            lp10 += lm.score_tokens(toks) / LN10
            n_tok += len(toks)
            n_sent += 1
            n_oov += lm.oov(toks)
    return {"sents": n_sent, "tokens": n_tok, "ppl": round(10 ** (-lp10 / (n_tok + n_sent)), 2),
            "oov_rate": round(n_oov / n_tok, 4)}


def _score(m):
    lm = _LM.score(m)
    pc = _PCFG.score(m).logprob if _PCFG is not None else float("nan")
    return lm, pc, len(m)


def _work(item):
    name, m, variants = item
    base = _score(m)
    return name, base, {k: _score(v) for k, v in variants.items()}


def main() -> None:
    global _LM, _PCFG
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v1")
    ap.add_argument("--order", type=int, default=5)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pcfg-root", type=Path, default=Path.home() / "projects/phdq_pcfg2")
    ap.add_argument("--no-pcfg", action="store_true")
    ap.add_argument("--lm", default=None, help="LM 경로(쉼표로 여럿이면 보간); 주면 --tag 디렉토리의 lm 대신")
    ap.add_argument("--lm-weights", default=None)
    ap.add_argument("--dev-dir", type=Path, default=None, help="dev_*.txt 위치(기본: --tag 디렉토리)")
    args = ap.parse_args()
    d = Path("datasets/lm") / args.tag

    t0 = time.time()
    _LM = load_lm(args.lm, args.lm_weights) if args.lm else MorphLM(d / f"lm{args.order}.bin")
    if not args.no_pcfg:
        sys.path.insert(0, str(args.pcfg_root))
        from pcfg.score import PCFGScorer
        _PCFG = PCFGScorer.load(args.pcfg_root / "data/models/pcfg_best.pkl")
    print(json.dumps({"load_sec": round(time.time() - t0, 1), "order": _LM.order}), flush=True)

    ppl = {p.stem[4:]: perplexity(_LM, p) for p in sorted((args.dev_dir or d).glob("dev_*.txt"))}
    print(json.dumps({"perplexity": ppl}, ensure_ascii=False), flush=True)

    rng = random.Random(args.seed)
    ana = MorphAnalyzer()
    items = []
    for name, (path, keep) in DEV.items():
        ys = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if keep(r) and r["y"].strip():
                    ys.append(r["y"])
        ys = rng.sample(ys, min(args.n, len(ys)))
        for y, ms in zip(ys, ana.analyze_many(ys)):
            m = [tuple(x) for x in ms]
            if not m:
                continue
            variants = {k: v for k in KINDS if (v := corrupt(m, k, rng)) is not None}
            items.append((name, m, variants))

    t0 = time.time()
    with mp.get_context("fork").Pool(args.workers) as pool:
        res = list(pool.imap_unordered(_work, items, chunksize=16))
    wall = time.time() - t0

    def better(a, b):
        return 1.0 if a > b else 0.5 if a == b else 0.0

    acc = defaultdict(lambda: defaultdict(list))
    for name, (blm, bpc, bn), variants in res:
        for kind, (lm, pc, n) in variants.items():
            key = (name, kind)
            acc[key]["lm_raw"].append(better(blm, lm))
            acc[key]["lm_norm"].append(better(blm / bn, lm / n))
            if _PCFG is not None:
                acc[key]["pcfg_raw"].append(better(bpc, pc))
                acc[key]["pcfg_norm"].append(better(bpc / bn, pc / n))
                acc[key]["sum_raw"].append(better(blm + bpc, lm + pc))
                acc[key]["sum_norm"].append(better((blm + bpc) / bn, (lm + pc) / n))
    table = {f"{name}/{kind}": {"n": len(v["lm_raw"]), **{m: round(sum(x) / len(x), 4) for m, x in v.items()}}
             for (name, kind), v in sorted(acc.items())}
    print(json.dumps({"wall_sec": round(wall, 1), "discrimination": table}, ensure_ascii=False))


if __name__ == "__main__":
    main()
