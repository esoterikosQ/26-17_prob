"""Explain frozen strong-C dev results by reference-edit coverage and paired selection changes."""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import sys
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import aggregate
from scripts.c_eval import lift_ci, pick
from scripts.c_metric import annotations, matched_edits, proposal_evidence, stats, work
from scripts.c_strong_dev import DOMAINS, ORDER, ROOT, SIZES, fixed_indices, gold_path, rows, save, scored_pools
from scripts.decode import m2_blocks, m2score, write_m2
from scripts.population_dev import is_selection

OUT = Path("outputs/c_proposal_diag_v1")
CELLS = ("s_only", "h_only", "both", "neither")
TYPED_SYSTEMS = ("archived", "s_fixed", "union_fixed", "s_no_channel", "union_no_channel")
COLUMNS = ("gold", *CELLS, "covered_not_selected", *TYPED_SYSTEMS, "retained", "gained", "lost",
           "gained_h_only", "channel_gained", "channel_lost")


def choices(hyps) -> dict[str, int]:
    indices = fixed_indices(hyps)
    # Same surfaces, tie breaker, priors and conditional scores; only remove the channel cost.
    free = [dict(h, ch=0.) for h in hyps]
    indices.update(s_no_channel=pick(free, "s", .5), union_no_channel=pick(free, "union", .5))
    return indices


def edit_fates(hyps, types, chosen):
    """One observation per (sentence, reference-edit index), not per beam occurrence."""
    s = matched_edits(h for h in hyps if h["s"] is not None)
    h = matched_edits(hyp for hyp in hyps if hyp["h"] is not None)
    if not (s | h) <= set(range(len(types))):
        raise ValueError("candidate match outside the reference annotation")
    hits = {name: set(hyps[chosen[name]]["tp"]) for name in TYPED_SYSTEMS}
    by_type = defaultdict(Counter)
    for k, typ in enumerate(types):
        c = by_type[typ]
        c["gold"] += 1
        c["both" if k in s and k in h else "h_only" if k in h else "s_only" if k in s else "neither"] += 1
        for name, ids in hits.items():
            c[name] += k in ids
        before, after, off = (k in hits[name] for name in ("s_fixed", "union_fixed", "union_no_channel"))
        c["covered_not_selected"] += (k in s or k in h) and not after
        c["retained"] += before and after
        c["gained"] += after and not before
        c["lost"] += before and not after
        c["gained_h_only"] += after and not before and k not in s
        c["channel_gained"] += after and not off
        c["channel_lost"] += off and not after
    return by_type


def checked_counts(counter):
    c = {k: counter[k] for k in COLUMNS}
    if (c["gold"] != sum(c[k] for k in CELLS)
            or c["gold"] != c["neither"] + c["covered_not_selected"] + c["union_fixed"]
            or c["s_fixed"] != c["retained"] + c["lost"]
            or c["union_fixed"] != c["retained"] + c["gained"]
            or c["union_fixed"] - c["union_no_channel"] != c["channel_gained"] - c["channel_lost"]
            or c["gained_h_only"] > min(c["h_only"], c["gained"])):
        raise ValueError("edit accounting does not close")
    return c


def summarize(ids, metric, recs, blocks, types, picks, archived, tmp):
    ev = proposal_evidence(metric, recs, ids)
    by_type, switch = defaultdict(Counter), Counter()
    for i in ids:
        hyps, sel = metric[i]["hyps"], picks[i]
        for typ, counts in edit_fates(hyps, types[i], sel).items():
            by_type[typ].update(counts)
        s, u = sel["s_fixed"], sel["union_fixed"]
        if u != s:
            if hyps[u]["s"] is not None or hyps[u]["h"] is None:
                raise ValueError("adding H changed the winner to another S candidate")
            switch["changed_sentences"] += 1
            switch["to_unedited"] += hyps[u]["proposed"] == 0
            switch["from_unedited"] += hyps[s]["proposed"] == 0
    total = Counter()
    for counts in by_type.values():
        total.update(counts)
    total = checked_counts(total)
    if {k: total[k] for k in CELLS} != ev["gold_edit"]:
        raise ValueError("typed and source coverage disagree")
    gp, hp = tmp / "gold.m2", tmp / "hypothesis.txt"
    write_m2([blocks[i] for i in ids], gp)
    systems, indices = {}, {name: [sel[name] for sel in picks] for name in picks[0]}
    for name, js in indices.items():
        chosen = [metric[i]["hyps"][js[i]] for i in ids]
        values = aggregate([stats(metric[i], hyp) for i, hyp in zip(ids, chosen, strict=True)]).metrics()
        if (name in TYPED_SYSTEMS and values["correct"] != total[name]) or values["gold"] != total["gold"]:
            raise ValueError(f"{name}: inconsistent reference counts")
        if name in archived and values != archived[name]:
            raise ValueError(f"{name}: failed to reproduce the original fixed run")
        hp.write_text("".join(h["text"] + "\n" for h in chosen), encoding="utf-8")
        if any(abs(values[k] - v) > .000051 for k, v in m2score(hp, gp).items()):
            raise ValueError(f"{name}: official CLI does not reproduce counts")
        systems[name] = values
    oracles = ev["oracle_m2"]
    if (oracles["union"]["F0.5"] < max(oracles[s]["F0.5"] for s in ("h", "s"))
            or any(systems[name]["F0.5"] > oracles["s" if name.startswith("s_") or name == "archived"
                                                      else "union"]["F0.5"] for name in systems)):
        raise ValueError("candidate oracle is below an attainable score")
    effects = {}
    for before, after in (("s_fixed", "union_fixed"), ("s_no_channel", "s_fixed"),
                          ("union_no_channel", "union_fixed"), ("s_no_channel", "union_no_channel")):
        effects[f"{after}-{before}"] = {"delta": systems[after]["F0.5"] - systems[before]["F0.5"],
                                        "ci95": lift_ci(metric, ids, recs, indices[before], indices[after])}
    return {"proposal": ev, "systems": systems, "delta": effects, "all_edits": total,
            "switches": dict(switch), "by_type": {k: checked_counts(v) for k, v in sorted(by_type.items())}}


def run(domain: str, workers: int) -> None:
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    src, out = ROOT / domain, OUT / domain
    fixed = json.loads((src / "fixed.json").read_text())
    out.mkdir(parents=True, exist_ok=False)
    inputs = scored_pools(OUT / "input" / domain, rows(src / "records.jsonl"))
    recs, blocks, types, picks, jobs = [], [], [], [], []
    n = 0
    for i, ((r, hyps), block) in enumerate(zip(inputs, m2_blocks(gold_path(domain)), strict=True)):
        n += 1
        if r["i"] != i:
            raise ValueError("nonsequential dev identity")
        if is_selection(domain, r):
            continue
        if not hyps or len({h["text"] for h in hyps}) != len(hyps):
            raise ValueError("empty or non-deduplicated candidate pool")
        picks.append(choices(hyps))
        recs.append(r)
        blocks.append(block)
        types.append(annotations(block)[1])
        p = {k: r[k] for k in ("i", "x", "unit_id")}
        p["hyps"] = [{k: h[k] for k in ("s", "h", "text")} for h in hyps]
        jobs.append((p, r, block))
    if n != SIZES[domain] or len(jobs) != fixed["n"]["check"]:
        raise ValueError("dev partition size mismatch")
    print(f"{domain}: matching {sum(len(p['hyps']) for p, _, _ in jobs)} candidates in {len(jobs)} check rows", flush=True)
    with mp.get_context("fork").Pool(workers) as pool:
        metric = list(pool.imap(work, jobs, chunksize=6))
    del jobs
    with (out / "check_edits.jsonl").open("x", encoding="utf-8") as f:
        for r, m, labels, sel in zip(recs, metric, types, picks, strict=True):
            if r["i"] != m["i"] or m["ngold"] != len(labels):
                raise ValueError("sentence/edit identity mismatch")
            hs = [{k: h[k] for k in ("s", "h", "tp", "proposed")} for h in m["hyps"]]
            f.write(json.dumps({"i": r["i"], "unit_id": r["unit_id"], "component": r["component"],
                                "types": labels, "hyps": hs, "chosen": sel}, ensure_ascii=False) + "\n")
    parts = {"check": list(range(len(recs)))}
    if domain == "union":
        parts.update({f"check/{d}": [i for i, r in enumerate(recs) if r["component"] == d] for d in ORDER})
    report = {}
    with TemporaryDirectory(dir=out) as tmp, (out / "by_type.csv").open("x", encoding="utf-8", newline="") as f:
        table = csv.DictWriter(f, fieldnames=("partition", "type", *COLUMNS))
        table.writeheader()
        for part, ids in parts.items():
            if len(ids) != fixed["n"][part]:
                raise ValueError(f"{part}: check group size mismatch")
            result = summarize(ids, metric, recs, blocks, types, picks, fixed["metrics"][part], Path(tmp))
            report[part] = result
            for typ, counts in {"__ALL__": result["all_edits"], **result["by_type"]}.items():
                table.writerow({"partition": part, "type": typ, **counts})
    save(out / "summary.json", {"domain": domain, "partitions": report})
    print(f"{domain}: fixed systems and no-channel controls validated against official M²", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", choices=DOMAINS)
    parser.add_argument("--workers", default=12, type=int)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    run(args.domain, args.workers)
