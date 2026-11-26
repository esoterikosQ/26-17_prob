"""Replay the frozen dev table. Only the full three-domain seal unlocks historical outputs and gold."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import aggregate, surface
from scripts.c_eval import lift_ci
from scripts.c_metric import annotations, per_candidate, stats
from scripts.c_strong_dev import ORDER, SIZES, gold_path, lines, output_choices
from scripts.c_transfer_pool import candidates
from scripts.core_ablation_table import H, METRICS, agree, subsets
from scripts.decode import m2_blocks, m2score, write_m2
from scripts.rehearsal_predict import CONFIG, DEV_SRC, WORK, rows, save, sha, systems
from scripts.rehearsal_seal import check_seal

EXPECTED = WORK/"setup/expected"
STRONG = EXPECTED/"strong"
RESULT = WORK/"scores"
C = {"r9": "archived", "r10": "union_no_s2s", "r11": "union_fixed", "s_only": "s_fixed"}
VARIANTS = {**C, "r1": "prior_only", "r2": "jamo_only", "r3": "morph_only", "r4": "both_channels",
            "r5": "plus_pcfg", "r6": "causal", "r7": "masked", "r8a": "causal_no_pcfg",
            "r8b": "masked_no_pcfg", "deployed_B": "deployed_B"}
CONTRASTS = (("r9", "r11"), ("r1", "r5"), ("r2", "r4"), ("r3", "r4"), ("r4", "r5"),
             ("r8a", "r6"), ("r8b", "r7"), ("r5", "r6"), ("r5", "r7"), ("r10", "r11"), ("s_only", "r11"))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_join(d, i, p, r, block, reference):
    source = surface(p["x"])
    if (p["i"] != i or p["unit_id"] != f"{d}:dev:{i}" or r["i"] != i
            or not block[0].startswith("S ") or source != surface(block[0][2:]) or source != surface(r["x"])
            or surface(reference) != surface(r["y"]) or p["predictions"].keys() != systems(d, False)):
        raise ValueError(f"{d}/{i}: wrong dev occurrence, sources, reference or system inventory")


def open_dev(d):
    seal = check_seal(WORK, False)
    n, epoch, _, _ = CONFIG[d]
    gen = read(WORK/d/"generation.json")
    if (gen["domain"], gen["epoch"], gen["n"], gen["baseline_origin"], gen["beam4_match"]) != (
            d, epoch, n, "fresh_beam4", None):
        raise ValueError(f"{d}: not the frozen fresh-baseline run")
    val = DEV_SRC/d/"lr5e-5_seed0/val"/f"epoch{epoch:02}"
    ps = list(rows(WORK/d/"predictions.full.jsonl"))
    recs = list(rows(STRONG/d/"records.jsonl"))
    blocks = m2_blocks(gold_path(d))
    if len(ps) != n:
        raise ValueError(f"{d}: wrong sealed count")
    components = ((name, j) for name in (ORDER if d == "union" else (d,)) for j in range(SIZES[name]))
    for i, (p, r, b, y, source) in enumerate(zip(ps, recs, blocks, lines(val/"reference.txt"), components, strict=True)):
        validate_join(d, i, p, r, b, y)
        if (r["component"], r["component_i"]) != source:
            raise ValueError(f"{d}/{i}: component membership mismatch")
    return seal, ps, recs, blocks


def table_cells(d, part):
    with (EXPECTED/"ablation.csv").open(encoding="utf-8") as f:
        eligible = [c for c in csv.DictReader(f) if c["domain"] == d and c["partition"] == part
                    and c["panel"] in ("main", "transfer", "control") and c["status"] == "measured"]
    result = {c["variant"]: c for c in eligible}
    if len(result) != len(eligible) or set(result) != {VARIANTS[k] for k in systems(d, False)}:
        raise ValueError(f"{d}/{part}: missing or duplicate original table cells")
    return result


def parity(d, seal, ps, recs):
    n, epoch, _, _ = CONFIG[d]
    val = DEV_SRC/d/"lr5e-5_seed0/val"/f"epoch{epoch:02}"
    old_c = output_choices(STRONG/d, recs)
    old_h = candidates(recs)
    mismatches = Counter()
    first = {}
    n_h = 0
    ab = {}
    if d == "korean_learner":
        cells = table_cells(d, "all_dev")
        special = {"r1": EXPECTED/"hyp_prior_only.txt", "r2": EXPECTED/"korean_learner/r1/hyp_lm1.0_len0.0_m0.0.txt",
                   "r3": EXPECTED/"hyp_morph_only.txt"}
        for k in systems(d, False) - C.keys():
            path = special.get(k, H/Path(cells[VARIANTS[k]]["evidence"]).name)
            ab[k] = lines(path)
            if len(ab[k]) != n:
                raise ValueError(f"{k}: incomplete archived A/B")

    def compare(k, same, i):
        if not same:
            mismatches[k] += 1
            first.setdefault(k, i)

    for i, (p, c, archived, h, old) in enumerate(zip(ps, old_c, lines(val/"hypothesis.txt"),
                                                     rows(WORK/d/"hnc.jsonl"), old_h, strict=True)):
        expect = {k: c[v] for k, v in C.items()} | {k: text[i] for k, text in ab.items()}
        for k, y in expect.items():
            compare(k, p["predictions"][k] == y, i)
        compare("direct_beam4", p["predictions"]["r9"] == surface(archived), i)
        compare("H8_order", [hyp["text"] for hyp in h["hyps"]] == old, i)
        n_h += len(h["hyps"])
    if d == "korean_learner":
        for h, old in zip(rows(WORK/d/"hnc.jsonl"), rows(H/"nbest.jsonl"), strict=True):
            same = all(new["text"] == prior["text"] and all(math.isclose(new[k], prior[k], rel_tol=0, abs_tol=1e-8)
                       for k in ("lm", "ch")) for new, prior in zip(h["hyps"], old["hyps"], strict=True))
            compare("H8_costs", same and h["i"] == old["i"], h["i"])
    report = {"seal": seal, "domain": d, "sentences": n, "h_candidates": n_h, "beam4_equal": n - mismatches["direct_beam4"],
              "systems": sorted(systems(d, False)), "mismatching_sentences": dict(mismatches), "first_index": first,
              "passed": not mismatches}
    save(RESULT/f"parity.{d}.json", report)
    if mismatches:
        raise ValueError(f"{d}: replay discrepancy: {dict(mismatches)}; see first_index (no retuning)")
    print(f"{d}: archived outputs and H order reproduced ({n} sentences, {n_h} candidates)", flush=True)


def require_parity(seal):
    for domain in CONFIG:
        r = read(RESULT/f"parity.{domain}.json")
        if r["seal"] != seal or r["domain"] != domain or r["passed"] is not True:
            raise ValueError("metrics require all three exact dev replays for this seal")


def partitions(d, recs):
    parts = subsets(d, recs)
    if d == "union":
        with Path("datasets/kor_gec/native_train.jsonl").open(encoding="utf-8") as f:
            seen = {surface(r["y"]) for r in map(json.loads, f)}
        for base, ids in list(parts.items()):
            for source in ORDER:
                parts[f"{base}/{source}"] = [i for i in ids if recs[i]["component"] == source]
            parts[f"{base}/native_novel"] = [i for i in parts[f"{base}/native"] if surface(recs[i]["y"]) not in seen]
    if not all(parts.values()):
        raise ValueError(f"{d}: empty development evaluation partition")
    return parts


def write_lines(path, values):
    with path.open("w", encoding="utf-8") as f:
        for value in values:
            f.write(value + "\n")


def metrics(d, seal, ps, recs, blocks):
    require_parity(seal)
    report = compute_m2(d, ps, recs, blocks, partitions(d, recs), RESULT)
    previous = read(STRONG/d/"fixed.json")
    for part, row in report.items():
        cells = table_cells(d, part) if part in ("all_dev", "check") else {}
        for k, v in row["metrics"].items():
            if cells:
                c = cells[VARIANTS[k]]
                if int(c["n"]) != row["n"]:
                    raise ValueError(f"{d}/{part}: wrong historical split")
                agree(v, {m: float(c[m]) for m in METRICS}, f"{d}/{part}/{k}: frozen table")
            if k in C and part in previous["metrics"]:
                original = previous["metrics"][part][C[k]]
                if any(v[m] != original[m] for m in ("correct", "proposed", "gold")):
                    raise ValueError(f"{d}/{part}/{k}: original raw edit counts differ")
    save(RESULT/f"m2.{d}.json", {"seal": seal, "domain": d, "official_cli_verified": True,
                                 "gold_sha": sha(gold_path(d)), "partitions": report})
    print(f"{d}: official M2 verified in {len(report)} partitions", flush=True)


def compute_m2(d, ps, recs, blocks, parts, output):
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    names = sorted(systems(d, False))
    evidence = []
    for p, b in zip(ps, blocks, strict=True):
        x, types, gold = annotations(b)
        ys = p["predictions"]
        unique = {y: per_candidate(y, x, gold) for y in set(ys.values())}
        evidence.append({"ngold": len(gold), "types": types, "hyps": [unique[ys[k]] for k in names]})
    report = {}
    with TemporaryDirectory(dir=output) as tmp:
        hp, gp = Path(tmp)/"hyp.txt", Path(tmp)/"gold.m2"
        for part, ids in parts.items():
            write_m2([blocks[i] for i in ids], gp)
            denom = Counter(t for i in ids for t in evidence[i]["types"])
            values, contrasts, audit = {}, {}, {}
            for j, k in enumerate(names):
                v = aggregate([stats(evidence[i], evidence[i]["hyps"][j]) for i in ids]).metrics()
                write_lines(hp, (ps[i]["predictions"][k] for i in ids))
                agree(m2score(hp, gp), v, f"{d}/{part}/{k}: official M2 CLI")
                hits = Counter(evidence[i]["types"][t] for i in ids for t in evidence[i]["hyps"][j]["tp"])
                values[k] = {**v, "tp_by_reference_type": {t: {"tp": hits[t], "gold": g, "recall": hits[t]/g}
                                                           for t, g in sorted(denom.items())}}
            for a, b in CONTRASTS:
                if a in names and b in names:
                    ci = lift_ci(evidence, ids, recs, [names.index(a)]*len(ps), [names.index(b)]*len(ps))
                    contrasts[f"{b}-{a}"] = {"delta": values[b]["F0.5"] - values[a]["F0.5"], "ci95": ci}
            for k in ("r9", "r11"):
                histogram = Counter(e["z"] for i in ids for e in ps[i]["audit"][k])
                backoff = sum(e["backoff_only"] for i in ids for e in ps[i]["audit"][k])
                audit[k] = {"changed_regions": histogram.total(), "map": dict(histogram),
                            "unclassified": histogram["UNCLASSIFIED"], "backoff_only": backoff}
            report[part] = {"n": len(ids), "metrics": values, "paired": contrasts, "automatic_audit": audit}
    return report


def readiness():
    seal = check_seal(WORK, False)
    require_parity(seal)
    files, domains, missing_total = {}, {}, 0
    for d, (n, _, _, _) in CONFIG.items():
        reports = {}
        for marker in ("parity", "m2", "gleu", "kagas"):
            p = RESULT/f"{marker}.{d}.json"
            reports[marker] = read(p)
            if reports[marker]["domain"] != d or reports[marker]["seal"] != seal:
                raise ValueError(f"{d}: mismatched {marker} receipt")
            files[p.name] = sha(p)
        parity, m2, gleu, kagas = (reports[k] for k in ("parity", "m2", "gleu", "kagas"))
        if parity["beam4_equal"] != n or m2["official_cli_verified"] is not True:
            raise ValueError(f"{d}: unverified primary score or baseline")
        cells = 0
        missing = {}
        mp, gp, kp = (r["partitions"] for r in (m2, gleu, kagas))
        if not mp.keys() == gp.keys() == kp.keys():
            raise ValueError(f"{d}: missing auxiliary partition")
        for part, row in mp.items():
            gr, kr = gp[part], kp[part]
            names = systems(d, False)
            if (not row["n"] == gr["n"] == kr["n"]
                    or not names == row["metrics"].keys() == gr["gleu_x100"].keys() == kr["typed"].keys()):
                raise ValueError(f"{d}/{part}: incomplete metric matrix")
            absent = {k for k, values in kr["typed"].items() if values is None}
            if absent != set(kr.get("unavailable", {})):
                raise ValueError(f"{d}/{part}: unexplained missing typed metric")
            if absent:
                missing[part] = kr["unavailable"]
            cells += len(names)
            missing_total += len(absent)
        unavailable = sum(map(len, missing.get("all_dev", {}).values()))
        if (mp["all_dev"]["n"] != n or kagas["sentences"] != n
                or kagas["evaluated_occurrences"] + unavailable != n*len(systems(d, False))):
            raise ValueError(f"{d}: inconsistent full-dev occurrence denominator")
        domains[d] = {"n": n, "beam4_equal": parity["beam4_equal"], "H8_hypotheses": parity["h_candidates"],
                      "systems": parity["systems"], "verified_m2_and_gleu_cells": cells,
                      "kagas_evaluated_occurrences": kagas["evaluated_occurrences"],
                      "kagas_required_occurrences": n*len(systems(d, False)), "unavailable_typed_cells": missing}
    save(RESULT/"readiness.json", {"split": "dev", "full_seal": seal, "partial_seal": check_seal(WORK, True),
         "report_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
         "pcfg": read(WORK/"korean_learner/pcfg.verified.json"), "domains": domains, "report_sha256": files,
         "unavailable_typed_cells": missing_total, "all_registered_metrics_available": missing_total == 0,
         "authorizes_test_or_unblinding": False})
    print(f"Dev rehearsal recorded: {missing_total} typed cells unavailable; no test or unblinding authorization")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("parity", "m2", "summary"))
    parser.add_argument("domain", choices=CONFIG, nargs="?")
    args = parser.parse_args()
    if args.stage == "summary":
        if args.domain is not None:
            parser.error("summary covers all domains")
        readiness()
        sys.exit(0)
    if args.domain is None:
        parser.error("parity and m2 require a development domain")
    seal, ps, recs, blocks = open_dev(args.domain)
    RESULT.mkdir(exist_ok=True)
    if args.stage == "parity":
        for cwd, manifest in ((EXPECTED, "INPUTS.sha256"), (EXPECTED, "A3G.sha256"), (STRONG, "INPUTS.sha256")):
            subprocess.run(["sha256sum", "--quiet", "--strict", "-c", manifest], cwd=cwd, check=True)
        parity(args.domain, seal, ps, recs)
    else:
        metrics(args.domain, seal, ps, recs, blocks)
