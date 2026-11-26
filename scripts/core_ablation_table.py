"""Collect §6.2 from existing dev predictions. No fitting, decoding, tuning, or test inputs."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import Counts, surface
from scripts.c_metric import annotations
from scripts.c_strong_dev import DOMAINS, ROOT, SIZES, gold_path, lines, rows
from scripts.decode import m2_blocks, m2score, write_m2
from scripts.population_dev import is_selection

OUT = Path("outputs/core_ablation_dev_v1")
GROUP = OUT / "input/a3g/korean_learner"
H = Path("outputs/rescore/learner_JJ_Ml30")
METRICS = ("P", "R", "F0.5")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def agree(observed, expected, label):
    if any(not math.isclose(observed[k], expected[k], rel_tol=0, abs_tol=.000051) for k in METRICS):
        raise ValueError(f"{label}: stored metrics differ: {observed} vs {expected}")


def subsets(domain, recs):
    return {"all_dev": list(range(len(recs))),
            "check": [i for i, r in enumerate(recs) if not is_selection(domain, r)]}


def grid_cell(grid, lm, pcfg, causal, masked):
    keys = ("lam_lm", "lam_pcfg", "lam_pko13", "lam_rbl", "lam_len")
    matches = [r for r in grid if tuple(r[k] for k in keys) == (lm, pcfg, causal, masked, 0)]
    if len(matches) != 1:
        raise ValueError(f"missing/ambiguous historical cell: {lm}, {pcfg}, {causal}, {masked}")
    return matches[0]["all"]


def collect():
    table, hashes = [], {}

    def add(panel, row, variant, domain, part, n, pool, weights, values, path=None, key=""):
        if path is not None and str(path) not in hashes:
            hashes[str(path)] = digest(path)
        table.append({"panel": panel, "row": row, "variant": variant, "domain": domain, "partition": part,
                      "n": n, "pool": pool, "weights": weights, "status": "missing" if values is None else "measured",
                      **{k: values[k] if values is not None else None for k in METRICS},
                      "evidence": str(path) if path else "", "selector": key})

    learner = "korean_learner"
    official = m2_blocks(gold_path(learner))
    recs = list(rows(ROOT / learner / "records.jsonl"))
    a = GROUP / "r1"
    parts = subsets(learner, recs)
    if (len(recs), len(parts["check"])) != (4264, 2133) or any(r["i"] != i for i, r in enumerate(recs)):
        raise ValueError("wrong Learner partition")
    if not (official == m2_blocks(a / "gold_all.m2") == m2_blocks(H / "gold_all.m2")):
        raise ValueError("A/B references differ from official M²")
    if any(surface(r["x"]) != surface(x) or surface(x) != surface(b[0][2:])
           for r, x, b in zip(recs, lines(a / "source.txt"), official, strict=True)):
        raise ValueError("Learner sentence alignment mismatch")
    grid = read(H / "summary_nlm.json")["grid"]
    conf = read(a / "summary.json")
    if (conf["mix_weight"], conf["morph_max_cands"], conf["mix_channel"], conf["lm_weights"]) != (
            1., 30, "datasets/channel/learner_lang8_J", "0.8,0.2"):
        raise ValueError("wrong A-3g channel, budget or LM")
    selected = {}
    for row, variant, lm, p, c, m in (
            (4, "both_channels", 1., 0., 0., 0.), (5, "plus_pcfg", 1., .3, 0., 0.),
            (6, "causal", 1., .3, 1., 0.), (7, "masked", 1., .3, 0., 1.),
            (8, "causal_no_pcfg", 1., 0., 1., 0.), (8, "masked_no_pcfg", 1., 0., 0., 1.),
            ("B", "deployed_B", .5, 0., 1., 0.)):
        path = H / f"hyp_lm{lm}_p{p}_len0.0_pko13{c}_rbl{m}.txt"
        selected[variant] = (row, (lm, p, c, m), path, lines(path), grid_cell(grid, lm, p, c, m))
    on = a / "hyp_lm1.0_len0.0_m1.0.txt"
    if digest(on) != digest(selected["both_channels"][2]):
        raise ValueError("row 4 is not the same prediction in the A/B runs")
    cache = (H / "nbest.jsonl", H / "nlm_pko13.jsonl", H / "nlm_rbl.jsonl")
    for i, (r, pko, rbl, _) in enumerate(zip(*(rows(f) for f in cache), recs, strict=True)):
        if not (r["i"] == pko["i"] == rbl["i"] == i) or not 1 <= len(r["hyps"]) <= 8:
            raise ValueError(f"{i}: misaligned H8 cache")
        cand = list(zip(r["hyps"], pko["scores"], rbl["scores"], strict=True))
        for variant, (_, (lm, p, c, m), _, hyp, _) in selected.items():
            best, _, _ = max(cand, key=lambda t: lm*t[0]["lm"] - t[0]["ch"] + p*t[0]["pcfg"]
                             + (c*t[1]["lp"] + m*t[2]["lp"]))
            if best["text"] != hyp[i]:
                raise ValueError(f"{variant}/{i}: stored winner differs from the original cache")
    off = next(r for r in conf["results"] if r["morph_weight"] == 0 and r["lam_lm"] == 1 and r["lam_len"] == 0)
    path = a / off["hyp"]
    selected["jamo_only"] = (2, (1., 0., 0., 0.), path, lines(path), off["all"])
    for f in (*cache, H / "summary_nlm.json", H / "summary.json", on, a / "summary.json",
              a / "source.txt", gold_path(learner)):
        hashes[str(f)] = digest(f)
    legacy = OUT / "input/legacy/evaluation.json"
    older = read(legacy)
    with TemporaryDirectory(dir=OUT) as tmp:
        hp, gp = Path(tmp) / "hyp.txt", Path(tmp) / "gold.m2"
        for part, ids in parts.items():
            write_m2([official[i] for i in ids], gp)
            for variant, (row, w, path, hyp, previous) in selected.items():
                if len(hyp) != len(recs):
                    raise ValueError(f"{variant}: incomplete hypothesis")
                hp.write_text("".join(hyp[i] + "\n" for i in ids), encoding="utf-8")
                score = m2score(hp, gp)
                if part == "all_dev":
                    agree(score, previous, variant)
                if row == 4:
                    agree(score, read(GROUP / "comparison.json")["comparison"]["r1_m1"][part], "A-on")
                if row == "B":
                    agree(score, older["metrics"][part]["frozen_B"], "frozen B")
                add("main" if row != "B" else "control", row, variant, learner, part, len(ids),
                    "J-beam8" if row == 2 else "H8", ",".join(map(str, w)), score, path)
            for row, variant, reason in ((1, "prior_only", "No archived A prior-only prediction; C without channel retains s2s."),
                                         (3, "morph_only", "No archived J-off/M-on prediction; M-off is row 2.")):
                add("main", row, variant, learner, part, len(ids), "", "", None, key=reason)
    for domain in DOMAINS:
        p = ROOT / domain
        fixed = read(p / "fixed.json")
        source = list(rows(p / "records.jsonl"))
        blocks = m2_blocks(gold_path(domain))
        if fixed["domain"] != domain or fixed["stage"] != "fixed" or len(source) != SIZES[domain]:
            raise ValueError(f"{domain}: wrong frozen result")
        gold_counts = []
        for i, (r, b) in enumerate(zip(source, blocks, strict=True)):
            x, _, edits = annotations(b)
            if r["i"] != i or surface(r["x"]) != x:
                raise ValueError(f"{domain}/{i}: wrong C reference")
            gold_counts.append(len(edits))
        hashes[str(p / "records.jsonl")] = digest(p / "records.jsonl")
        hashes[str(gold_path(domain))] = digest(gold_path(domain))
        for part, ids in subsets(domain, source).items():
            ngold = sum(gold_counts[i] for i in ids)
            if len(ids) != fixed["n"][part]:
                raise ValueError(f"{domain}/{part}: wrong sample size")
            for row, name, pool, w in ((9, "archived", "S1", "beam4"), (10, "union_no_s2s", "S8+H8", "s2s=0"),
                                       (11, "union_fixed", "S8+H8", "s2s=0.5"), ("S", "s_fixed", "S8", "s2s=0.5")):
                v = fixed["metrics"][part][name]
                if v["gold"] != ngold or not 0 <= v["correct"] <= min(v["proposed"], ngold):
                    raise ValueError(f"{domain}/{part}/{name}: wrong edit counts")
                agree(v, Counts(v["correct"], v["proposed"], ngold).metrics(), name)
                panel = "control" if row == "S" else ("main" if domain == learner else "transfer")
                add(panel, row, name, domain, part, len(ids), pool, w, v, p / "fixed.json", f"metrics.{part}.{name}")
                if domain == learner and row != "S":
                    k = {"archived": "kobart", "union_no_s2s": "union_no_s2s", "union_fixed": "union_selected"}[name]
                    if older["lambda4"] != .5 or part not in older["reference_metric_verified"]:
                        raise ValueError("unverified legacy C result")
                    add("legacy", row, k, domain, part, len(ids), pool, w, older["metrics"][part][k], legacy,
                        f"metrics.{part}.{k}")
    for d, ref in (("native", "r0_m0"), ("ec2", "bm")):
        path = OUT / "input/a3g" / d / "comparison.json"
        result = read(path)
        if result["domain"] != d or result["reference"] != ref:
            raise ValueError(f"{d}: expected domain-specific jamo baseline")
        for part in (("all_dev", "novel_all") if d == "native" else ("all_dev",)):
            n = result["dev"] if part == "all_dev" else result["sizes"][part]
            add("domain_reference", 2, ref, d, part, n, "domain J-beam8", "G=.5" if d == "native" else "G=1",
                result["comparison"][ref][part], path, f"comparison.{ref}.{part}")
    for part in parts:
        main = [r for r in table if r["panel"] == "main" and r["partition"] == part]
        if sorted(r["row"] for r in main) != [1, 2, 3, 4, 5, 6, 7, 8, 8, 9, 10, 11]:
            raise ValueError("the main table must cover all original rows and both row-8 contrasts")
    with (OUT / "ablation.csv").open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    with (OUT / "source_hashes.json").open("x", encoding="utf-8") as f:
        json.dump(hashes, f, indent=1, sort_keys=True)
    print(f"{len(table)} evidence/gap records; all stored Learner A/B winners and official M² controls verified.")


if __name__ == "__main__":
    collect()
