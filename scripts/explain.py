"""A-3 설명 출력과 z 평가: 디코딩 1-best의 바뀐 어절마다 오류유형 사후분포 P(z|x,ŷ)(설계서 §1.5)를 내고,
정답과 같은 교정(참 양성)에 대해 예측 z를 KAGAS 자동 유형과 대조한다(설계서 §6.3 "z 예측 정확도·혼동행렬").

    python scripts/explain.py --set korean_learner --split dev --channel datasets/channel/learner_lang8 \
        --morph-channel datasets/morph_channel/learner --lam-lm 1.0 --tag learner_Jl_Ml

- 참 양성: 시스템이 바꾼 x 어절 구간 [i, i+L)과 같은 구간의 정답 편집이 있고 교정 문자열이 같을 때.
- z 부류 대응: PARTICLE(·_DEL·_INS) ↔ KAGAS PARTICLE, ENDING(·_DEL·_INS) ↔ ENDING,
  JAMO ↔ SPELL·CONJUGATION·SHORTEN·WS; 어휘 대치 옵션을 켜면 LEXICAL_NOUN·LEXICAL_VERB ↔ NOUN·VERB.
- 출력: outputs/a3/explain_<tag>/{summary.json, sample.md(앞 ``--sample``문장, 사람 검토용 — 말뭉치 문장 포함, git 제외)}.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.decode import SPLIT_FILE, load_vocab, m2_blocks  # noqa: E402
from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig  # noqa: E402
from scripts.channel_options import add_channel_options, load_channel  # noqa: E402
from hnc.morph import MorphAnalyzer  # noqa: E402
from hnc.morph_channel import MorphChannel, Z_KO  # noqa: E402
from hnc.morph_lm import load_lm  # noqa: E402

_DEC = None
_CFG = None
FAM = {"PARTICLE": "PARTICLE", "PARTICLE_DEL": "PARTICLE", "PARTICLE_INS": "PARTICLE",
       "ENDING": "ENDING", "ENDING_DEL": "ENDING", "ENDING_INS": "ENDING", "JAMO": "SPELL",
       "LEXICAL_NOUN": "NOUN", "LEXICAL_VERB": "VERB"}
KAGAS_FAM = {"PARTICLE": "PARTICLE", "ENDING": "ENDING", "SPELL": "SPELL", "CONJUGATION": "SPELL",
             "SHORTEN": "SPELL", "WS": "SPELL"}


def _work(item):
    i, x = item
    prep = _DEC.prepare(x)
    nb = _DEC.search_nbest(x, prep, _CFG, 1)
    if not nb:
        return i, x, []
    return i, nb[0].text, _DEC.explain(x, nb[0])


def gold_edits(block):
    out = []
    for a in block[1:]:
        span, typ, corr = a[2:].split("|||")[:3]
        s, e = map(int, span.split())
        if typ != "noop":
            out.append((s, e, typ, " ".join(corr.split())))
    return out


def main() -> None:
    global _DEC, _CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="korean_learner")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--lm", default="datasets/lm/v0/lm5.bin,datasets/lm/v1/lm5.bin")
    ap.add_argument("--lm-weights", default="0.8,0.2")
    ap.add_argument("--channel", type=Path, default=Path("datasets/channel/learner_lang8"))
    add_channel_options(ap)
    ap.add_argument("--morph-channel", type=Path, default=Path("datasets/morph_channel/learner"))
    ap.add_argument("--vocab", type=Path, default=Path("datasets/vocab/eojeol_freq.tsv"))
    ap.add_argument("--vocab-min", type=int, default=5)
    ap.add_argument("--lam-lm", type=float, default=1.0)
    ap.add_argument("--morph-max-cands", type=int, default=30, help="어절당 형태소 편집 후보 수")
    ap.add_argument("--morph-two-edits", action="store_true", help="어절당 형태소 편집 2개")
    ap.add_argument("--morph-combine", type=int, default=0, help="형태소 편집을 얹을 자모 후보 수")
    ap.add_argument("--beam", type=int, default=8)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--tag", default="learner_Jl_Ml")
    args = ap.parse_args()
    out = Path("outputs/a3") / f"explain_{args.tag}"
    out.mkdir(parents=True, exist_ok=True)
    channel = load_channel(args.channel, args.mix_channel, args.mix_weight)
    _CFG = DecoderConfig(beam=args.beam, lam_lm=args.lam_lm, morph_max_cands=args.morph_max_cands,
                         morph_two_edits=args.morph_two_edits, morph_combine=args.morph_combine,
                         morph_weight=args.morph_weight, lexical_k=args.lexical_k,
                         lexical_min_count=args.lexical_min_count)
    mc = MorphChannel.from_json((args.morph_channel / "params.json").read_text(encoding="utf-8"))
    _DEC = Decoder(CandidateGenerator(channel.params, load_vocab(args.vocab, args.vocab_min), channel, _CFG),
                   load_lm(args.lm, args.lm_weights), MorphAnalyzer(), _CFG, morph_channel=mc)
    recs = [json.loads(line) for line in open(f"datasets/kor_gec/{args.set}_{args.split}.jsonl", encoding="utf-8")]
    gold = m2_blocks(Path("data/kor_gec") / args.set / f"{args.set}_{SPLIT_FILE[args.split]}.m2")
    if args.n:
        recs, gold = recs[:args.n], gold[:args.n]
    with mp.get_context("fork").Pool(args.workers) as pool:
        res = sorted(pool.imap_unordered(_work, list(enumerate(r["x"] for r in recs)), chunksize=4))

    z_all, conf = Counter(), defaultdict(Counter)
    n_changed = n_tp = 0
    conf_top = Counter()
    with open(out / "sample.md", "w", encoding="utf-8") as f:
        f.write(f"# 오류유형 설명 표본 ({args.set} {args.split})\n\n각 교정에 오류유형 사후확률 P(z | x, ŷ)를 붙였다. "
                "정답 편집과 같은 교정이면 KAGAS 자동 유형을 함께 적었다.\n\n")
        for (i, hyp, ex), r, g in zip(res, recs, gold):
            ge = {(s, e): (t, c) for s, e, t, c in gold_edits(g)}
            rows = []
            for e in ex:
                n_changed += 1
                z = next(iter(e["posterior"]))
                z_all[z] += 1
                gk = ge.get((e["i"], e["i"] + e["L"]))
                tp = gk is not None and gk[1] == e["y"]
                if tp:
                    n_tp += 1
                    conf[KAGAS_FAM.get(gk[0], gk[0])][FAM.get(z, z)] += 1
                    conf_top[(gk[0], z)] += 1
                post = ", ".join(f"{Z_KO.get(k, k)} {v:.2f}" for k, v in list(e["posterior"].items())[:3])
                rows.append(f"  - `{e['x']}` → `{e['y']}` | {post}" + (f" | 정답 ✓ KAGAS {gk[0]}" if tp else
                                                                     (f" | 정답은 `{gk[1]}` ({gk[0]})" if gk else " | 정답에 없음")))
            if i < args.sample and rows:
                f.write(f"## {i}\n\n- x: `{r['x']}`\n- 출력: `{hyp}`\n- 정답: `{r['y']}`\n\n" + "\n".join(rows) + "\n\n")

    def acc(row, key):
        tot = sum(row.values())
        return {"n": tot, "acc": round(row[key] / tot, 3) if tot else None, **dict(row.most_common(4))}
    summary = {"set": args.set, "split": args.split, "sents": len(recs), "lam_lm": args.lam_lm,
               "mix_weight": args.mix_weight, "morph_weight": args.morph_weight,
               "lexical_k": args.lexical_k, "lexical_min_count": args.lexical_min_count,
               "changed_spans": n_changed, "true_positive_spans": n_tp, "pred_z": dict(z_all.most_common()),
               "z_vs_kagas_on_tp": {k: acc(v, k) for k, v in sorted(conf.items(), key=lambda kv: -sum(kv[1].values()))},
               "top_pairs": [[a, b, n] for (a, b), n in conf_top.most_common(20)]}
    comparable = {k: v for k, v in conf.items() if k in ("PARTICLE", "ENDING", "SPELL")}
    tot = sum(sum(v.values()) for v in comparable.values())
    summary["z_accuracy_comparable"] = round(sum(v[k] for k, v in comparable.items()) / tot, 4) if tot else None
    if args.lexical_k:
        incl = {k: v for k, v in conf.items() if k in ("PARTICLE", "ENDING", "SPELL", "NOUN", "VERB")}
        total = sum(sum(v.values()) for v in incl.values())
        summary["z_with_lexical"] = {"n": total, "accuracy": round(sum(v[k] for k, v in incl.items()) / total, 4)
                                     if total else None}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
