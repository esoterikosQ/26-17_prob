"""Measure §6.2 rows 1 and 3 on Learner dev. No training, parameter selection or test data."""

from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing as mp
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import Counts, aggregate, surface
from hnc.decoder import CandidateGenerator, Decoder, DecoderConfig
from hnc.jamo import ALPHABET
from hnc.jamo_channel import ChannelParams, JamoChannel
from hnc.morph import MorphAnalyzer
from hnc.morph_channel import SUB_Z, MorphChannel
from hnc.morph_lm import load_lm
from scripts.c_eval import lift_ci
from scripts.c_metric import annotations, per_candidate
from scripts.c_strong_dev import ROOT, lines, rows, save
from scripts.channel_options import load_channel
from scripts.core_ablation_table import H, METRICS, agree, digest, read, subsets
from scripts.decode import load_vocab, m2_blocks, m2score, write_m2
from scripts.population_dev import LEARNER, LM, MORPH, data_paths

OUT = Path("outputs/core_controls_dev_v1")
TABLE = OUT / "input/table"
A = OUT / "input/a3g/korean_learner/r1"
DOMAIN = "korean_learner"
VOCAB = Path("datasets/vocab/eojeol_freq.tsv")
M_LABELS = set(SUB_Z) | {"PARTICLE_DEL", "ENDING_DEL", "PARTICLE_INS", "ENDING_INS"}
NAMES = ("prior_only", "jamo_only", "morph_only", "both_channels", "plus_pcfg")


def prior_rank(hyps):
    # As in the original H8 grid, ties retain the first cached candidate.
    return max(range(len(hyps)), key=lambda j: hyps[j]["lm"] + .3 * hyps[j]["pcfg"])


def make_decoder(vocab, lm, ana, morph, jamo_channel=None):
    cfg = DecoderConfig(beam=8, k_cand=8, lam_lm=1., lam_len=0., morph_max_cands=30)
    if jamo_channel is None:
        cfg = replace(cfg, max_edits=0, merge_spans=False)
        jamo_channel = JamoChannel(ChannelParams(identity={s: 1. for s in ALPHABET}))
    gen = CandidateGenerator(jamo_channel.params, vocab, jamo_channel, cfg)
    return Decoder(gen, lm, ana, cfg, morph_channel=morph)


def audit_morph_candidates(x, prep):
    words = x.split()
    if set(prep) != {(i, 1) for i in range(len(words))}:
        raise ValueError("morphology-only search prepared a split/merge or omitted a position")
    totals = Counter()
    for i, word in enumerate(words):
        candidates = prep[i, 1]
        if [cost for y, cost, _ in candidates if y == word] != [0.]:
            raise ValueError("row 3 requires one zero-cost copy per word")
        for y, cost, _ in candidates:
            labels = set(prep.paths.get((i, y), {}))
            if y.split() != [y] or not math.isfinite(cost):
                raise ValueError("invalid row-3 candidate")
            if y == word:
                if labels - {"IDENT"}:
                    raise ValueError("non-identity copy path")
            elif not labels or labels - M_LABELS:
                raise ValueError("changed row-3 surface without exclusively morphological support")
            totals["copy" if y == word else "morph_change"] += 1
    return totals


def decode_one(item):
    i, x = item
    full = _full.search_nbest(x, _full.prepare(x), n_best=8)
    prep = _morph.prepare(x)
    audit = audit_morph_candidates(x, prep)
    beam = _morph.search_nbest(x, prep, n_best=8)
    if not beam or any(not math.isclose(h.score, h.lm - h.ch, abs_tol=1e-8) for h in beam):
        raise ValueError(f"{i}: empty beam or row-3 scoring mismatch")
    return i, [(h.text, h.lm, h.ch) for h in full], audit, [
        {"text": h.text, "lm": h.lm, "ch": h.ch,
         "changes": [(j, sorted(paths)) for j, (word, y, paths) in enumerate(zip(x.split(), h.outs, h.paths, strict=True))
                     if y != word]} for h in beam]


def fill_table(table, metrics, hyp_files):
    filled = set()
    for r in table:
        if r["status"] != "missing":
            continue
        key = r["variant"], r["partition"]
        if (r["panel"] != "main" or r["domain"] != DOMAIN or r["variant"] not in hyp_files or key in filled
                or any(r[k] for k in METRICS) or r["row"] != {"prior_only": "1", "morph_only": "3"}[r["variant"]]):
            raise ValueError("unexpected or duplicate empty cell")
        name, part = key
        if int(r["n"]) != metrics[part]["n"]:
            raise ValueError("cannot insert a control from another partition")
        r.update(metrics[part]["scores"][name], status="measured", evidence=str(hyp_files[name]), selector="",
                 pool="H8" if name == "prior_only" else "M-beam8",
                 weights="G=1,P=0.3,channel=0,neural=0" if name == "prior_only" else "G=1,P=0,M=1,J=copy,neural=0")
        filled.add(key)
    if filled != {(s, p) for s in hyp_files for p in metrics}:
        raise ValueError("missing required main-table cells")


def run(workers):
    global _full, _morph
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    records_path, gold_path = data_paths(DOMAIN)
    recs, official = list(rows(records_path)), m2_blocks(gold_path)
    parts = subsets(DOMAIN, recs)
    if (len(recs), len(parts["check"])) != (4264, 2133):
        raise ValueError("wrong Learner partitions")
    if not (official == m2_blocks(H / "gold_all.m2") == m2_blocks(A / "gold_all.m2")):
        raise ValueError("gold differs across comparisons")
    strong_records = ROOT / DOMAIN / "records.jsonl"
    for i, (r, c, x, b) in enumerate(zip(recs, rows(strong_records), lines(A / "source.txt"), official, strict=True)):
        if (c["i"] != i or r["unit_id"] != c["unit_id"] or surface(r["y"]) != surface(c["y"])
                or not surface(r["x"]) == surface(c["x"]) == surface(x) == surface(b[0][2:])):
            raise ValueError(f"{i}: dev input/reference mismatch")
    j_off_m = A / "hyp_lm1.0_len0.0_m0.0.txt"
    h4, h5 = [H / f"hyp_lm1.0_p{p}_len0.0_pko130.0_rbl0.0.txt" for p in ("0.0", "0.3")]
    hcache = H / "nbest.jsonl"
    historical = read(TABLE / "source_hashes.json")
    for f in (hcache, h4, h5, gold_path, strong_records):
        if digest(f) != historical[str(f)]:
            raise ValueError(f"{f}: frozen input hash mismatch")
    if digest(h4) != digest(A / "hyp_lm1.0_len0.0_m1.0.txt"):
        raise ValueError("full decoder baseline differs from saved A row 4")
    with (TABLE / "ablation.csv").open(encoding="utf-8") as f:
        table = list(csv.DictReader(f))
    morph_path = Path(MORPH) / "params.json"
    lm, ana = load_lm(LM, "0.8,0.2"), MorphAnalyzer()
    vocab, mc = load_vocab(VOCAB, 5), MorphChannel.from_json(morph_path.read_text())
    _full = make_decoder(vocab, lm, ana, mc, load_channel(Path(LEARNER), None, 0.))
    _morph = make_decoder(vocab, lm, ana, mc)
    files = {n: OUT / f"hyp_{n}.txt" for n in ("prior_only", "morph_only")}
    evidence, audits = [], []
    with (OUT / "choices.jsonl").open("x", encoding="utf-8") as dest, mp.get_context("fork").Pool(workers) as pool:
        jobs = pool.imap(decode_one, enumerate(r["x"] for r in recs), chunksize=4)
        inputs = zip(recs, jobs, rows(hcache), lines(j_off_m), lines(h4), lines(h5), strict=True)
        for i, (r, (j, original, audit, morph), cache, jamo, both, pcfg) in enumerate(inputs):
            hs = cache["hyps"]
            if not (j == cache["i"] == i) or not 1 <= len(hs) <= 8 or len(original) != len(hs):
                raise ValueError(f"{i}: invalid H8 replay")
            for (y, g, ch), h in zip(original, hs, strict=True):
                if (y != h["text"] or abs(g - h["lm"]) > 1e-8 or abs(ch - h["ch"]) > 1e-8
                        or not all(math.isfinite(h[k]) for k in ("lm", "ch", "pcfg"))):
                    raise ValueError(f"{i}: rerun H8 surfaces/order/scores differ from the original")
            if (max(hs, key=lambda h: h["lm"] - h["ch"])["text"] != both
                    or max(hs, key=lambda h: h["lm"] - h["ch"] + .3*h["pcfg"])["text"] != pcfg):
                raise ValueError(f"{i}: row 4/5 controls do not reproduce")
            rank = prior_rank(hs)
            texts = [hs[rank]["text"], jamo, morph[0]["text"], both, pcfg]
            x, types, gold = annotations(official[i])
            unique = {y: per_candidate(y, x, gold) for y in set(texts)}
            e = {"ngold": len(gold), "types": types, "hyps": [unique[y] for y in texts]}
            evidence.append(e)
            audit.update(h8=len(hs), m8=len(morph))
            audits.append(audit)
            dest.write(json.dumps({"i": i, "unit_id": r["unit_id"], "prior_rank0": rank, "morph_nbest": morph,
                                   "edges": dict(audit), **e}, ensure_ascii=False, allow_nan=False) + "\n")
    for name, path in files.items():
        with path.open("x", encoding="utf-8") as f:
            for e in evidence:
                f.write(e["hyps"][NAMES.index(name)]["text"] + "\n")
    metrics = {}
    with TemporaryDirectory(dir=OUT) as tmp:
        hp, gp = Path(tmp) / "hyp.txt", Path(tmp) / "gold.m2"
        for part, ids in parts.items():
            write_m2([official[i] for i in ids], gp)
            scores, by_type, changed = {}, {}, {}
            for j, name in enumerate(NAMES):
                vals = [evidence[i]["hyps"][j] for i in ids]
                counts = aggregate([Counts(len(v["tp"]), v["proposed"], evidence[i]["ngold"])
                                    for i, v in zip(ids, vals, strict=True)])
                hp.write_text("".join(v["text"] + "\n" for v in vals), encoding="utf-8")
                scores[name] = m2score(hp, gp)
                agree(scores[name], counts.metrics(), f"{part}/{name}: official CLI")
                if name not in files:
                    cell, = [r for r in table if (r["panel"], r["partition"], r["variant"]) == ("main", part, name)]
                    agree(scores[name], {k: float(cell[k]) for k in METRICS}, f"{part}/{name}: frozen table")
                by_type[name] = dict(Counter(evidence[i]["types"][k] for i, v in zip(ids, vals, strict=True) for k in v["tp"]))
                changed[name] = sum(v["text"] != surface(recs[i]["x"]) for i, v in zip(ids, vals, strict=True))
            metrics[part] = {"n": len(ids), "scores": scores, "changed_sentences": changed, "type_hits": by_type,
                             "type_gold": dict(Counter(t for i in ids for t in evidence[i]["types"])),
                             "audit": dict(sum((audits[i] for i in ids), Counter()))}
    effects = {}
    ids = parts["check"]
    for before, after in (("prior_only", "plus_pcfg"), ("morph_only", "both_channels")):
        a, b = NAMES.index(before), NAMES.index(after)
        pair = [[j]*len(recs) for j in (a, b)]
        counts = [aggregate([Counts(len(evidence[i]["hyps"][j]["tp"]), evidence[i]["hyps"][j]["proposed"],
                                    evidence[i]["ngold"]) for i in ids]) for j in (a, b)]
        effects[f"{after}-{before}"] = {"delta": counts[1].f - counts[0].f,
                                       "paired_ci95": lift_ci(evidence, ids, recs, *pair),
                                       "different_outputs": sum(evidence[i]["hyps"][a]["text"] !=
                                                                evidence[i]["hyps"][b]["text"] for i in ids)}
    fill_table(table, metrics, files)
    with (OUT / "ablation.csv").open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    inputs = [hcache, h4, h5, records_path, strong_records, gold_path, VOCAB, morph_path, A / "summary.json", j_off_m,
              TABLE / "ablation.csv", TABLE / "source_hashes.json", Path(LEARNER) / "params.json",
              Path(LEARNER) / "other_symbols.json", *map(Path, LM.split(","))]
    save(OUT / "summary.json", {"domain": DOMAIN, "partitions": metrics, "check_contrasts": effects,
                                 "input_sha256": {str(p): digest(p) for p in inputs}})
    print(json.dumps({"F0.5": {k: {n: s["F0.5"] for n, s in v["scores"].items()} for k, v in metrics.items()},
                      "check_contrasts": effects}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=12)
    args = p.parse_args()
    if args.workers < 1:
        p.error("--workers must be positive")
    run(args.workers)
