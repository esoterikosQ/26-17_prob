"""Train-only single-surplus-eojeol pilot. Re-score frozen dev selection pools; do not generate candidates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import Counts, aggregate, surface
from hnc.extra_eojeol import CONTEXTS, ExtraEojeol
from hnc.morph import MorphAnalyzer
from scripts.c_deletion_paths import EXPECTED as PAIR_SIZES
from scripts.c_edit_select import WEIGHT, write_cases
from scripts.c_eval import candidate_score, lift_ci, pick
from scripts.c_features import load_external_channel
from scripts.c_metric import aligned_edits, annotations
from scripts.c_strong_dev import DOMAINS, ORDER, ROOT, SIZES, gold_path, rows, save, scored_pools
from scripts.decode import m2_blocks, m2score, write_m2
from scripts.population_dev import is_selection

OUT = Path("outputs/c_extra_eojeol_v1")
TRAIN = {"korean_learner": 19898, "lang8": 76692}
SYSTEMS = ("base", "reserve", "pilot")


def sha(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def fit():
    receipts = {}

    def training_pairs():
        for d, expected in TRAIN.items():
            path = Path("datasets/kor_gec") / f"{d}_train.jsonl"
            count = 0
            for r in rows(path):
                count += 1
                yield r["x"], r["y"]
            if count != expected:
                raise ValueError(f"{d}: unexpected train size")
            receipts[d] = {"pairs": count, "sha256": sha(path)}

    model, info = ExtraEojeol.fit(training_pairs())
    dest = OUT / "model.json"
    save(dest, {"opportunities": model.opportunities, "counts": model.counts})
    report = {**info, "train": receipts, "model_sha256": sha(dest), "opportunities": model.opportunities,
              "vocabulary": len(model.log_emission["start"]),
              "boundary_events": {k: sum(model.counts[k].values()) for k in CONTEXTS},
              "boundary_rate": {k: 1 / (1 + math.exp(-model.log_odds[k])) for k in CONTEXTS}}
    save(OUT / "fit.json", report)
    print(json.dumps(report), flush=True)


def rerank(x, hyps, model, identity):
    adjustments = [model.adjust(h["text"], x, h["ch"], identity) for h in hyps]
    reserve = [dict(h, ch=a.reserve_cost) for h, a in zip(hyps, adjustments, strict=True)]
    pilot = [dict(h, ch=a.pilot_cost) for h, a in zip(hyps, adjustments, strict=True)]
    base, reserved, final = (pick(hs, "union", WEIGHT) for hs in (hyps, reserve, pilot))
    if ((base != reserved and adjustments[base].kind != "identity")
            or (reserved != final and adjustments[final].kind != "trained")):
        raise ValueError("a winner changed outside the declared mass transfer")
    return (base, reserved, final), adjustments


def assessed(text, x, types, gold):
    import levenshtein as lev
    ids, edits = aligned_edits(text, x, gold)
    matched = lev.matchSeq(edits, gold)
    if len(ids) != len(matched):
        raise ValueError("M² match sequence differs from reference IDs")
    def one_deleted(edit):
        return edit[1] == edit[0] + 1 and edit[3] == ""
    proposed = Counter(e[0] for e in edits if one_deleted(e))
    matched_starts = Counter(e[0] for e in matched if one_deleted(e))
    hits = [k for k, e in zip(ids, matched, strict=True) if one_deleted(e)]
    fp = sorted((proposed - matched_starts).elements())
    if len(hits) + len(fp) != proposed.total():
        raise ValueError("deletion precision denominator does not close")
    return {"tp": ids, "proposed": len(edits), "del_tp": hits, "del_fp": fp,
            "kagas_deletion_tp": sum(types[k] == "DELETION" for k in ids)}


def pair_rows(records, rec, hyps, x, adj, choices, annotation):
    if not records:
        return []
    xwords = x.split()
    idx = {h["text"]: j for j, h in enumerate(hyps)}
    keep = idx[x]
    _, types, gold = annotation
    out = []
    for row in records:
        k, a, b = (int(row[key]) for key in ("edit", "start", "end"))
        if (row["unit_id"] != str(rec["unit_id"]) or row["component"] != rec["component"]
                or not (b == a+1 and types[k] == "DELETION" and gold[k][:2] == (a,b) and gold[k][3] == [""])):
            raise ValueError("isolated edit provenance mismatch")
        y = " ".join(xwords[:a] + xwords[b:])
        j = idx[y]
        ids, _ = aligned_edits(y, x, gold)
        if k not in ids:
            raise ValueError("isolated candidate no longer matches its reference edit")
        old = candidate_score(hyps[keep], WEIGHT) - candidate_score(hyps[j], WEIGHT)
        old_ch = hyps[j]["ch"] - hyps[keep]["ch"]
        if abs(old - float(row["full_gap"])) > 1e-8 or abs(old_ch - float(row["channel_gap"])) > 1e-8:
            raise ValueError("isolated base scores not reproduced")
        new_ch = adj[j].pilot_cost - adj[keep].pilot_cost
        out.append({"i": rec["i"], "edit": k, "component": rec["component"], "coverage": adj[j].kind,
                    "base_ch_gap": old_ch, "pilot_ch_gap": new_ch, "base_gap": old,
                    "pilot_gap": old - old_ch + new_ch, "posterior": adj[j].event_posterior,
                    "base_picked_pure": choices[0] == j, "pilot_picked_pure": choices[2] == j})
    return out


def summarize(ids, results, recs, blocks, fixed, part, tmp):
    gp, hp = tmp / "gold.m2", tmp / "hyp.txt"
    write_m2([blocks[recs[i]["i"]] for i in ids], gp)
    out, dels = {}, {}
    for j, name in enumerate(SYSTEMS):
        chosen = [results[i]["hyps"][j] for i in ids]
        m = aggregate([Counts(len(h["tp"]), h["proposed"], results[i]["ngold"])
                       for i, h in zip(ids, chosen, strict=True)]).metrics()
        hp.write_text("".join(h["text"] + "\n" for h in chosen), encoding="utf-8")
        if any(abs(m[k]-v) > .000051 for k, v in m2score(hp, gp).items()):
            raise ValueError(f"{part}/{name}: official CLI mismatch")
        if part == "select" and name == "base":
            for k in ("correct", "gold", "proposed"):
                if m[k] != fixed["metrics"]["all_dev"]["union_fixed"][k] - fixed["metrics"]["check"]["union_fixed"][k]:
                    raise ValueError("original selection baseline differs")
        out[name] = m
        dels[name] = {"correct": sum(len(h["del_tp"]) for h in chosen), "unmatched": sum(len(h["del_fp"]) for h in chosen),
                      "gold": sum(results[i]["ndel"] for i in ids),
                      "kagas_deletion_tp": sum(h["kagas_deletion_tp"] for h in chosen)}
    deltas, switches = Counter(), Counter()
    for i in ids:
        r = results[i]
        base, pilot = r["hyps"][0], r["hyps"][2]
        a, b = set(base["del_tp"]), set(pilot["del_tp"])
        fa, fb = Counter(base["del_fp"]), Counter(pilot["del_fp"])
        deltas.update({"gained": len(b-a), "lost": len(a-b),
                       "new_unmatched": (fb-fa).total(), "removed_unmatched": (fa-fb).total()})
        c = r["counts"]
        switches.update({"trained_candidates": c.get("trained", 0), "unseen_candidates": c.get("unseen", 0),
                         "punctuation_candidates": c.get("punctuation", 0), "candidate_sentences": c.get("trained", 0)>0,
                         "hypotheses": sum(c.values()), "reserve_switch": r["chosen"][0] != r["chosen"][1],
                         "event_switch": r["chosen"][1] != r["chosen"][2], "base_to_pilot": r["chosen"][0] != r["chosen"][2]})
    ca, cb = dels["base"], dels["pilot"]
    if (cb["correct"] - ca["correct"] != deltas["gained"]-deltas["lost"]
            or cb["unmatched"]-ca["unmatched"] != deltas["new_unmatched"]-deltas["removed_unmatched"]):
        raise ValueError("per-edit deletion changes do not close")
    comparisons = {}
    for a, b in ((0, 1), (0, 2), (1, 2)):
        comparisons[f"{SYSTEMS[b]}-{SYSTEMS[a]}"] = {
            "f_delta": out[SYSTEMS[b]]["F0.5"] - out[SYSTEMS[a]]["F0.5"],
            "paired_ci95": lift_ci(results, ids, recs, [a]*len(recs), [b]*len(recs))}
    passed = (cb["correct"] > ca["correct"] and cb["unmatched"] <= ca["unmatched"]
              and out["pilot"]["F0.5"] >= out["base"]["F0.5"])
    return {"n": len(ids), "scores": out, "one_eojeol_deletions": dels, "edit_changes": dict(deltas),
            "coverage": dict(switches), "comparisons": comparisons, "passes_local_gate": passed}


def run(domain):
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    src, out = ROOT / domain, OUT / domain
    model_file = OUT / "model.json"
    if sha(model_file) != json.loads((OUT / "fit.json").read_text())["model_sha256"]:
        raise ValueError("train-only parameter hash mismatch")
    model = ExtraEojeol(**json.loads(model_file.read_text()))
    out.mkdir(exist_ok=False)
    old = load_external_channel()
    kiwi = MorphAnalyzer(num_workers=1)

    @lru_cache(maxsize=8192)
    def identity(y):
        spans = kiwi.spans(y)
        cost, edits = old.score(y, y, spans, spans)
        if edits:
            raise ValueError("copy route must be unedited")
        return cost

    fixed = json.loads((src / "fixed.json").read_text())
    records, blocks = list(rows(src / "records.jsonl")), m2_blocks(gold_path(domain))
    if not len(records) == len(blocks) == SIZES[domain] or any(r["i"] != i for i,r in enumerate(records)):
        raise ValueError("dev record fingerprint mismatch")
    selected = [r for r in records if is_selection(domain, r)]
    sel_ids = {r["i"] for r in selected}
    if len(selected) != len(records)-fixed["n"]["check"]:
        raise ValueError("grouped selection split differs")
    isolated = defaultdict(list)
    with (OUT / "input/pairs" / domain / "pairs.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            isolated[int(row["i"])].append(row)
    results, pairs = [], []
    inputs = scored_pools(OUT / "input/beams" / domain, records, include=lambda r:r["i"] in sel_ids)
    with (out / "selection.jsonl").open("x", encoding="utf-8") as fout:
        for rec, hs in inputs:
            x = surface(rec["x"])
            if len({h["text"] for h in hs}) != len(hs):
                raise ValueError("duplicate frozen hypothesis")
            choices, adj = rerank(x, hs, model, identity)
            ref = annotations(blocks[rec["i"]])
            if x != ref[0]:
                raise ValueError("misaligned M² source")
            for j, h in enumerate(hs):
                if adj[j].kind == "identity" and abs(identity(x)-h["ch"]) > 1e-8:
                    raise ValueError("frozen copy channel mismatch")
            unique = {j: dict(assessed(hs[j]["text"], *ref), text=hs[j]["text"]) for j in set(choices)}
            result = {"i": rec["i"], "unit_id": rec["unit_id"], "component": rec["component"], "chosen": choices,
                      "ngold": len(ref[2]), "ndel": sum(b==a+1 and "" in cs for a,b,_,cs in ref[2]),
                      "counts": dict(Counter(v.kind for v in adj)), "hyps": [unique[j] for j in choices]}
            results.append(result)
            pairs.extend(pair_rows(isolated.pop(rec["i"], []), rec, hs, x, adj, choices, ref))
            eligible = [{"j": j, "slots": a.slots, "kind": a.kind, "old": hs[j]["ch"], "reserve": a.reserve_cost,
                         "pilot": a.pilot_cost, "posterior": a.event_posterior} for j,a in enumerate(adj)
                        if a.kind != "other"]
            fout.write(json.dumps({**result, "eligible": eligible}, ensure_ascii=False, allow_nan=False) + "\n")
    if ([r["i"] for r in results] != [r["i"] for r in selected] or isolated or len(pairs) != PAIR_SIZES[domain]
            or len({(p["i"], p["edit"]) for p in pairs}) != len(pairs)):
        raise ValueError("incomplete selection or minimum pairs")
    write_cases(out / "minimum_pairs.csv", pairs)
    parts = {"select": list(range(len(results)))}
    if domain == "union":
        parts.update({f"select/{d}":[i for i,r in enumerate(selected) if r["component"] == d] for d in ORDER})
    report = {}
    with TemporaryDirectory(dir=out) as tmp:
        for part, ids in parts.items():
            r = summarize(ids, results, selected, blocks, fixed, part, Path(tmp))
            keys = {selected[i]["i"] for i in ids}
            local = [p for p in pairs if p["i"] in keys]
            r["pairs"] = {"n": len(local), "covered": sum(p["coverage"]=="trained" for p in local),
                          "base_prefers_delete": sum(p["base_gap"]<0 for p in local),
                          "pilot_prefers_delete": sum(p["pilot_gap"]<0 for p in local),
                          "pilot_ties": sum(p["pilot_gap"]==0 for p in local)}
            report[part] = r
    save(out / "summary.json", {"domain": domain, "partitions": report})
    print(f"{domain}: {len(results)} selection rows, {len(pairs)} audited pairs; "
          f"selection F0.5 {report['select']['scores']['base']['F0.5']:.6f} -> "
          f"{report['select']['scores']['pilot']['F0.5']:.6f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("fit", *DOMAINS))
    stage = ap.parse_args().stage
    if stage == "fit":
        fit()
    else:
        run(stage)
