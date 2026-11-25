"""B안식 재채점: 디코더 n-best + PCFG(phdq_pcfg2) → λ 격자, n-best 오라클(설계서 B-0·B-5).

    python scripts/rescore.py --set native --split dev --channel datasets/channel/ec2_native_em \
        --base-lam-lm 0.5 --nbest 8 --workers 12 --tag native_em_rescore

- 디코딩은 기준 λ_LM(``--base-lam-lm``)으로 하고 최종 빔의 n-best를 성분 점수(LM, 채널, 형태소 수)와
  함께 받는다. 각 가설을 Kiwi로 분석해 PCFG 비터비 로그확률을 붙인다.
- 재채점 점수 = λ_LM·LM − 채널 + λ_P·PCFG + λ_len·형태소 수. 격자의 각 설정으로 문장마다 최고 가설을
  골라 m2scorer로 채점한다(전체·WS+SPELL). λ_P = 0이 PCFG 없는 기준이다.
- 오라클: 문장마다 정답 교정문 y와 문자 유사도가 가장 높은 가설을 골랐을 때의 F0.5 (n-best의 상한 추정).
- 신경 LM(B안, ``--nlm``): ``scripts/nlm_score.py``가 만든 ``nlm_<이름>.jsonl``의 문장 로그확률(인과 LM)·PLL(마스크
  LM)을 λ_N·점수로 더한다(``--from-nbest``와 함께). λ_N = 0이 신경 LM 없는 기준이다.
"""

from __future__ import annotations

import argparse
import difflib
import itertools
from concurrent.futures import ThreadPoolExecutor
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
from hnc.morph_channel import MorphChannel  # noqa: E402
from hnc.morph_lm import load_lm  # noqa: E402

_DEC = None
_PCFG = None
_ANA = None
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


def _repcfg(item):
    i, hyps = item
    for h in hyps:
        r = _PCFG.score([tuple(t) for t in _ANA.analyze(h["text"])])
        h["pcfg"], h["parsed"] = r.logprob, r.parsed
    return i, hyps


def main() -> None:
    global _DEC, _PCFG, _CFG, _NB, _ANA
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="native")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--records", type=Path, default=None)
    ap.add_argument("--gold-m2", type=Path, default=None)
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--lm", default="datasets/lm/v1/lm5.bin", help="LM 경로; 쉼표로 여럿이면 선형 보간")
    ap.add_argument("--lm-weights", default=None, help="보간 가중치(쉼표, --lm 순서)")
    ap.add_argument("--channel", type=Path, default=Path("datasets/channel/ec2_native_em"))
    ap.add_argument("--vocab", type=Path, default=Path("datasets/vocab/eojeol_freq.tsv"))
    ap.add_argument("--vocab-min", type=int, default=5)
    ap.add_argument("--morph-channel", type=Path, default=None, help="A-3 형태소 채널 디렉토리(params.json)")
    ap.add_argument("--morph-max-cands", type=int, default=30, help="어절당 형태소 편집 후보 수")
    ap.add_argument("--morph-two-edits", action="store_true", help="어절당 형태소 편집 2개")
    ap.add_argument("--morph-combine", type=int, default=0, help="형태소 편집을 얹을 자모 후보 수")
    ap.add_argument("--base-lam-lm", type=float, default=0.5)
    ap.add_argument("--nbest", type=int, default=8)
    ap.add_argument("--beam", type=int, default=8)
    ap.add_argument("--lam-lm", default="0.5,1,1.5")
    ap.add_argument("--lam-pcfg", default="0,0.02,0.05,0.1,0.2,0.3")
    ap.add_argument("--lam-len", default="0")
    ap.add_argument("--pcfg-root", type=Path, default=Path.home() / "projects/phdq_pcfg2")
    ap.add_argument("--pcfg-model", type=Path, default=None, help="PCFG 채점기(기본: <pcfg-root>/data/models/pcfg_best.pkl)")
    ap.add_argument("--repcfg", action="store_true", help="--from-nbest에서 PCFG 점수를 --pcfg-model로 다시 계산")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--tag", default="native_em_rescore")
    ap.add_argument("--from-nbest", action="store_true", help="outputs/rescore/<tag>/nbest.jsonl을 다시 쓰고 디코딩·PCFG 채점은 건너뜀")
    ap.add_argument("--nlm", default="", help="신경 LM 점수 이름(쉼표): outputs/rescore/<tag>/nlm_<이름>.jsonl")
    ap.add_argument("--lam-nlm", default="0,0.25,0.5,1", help="신경 LM 가중치 격자(이름마다 같은 목록)")
    ap.add_argument("--out-name", default=None, help="요약 파일 이름(기본: summary.json / summary_regrid.json)")
    ap.add_argument("--hyp-prefix", default="", help="가설 파일 이름 접두어(다른 설정의 가설 파일을 덮어쓰지 않게)")
    args = ap.parse_args()
    out = Path("outputs/rescore") / args.tag
    out.mkdir(parents=True, exist_ok=True)

    if not args.from_nbest:
        params = ChannelParams.from_json((args.channel / "params.json").read_text(encoding="utf-8"))
        others = json.loads((args.channel / "other_symbols.json").read_text(encoding="utf-8"))
        base = DecoderConfig(beam=args.beam, lam_lm=args.base_lam_lm, morph_max_cands=args.morph_max_cands, morph_two_edits=args.morph_two_edits, morph_combine=args.morph_combine)
        _DEC = Decoder(CandidateGenerator(params, load_vocab(args.vocab, args.vocab_min),
                                          JamoChannel(params, others), base), load_lm(args.lm, args.lm_weights), MorphAnalyzer(), base,
                       morph_channel=MorphChannel.from_json((args.morph_channel / "params.json").read_text(encoding="utf-8"))
                       if args.morph_channel else None)
        _CFG, _NB = base, args.nbest
    pcfg_model = args.pcfg_model or args.pcfg_root / "data/models/pcfg_best.pkl"
    if not args.from_nbest or args.repcfg:
        sys.path.insert(0, str(args.pcfg_root))
        from pcfg.score import PCFGScorer
        _PCFG = PCFGScorer.load(pcfg_model)

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
        if args.repcfg:
            _ANA = MorphAnalyzer()
            with mp.get_context("fork").Pool(args.workers) as pool:
                res = sorted(pool.imap_unordered(_repcfg, res, chunksize=4))
    else:
        with mp.get_context("fork").Pool(args.workers) as pool:
            res = sorted(pool.imap_unordered(_work, list(enumerate(r["x"] for r in recs)), chunksize=4))
        with open(out / "nbest.jsonl", "w", encoding="utf-8") as f:
            for i, hyps in res:
                f.write(json.dumps({"i": i, "hyps": hyps}, ensure_ascii=False) + "\n")
    wall = time.time() - t0
    nlms = [x for x in args.nlm.split(",") if x]
    for name in nlms:
        with open(out / f"nlm_{name}.jsonl", encoding="utf-8") as f:
            sc = {r["i"]: r["scores"] for r in map(json.loads, f)}
        for i, hyps in res:
            assert len(sc[i]) == len(hyps), (name, i)
            for h, v in zip(hyps, sc[i]):
                h[f"nlm_{name}"] = v["lp"]

    def evaluate(choose, name):
        hyp = out / f"hyp_{args.hyp_prefix}{name}.txt"
        hyp.write_text("\n".join(choose(i, hyps) for i, hyps in res) + "\n", encoding="utf-8")
        return {"all": m2score(hyp, out / "gold_all.m2"), "a1": m2score(hyp, out / "gold_a1.m2")}

    grids = [list(map(float, v.split(","))) for v in (args.lam_lm, args.lam_pcfg, args.lam_len)]
    grids += [list(map(float, args.lam_nlm.split(",")))] * len(nlms)

    def run(combo):
        lm_w, p_w, len_w, *n_w = combo

        def choose(i, hyps):
            return max(hyps, key=lambda h: lm_w * h["lm"] - h["ch"] + p_w * h["pcfg"] + len_w * h["nm"]
                       + sum(w * h[f"nlm_{n}"] for n, w in zip(nlms, n_w)))["text"]
        tag = f"lm{lm_w}_p{p_w}_len{len_w}" + "".join(f"_{n}{w}" for n, w in zip(nlms, n_w))
        return {"lam_lm": lm_w, "lam_pcfg": p_w, "lam_len": len_w,
                **{f"lam_{n}": w for n, w in zip(nlms, n_w)}, **evaluate(choose, tag)}
    with ThreadPoolExecutor(args.workers) as ex:
        rows = list(ex.map(run, itertools.product(*grids)))
    ys = {i: " ".join(r["y"].split()) for i, r in enumerate(recs)}

    def oracle(i, hyps):
        return max(hyps, key=lambda h: difflib.SequenceMatcher(None, h["text"], ys[i], autojunk=False).ratio())["text"]
    oracle_rows = evaluate(oracle, "oracle")
    n_parsed = sum(h["parsed"] for _, hyps in res for h in hyps)
    n_hyps = sum(len(hyps) for _, hyps in res)
    summary = {"sents": len(recs), "wall_sec": round(wall, 1), "nbest": args.nbest, "base_lam_lm": args.base_lam_lm,
               "mean_hyps": round(n_hyps / len(res), 2), "pcfg_parse_rate": round(n_parsed / n_hyps, 4),
               "oracle": oracle_rows, "grid": rows}
    name = args.out_name or ("summary_regrid.json" if args.from_nbest else "summary.json")
    (out / name).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
