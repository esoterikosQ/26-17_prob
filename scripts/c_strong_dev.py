"""Dev-only archived baselines and fixed C comparisons, including Union by source."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import aggregate, surface
from scripts.c_eval import lift_ci, pick
from scripts.c_metric import annotations, per_candidate, stats
from scripts.decode import m2_blocks, m2score, write_m2
from scripts.population_dev import data_paths, is_selection

ASSETS = Path("data/c_gec2_dev_v1")
ARCH = ASSETS / "archive"
ROOT = Path("outputs/c_gec2_dev_v1")
RUNS = ARCH / "Standard_Korean_GEC/outputs2/kobart-base-v1"
UNION = ARCH / "data/Preprocessed/union"
DOMAINS = ("korean_learner", "lang8", "union")
ORDER = ("lang8", "korean_learner", "native")
SIZES = {"korean_learner": 4264, "lang8": 16434, "native": 2634, "union": 23332}


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def rows(path):
    with path.open(encoding="utf-8") as f:
        yield from map(json.loads, f)


def save(path, obj):
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, allow_nan=False)


def gold_path(domain):
    return UNION / "union_val.m2" if domain == "union" else data_paths(domain)[1]


def union_pairs(text):
    out = []
    for line in text:
        if line == "":
            continue  # distributed union_val.txt has a leading empty separator, not a pair
        pair = line.split("\t")
        if len(pair) != 2:
            raise ValueError("malformed Union dev pair")
        out.append(tuple(map(surface, pair)))
    return out


def prepare(domain):
    source_domains = ORDER if domain == "union" else (domain,)
    records, original_blocks = [], []
    for d in source_domains:
        part = list(rows(data_paths(d)[0]))
        blocks = m2_blocks(data_paths(d)[1])
        if len(part) != SIZES[d] or len(blocks) != len(part):
            raise ValueError(f"{d}: component size mismatch")
        for j, (r, b) in enumerate(zip(part, blocks, strict=True)):
            if surface(r["x"]) != surface(b[0][2:]):
                raise ValueError(f"{d}/{j}: dev source mismatch")
            records.append({"i": len(records), "unit_id": r["unit_id"], "x": r["x"], "y": r["y"],
                            "component": d, "component_i": j})
        original_blocks.extend(blocks)
    if domain == "union":
        pairs = union_pairs(lines(UNION / "union_val.txt"))
        for r, (x, y), a, b in zip(records, pairs, original_blocks, m2_blocks(gold_path(domain)), strict=True):
            if (surface(r["x"]), surface(r["y"])) != (x, y) or a != b:
                raise ValueError(f"Union component order or annotations mismatch at {r['i']}")
    run = RUNS / domain / "lr5e-5_seed0"
    args = json.loads((run / "run_info.json").read_text())["args"]
    if any(args[k] != v for k, v in {"model": "skt/kobart-base-v1", "data": domain, "seed": 0,
                                     "lr": 5e-5, "num_beams": 4, "gen_len_ratio": 2., "gen_len_add": 10,
                                     "max_gen_len": 512, "gen_batch_size": 64, "precision": "bf16-mixed"}.items()):
        raise ValueError("unexpected gec2 training/generation recipe")
    epochs = list(rows(run / "epochs.jsonl"))
    ep = max(epochs, key=lambda e: e["val_gleu"])
    if ep["epoch"] != epochs[-1]["best_epoch"]:
        raise ValueError("historical best epoch mismatch")
    val = run / "val" / f"epoch{ep['epoch']:02d}"
    for r, x, y, h in zip(records, *(lines(val / f"{name}.txt") for name in ("source", "reference", "hypothesis")),
                          strict=True):
        if (surface(x), surface(y)) != (surface(r["x"]), surface(r["y"])):
            raise ValueError(f"{domain}/{r['i']}: archived source/reference mismatch")
        r["archived"] = surface(h)
    out = ROOT / domain
    out.mkdir(parents=True, exist_ok=True)
    with (out / "records.jsonl").open("x") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    save(out / "recipe.json", {"domain": domain, "n": len(records), "epoch": ep["epoch"], "val_gleu": ep["val_gleu"]})


def scored_pools(out, recs, *, s1h8=False, include: Callable[[dict], bool] | None = None):
    causal = {c["text"]: c["lp"] for c in rows(out / "causal.jsonl")}
    for rec, pool in zip(recs, rows(out / "features.jsonl"), strict=True):
        if (rec["i"], rec["unit_id"], rec["x"]) != (pool["i"], pool["unit_id"], pool["x"]):
            raise ValueError("feature identity mismatch")
        if include is not None and not include(rec):
            continue
        hyps = pool["hyps"]
        if [h["text"] for h in hyps if h["s"] == 0] != [rec["archived"]]:
            raise ValueError("baseline not found or ambiguous")
        for h in hyps:
            h["causal"] = causal[h["text"]]
            h["prior"] = .5 * h["lm"] + h["causal"]
            if not all(math.isfinite(h[k]) for k in (("prior", "ch") if s1h8 else ("prior", "ch", "s2s"))):
                raise ValueError("non-finite transfer score")
        yield rec, hyps


def fixed_indices(hyps) -> dict[str, int]:
    return {"archived": next(i for i, h in enumerate(hyps) if h["s"] == 0),
            "s_fixed": pick(hyps, "s", .5), "union_no_s2s": pick(hyps, "union", 0.),
            "union_fixed": pick(hyps, "union", .5)}


def output_choices(out, recs, *, s1h8=False):
    for rec, hyps in scored_pools(out, recs, s1h8=s1h8):
        if s1h8:
            eligible = [h for h in hyps if h["h"] is not None or h["s"] == 0]
            best = min(eligible, key=lambda h: (h["ch"] - h["prior"], h["text"]))
            yield {"archived": rec["archived"], "s1h8_no_s2s": best["text"]}
            continue
        yield {name: hyps[j]["text"] for name, j in fixed_indices(hyps).items()}


def old_choices(recs):
    old = ASSETS / "old_choices"
    for r, c, m in zip(recs, rows(old / "choices.jsonl"), rows(old / "m2_stats.jsonl"), strict=True):
        if not (r["i"] == c["i"] == m["i"] and c["unit_id"] == r["unit_id"]):
            raise ValueError("archived Learner choices are misaligned")
        yield {"archived": r["archived"], "old_kobart": m["hyps"][c["kobart"]]["text"],
               "old_C": m["hyps"][c["union_selected"]]["text"]}


def evaluate(domain, stage):
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    out = ROOT / domain
    recs = list(rows(out / "records.jsonl"))
    blocks = m2_blocks(gold_path(domain))
    if stage == "s1h8":
        features = out if domain == "korean_learner" else out / "s1h8"
        decisions = output_choices(features, recs, s1h8=True)
    elif stage == "fixed":
        decisions = output_choices(out, recs)
    elif domain == "korean_learner":
        decisions = old_choices(recs)
    else:
        decisions = ({"archived": r["archived"]} for r in recs)
    metrics, names = [], []
    for i, (decision, r, block) in enumerate(zip(decisions, recs, blocks, strict=True)):
        if not names:
            names = list(decision)
        x, _, gold = annotations(block)
        if list(decision) != names or r["i"] != i or surface(r["x"]) != x:
            raise ValueError("misaligned decisions/gold")
        unique = {y: per_candidate(y, x, gold) for y in set(decision.values())}
        metrics.append({"ngold": len(gold), "hyps": [unique[decision[name]] for name in names]})
    if len(metrics) != SIZES[domain]:
        raise ValueError("incomplete dev evaluation")
    check = [i for i, r in enumerate(recs) if not is_selection(domain, r)]
    if domain == "korean_learner" and len(check) != 2133:
        raise ValueError("wrong Learner check fingerprint")
    parts = {"check": check, "all_dev": list(range(len(recs)))}
    if domain == "union":
        for d in ORDER:
            parts[f"check/{d}"] = [i for i in check if recs[i]["component"] == d]
    report = {"domain": domain, "stage": stage, "metrics": {}, "paired_check_delta": {},
              "n": {p: len(ids) for p, ids in parts.items()}}
    with TemporaryDirectory(dir=out) as tmp:
        hp, gp = Path(tmp) / "system.txt", Path(tmp) / "gold.m2"
        for part, ids in parts.items():
            write_m2([blocks[i] for i in ids], gp)
            report["metrics"][part] = {}
            for j, name in enumerate(names):
                counts = aggregate([stats(metrics[i], metrics[i]["hyps"][j]) for i in ids]).metrics()
                hp.write_text("".join(metrics[i]["hyps"][j]["text"] + "\n" for i in ids))
                if any(abs(v - counts[k]) > .000051 for k, v in m2score(hp, gp).items()):
                    raise ValueError(f"{domain}/{part}/{name}: official M² mismatch")
                report["metrics"][part][name] = counts
            if part.startswith("check"):
                for a, b in (("archived", "old_C"), ("old_kobart", "archived"), ("archived", "s_fixed"),
                             ("s_fixed", "union_fixed"), ("archived", "union_fixed"), ("archived", "s1h8_no_s2s")):
                    if a in names and b in names:
                        v = report["metrics"][part]
                        report["paired_check_delta"][f"{part}:{b}-{a}"] = {
                            "delta": v[b]["F0.5"] - v[a]["F0.5"],
                            "ci95": lift_ci(metrics, ids, recs, [names.index(a)] * len(recs),
                                            [names.index(b)] * len(recs))}
    save(out / f"{stage}.json", report)
    print(json.dumps({"domain": domain, "stage": stage, "check": report["metrics"]["check"]}), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("prepare", "baseline", "fixed", "s1h8"))
    ap.add_argument("domain", choices=DOMAINS)
    a = ap.parse_args()
    prepare(a.domain) if a.stage == "prepare" else evaluate(a.domain, a.stage)
