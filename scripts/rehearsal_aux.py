"""Source-aware GLEU and independently aligned, typed KAGAS edit metrics on sealed dev decisions."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface
from scripts.c_metric import annotations
from scripts.decode import m2_blocks
from scripts.rehearsal_predict import CONFIG, save, sha, systems
from scripts.rehearsal_score import RESULT, open_dev, partitions, require_parity, write_lines

PYTHON = Path(".venv-kagas/bin/python").absolute()
GLEU = Path("tools/gleu_final_v1")
KAGAS = Path("tools/KAGAS")


def gleu(d, seal, ps, recs, parts):
    blobs = subprocess.check_output(["git", "hash-object", str(GLEU/"metric/gleu.py"),
                                     str(GLEU/"metric/gleumodule.py")], text=True).splitlines()
    if blobs != ["42976ea997d5e8c3cf6d3abb5020732213cf0da3", "ab280799d737f957ac244a04bde4e3f437259aa2"]:
        raise ValueError("GLEU is not the pinned BART evaluator")
    names = sorted(systems(d, False))
    results = {}
    with TemporaryDirectory(dir=RESULT) as tmp:
        folder = Path(tmp)
        for part, ids in parts.items():
            write_lines(folder/"x", (ps[i]["x"] for i in ids))
            write_lines(folder/"y", (recs[i]["y"] for i in ids))
            for k in names:
                write_lines(folder/k, (ps[i]["predictions"][k] for i in ids))
            cmd = ("import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                   "from metric.gleumodule import run_gleu; p=Path(sys.argv[2]); "
                   "print(json.dumps({k:100*float(run_gleu(reference=p/'y',source=p/'x',hypothesis=p/k)) "
                   "for k in sys.argv[3:]},allow_nan=False))")
            result = json.loads(subprocess.check_output([str(PYTHON), "-c", cmd, str(GLEU.resolve()), tmp, *names], text=True))
            if result.keys() != set(names) or not all(0 <= value <= 100 for value in result.values()):
                raise ValueError(f"{d}/{part}: invalid GLEU result")
            results[part] = {"n": len(ids), "gleu_x100": result}
    baseline = results["all_dev"]["gleu_x100"]["r9"]
    if not math.isclose(baseline, CONFIG[d][2], rel_tol=0, abs_tol=.00011):
        raise ValueError(f"{d}: fresh beam-4 GLEU {baseline} differs from archived {CONFIG[d][2]}")
    save(RESULT/f"gleu.{d}.json", {"seal": seal, "domain": d, "evaluator_blobs": blobs, "partitions": results})
    print(f"{d}: source-aware GLEU, full beam-4 {baseline:.4f}", flush=True)


def matching(predictions, accepted):
    # Duplicate proposals and overlapping lists of allowed answers require an augmenting path, not greedy matching.
    owner = {}

    def place(i, visited):
        for j, choices in enumerate(accepted):
            if j not in visited and predictions[i] & choices:
                visited.add(j)
                if j not in owner or place(owner[j], visited):
                    owner[j] = i
                    return True
        return False
    return sum(place(i, set()) for i in range(len(predictions)))


def edit_groups(block):
    x, types, edits = annotations(block)
    groups = defaultdict(list)
    for t, (start, end, _, corrections) in zip(types, edits, strict=True):
        groups[t, start, end].append(frozenset(map(surface, corrections)))
    return x, groups, Counter(types)


def typed_counts(reference, proposed):
    x, gold, ng = reference
    orig, pred, np = proposed
    if orig != x:
        raise ValueError("typed edit alignment has a different source")
    hits = Counter()
    for group in gold.keys() & pred.keys():
        hits[group[0]] += matching(pred[group], gold[group])
    return hits, np, ng


def ratios(tp, proposed, gold):
    return {"tp": tp, "proposed": proposed, "gold": gold, "P": tp/proposed if proposed else None,
            "R": tp/gold if gold else None, "F0.5": 5*tp/(4*proposed+gold) if proposed or gold else None}


def kagas(d, seal, ps, blocks, parts):
    script = KAGAS/"parallel_to_m2_korean.py"
    lock = Path("evaluation/final_v1.sha256")
    expected, = [row.split()[0] for row in lock.read_text().splitlines() if row.split()[1] == str(script)]
    if sha(script) != expected:
        raise ValueError("the KAGAS CLI differs from the preregistered version")
    names = sorted(systems(d, False))
    pairs = list(dict.fromkeys((surface(p["x"]), surface(p["predictions"][k])) for p in ps for k in names))
    out = RESULT/f"kagas.{d}"
    out.mkdir(exist_ok=False)
    orig, corr, m2 = out/"source.txt", out/"hyp.txt", out/"proposed.m2"
    write_lines(orig, (x for x, _ in pairs))
    write_lines(corr, (y for _, y in pairs))
    with (out/"annotate.log").open("x", encoding="utf-8") as log:
        subprocess.run([str(PYTHON), script.name, "-orig", str(orig.resolve()), "-cor", str(corr.resolve()),
                        "-out", str(m2.resolve()), "-hunspell", "./aff-dic", "-noprint"], cwd=KAGAS,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    proposals = {}
    types = set()
    for pair, block in zip(pairs, m2_blocks(m2), strict=True):
        data = edit_groups(block)
        if data[0] != pair[0]:
            raise ValueError(f"{d}: KAGAS changed order or skipped a source")
        proposals[pair] = data
        types.update(data[2])
    evidence = []
    for p, block in zip(ps, blocks, strict=True):
        gold = edit_groups(block)
        types.update(gold[2])
        evidence.append({k: typed_counts(gold, proposals[surface(p["x"]), surface(p["predictions"][k])])
                         for k in names})
    result = {}
    for part, ids in parts.items():
        result[part] = {"n": len(ids), "typed": {}}
        for k in names:
            hits, prop, ref = Counter(), Counter(), Counter()
            for i in ids:
                h, pcount, g = evidence[i][k]
                hits.update(h)
                prop.update(pcount)
                ref.update(g)
            result[part]["typed"][k] = {t: ratios(hits[t], prop[t], ref[t]) for t in sorted(types)}
    resources = [script, KAGAS/"scripts/align_text_korean.py", KAGAS/"scripts/extractPos/align.py",
                 KAGAS/"aff-dic/ko.aff", KAGAS/"aff-dic/ko.dic"]
    save(RESULT/f"kagas.{d}.json", {"seal": seal, "domain": d, "sentences": len(ps), "systems": len(names),
                                   "evaluated_occurrences": len(ps)*len(names), "distinct_pairs": len(pairs),
                                   "sha256": {str(p): sha(p) for p in [*resources, orig, corr, m2]},
                                   "partitions": result})
    print(f"{d}: all {len(ps)*len(names)} occurrence/system pairs counted ({len(pairs)} distinct)", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("metric", choices=("gleu", "kagas"))
    ap.add_argument("domain", choices=CONFIG)
    a = ap.parse_args()
    seal, ps, recs, blocks = open_dev(a.domain)
    require_parity(seal)
    parts = partitions(a.domain, recs)
    if a.metric == "gleu":
        gleu(a.domain, seal, ps, recs, parts)
    else:
        kagas(a.domain, seal, ps, blocks, parts)
