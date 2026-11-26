"""고정된 dev 합집합의 소스 독립 특성. GPU(.venv-nlm)와 CPU(.venv) 단계를 분리한다."""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def causal(rows, out, batch):
    import torch
    from scripts.nlm_score import causal_scores
    texts = sorted({h["text"] for r in rows for h in r["hyps"]})
    vals = causal_scores(texts, "EleutherAI/polyglot-ko-1.3b", batch, "cuda", torch.bfloat16)
    with (out / "causal.jsonl").open("x", encoding="utf-8") as dest:
        for text, score in zip(texts, vals, strict=True):
            if not math.isfinite(score["lp"]):
                raise ValueError("인과 LM의 비유한 점수")
            dest.write(json.dumps({"text": text, "lp": score["lp"]}, ensure_ascii=False) + "\n")


def init_worker():
    from hnc.morph import MorphAnalyzer
    global _ana
    _ana = MorphAnalyzer(num_workers=1)


@lru_cache(maxsize=4096)
def analyze(text):
    toks = _ana.kiwi.tokenize(text)
    return ([(t.form, t.tag, t.start, t.start + t.len) for t in toks],
            _lm.score(_ana._to_morphs(text, toks)))


def work(row):
    from hnc.candidates_c import surface
    x = surface(row["x"])
    xs, _ = analyze(x)
    for h in row["hyps"]:
        y = h["text"]
        ys, lm = analyze(y)
        ch, spans = _channel.score(y, x, ys, xs)
        h.update(ch=ch, edits=spans, lm=lm)
        if not math.isfinite(lm):
            raise ValueError("형태소 LM의 비유한 점수")
    return row


def load_external_channel():
    from hnc.external_channel import ExternalChannel, JamoPaths
    from hnc.jamo_channel import ChannelParams
    from hnc.morph_channel import MorphChannel
    d = Path("datasets/channel/learner_lang8_J")
    params = ChannelParams.from_json((d / "params.json").read_text())
    observed = set()
    with (d / "edits.jsonl").open() as f:
        for line in f:
            r = json.loads(line)
            if r["c"] > 0:
                observed.add((tuple(r["a"]), tuple(r["b"])))
    morph = MorphChannel.from_json(Path("datasets/morph_channel/learner/params.json").read_text())
    return ExternalChannel(JamoPaths(params, observed), morph)


def cpu(rows, out, workers):
    from hnc.morph_lm import load_lm
    global _lm, _channel
    _channel = load_external_channel()
    _lm = load_lm("datasets/lm/v0/lm5.bin,datasets/lm/v1/lm5.bin", "0.8,0.2")
    with mp.get_context("fork").Pool(workers, initializer=init_worker) as pool, \
            (out / "features.jsonl").open("x", encoding="utf-8") as f:
        for row in pool.imap(work, rows, chunksize=4):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--stage", required=True, choices=["causal", "cpu"])
    ap.add_argument("--batch", type=int, default=48)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    with (args.out / "pool.jsonl").open() as f:
        rows = [json.loads(line) for line in f]
    if len(rows) != json.loads((args.out / "generation.json").read_text())["n"]:
        raise ValueError("미완료 후보 파일")
    if args.stage == "causal":
        causal(rows, args.out, args.batch)
    else:
        cpu(rows, args.out, args.workers)
