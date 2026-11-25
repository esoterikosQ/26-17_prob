"""A-1d/A-1e 디코딩 + M² 평가 (λ 격자).

    python scripts/decode.py --set native --split dev --workers 12 --tag native_v1

- 문장마다 후보 준비(``Decoder.prepare``)는 한 번만 하고, 격자의 각 λ 설정으로 빔 탐색만 다시 한다.
- 평가: m2scorer(tools/m2scorer)로 (a) 정답 M² 전체, (b) A-1 범위 유형(KAGAS WS·SPELL)만 남긴 정답에
  대해 P/R/F0.5. (b)에서 범위 밖 편집을 고친 출력은 오탐으로 잡힌다(보수적).
- 출력: outputs/a1d/<tag>/ (가설 파일·부분 정답 M²·요약; git 제외).
"""

from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing as mp
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig  # noqa: E402
from scripts.channel_options import add_channel_options, load_channel  # noqa: E402
from hnc.morph import MorphAnalyzer  # noqa: E402
from hnc.morph_channel import MorphChannel  # noqa: E402
from hnc.morph_lm import load_lm  # noqa: E402

SPLIT_FILE = {"dev": "val", "test": "test"}
A1_TYPES = {"WS", "SPELL"}
_DEC = None
_CFGS = None


def load_vocab(path: Path, min_count: int) -> set:
    vocab = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            w, _, n = line.rstrip("\n").rpartition("\t")
            if int(n) < min_count:
                break
            vocab.add(w)
    return vocab


def m2_blocks(path: Path):
    blocks, cur = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("S "):
            if cur:
                blocks.append(cur)
            cur = [line]
        elif line.startswith("A "):
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def write_m2(blocks, path: Path, keep_types=None):
    with open(path, "w", encoding="utf-8") as f:
        for b in blocks:
            f.write(b[0] + "\n")
            edits = [a for a in b[1:] if keep_types is None or a.split("|||")[1] in keep_types]
            if not edits:
                edits = ["A -1 -1|||noop|||-NONE-|||REQUIRED|||-NONE-|||0"]
            for a in edits:
                f.write(a + "\n")
            f.write("\n")


def m2score(hyp: Path, gold: Path) -> dict:
    out = subprocess.run([sys.executable, "tools/m2scorer/scripts/m2scorer.py", str(hyp), str(gold)],
                         capture_output=True, text=True, check=True).stdout
    vals = dict(re.findall(r"(Precision|Recall|F_0\.5)\s*:\s*([0-9.]+)", out))
    return {"P": float(vals["Precision"]), "R": float(vals["Recall"]), "F0.5": float(vals["F_0.5"])}


def _work(item):
    i, x = item
    t = time.time()
    prep = _DEC.prepare(x)
    outs = [_DEC.search(x, prep, c) for c in _CFGS]
    return i, outs, time.time() - t


def main() -> None:
    global _DEC, _CFGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="native")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=0, help="앞에서부터 n문장(0=전체)")
    ap.add_argument("--records", type=Path, default=None, help="입력 레코드(jsonl, x 필드); 주면 --set/--split 대신")
    ap.add_argument("--gold-m2", type=Path, default=None, help="--records와 함께 쓸 정답 M²")
    ap.add_argument("--type-eval", default="", help="유형별 재현율을 볼 KAGAS 유형(쉼표, 예: PARTICLE,ENDING)")
    ap.add_argument("--lm", default="datasets/lm/v1/lm5.bin", help="LM 경로; 쉼표로 여럿이면 선형 보간")
    ap.add_argument("--lm-weights", default=None, help="보간 가중치(쉼표, --lm 순서)")
    ap.add_argument("--channel", type=Path, default=Path("datasets/channel/ec2_native"))
    add_channel_options(ap, grid=True)
    ap.add_argument("--vocab", type=Path, default=Path("datasets/vocab/eojeol_freq.tsv"))
    ap.add_argument("--vocab-min", type=int, default=5)
    ap.add_argument("--morph-channel", type=Path, default=None, help="A-3 형태소 채널 디렉토리(params.json)")
    ap.add_argument("--morph-max-cands", type=int, default=30, help="어절당 형태소 편집 후보 수")
    ap.add_argument("--morph-two-edits", action="store_true", help="어절당 형태소 편집 2개")
    ap.add_argument("--morph-combine", type=int, default=0, help="형태소 편집을 얹을 자모 후보 수")
    ap.add_argument("--lam-lm", default="0.5,1,2")
    ap.add_argument("--lam-len", default="0,2")
    ap.add_argument("--beam", type=int, default=8)
    ap.add_argument("--k-cand", type=int, default=8)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--tag", default="native_v1")
    args = ap.parse_args()
    if args.morph_channel is None and args.morph_weight != [1.0] and args.morph_weight != [0.0]:
        ap.error("--morph-weight 격자에는 --morph-channel이 필요하다")
    out = Path("outputs/a1d") / args.tag
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    channel = load_channel(args.channel, args.mix_channel, args.mix_weight)
    vocab = load_vocab(args.vocab, args.vocab_min)
    base = DecoderConfig(beam=args.beam, k_cand=args.k_cand, morph_max_cands=args.morph_max_cands,
                         morph_two_edits=args.morph_two_edits, morph_combine=args.morph_combine,
                         lexical_k=args.lexical_k, lexical_min_count=args.lexical_min_count)
    mc = MorphChannel.from_json((args.morph_channel / "params.json").read_text(encoding="utf-8")) if args.morph_channel else None
    _DEC = Decoder(CandidateGenerator(channel.params, vocab, channel, base), load_lm(args.lm, args.lm_weights), MorphAnalyzer(), base,
                   morph_channel=mc)
    _CFGS = [replace(base, lam_lm=float(a), lam_len=float(b), morph_weight=w)
             for a, b, w in itertools.product(args.lam_lm.split(","), args.lam_len.split(","), args.morph_weight)]
    print(json.dumps({"load_sec": round(time.time() - t0, 1), "vocab": len(vocab),
                      "reverse_edit_keys": len(_DEC.gen.rev), "configs": len(_CFGS)}), flush=True)

    rec_path = args.records or Path("datasets/kor_gec") / f"{args.set}_{args.split}.jsonl"
    gold_path = args.gold_m2 or Path("data/kor_gec") / args.set / f"{args.set}_{SPLIT_FILE[args.split]}.m2"
    with open(rec_path, encoding="utf-8") as f:
        xs = [json.loads(line)["x"] for line in f]
    gold_all = m2_blocks(gold_path)
    assert len(gold_all) == len(xs), (len(gold_all), len(xs))
    if args.n:
        xs, gold_all = xs[:args.n], gold_all[:args.n]
    write_m2(gold_all, out / "gold_all.m2")
    write_m2(gold_all, out / "gold_a1.m2", A1_TYPES)
    types = [t for t in args.type_eval.split(",") if t]
    for t in types:
        write_m2(gold_all, out / f"gold_{t}.m2", {t})

    t0 = time.time()
    with mp.get_context("fork").Pool(args.workers) as pool:
        res = sorted(pool.imap_unordered(_work, list(enumerate(xs)), chunksize=4))
    wall = time.time() - t0
    secs = [r[2] for r in res]

    (out / "source.txt").write_text("\n".join(xs) + "\n", encoding="utf-8")
    rows = []
    for k, cfg in enumerate(_CFGS):
        suffix = "" if args.morph_weight == [1.0] else f"_m{cfg.morph_weight}"
        hyp = out / f"hyp_lm{cfg.lam_lm}_len{cfg.lam_len}{suffix}.txt"
        hyp.write_text("\n".join(r[1][k] for r in res) + "\n", encoding="utf-8")
        changed = sum(r[1][k] != " ".join(x.split()) for r, x in zip(res, xs))
        rows.append({"lam_lm": cfg.lam_lm, "lam_len": cfg.lam_len, "morph_weight": cfg.morph_weight,
                     "hyp": hyp.name, "changed": changed,
                     "all": m2score(hyp, out / "gold_all.m2"), "a1": m2score(hyp, out / "gold_a1.m2"),
                     # 유형별: 그 유형만 남긴 정답에 대한 재현율(정밀도는 다른 유형 교정이 오탐으로 잡혀 의미 없음)
                     **{f"R_{t}": m2score(hyp, out / f"gold_{t}.m2")["R"] for t in types}})
    summary = {"set": args.set, "split": args.split, "sents": len(xs), "decode_wall_sec": round(wall, 1),
               "channel": str(args.channel), "mix_channel": str(args.mix_channel) if args.mix_channel else None,
               "mix_weight": args.mix_weight, "morph_channel": str(args.morph_channel) if args.morph_channel else None,
               "lm": args.lm, "lm_weights": args.lm_weights, "morph_max_cands": args.morph_max_cands,
               "lexical_k": args.lexical_k, "lexical_min_count": args.lexical_min_count,
               "sec_per_sent_mean": round(sum(secs) / len(secs), 3), "sec_per_sent_max": round(max(secs), 2),
               "results": rows}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
