"""Replay the frozen deletion-only pairs and audit observed vs. fallback channel routes."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface
from hnc.external_channel import UNK, marginal
from hnc.morph import MorphAnalyzer
from scripts.c_features import load_external_channel
from scripts.c_metric import annotations, per_candidate
from scripts.c_strong_dev import DOMAINS, ORDER, ROOT, SIZES, gold_path, rows, save
from scripts.decode import m2_blocks
from scripts.population_dev import is_selection

OUT = Path("outputs/c_deletion_path_v1")
EXPECTED = {"korean_learner": 6, "lang8": 96, "union": 88}
OBSERVED = {"jamo_observed", "morph_observed"}
TOL = 1e-8


def support(regions):
    """Joint observed-only probability within the scorer's independent region approximation."""
    logs = []
    for reg in regions:
        rc = reg["route_costs"]
        if not math.isclose(sum(math.exp(reg["local"] - c) for c in rc.values()), 1., abs_tol=1e-10):
            raise ValueError("region mass does not sum to one")
        seen = [(name, c) for name, c in rc.items() if name in OBSERVED]
        lp = reg["local"] - marginal(seen)[0] if seen else None
        if lp is not None and lp > 1e-10:
            raise ValueError("observed-only mass exceeds marginal mass")
        logs.append(min(0., lp) if lp is not None else None)
    if any(v is None for v in logs):
        return "requires_backoff", None, 1.
    joint = sum(v for v in logs if v is not None)
    half = -math.log(2)
    group = ("mass_tie" if math.isclose(joint, half, abs_tol=1e-12, rel_tol=0) else
             "observed_mass_larger" if joint > half else "backoff_mass_larger_with_support")
    return group, joint, -math.expm1(joint)


def replay(channel, spans, h, x):
    y = h["text"]
    detail = {}
    c, edits = channel.score(y, x, spans(y), spans(x), trace=detail)
    plain, plain_edits = channel.score(y, x, spans(y), spans(x))
    error = abs(c - h["ch"])
    if (error > TOL or c != plain or edits != plain_edits or edits != h["edits"]
            or len(detail["regions"]) != len(edits)):
        raise ValueError("frozen channel cost, spans or explanations were not reproduced")
    base = detail["jamo_identity"] + detail["morph_identity"]
    if abs(c - (base + sum(r["increment"] for r in detail["regions"]))) > TOL:
        raise ValueError("absolute channel cost does not close")
    for reg, e in zip(detail["regions"], edits, strict=True):
        grp, _, back = support([reg])
        if ((grp == "requires_backoff") != e["backoff_only"]
                or abs(back - e["posterior"].get(UNK, 0.)) > .00000051):
            raise ValueError("support or fallback mass disagrees with archived posterior")
        costs = reg["route_costs"]
        best = [k for k, v in costs.items() if abs(v - min(costs.values())) < 1e-10]
        reg.update(best_route=best[0] if len(best) == 1 else "tie", backoff_mass=back, map_label=e["z"])
    return c, detail, error


def morph_status(reg):
    if reg["guards"] and not reg["space_probe"]:
        return "not_evaluated"
    if reg["morph_label"] is None:
        return "no_nonidentity_op"
    floor = any(s["floor"] for s in reg["morph_steps"])
    if floor != (reg["morph_label"] == UNK):
        raise ValueError("morphology label and actual table hits disagree")
    return "floor" if floor else "fitted"


def describe(values):
    return {"mean": mean(values), "median": median(values)} if values else None


def summarize(pairs):
    regs = [r for _, trace in pairs for r in trace["regions"]]
    cases = [c for c, _ in pairs]
    categories = Counter(c["support"] for c in cases)
    return {"pairs": len(cases), "regions": len(regs), "support": dict(categories),
            "channel_favors_keep": sum(c["channel_gap"] > 0 for c in cases),
            "full_favors_keep": sum(c["full_gap"] > 0 for c in cases),
            "all_regions_jamo_observed": sum(c["observed_jamo_steps"] is not None for c in cases),
            "composed_observed_jamo": sum((c["observed_jamo_steps"] or 0) > 1 for c in cases),
            "observed_jamo_steps": describe([c["observed_jamo_steps"] for c in cases
                                              if c["observed_jamo_steps"] is not None]),
            "cost": {k: describe([c[k] for c in cases]) for k in
                     ("channel_gap", "identity_jamo_delta", "identity_morph_delta", "edit_increment", "backoff_mass")},
            "gap_by_support": {g: describe([c["channel_gap"] for c in cases if c["support"] == g]) for g in categories},
            "region_kind": dict(Counter(r["kind"] for r in regs)),
            "region_guards": dict(Counter("+".join(r["guards"]) or "eligible" for r in regs)),
            "best_route": dict(Counter(r["best_route"] for r in regs)),
            "map_label": dict(Counter(r["map_label"] for r in regs)),
            "morph_when_eligible": dict(Counter(morph_status(r) for r in regs if not r["guards"])),
            "morph_space_probe": dict(Counter(morph_status(r) for r in regs if r["space_probe"])),
            "pairs_with_space_probe": sum(any(r["space_probe"] for r in t["regions"]) for _, t in pairs),
            "pairs_with_fitted_space_probe": sum(any(r["space_probe"] and morph_status(r) == "fitted"
                                                     for r in t["regions"]) for _, t in pairs),
            "max_replay_error": max((c["replay_error"] for c in cases), default=None)}


def run(domain: str):
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    inp, out = OUT / "input", OUT / domain
    out.mkdir(exist_ok=False)
    cases = defaultdict(dict)
    with (inp / "cases" / domain / "deletions.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            i, k = int(row["i"]), int(row["edit"])
            if k in cases[i]:
                raise ValueError("duplicate reference ID")
            cases[i][k] = row
    model = load_external_channel()
    analyzer = MorphAnalyzer(num_workers=1)
    spans = lru_cache(maxsize=512)(analyzer.spans)
    records = rows(ROOT / domain / "records.jsonl")
    beams = rows(inp / "beams" / domain / "features.jsonl")
    pairs = []
    n = 0
    for i, (r, beam, block) in enumerate(zip(records, beams, m2_blocks(gold_path(domain)), strict=True)):
        n += 1
        if r["i"] != i or (i, r["unit_id"], r["x"]) != (beam["i"], beam["unit_id"], beam["x"]):
            raise ValueError("beam and record misaligned")
        if not is_selection(domain, r):
            if i in cases:
                raise ValueError("non-selection sentence in audit")
            continue
        x, types, gold = annotations(block)
        case = cases.pop(i, {})
        if x != surface(r["x"]) or set(case) != {k for k, t in enumerate(types) if t == "DELETION"}:
            raise ValueError("wrong selection edit inventory")
        if not case:
            continue
        hyps = {h["text"]: h for h in beam["hyps"]}
        if len(hyps) != len(beam["hyps"]):
            raise ValueError("duplicate frozen hypothesis")
        words = x.split()
        for k, old in case.items():
            a, b, _, corrs = gold[k]
            if (corrs != [""] or (a, b) != (int(old["start"]), int(old["end"]))
                    or (str(r["unit_id"]), r["component"]) != (old["unit_id"], old["component"])):
                raise ValueError("reference deletion differs from previous audit")
            y = " ".join(words[:a] + words[b:])
            both = x in hyps and y in hyps and k in per_candidate(y, x, gold)["tp"]
            if both != bool(old.get("isolated_channel")):
                raise ValueError("isolated cohort differs from archived candidates")
            if not both:
                continue
            if b != a + 1:
                raise ValueError("isolated case is not a single eojeol deletion")
            keep, kt, ek = replay(model, spans, hyps[x], x)
            drop, dt, ed = replay(model, spans, hyps[y], x)
            regs = dt["regions"]
            if kt["regions"] or not regs:
                raise ValueError("expected unchanged x and changed deletion candidate")
            gap = drop - keep
            jd, md = (dt[j] - kt[j] for j in ("jamo_identity", "morph_identity"))
            inc = sum(z["increment"] for z in regs)
            full = float(old["isolated_total"])
            errors = [ek, ed, abs(gap - float(old["isolated_channel"])), abs(gap - (jd + md + inc)),
                      abs(full - (gap + float(old["isolated_lm_prior"]) + float(old["isolated_seq"])))]
            if max(errors) > TOL:
                raise ValueError("archived minimal-pair contrast not reproduced")
            group, lp, back = support(regs)
            paths = [z["jamo_paths"]["observed"] for z in regs]
            edit_steps = (sum(s["kind"] != "identity" for path in paths if path is not None for s in path)
                          if all(path is not None for path in paths) else None)
            rec = {"i": i, "unit_id": r["unit_id"], "component": r["component"], "component_i": r["component_i"],
                   "edit": k, "start": a, "end": b, "regions": len(regs), "cost_keep": keep, "cost_delete": drop,
                   "channel_gap": gap, "full_gap": full, "identity_jamo_delta": jd, "identity_morph_delta": md,
                   "edit_increment": inc, "support": group, "observed_log_mass": lp, "backoff_mass": back,
                   "observed_jamo_steps": edit_steps, "replay_error": max(errors)}
            pairs.append((rec, dt))
    if n != SIZES[domain] or cases or len(pairs) != EXPECTED[domain]:
        raise ValueError("incomplete minimal-pair audit")
    with (out / "pairs.csv").open("x", newline="", encoding="utf-8") as f, \
            (out / "routes.jsonl").open("x", encoding="utf-8") as paths_file:
        writer = csv.DictWriter(f, fieldnames=list(pairs[0][0]))
        writer.writeheader()
        for rec, trace in pairs:
            writer.writerow(rec)
            paths_file.write(json.dumps({"i": rec["i"], "edit": rec["edit"], **trace}, allow_nan=False) + "\n")
    report = {"select": summarize(pairs)}
    if domain == "union":
        report.update({f"select/{d}": summarize([(c, t) for c, t in pairs if c["component"] == d]) for d in ORDER})
    save(out / "summary.json", {"domain": domain, "partitions": report})
    print(f"{domain}: {len(pairs)} exact pairs, absolute scores, spans, support and stored margins verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", choices=DOMAINS)
    run(parser.parse_args().domain)
