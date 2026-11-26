"""Seal all preregistered dev domains before accessing any references. Partial seals are not test-ready."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rehearsal_predict import CONFIG, WORK, require_source, rows, save, sha, systems


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


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--partial-no-pcfg", action="store_true")
    print("dev seal:", check_seal(WORK, p.parse_args().partial_no_pcfg, create=True), flush=True)
