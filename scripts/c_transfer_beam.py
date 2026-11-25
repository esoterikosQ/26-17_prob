"""Frozen Learner HNC proposals for transfer; no parse scores or gold in search."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig
from hnc.morph import MorphAnalyzer
from hnc.morph_channel import MorphChannel
from hnc.morph_lm import load_lm
from scripts.channel_options import load_channel
from scripts.decode import load_vocab
from scripts.population_dev import LEARNER, LM, MORPH


def search(item):
    i, uid, x = item
    hyps = _decoder.search_nbest(x, _decoder.prepare(x), _config, 8)
    if not hyps:
        raise ValueError(f"{uid}: empty HNC beam")
    return {"i": i, "unit_id": uid, "x": x, "hyps": [{"text": h.text} for h in hyps]}


def main(domain: str, dest: Path, workers: int):
    global _decoder, _config
    channel = load_channel(Path(LEARNER), None, 0)
    _config = DecoderConfig(beam=8, lam_lm=1., morph_max_cands=30)
    _decoder = Decoder(CandidateGenerator(channel.params, load_vocab(Path("datasets/vocab/eojeol_freq.tsv"), 5),
                                          channel, _config), load_lm(LM, "0.8,0.2"), MorphAnalyzer(), _config,
                       morph_channel=MorphChannel.from_json((Path(MORPH) / "params.json").read_text()))
    source = Path("datasets/kor_gec") / f"{domain}_dev.jsonl"
    tmp = dest.with_suffix(".partial")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(dest)
    with source.open() as src, tmp.open("x") as out, mp.get_context("fork").Pool(workers) as pool:
        items = ((i, r["unit_id"], r["x"]) for i, r in enumerate(map(json.loads, src)))
        for n, row in enumerate(pool.imap(search, items, chunksize=4), start=1):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            if n % 500 == 0:
                print(f"{domain}: {n}", flush=True)
    tmp.rename(dest)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("domain", choices=("lang8", "native", "korean_learner"))
    p.add_argument("dest", type=Path)
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    main(a.domain, a.dest, a.workers)
