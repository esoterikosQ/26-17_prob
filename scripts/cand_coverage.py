"""A-3e 후보 포괄률 진단: 정답 편집(한 어절 구간)의 교정 어절이 디코더 후보에 들어 있는가.

    python scripts/cand_coverage.py --set korean_learner --split dev --n 1500 \
        --channel datasets/channel/learner_lang8_J --morph-channel datasets/morph_channel/learner

- 정답 M²에서 x 어절 하나를 바꾸는 편집(e = s + 1)만 본다. 교정 문자열이 그 어절 후보(앞뒤 부호 포함)에
  있으면 포괄. 유형(KAGAS)별 포괄률과, 포괄된 것 중 형태소 경로·자모 경로 비율을 낸다.
- 디코더 설정 실험: ``--max-cands``(형태소 후보 수), ``--two-edits``(어절당 형태소 편집 2개),
  ``--combine``(자모 후보 위에 형태소 편집) — ``hnc.decoder.DecoderConfig``의 같은 이름 항목.
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
from scripts.explain import gold_edits  # noqa: E402
from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig  # noqa: E402
from scripts.channel_options import add_channel_options, load_channel  # noqa: E402
from hnc.morph import MorphAnalyzer  # noqa: E402
from hnc.morph_channel import MorphChannel  # noqa: E402
from hnc.morph_lm import load_lm  # noqa: E402

_DEC = None


def _work(item):
    i, x, edits = item
    prep = _DEC.prepare(x)
    out = []
    for s, e, typ, corr in edits:
        if e != s + 1:
            continue
        paths = {y: p for (k, y), p in prep.paths.items() if k == s}
        if _DEC.cfg.morph_weight == 0:
            paths = {y: {z: c for z, c in p.items() if z in ("JAMO", "IDENT")} for y, p in paths.items()}
            paths = {y: p for y, p in paths.items() if p}
        hit = paths.get(corr)
        out.append((typ, hit is not None, sorted(hit) if hit else [], len(paths)))
    return out


def main() -> None:
    global _DEC
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="korean_learner")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--channel", type=Path, default=Path("datasets/channel/learner_lang8_J"))
    add_channel_options(ap)
    ap.add_argument("--morph-channel", type=Path, default=Path("datasets/morph_channel/learner"))
    ap.add_argument("--vocab", type=Path, default=Path("datasets/vocab/eojeol_freq.tsv"))
    ap.add_argument("--max-cands", type=int, default=12)
    ap.add_argument("--two-edits", action="store_true")
    ap.add_argument("--combine", type=int, default=0, help="형태소 편집을 얹을 자모 후보 수(0 = 안 함)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    channel = load_channel(args.channel, args.mix_channel, args.mix_weight)
    cfg = DecoderConfig(morph_max_cands=args.max_cands, morph_two_edits=args.two_edits, morph_combine=args.combine,
                        morph_weight=args.morph_weight)
    mc = MorphChannel.from_json((args.morph_channel / "params.json").read_text(encoding="utf-8"))
    _DEC = Decoder(CandidateGenerator(channel.params, load_vocab(args.vocab, 5), channel, cfg),
                   load_lm("datasets/lm/v0/lm5.bin"), MorphAnalyzer(), cfg, morph_channel=mc)
    recs = [json.loads(line) for line in open(f"datasets/kor_gec/{args.set}_{args.split}.jsonl", encoding="utf-8")]
    gold = m2_blocks(Path("data/kor_gec") / args.set / f"{args.set}_{SPLIT_FILE[args.split]}.m2")
    items = [(i, r["x"], gold_edits(g)) for i, (r, g) in enumerate(zip(recs, gold))][:args.n]
    with mp.get_context("fork").Pool(args.workers) as pool:
        res = [o for out in pool.imap_unordered(_work, items, chunksize=4) for o in out]
    by = defaultdict(Counter)
    ncand = []
    for typ, hit, paths, nc in res:
        by[typ]["n"] += 1
        ncand.append(nc)
        if hit:
            by[typ]["hit"] += 1
            by[typ]["morph" if any(p not in ("JAMO", "IDENT") for p in paths) else "jamo_only"] += 1
    table = {t: {"n": c["n"], "coverage": round(c["hit"] / c["n"], 3), "via_morph": c["morph"],
                 "jamo_only": c["jamo_only"]} for t, c in sorted(by.items(), key=lambda kv: -kv[1]["n"])}
    tot = sum(c["n"] for c in by.values())
    summary = {"tag": args.tag, "max_cands": args.max_cands, "two_edits": args.two_edits, "combine": args.combine,
               "mix_weight": args.mix_weight, "morph_weight": args.morph_weight,
               "single_token_edits": tot, "coverage": round(sum(c["hit"] for c in by.values()) / tot, 3),
               "mean_cands_per_token": round(sum(ncand) / len(ncand), 1), "by_type": table}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
