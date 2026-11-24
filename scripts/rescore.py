"""B안식 재채점: 디코더 n-best + PCFG(phdq_pcfg2) → λ 격자, n-best 오라클(설계서 B-0·B-5).

    python scripts/rescore.py --set native --split dev --channel datasets/channel/ec2_native_em \
        --base-lam-lm 0.5 --nbest 8 --workers 12 --tag native_em_rescore

- 디코딩은 기준 λ_LM(``--base-lam-lm``)으로 하고 최종 빔의 n-best를 성분 점수(LM, 채널, 형태소 수)와
  함께 받는다. 각 가설을 Kiwi로 분석해 PCFG 비터비 로그확률을 붙인다.
- 재채점 점수 = λ_LM·LM − 채널 + λ_P·PCFG + λ_len·형태소 수. 격자의 각 설정으로 문장마다 최고 가설을
  골라 m2scorer로 채점한다(전체·WS+SPELL). λ_P = 0이 PCFG 없는 기준이다.
- 오라클: 문장마다 정답 교정문 y와 문자 유사도가 가장 높은 가설을 골랐을 때의 F0.5 (n-best의 상한 추정).
"""

from __future__ import annotations

import argparse
import difflib
import itertools
import json
import multiprocessing as mp
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.decode import A1_TYPES, SPLIT_FILE, load_vocab, m2_blocks, m2score, write_m2  # noqa: E402
from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig  # noqa: E402
from hnc.jamo_channel import ChannelParams, JamoChannel  # noqa: E402
from hnc.morph import MorphAnalyzer  # noqa: E402
from hnc.morph_lm import MorphLM  # noqa: E402

_DEC = None
_PCFG = None
_CFG = None
_NB = 8


def _work(item):
    i, x = item
    prep = _DEC.prepare(x)
    hyps = _DEC.search_nbest(x, prep, _CFG, _NB)
    out = []
    for h in hyps:
        m = [tuple(t) for t in _DEC.ana.analyze(h.text)]
        r = _PCFG.score(m)
        out.append({"text": h.text, "lm": h.lm, "ch": h.ch, "nm": h.n_morph, "pcfg": r.logprob,
                    "parsed": r.parsed})
    return i, out


def main() -> None:
    global _DEC, _PCFG, _CFG, _NB
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="native")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--records", type=Path, default=None)
    ap.add_argument("--gold-m2", type=Path, default=None)
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--lm", type=Path, default=Path("datasets/lm/v1/lm5.bin"))
    ap.add_argument("--channel", type=Path, default=Path("datasets/channel/ec2_native_em"))
    ap.add_argument("--vocab", type=Path, default=Path("datasets/vocab/eojeol_freq.tsv"))
    ap.add_argument("--vocab-min", type=int, default=5)
    ap.add_argument("--base-lam-lm", type=float, default=0.5)
    ap.add_argument("--nbest", type=int, default=8)
    ap.add_argument("--beam", type=int, default=8)
    ap.add_argument("--lam-lm", default="0.5,1,1.5")
    ap.add_argument("--lam-pcfg", default="0,0.02,0.05,0.1,0.2,0.3")
    ap.add_argument("--lam-len", default="0")
    ap.add_argument("--pcfg-root", type=Path, default=Path.home() / "projects/phdq_pcfg2")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--tag", default="native_em_rescore")
    ap.add_argument("--from-nbest", action="store_true", help="outputs/rescore/<tag>/nbest.jsonl을 다시 쓰고 디코딩·PCFG 채점은 건너뜀")
    args = ap.parse_args()
    out = Path("outputs/rescore") / args.tag
    out.mkdir(parents=True, exist_ok=True)

    if not args.from_nbest:
        params = ChannelParams.from_json((args.channel / "params.json").read_text(encoding="utf-8"))
        others = json.loads((args.channel / "other_symbols.json").read_text(encoding="utf-8"))
        base = DecoderConfig(beam=args.beam, lam_lm=args.base_lam_lm)
        _DEC = Decoder(CandidateGenerator(params, load_vocab(args.vocab, args.vocab_min),
                                          JamoChannel(params, others), base), MorphLM(args.lm), MorphAnalyzer(), base)
        _CFG, _NB = base, args.nbest
        sys.path.insert(0, str(args.pcfg_root))
        from pcfg.score import PCFGScorer
        _PCFG = PCFGScorer.load(args.pcfg_root / "data/models/pcfg_best.pkl")

    rec_path = args.records or Path("datasets/kor_gec") / f"{args.set}_{args.split}.jsonl"
    gold_path = args.gold_m2 or Path("data/kor_gec") / args.set / f"{args.set}_{SPLIT_FILE[args.split]}.m2"
    recs = [json.loads(line) for line in open(rec_path, encoding="utf-8")]
    gold = m2_blocks(gold_path)
    if args.n:
        recs, gold = recs[:args.n], gold[:args.n]
    write_m2(gold, out / "gold_all.m2")
    write_m2(gold, out / "gold_a1.m2", A1_TYPES)

    t0 = time.time()
    if args.from_nbest:
        with open(out / "nbest.jsonl", encoding="utf-8") as f:
            res = [(r["i"], r["hyps"]) for r in map(json.loads, f)]
    else:
        with mp.get_context("fork").Pool(args.workers) as pool:
            res = sorted(pool.imap_unordered(_work, list(enumerate(r["x"] for r in recs)), chunksize=4))
        with open(out / "nbest.jsonl", "w", encoding="utf-8") as f:
            for i, hyps in res:
                f.write(json.dumps({"i": i, "hyps": hyps}, ensure_ascii=False) + "\n")
    wall = time.time() - t0

    def evaluate(choose, name):
        hyp = out / f"hyp_{name}.txt"
        hyp.write_text("\n".join(choose(i, hyps) for i, hyps in res) + "\n", encoding="utf-8")
        return {"all": m2score(hyp, out / "gold_all.m2"), "a1": m2score(hyp, out / "gold_a1.m2")}

    rows = []
    for lm_w, p_w, len_w in itertools.product(*(map(float, v.split(",")) for v in
                                                (args.lam_lm, args.lam_pcfg, args.lam_len))):
        def choose(i, hyps, lm_w=lm_w, p_w=p_w, len_w=len_w):
            return max(hyps, key=lambda h: lm_w * h["lm"] - h["ch"] + p_w * h["pcfg"] + len_w * h["nm"])["text"]
        rows.append({"lam_lm": lm_w, "lam_pcfg": p_w, "lam_len": len_w,
                     **evaluate(choose, f"lm{lm_w}_p{p_w}_len{len_w}")})
    ys = {i: " ".join(r["y"].split()) for i, r in enumerate(recs)}

    def oracle(i, hyps):
        return max(hyps, key=lambda h: difflib.SequenceMatcher(None, h["text"], ys[i], autojunk=False).ratio())["text"]
    oracle_rows = evaluate(oracle, "oracle")
    n_parsed = sum(h["parsed"] for _, hyps in res for h in hyps)
    n_hyps = sum(len(hyps) for _, hyps in res)
    summary = {"sents": len(recs), "wall_sec": round(wall, 1), "nbest": args.nbest, "base_lam_lm": args.base_lam_lm,
               "mean_hyps": round(n_hyps / len(res), 2), "pcfg_parse_rate": round(n_parsed / n_hyps, 4),
               "oracle": oracle_rows, "grid": rows}
    name = "summary_regrid.json" if args.from_nbest else "summary.json"
    (out / name).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
