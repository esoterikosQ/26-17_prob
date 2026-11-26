"""Frozen C on dev selection only: deletion score margins and insertion proposal gaps."""

from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing as mp
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from statistics import mean, median
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import Counts, aggregate, surface
from hnc.morph import MorphAnalyzer
from scripts.c_eval import candidate_score, pick
from scripts.c_metric import aligned_edits, annotations
from scripts.c_strong_dev import DOMAINS, ORDER, ROOT, SIZES, fixed_indices, gold_path, rows, save, scored_pools
from scripts.decode import m2_blocks, m2score, write_m2
from scripts.population_dev import is_selection

OUT = Path("outputs/c_edit_diag_v1")
WEIGHT = .5
GROUPS = ("channel", "lm_prior", "seq")
TERMS = (*GROUPS, "morph_lm", "causal_lm", "total")
OFF = {"channel": "no_channel", "lm_prior": "no_prior", "seq": "union_no_s2s"}


def source_cell(s: bool, h: bool) -> str:
    return "both" if s and h else "s_only" if s else "h_only" if h else "neither"


def origin(hyp) -> str:
    return source_cell(hyp["s"] is not None, hyp["h"] is not None)


def margin(left, right) -> dict[str, float]:
    """Weighted log-score difference, positive if the term favors the left sentence."""
    d = {"channel": right["ch"] - left["ch"], "lm_prior": left["prior"] - right["prior"],
         "seq": WEIGHT * (left["s2s"] - right["s2s"]), "morph_lm": .5 * (left["lm"] - right["lm"]),
         "causal_lm": left["causal"] - right["causal"],
         "total": candidate_score(left, WEIGHT) - candidate_score(right, WEIGHT)}
    if (not math.isclose(d["total"], sum(d[k] for k in GROUPS), abs_tol=1e-8, rel_tol=1e-10)
            or not math.isclose(d["lm_prior"], d["morph_lm"] + d["causal_lm"], abs_tol=1e-8, rel_tol=1e-10)):
        raise ValueError("score margin does not decompose")
    return d


def deletion_case(x, k, edit, hs, hits, ops, rank, choices, by_text):
    a, b, _, corrs = edit
    if not a < b or corrs != [""]:
        raise ValueError("expected a pure reference deletion")
    u = choices["union_fixed"]
    witnesses = [j for j in rank if k in hits[j]]
    cell = source_cell(any(hs[j]["s"] is not None for j in witnesses), any(hs[j]["h"] is not None for j in witnesses))
    matched = k in hits[u]
    action = ("matched" if matched else "unchanged" if hs[u]["text"] == x else
              "other_overlapping_edit" if any((i < b and j > a) for i, j, _, _ in ops[u]) else "only_elsewhere")
    row = {"edit": k, "start": a, "end": b, "coverage": cell,
           "state": "selected" if matched else "missed" if witnesses else "absent",
           "winner": u, "winner_source": origin(hs[u]), "winner_action": action,
           "s_hit": k in hits[choices["s_fixed"]], **{f"without_{t}": k in hits[choices[s]] for t, s in OFF.items()}}
    if not witnesses:
        return row
    j = witnesses[0]
    row.update(witness=j, witness_rank=rank.index(j) + 1, witness_source=origin(hs[j]))
    if not matched:
        d = margin(hs[u], hs[j])
        if d["total"] < 0 or (d["total"] == 0 and hs[u]["text"] > hs[j]["text"]):
            raise ValueError("deletion witness outranks the winner")
        largest = [t for t in GROUPS if math.isclose(d[t], max(d[v] for v in GROUPS), rel_tol=0, abs_tol=1e-10)]
        row.update(d, dominant="score_tie" if d["total"] == 0 else largest[0] if len(largest) == 1 else "term_tie")
    words = x.split()
    keep, pure = by_text.get(x), by_text.get(" ".join(words[:a] + words[b:]))
    if keep is not None and pure is not None and k in hits[pure]:
        row.update({f"isolated_{t}": v for t, v in margin(hs[keep], hs[pure]).items()})
    return row


def contains_words(text: str, form: str) -> bool:
    return f" {form} " in f" {text} "


def insertion_case(x, k, edit, hs, hits, ops, winner):
    a, b, _, corrs = edit
    if a != b or len(corrs) != 1 or not surface(corrs[0]):
        raise ValueError("expected one non-empty reference insertion")
    form = surface(corrs[0])
    row = {"edit": k, "start": a, "end": b, "form": form, "width": min(len(form.split()), 3),
           "position": "empty" if not x else "start" if a == 0 else "end" if a == len(x.split()) else "interior",
           "selected": k in hits[winner], "already_in_x": contains_words(x, form)}
    for src in ("s", "h", "union"):
        ids = [j for j, h in enumerate(hs) if src == "union" or h[src] is not None]
        at = {surface(c) for j in ids for lo, hi, _, c in ops[j] if lo == hi == a and c.strip()}
        exact = any(k in hits[j] for j in ids)
        if exact and form not in at:
            raise ValueError("M² insertion hit without matching boundary and form")
        row[src] = ("hit" if exact else "same_form_unassigned" if form in at else
                    "other_form" if at else "no_boundary_insertion")
        row[f"in_{src}_surface"] = any(contains_words(hs[j]["text"], form) for j in ids)
    row["coverage"] = source_cell(row["s"] == "hit", row["h"] == "hit")
    return row


def sentence(item):
    r, hs, block = item
    x, types, gold = annotations(block)
    by_text = {h["text"]: j for j, h in enumerate(hs)}
    if x != surface(r["x"]) or len(by_text) != len(hs):
        raise ValueError("misaligned or non-unique candidates")
    choices = fixed_indices(hs)
    choices["no_channel"] = pick([dict(h, ch=0.) for h in hs], "union", WEIGHT)
    choices["no_prior"] = pick([dict(h, prior=0.) for h in hs], "union", WEIGHT)
    rank = sorted(range(len(hs)), key=lambda j: (-candidate_score(hs[j], WEIGHT), hs[j]["text"]))
    u = choices["union_fixed"]
    if rank[0] != u:
        raise ValueError("ranking differs from frozen C")
    target = [(k, t) for k, t in enumerate(types) if t in ("DELETION", "INSERTION")]
    js = rank if target else set(choices.values())
    aligned = {j: aligned_edits(hs[j]["text"], x, gold) for j in js}
    hits = {j: set(matched) for j, (matched, _) in aligned.items()}
    ops = {j: edits for j, (_, edits) in aligned.items()}
    dels, ins = [], []
    common = {"i": r["i"], "unit_id": r["unit_id"], "component": r["component"]}
    for k, t in target:
        if t == "DELETION":
            dels.append({**common, **deletion_case(x, k, gold[k], hs, hits, ops, rank, choices, by_text)})
        else:
            ins.append({**common, **insertion_case(x, k, gold[k], hs, hits, ops, u)})
    outputs = {s: (hs[j]["text"], Counts(len(hits[j]), len(ops[j]), len(gold))) for s, j in choices.items()}
    return (r["i"], outputs, dels, ins)


def frequencies(cases, key):
    return dict(Counter(c[key] for c in cases))


def distribution(values):
    return {"mean": mean(values), "median": median(values)} if values else None


def deletion_summary(cases):
    missed = [c for c in cases if c["state"] == "missed"]
    kept = [c for c in cases if c["state"] == "selected"]
    isolated = [c for c in cases if "isolated_total" in c]
    return {"gold": len(cases), "state": frequencies(cases, "state"), "coverage": frequencies(cases, "coverage"),
            "missed_winner": frequencies(missed, "winner_action"), "missed_winner_source": frequencies(missed, "winner_source"),
            "missed_witness_source": frequencies(missed, "witness_source"),
            "missed_after_s_hit": sum(c["s_hit"] for c in missed),
            "missed_rank": distribution([c["witness_rank"] for c in missed]),
            "dominant": frequencies(missed, "dominant"), "margin": {t: distribution([c[t] for c in missed]) for t in TERMS},
            "ablation": {t: {"recovered": sum(c[f"without_{t}"] for c in missed),
                             "lost": sum(not c[f"without_{t}"] for c in kept)} for t in OFF},
            "isolated": {"pairs": len(isolated), "keep_score_higher": sum(c["isolated_total"] > 0 for c in isolated),
                         "score_ties": sum(c["isolated_total"] == 0 for c in isolated),
                         "favor_keep": {t: sum(c[f"isolated_{t}"] > 0 for c in isolated) for t in TERMS},
                         "margin": {t: distribution([c[f"isolated_{t}"] for c in isolated]) for t in TERMS}}}


def insertion_summary(cases):
    def group(rs):
        missed = [c for c in rs if c["coverage"] == "neither"]
        return {"gold": len(rs), "coverage": frequencies(rs, "coverage"), "selected": sum(c["selected"] for c in rs),
                "boundary": {s: frequencies(rs, s) for s in ("s", "h", "union")},
                "missed_form_absent_from_all_surfaces": sum(not c["in_union_surface"] for c in missed),
                "missed_form_already_in_x": sum(c["already_in_x"] for c in missed)}
    return {"all": group(cases), **{k: {str(v): group([c for c in cases if c[k] == v])
                                        for v in sorted({c[k] for c in cases})}
                                  for k in ("width", "position", "first_tag")}}


def write_cases(path, cases):
    with path.open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for c in cases for k in c)))
        writer.writeheader()
        writer.writerows(cases)


def run(domain: str, workers: int) -> None:
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    src, out = ROOT / domain, OUT / domain
    out.mkdir(parents=True, exist_ok=False)
    fixed = json.loads((src / "fixed.json").read_text())
    recs, blocks = list(rows(src / "records.jsonl")), m2_blocks(gold_path(domain))
    if not len(recs) == len(blocks) == SIZES[domain] or any(r["i"] != i for i, r in enumerate(recs)):
        raise ValueError("dev fingerprint mismatch")
    selected = [r["i"] for r in recs if is_selection(domain, r)]
    if len(selected) != len(recs) - fixed["n"]["check"]:
        raise ValueError("selection fingerprint mismatch")
    select_ids = set(selected)
    pools = scored_pools(OUT / "input" / domain, recs, include=lambda r: r["i"] in select_ids)
    jobs = ((r, [{k: h[k] for k in ("text", "s", "h", "prior", "ch", "s2s", "lm", "causal")} for h in hs],
             blocks[r["i"]]) for r, hs in pools)
    with mp.get_context("fork").Pool(workers) as pool:
        results = list(pool.imap(sentence, jobs, chunksize=6))
    if [i for i, _, _, _ in results] != selected:
        raise ValueError("non-selection rows or missing selection outputs")
    dels = [e for _, _, ds, _ in results for e in ds]
    ins = [e for _, _, _, es in results for e in es]
    tagger = MorphAnalyzer(num_workers=1)

    @lru_cache(maxsize=None)
    def first_tag(form):
        ms = tagger.analyze(form)
        return ms[0].tag if ms else "NO_TOKEN"

    for case in ins:
        case["first_tag"] = first_tag(case.pop("form"))
    write_cases(out / "deletions.csv", dels)
    write_cases(out / "insertions.csv", ins)
    parts = {"select": list(range(len(selected)))}
    if domain == "union":
        parts.update({f"select/{d}": [n for n, i in enumerate(selected) if recs[i]["component"] == d] for d in ORDER})
    report = {}
    with TemporaryDirectory(dir=out) as tmp:
        hp, gp = Path(tmp) / "hyp.txt", Path(tmp) / "gold.m2"
        for part, ids in parts.items():
            original = {selected[n] for n in ids}
            ds, es = [e for e in dels if e["i"] in original], [e for e in ins if e["i"] in original]
            write_m2([blocks[selected[n]] for n in ids], gp)
            metrics = {}
            for name in results[0][1]:
                vals = [results[n][1][name] for n in ids]
                m = aggregate([c for _, c in vals]).metrics()
                hp.write_text("".join(h + "\n" for h, _ in vals), encoding="utf-8")
                if any(abs(m[k] - v) > .000051 for k, v in m2score(hp, gp).items()):
                    raise ValueError(f"{domain}/{part}/{name}: official M² mismatch")
                if part == "select" and name in fixed["metrics"]["all_dev"]:
                    for k in ("correct", "proposed", "gold"):
                        if m[k] != fixed["metrics"]["all_dev"][name][k] - fixed["metrics"]["check"][name][k]:
                            raise ValueError(f"{name}: selection counts differ from archived complement")
                metrics[name] = m
            report[part] = {"sentences": len(ids), "deletion": deletion_summary(ds),
                            "insertion": insertion_summary(es), "metrics": metrics}
    save(out / "summary.json", {"domain": domain, "partitions": report})
    print(f"{domain}: validated {len(selected)} selection rows / {len(dels)} deletions / {len(ins)} insertions",
          flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", choices=DOMAINS)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers must be positive")
    run(args.domain, args.workers)
