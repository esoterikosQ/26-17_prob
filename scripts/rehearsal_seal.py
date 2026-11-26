"""Seal all preregistered dev domains before accessing any references. Partial seals are not test-ready."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rehearsal_predict import CONFIG, WORK, choose, require_source, rows, save, sha, systems


def inventory(partial):
    mode = "partial" if partial else "full"
    names = set()
    for d in CONFIG:
        files = {"sources.jsonl", "hnc.jsonl", "pool.jsonl", "generation.json", "features.jsonl",
                 "causal.jsonl", f"predictions.{mode}.jsonl"}
        if d == "korean_learner":
            files.add("b_scores.jsonl")
            if not partial:
                files.update(("pcfg.jsonl", "pcfg.verified.json"))
        names.update(f"{d}/{f}" for f in files)
    return names


def check_seal(root, partial, *, create=False):
    mode = "partial" if partial else "full"
    path = root / f"seal.{mode}.json"
    expected = inventory(partial)
    if create:
        if path.exists():
            raise FileExistsError(path)
        for domain, (n, _, _, _) in CONFIG.items():
            count = 0
            for i, (src, pred) in enumerate(zip(rows(root/domain/"sources.jsonl"),
                                                 rows(root/domain/f"predictions.{mode}.jsonl"), strict=True)):
                require_source(src, domain, i)
                if any(src[k] != pred[k] for k in src) or pred["predictions"].keys() != systems(domain, partial):
                    raise ValueError("prediction ID or system inventory mismatch")
                count += 1
            if count != n:
                raise ValueError("cannot seal incomplete dev predictions")
    hashes = {name: sha(root/name) for name in sorted(expected)}
    info = {"protocol": "final_v1", "split": "dev", "mode": mode, "omitted_learner_rows": [1, 5, 6, 7] if partial else [],
            "settings": {str(p): sha(p) for p in (Path("evaluation/final_v1.md"), Path("evaluation/final_v1.sha256"))},
            "sha256": hashes}
    if create:
        save(path, info)
    elif json.loads(path.read_text()) != info:
        raise ValueError("invalid/incomplete seal or changed predictions/settings")
    return sha(path)


def unchanged_decisions(partial, full):
    for old, new in zip(partial, full, strict=True):
        projection = {k: new["predictions"][k] for k in old["predictions"]}
        if old != {**new, "predictions": projection}:
            raise ValueError("adding PCFG changed an already sealed decision or audit")


def promote():
    check_seal(WORK, True)
    pcfg = json.loads((WORK/"korean_learner/pcfg.verified.json").read_text())
    if (pcfg["candidates"], pcfg["parsed"]) != (34110, 33774) or not 0 <= pcfg["max_error"] <= 1e-8:
        raise ValueError("incomplete PCFG replay")
    for d in CONFIG:
        choose(d, False)
        unchanged_decisions(*(rows(WORK/d/f"predictions.{mode}.jsonl") for mode in ("partial", "full")))
    check_seal(WORK, True)
    return check_seal(WORK, False, create=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    opts = p.add_mutually_exclusive_group()
    opts.add_argument("--partial-no-pcfg", action="store_true")
    opts.add_argument("--promote", action="store_true", help="reuse a verified partial run after the PCFG replay")
    args = p.parse_args()
    print("dev seal:", promote() if args.promote else check_seal(WORK, args.partial_no_pcfg, create=True), flush=True)
