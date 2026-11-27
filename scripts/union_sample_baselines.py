"""Score archived Union decisions on a fixed 1,000-occurrence sample; no inference or data export."""

from __future__ import annotations

import csv
import math
import random
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import aggregate, surface
from scripts.c_eval import lift_ci
from scripts.c_metric import per_candidate, stats
from scripts.decode import m2_blocks
from scripts.domain_report import SEALS, examples
from scripts.final_raw_score import raw_rows
from scripts.final_test import N, original, read
from scripts.final_test_score import reference
from scripts.rehearsal_aux import compute_gleu
from scripts.rehearsal_predict import C_NAMES, rows, save, sha

OUT = Path("outputs/union_sample_1k_v1")
DATA = Path("data/union_sample_1k_v1")
SEED = 261127
SIZE = 1000


def main():
    sample = read(OUT/"sample.json")
    id_file = OUT/"sample_ids.txt"
    ids = sorted(random.Random(SEED).sample(range(N["union"]), SIZE))
    keys = [f"union:test:{i}" for i in ids]
    source = original("union")
    if (sample["sample_ids_sha"] != sha(id_file) or keys != id_file.read_text().splitlines()
            or (sample["seed"], sample["sample_n"], sample["population_n"]) != (SEED, SIZE, N["union"])
            or sha(source) != sample["original_source_sha"]):
        raise ValueError("sample receipt or original source changed")
    src = source.read_text(encoding="utf-8").splitlines()
    records = examples("union")
    gold = reference("union")
    with TemporaryDirectory(dir=OUT) as tmp:
        raw, _ = raw_rows(gold, m2_blocks(gold), Path(tmp))
    if len(src) != N["union"] or not len(src) == len(raw) == len(records):
        raise ValueError("incomplete Union source or annotation stream")
    selected = [records[i] for i in ids]
    if dict(Counter(r["component"] for r in selected)) != sample["source_counts"]:
        raise ValueError("component counts differ from the frozen sample")
    predictions, gleu = {}, {}
    for family, filename in (("kobart", "union/predictions.full.jsonl"), ("t5", "test/union/predictions.jsonl")):
        root = DATA/family
        path = root/filename
        if (sha(root/"seal.json") != SEALS[family] or read(root/"seal.json")["sha256"][filename] != sha(path)
                or read(root/"scores/m2.union.json")["official_m2_sha"] != sha(gold)):
            raise ValueError(f"{family}: wrong sealed predictions or reference")
        ps = list(rows(path))
        for i, (p, x) in enumerate(zip(ps, src, strict=True)):
            if p["unit_id"] != f"union:test:{i}" or p["i"] != i or p["x"] != x or set(p["predictions"]) != set(C_NAMES):
                raise ValueError(f"{family}/{i}: source or system mismatch")
        subset = [ps[i] for i in ids]
        g, _ = compute_gleu("union", subset, selected, {"sample": list(range(SIZE))}, OUT)
        for k in C_NAMES:
            name = f"{family}/{k}"
            predictions[name] = [p["predictions"][k] for p in subset]
            gleu[name] = g["sample"]["gleu_x100"][k]

    import levenshtein as official  # raw_rows made the pinned M² scorer available
    names = list(predictions)
    evidence, xs, golds = [], [], []
    for j, i in enumerate(ids):
        x, _, edits = raw[i]
        if not surface(x) == surface(src[i]) == selected[j]["x"]:
            raise ValueError(f"{i}: raw official reference out of order")
        xs.append(x)
        golds.append({0: edits})
        unique = {y: per_candidate(y, x, edits) for y in {predictions[k][j] for k in names}}
        evidence.append({"ngold": len(edits), "hyps": [unique[predictions[k][j]] for k in names]})
    scores = {}
    for j, k in enumerate(names):
        v = aggregate([stats(r, r["hyps"][j]) for r in evidence]).metrics()
        values = official.batch_multi_pre_rec_f1(predictions[k], xs, golds, 2, .5)
        if not all(math.isclose(v[m], z, rel_tol=0, abs_tol=1e-12)
                   for m, z in zip(("P", "R", "F0.5"), values, strict=True)):
            raise ValueError(f"{k}: official evaluation mismatch")
        scores[k] = {**v, "GLEU_x100": gleu[k]}
    paired = {}
    for a, b in (("kobart/r9", "kobart/r11"), ("t5/r9", "t5/r11"), ("kobart/r9", "t5/r9")):
        ci = lift_ci(evidence, range(SIZE), selected, [names.index(a)]*SIZE, [names.index(b)]*SIZE)
        paired[f"{b} minus {a}"] = {"delta_F0.5": scores[b]["F0.5"]-scores[a]["F0.5"], "ci95": ci}
    save(OUT/"summary.json", {"experiment": "union_sample_1k_v1", "sample": sample, "seal_sha": SEALS,
         "official_gold_sha": sha(gold), "official_gold_edits": sum(r["ngold"] for r in evidence),
         "scores": scores, "paired": paired,
         "haiku_5_5_low": {"status": "not_measured", "reason": "no approved local runner or predictions",
                            "n_predictions": 0, "metrics": None, "delta": None}})
    with (OUT/"baseline_scores.csv").open("x", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        fields = ("correct", "proposed", "gold", "P", "R", "F0.5", "GLEU_x100")
        w.writerow(["system", "sample_n", *fields])
        for k, v in scores.items():
            w.writerow([k, SIZE, *[v[m] for m in fields]])


if __name__ == "__main__":
    main()
