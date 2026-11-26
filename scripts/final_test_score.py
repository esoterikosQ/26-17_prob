"""Open official test references only behind the immutable three-domain v1 seal."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface
from scripts.c_strong_dev import ORDER, lines, union_pairs
from scripts.decode import m2_blocks
from scripts.final_test import INPUT, N, WORK, read, seal
from scripts.rehearsal_aux import KAGAS, annotate, compute_gleu, edit_groups, typed_counts, typed_partition
from scripts.rehearsal_predict import require_source, rows, save, sha, systems
from scripts.rehearsal_score import compute_m2

RESULT = WORK/"scores"


def reference(d):
    return INPUT/"gold"/f"union_test.m2" if d == "union" else Path("data/kor_gec")/d/f"{d}_test.m2"


def open_gold(d):
    fingerprint = seal()
    parts = ORDER if d == "union" else (d,)
    recs, official = [], []
    for component in parts:
        source = Path("datasets/kor_gec")/f"{component}_test.jsonl"
        blocks = m2_blocks(reference(component))
        expected_n = N[component] if component != "native" else N["union"] - N["lang8"] - N["korean_learner"]
        if len(blocks) != expected_n:
            raise ValueError(f"{component}: wrong official test size")
        for j, (r, b) in enumerate(zip(rows(source), blocks, strict=True)):
            if r["unit_id"] != f"{component}.test.{j}" or surface(r["x"]) != surface(b[0][2:]):
                raise ValueError(f"{component}/{j}: bad official ID or M2 source")
            recs.append({"x": r["x"], "y": r["y"], "component": component})
        official.extend(blocks)
    if d == "union":
        if official != m2_blocks(reference(d)):
            raise ValueError("distributed Union test M2 differs from its component blocks/order")
        for r, xy in zip(recs, union_pairs(lines(INPUT/"gold/union_test.txt")), strict=True):
            if (surface(r["x"]), surface(r["y"])) != xy:
                raise ValueError("distributed Union test pair differs from component records")
    ps = list(rows(WORK/d/"predictions.full.jsonl"))
    for i, (r, src, p) in enumerate(zip(recs, rows(WORK/d/"sources.jsonl"), ps, strict=True)):
        require_source(src, d, i, "test")
        if surface(src["x"]) != surface(r["x"]) or any(src[k] != p[k] for k in src):
            raise ValueError(f"{d}/{i}: prediction/reference mismatch")
    if len(ps) != N[d]:
        raise ValueError("incomplete test")
    groups = {"all_test": list(range(len(ps)))}
    if d == "union":
        for name in ORDER:
            groups[name] = [i for i, r in enumerate(recs) if r["component"] == name]
        seen = {surface(r["y"]) for r in rows(Path("datasets/kor_gec/native_train.jsonl"))}
        groups["native_novel"] = [i for i in groups["native"] if surface(recs[i]["y"]) not in seen]
    if not all(groups.values()):
        raise ValueError("empty registered test partition")
    return fingerprint, ps, recs, official, groups


def annotate_test_pairs(out, pairs):
    script = KAGAS/"parallel_to_m2_korean.py"
    proposals, failed = {}, {}
    normal = [p for p in pairs if all(p)]
    empty = [p for p in pairs if not all(p)]
    for j in range(0, len(normal), 4000):
        group = normal[j:j+4000]
        path = out/f"batch-{j}"
        annotate(script, path, group)
        for pair, block in zip(group, m2_blocks(path/"proposed.m2"), strict=True):
            data = edit_groups(block)
            if pair[0] != data[0]:
                raise ValueError("misordered KAGAS batch")
            proposals[pair] = data
    for j, pair in enumerate(empty):
        p = out/f"empty-{j}"
        rc = annotate(script, p, [pair], check=False)
        blocks = m2_blocks(p/"proposed.m2")
        if rc == 0 and len(blocks) == 1:
            data = edit_groups(blocks[0])
            if data[0] != pair[0]:
                raise ValueError("empty pair source changed")
            proposals[pair] = data
        elif (rc == 1 and not blocks and (p/"annotate.log").read_text().rstrip().endswith("IndexError: list index out of range")
              or rc == 0 and not any(pair) and not blocks):
            failed[pair] = {"returncode": rc, "log_sha": sha(p/"annotate.log")}
        else:
            raise ValueError(f"unrecognized KAGAS failure on isolated pair {j} (exit {rc})")
    if set(proposals) | set(failed) != set(pairs):
        raise ValueError("unaccounted KAGAS input")
    return proposals, failed


def kagas(d, ps, blocks, parts):
    names = sorted(systems(d, False))
    pairs = list(dict.fromkeys((surface(p["x"]), surface(p["predictions"][k])) for p in ps for k in names))
    dest = RESULT/f"kagas.{d}"
    dest.mkdir(exist_ok=False)
    proposals, failed = annotate_test_pairs(dest, pairs)
    evidence, types = [], set()
    for p, block in zip(ps, blocks, strict=True):
        gold = edit_groups(block)
        types.update(gold[2])
        row = {}
        for k in names:
            pair = surface(p["x"]), surface(p["predictions"][k])
            if pair in failed:
                row[k] = None
            else:
                prop = proposals[pair]
                types.update(prop[2])
                row[k] = typed_counts(gold, prop)
        evidence.append(row)
    result = {}
    for part, ids in parts.items():
        typed, unavailable = {}, {}
        for k in names:
            typed[k], missing = typed_partition(evidence, ids, k, types)
            if missing:
                unavailable[k] = missing
        result[part] = {"n": len(ids), "typed": typed, "unavailable": unavailable}
    return {"partitions": result, "required_occurrences": len(ps)*len(names), "distinct_pairs": len(pairs),
            "failed_distinct_pairs": len(failed), "failed_pair_diagnostics": list(failed.values())}


def summary():
    s = seal()
    domains = {}
    for d in N:
        metrics = {}
        for kind in ("m2", "gleu", "kagas"):
            path = RESULT/f"{kind}.{d}.json"
            v = read(path)
            if (v["seal"], v["domain"], v["split"]) != (s, d, "test"):
                raise ValueError("wrong score receipt")
            metrics[kind] = v["partitions"]
        a, g, t = (metrics[k]["all_test"] for k in ("m2", "gleu", "kagas"))
        if a["n"] != N[d] or t["n"] != a["n"] or g["n"] != a["n"]:
            raise ValueError("incomplete full test")
        domains[d] = {"n": a["n"], "official_m2": {k: {m: w[m] for m in ("P", "R", "F0.5", "correct", "proposed", "gold")}
                      for k, w in a["metrics"].items()}, "primary_contrast": a["paired"]["r11-r9"],
                      "gleu_x100": g["gleu_x100"], "unavailable_typed": {p: v["unavailable"] for p, v in metrics["kagas"].items()},
                      "report_sha": {k: sha(RESULT/f"{k}.{d}.json") for k in metrics}}
    save(RESULT/"summary.json", {"split": "test", "seal": s, "domains": domains})


if __name__ == "__main__":
    os.umask(0o077)
    ap = argparse.ArgumentParser()
    ap.add_argument("metric", choices=("m2", "gleu", "kagas", "summary"))
    ap.add_argument("domain", nargs="?", choices=N)
    a = ap.parse_args()
    if (a.domain is None) != (a.metric == "summary"):
        ap.error("summary covers all domains; other stages need one")
    if a.metric == "summary":
        summary()
    else:
        s, ps, rs, bs, groups = open_gold(a.domain)
        RESULT.mkdir(exist_ok=True)
        if a.metric == "m2":
            result = {"partitions": compute_m2(a.domain, ps, rs, bs, groups, RESULT), "official_cli_verified": True}
        elif a.metric == "gleu":
            values, blobs = compute_gleu(a.domain, ps, rs, groups, RESULT)
            result = {"partitions": values, "evaluator_blobs": blobs}
        else:
            result = kagas(a.domain, ps, bs, groups)
        save(RESULT/f"{a.metric}.{a.domain}.json", {**result, "seal": s, "domain": a.domain, "split": "test",
                                                     "official_m2_sha": sha(reference(a.domain))})
