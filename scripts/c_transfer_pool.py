"""Load the frozen HNC proposals, optionally pairing with one archived neural output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface, union_pool

ROOT = Path("outputs/c_gec2_dev_v1")
LEARNER = Path("outputs/rescore/learner_JJ_Ml30")


def rows(path):
    with path.open(encoding="utf-8") as f:
        return list(map(json.loads, f))


def candidates(recs):
    lookup = {}
    for d in dict.fromkeys(r["component"] for r in recs):
        if d == "korean_learner":
            hs = rows(LEARNER / "nbest.jsonl")
            with (LEARNER / "gold_all.m2").open() as f:
                sources = [line[2:].strip() for line in f if line.startswith("S ")]
            for h, x in zip(hs, sources, strict=True):
                h["x"] = x
        else:
            hs = rows(ROOT / "beam" / f"{d}.jsonl")
        lookup[d] = hs
    result = []
    for r in recs:
        j = r["component_i"]
        h = lookup[r["component"]][j]
        if (h["i"] != j or surface(h["x"]) != surface(r["x"])
                or ("unit_id" in h and r["unit_id"] != h["unit_id"])):
            raise ValueError(f"HNC identity mismatch: {r['i']}")
        result.append([a["text"] for a in h["hyps"]])
    return result


def restricted_pool(domain):
    data = ROOT / domain
    recs = rows(data / "records.jsonl")
    meta = json.loads((data / "recipe.json").read_text())
    if len(recs) != meta["n"] or meta["domain"] != domain:
        raise ValueError("incomplete dev manifest")
    dest = data / "s1h8"
    dest.mkdir(exist_ok=False)
    n = 0
    with (dest / "pool.jsonl").open("x") as f:
        for r, hs in zip(recs, candidates(recs), strict=True):
            pool = union_pool(hs, [r["archived"]])
            f.write(json.dumps({"i": r["i"], "x": r["x"], "unit_id": r["unit_id"], "hyps": pool},
                               ensure_ascii=False) + "\n")
            n += len(pool)
    with (dest / "generation.json").open("x") as f:
        json.dump({**meta, "hypotheses": n, "neural_width": 1, "hnc_width": 8,
                   "conditional_score_available": False}, f, indent=1)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("domain", choices=("korean_learner", "lang8", "union"))
    restricted_pool(p.parse_args().domain)
