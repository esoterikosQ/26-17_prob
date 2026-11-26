"""Verify a trusted project-local v3 grammar against every frozen Learner dev H8 candidate."""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.morph import MorphAnalyzer
from scripts.rehearsal_predict import WORK, line, rows, save, sha, source_beams

MODEL = Path("data/models/pcfg_best.pkl")
PACKAGE = Path("tools/pcfg_final_v1")
REV = "0ca1883048dd33ab1509989a8aa8d4909bcf98e2"
CACHE = Path("outputs/rescore/learner_JJ_Ml30/nbest.jsonl")


def init():
    global _kiwi
    _kiwi = MorphAnalyzer(num_workers=1)


def score(text):
    r = _parser.score([tuple(t) for t in _kiwi.analyze(text)])
    if not math.isfinite(r.logprob):
        raise ValueError("nonfinite PCFG score")
    return r.logprob, r.parsed


def main(workers):
    global _parser
    receipt = (WORK/"setup/grammar.sha256").read_text().split()
    if len(receipt) != 2 or receipt[1] != str(MODEL) or sha(MODEL) != receipt[0]:
        raise ValueError("trusted transfer receipt does not match staged grammar")
    rev = subprocess.check_output(["git", "-C", str(PACKAGE), "rev-parse", "HEAD"], text=True).strip()
    if rev != REV or subprocess.check_output(["git", "-C", str(PACKAGE), "diff", "HEAD", "--", "pcfg", "setup.py"]):
        raise ValueError("parser source changed")
    sys.path.insert(0, str(PACKAGE.resolve()))
    from pcfg.score import BEST_CONFIG, PCFGScorer
    _parser = PCFGScorer.load(MODEL)
    if _parser.config != BEST_CONFIG or BEST_CONFIG["parent_v"] != 3:
        raise ValueError("not the preregistered v3 grammar")
    out = WORK / "korean_learner"
    _, beam = source_beams("korean_learner")
    texts = sorted({h["text"] for r in beam for h in r["hyps"]})
    with mp.get_context("fork").Pool(workers, initializer=init) as pool:
        scores = dict(zip(texts, pool.imap(score, texts, chunksize=8), strict=True))
    err, n, parsed = 0., 0, 0
    with (out / "pcfg.jsonl").open("x", encoding="utf-8") as f:
        for r, old in zip(beam, rows(CACHE), strict=True):
            if r["i"] != old["i"]:
                raise ValueError("frozen H8 order mismatch")
            hyps = []
            for h, saved in zip(r["hyps"], old["hyps"], strict=True):
                lp, ok = scores[h["text"]]
                if h["text"] != saved["text"] or ok != saved["parsed"]:
                    raise ValueError("H8 candidate or parsed flag changed")
                err = max(err, abs(lp-saved["pcfg"]))
                parsed += ok
                n += 1
                hyps.append({"text": h["text"], "pcfg": lp, "parsed": ok})
            line(f, {"i": r["i"], "hyps": hyps})
    if (n, parsed) != (34110, 33774) or err > 1e-8:
        raise ValueError(f"PCFG gate failed: {n}, {parsed}, {err}")
    save(out/"pcfg.verified.json", {"grammar_sha": receipt[0], "candidate_reference_sha": sha(CACHE),
                                    "scores_sha": sha(out/"pcfg.jsonl"), "candidates": n, "parsed": parsed,
                                    "max_error": err, "source": REV})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()
    if a.workers < 1:
        ap.error("workers must be positive")
    main(a.workers)
