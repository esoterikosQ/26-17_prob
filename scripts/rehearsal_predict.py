"""Source-only development rehearsal. No references, M² annotations or historical decisions are inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface
from scripts.c_eval import pick
from scripts.core_ablation_controls import VOCAB, audit_morph_candidates, make_decoder, prior_rank
from scripts.channel_options import load_channel
from scripts.decode import load_vocab
from scripts.population_dev import LEARNER, LM, MORPH

WORK = Path("outputs/final_rehearsal_v1")
DEV_SRC = Path("data/c_gec2_dev_v1/archive/Standard_Korean_GEC/outputs2/kobart-base-v1")
CONFIG = {
    "korean_learner": (4264, 9, 54.5049, "032376e349eb240aa95fe0e472a84b7c5e10be7853f90f66d10b4e0fa330ba62"),
    "lang8": (16434, 3, 34.9182, "f499e966c2386d64a74c6c14edd151ca1adc94de62dada3504df8335d5f6c427"),
    "union": (23332, 8, 44.2453, "b6b699862a8b47a5a151d1a71051bac2853784d2c1e07545874d6ec3d528210d"),
}
HUB = Path(".cache/huggingface/hub")
CAUSAL = HUB / "models--EleutherAI--polyglot-ko-1.3b/snapshots/557e162cf6e944fdbae05bab2e45d066a125eacb"
MASKED = HUB / "models--klue--roberta-large/snapshots/28d911204e9022eda172571ca8cc61eaffd942f7"
C_NAMES = ("r9", "r10", "r11", "s_only")
PCFG_NAMES = ("r1", "r5", "r6", "r7")


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def rows(path):
    with path.open(encoding="utf-8") as f:
        yield from map(json.loads, f)


def save(path, obj):
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, allow_nan=False)


def line(f, obj):
    f.write(json.dumps(obj, ensure_ascii=False, allow_nan=False) + "\n")


def systems(domain, partial):
    if domain != "korean_learner":
        return set(C_NAMES)
    return set(C_NAMES) | {"r2", "r3", "r4", "r8a", "r8b", "deployed_B"} | (set() if partial else set(PCFG_NAMES))


def sources(domain):
    n, ep, _, fingerprint = CONFIG[domain]
    src = DEV_SRC / domain / "lr5e-5_seed0/val" / f"epoch{ep:02}" / "source.txt"
    raw = src.read_bytes()
    if hashlib.sha256(raw).hexdigest() != fingerprint:
        raise ValueError(f"{domain}: source-only input fingerprint mismatch")
    texts = raw.decode("utf-8").splitlines()
    if len(texts) != n:
        raise ValueError(f"{domain}: wrong dev source length")
    return [{"i": i, "unit_id": f"{domain}:dev:{i}", "x": x} for i, x in enumerate(texts)]


def require_source(row, domain, i):
    if (set(row) != {"i", "unit_id", "x"} or row["i"] != i or row["unit_id"] != f"{domain}:dev:{i}"
            or not isinstance(row["x"], str)):
        raise ValueError(f"{domain}/{i}: expected only occurrence ID and raw source")


def hnc_one(row):
    x = row["x"]
    prep = _full.prepare(x)
    hs = _full.search_nbest(x, prep, n_best=8)
    if not hs:
        raise ValueError("empty H beam")
    result = {**row, "hyps": [{"text": h.text, "lm": h.lm, "ch": h.ch} for h in hs]}
    if _morph is not None:
        mprep = _morph.prepare(x)
        audit_morph_candidates(x, mprep)
        result.update(r2=_full.search(x, prep, replace(_full.cfg, morph_weight=0.)),
                      r3=_morph.search(x, mprep))
    return result


def hnc(domain, workers):
    from hnc.morph import MorphAnalyzer
    from hnc.morph_channel import MorphChannel
    from hnc.morph_lm import load_lm
    global _full, _morph
    out = WORK / domain
    out.mkdir(exist_ok=False)
    src = sources(domain)
    with (out / "sources.jsonl").open("x", encoding="utf-8") as f:
        for i, r in enumerate(src):
            require_source(r, domain, i)
            line(f, r)
    args = (load_vocab(VOCAB, 5), load_lm(LM, "0.8,0.2"), MorphAnalyzer(),
            MorphChannel.from_json((Path(MORPH) / "params.json").read_text()))
    _full = make_decoder(*args, load_channel(Path(LEARNER), None, 0.))
    _morph = make_decoder(*args) if domain == "korean_learner" else None
    with (out / "hnc.jsonl").open("x", encoding="utf-8") as f, mp.get_context("fork").Pool(workers) as pool:
        for i, r in enumerate(pool.imap(hnc_one, src, chunksize=4)):
            if any(r[k] != src[i][k] for k in src[i]):
                raise ValueError("H beam source mismatch")
            line(f, r)
    save(out / "hnc.done.json", {"source_sha": sha(out / "sources.jsonl"), "hnc_sha": sha(out / "hnc.jsonl")})


def source_beams(domain):
    out = WORK / domain
    receipt = json.loads((out / "hnc.done.json").read_text())
    if (sha(out / "sources.jsonl"), sha(out / "hnc.jsonl")) != (receipt["source_sha"], receipt["hnc_sha"]):
        raise ValueError("incomplete or changed H beam")
    src, beam = list(rows(out / "sources.jsonl")), list(rows(out / "hnc.jsonl"))
    if len(src) != CONFIG[domain][0]:
        raise ValueError("incomplete sources")
    for i, (r, h) in enumerate(zip(src, beam, strict=True)):
        require_source(r, domain, i)
        if any(h[k] != r[k] for k in r):
            raise ValueError("misordered beam")
    return src, beam


def generate(domain):
    from scripts.c_strong_generate import generate_pool
    n, epoch, gleu, _ = CONFIG[domain]
    src, h = source_beams(domain)
    generate_pool(domain, WORK/domain, src, [[c["text"] for c in r["hyps"]] for r in h],
                  {"domain": domain, "n": n, "epoch": epoch, "val_gleu": gleu})


def neural_b(domain):
    import torch
    from scripts.nlm_score import causal_scores, masked_scores
    if domain != "korean_learner":
        raise ValueError("B contrasts only apply to Learner")
    _, h = source_beams(domain)
    texts = sorted({c["text"] for r in h for c in r["hyps"]})
    c = causal_scores(texts, str(CAUSAL), 64, "cuda", torch.bfloat16)
    m = masked_scores(texts, str(MASKED), 512, "cuda", torch.bfloat16)
    with (WORK/domain/"b_scores.jsonl").open("x", encoding="utf-8") as f:
        for text, a, b in zip(texts, c, m, strict=True):
            line(f, {"text": text, "causal": a["lp"], "masked": b["lp"]})


def choose(domain, partial):
    out = WORK / domain
    src, beam = source_beams(domain)
    causal = {r["text"]: r["lp"] for r in rows(out / "causal.jsonl")}
    b = {r["text"]: r for r in rows(out / "b_scores.jsonl")} if domain == "korean_learner" else {}
    pcfg = iter(rows(out / "pcfg.jsonl")) if domain == "korean_learner" and not partial else None
    if pcfg is not None:
        receipt = json.loads((out / "pcfg.verified.json").read_text())
        if sha(out / "pcfg.jsonl") != receipt["scores_sha"] or receipt["candidates"] != 34110:
            raise ValueError("unverified PCFG scores")
    mode = "partial" if partial else "full"
    with (out / f"predictions.{mode}.jsonl").open("x", encoding="utf-8") as f:
        for r, h, pool in zip(src, beam, rows(out / "features.jsonl"), strict=True):
            if any(pool[k] != r[k] for k in r):
                raise ValueError("external feature identity mismatch")
            hyps = pool["hyps"]
            for c in hyps:
                c["prior"] = .5*c["lm"] + causal[c["text"]]
                if not all(math.isfinite(c[k]) for k in ("s2s", "prior", "ch")):
                    raise ValueError("invalid C feature")
            orig, = [j for j, c in enumerate(hyps) if c["s"] == 0]
            js = {"r9": orig, "s_only": pick(hyps, "s", .5), "r10": pick(hyps, "union", 0.),
                  "r11": pick(hyps, "union", .5)}
            pred = {name: hyps[j]["text"] for name, j in js.items()}
            if domain == "korean_learner":
                hs = h["hyps"]
                def best(g, p, nc, nm):
                    def score(c):
                        v = g*c["lm"] - c["ch"] + p*c.get("pcfg", 0.) + (
                            nc*b[c["text"]]["causal"] + nm*b[c["text"]]["masked"])
                        if not math.isfinite(v):
                            raise ValueError("invalid H feature")
                        return v
                    return max(hs, key=score)["text"]
                pred.update(r2=h["r2"], r3=h["r3"], r4=best(1., 0., 0., 0.),
                            r8a=best(1., 0., 1., 0.), r8b=best(1., 0., 0., 1.), deployed_B=best(.5, 0., 1., 0.))
                if pcfg is not None:
                    pr = next(pcfg)
                    if pr["i"] != r["i"]:
                        raise ValueError("PCFG order mismatch")
                    for candidate, feature in zip(hs, pr["hyps"], strict=True):
                        if candidate["text"] != feature["text"] or not math.isfinite(feature["pcfg"]):
                            raise ValueError("PCFG candidate mismatch")
                        candidate["pcfg"] = feature["pcfg"]
                    pred.update(r1=hs[prior_rank(hs)]["text"], r5=best(1., .3, 0., 0.),
                                r6=best(1., .3, 1., 0.), r7=best(1., .3, 0., 1.))
            if pred.keys() != systems(domain, partial):
                raise ValueError("unexpected system set")
            line(f, {**r, "predictions": pred, "c_choices": js,
                     "audit": {k: [{"z": e["z"], "backoff_only": e["backoff_only"]} for e in hyps[js[k]]["edits"]]
                               for k in ("r9", "r11")}})
    if pcfg is not None and next(pcfg, None) is not None:
        raise ValueError("extra PCFG rows")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("hnc", "generate", "neural-b", "cpu", "causal", "choose"))
    ap.add_argument("domain", choices=CONFIG)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--partial-no-pcfg", action="store_true", help="dev rehearsal only, explicitly omit 1/5/6/7")
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("workers must be positive")
    if args.stage in ("cpu", "causal"):
        from scripts.c_features import causal, cpu
        directory = WORK / args.domain
        meta = json.loads((directory / "generation.json").read_text())
        items = list(rows(directory / "pool.jsonl"))
        if meta["baseline_origin"] != "fresh_beam4" or meta["n"] != len(items) or len(items) != CONFIG[args.domain][0]:
            raise ValueError("not a complete fresh pool")
        if args.stage == "cpu":
            cpu(items, directory, args.workers)
        else:
            causal(items, directory, 24, str(CAUSAL))
    elif args.stage == "hnc":
        hnc(args.domain, args.workers)
    elif args.stage == "generate":
        generate(args.domain)
    elif args.stage == "neural-b":
        neural_b(args.domain)
    else:
        choose(args.domain, args.partial_no_pcfg)
