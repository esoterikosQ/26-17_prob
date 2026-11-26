"""One authorized source-only test run. This module never opens test references or old test outputs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rehearsal_pcfg import MODEL, REV, score_texts
from scripts.rehearsal_predict import (CAUSAL, CONFIG, choose, generate, line, neural_b, require_source,
                                       rows, save, sha, source_beams, systems, write_hnc)
from scripts.rehearsal_seal import inventory

WORK = Path("outputs/final_test_v1")
INPUT = Path("data/final_inputs_v1")
GATE = INPUT/"dev_gate"
PERMIT = INPUT/"opened_once.json"
N = {"korean_learner": 4265, "lang8": 16434, "union": 23333}
DEV_SEAL = "2bb0d5c8d55becfd8a9b53af6b62be29cb5c8aa19984126860004d7ae8cd0768"
READINESS = "8809ea868824bcdceccf786e0e6a8438aa1aac2a6365dc521a11c3e2946a1909"
GRAMMAR = "b588226df158c882a1ada18cc32114d5337dfd77237bdcef84be59452748f5cc"
LOCK = Path("evaluation/final_v1.sha256")


def read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def original(d):
    return INPUT/"original"/d/f"{d}_test_original.txt"


def assets():
    subprocess.run(["sha256sum", "--strict", "--quiet", "-c", str(LOCK)], check=True)
    for d in N:
        manifest = Path("outputs/c_gec2_dev_v1/restore")/f"{d}.sha256"
        subprocess.run(["sha256sum", "--strict", "--quiet", "-c", str(manifest.resolve())],
                       cwd=Path("data/c_gec2_dev_v1/models")/d, check=True)
    if sha(MODEL) != GRAMMAR or sha(GATE/"scores/readiness.json") != READINESS or sha(GATE/"seal.full.json") != DEV_SEAL:
        raise ValueError("missing or changed verified rehearsal/grammar")


def dev_gate():
    assets()
    subprocess.run(["sha256sum", "--strict", "--quiet", "-c", "INPUTS.sha256"], cwd=GATE, check=True)
    expected = read(GATE/"seal.full.json")["sha256"]
    for d in N:
        choose(d, False, work=GATE)
        name = f"{d}/predictions.full.jsonl"
        if sha(GATE/name) != expected[name]:
            raise ValueError(f"{d}: shared runner failed full dev decision/audit replay")
    save(GATE/"runner_verified.json", {d: expected[f"{d}/predictions.full.jsonl"] for d in N})


def code_check(revision):
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", revision, "scripts", "hnc", "evaluation"],
                                    text=True).splitlines()
    subprocess.run(["git", "diff", "--quiet", revision, "--", *paths], check=True)


def authorize():
    if PERMIT.exists() or WORK.exists():
        raise FileExistsError("v1 has already been opened or has an existing run; never start a second run")
    assets()
    previous = read(GATE/"seal.full.json")["sha256"]
    if read(GATE/"runner_verified.json") != {d: previous[f"{d}/predictions.full.jsonl"] for d in N}:
        raise ValueError("all three frozen dev outputs must reproduce first")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    code_check(revision)
    subprocess.run(["sha256sum", "--strict", "--quiet", "-c", "SOURCE.sha256"], cwd=INPUT/"original", check=True)
    permit = {"protocol": "final_v1", "split": "test", "revision": revision, "empty_kagas": "whole_cell_NA",
              "n": N, "dev_seal": DEV_SEAL, "grammar": GRAMMAR, "model_lock": sha(LOCK),
              "protocol_sha": sha(Path("evaluation/final_v1.md")), "sources": {d: sha(original(d)) for d in N}}
    save(PERMIT, permit)
    WORK.mkdir(mode=0o700)
    save(WORK/"settings.json", permit)
    print("One-time v1 source-only run opened", revision, flush=True)


def settings():
    a = read(PERMIT)
    if (a != read(WORK/"settings.json") or a["n"] != N or a["split"] != "test" or a["protocol"] != "final_v1"
            or a["empty_kagas"] != "whole_cell_NA" or a["model_lock"] != sha(LOCK)
            or a["protocol_sha"] != sha(Path("evaluation/final_v1.md")) or a["dev_seal"] != DEV_SEAL):
        raise ValueError("test approval, settings, or locked resources changed")
    code_check(a["revision"])
    return a


def source_rows(d, texts):
    if len(texts) != N[d]:
        raise ValueError(f"{d}: source count {len(texts)} != {N[d]}")
    if any("\t" in x for x in texts):
        raise ValueError("source-only file contains a tab/composite record")
    return [{"i": i, "unit_id": f"{d}:test:{i}", "x": x} for i, x in enumerate(texts)]


def source_input(d, a):
    if sha(original(d)) != a["sources"][d]:
        raise ValueError("official original changed")
    return source_rows(d, original(d).read_text(encoding="utf-8").splitlines())


def pcfg(d, opts, workers):
    if d != "korean_learner" or sha(MODEL) != GRAMMAR:
        raise ValueError("wrong test PCFG domain/grammar")
    _, hs = source_beams(d, **opts)
    texts = sorted({c["text"] for r in hs for c in r["hyps"]})
    values, fingerprint = score_texts(texts, workers, Path("data/models/pcfg_best.sha256"))
    out = WORK/d
    n = parsed = 0
    with (out/"pcfg.jsonl").open("x", encoding="utf-8") as f:
        for r in hs:
            hyp = []
            for c in r["hyps"]:
                value, ok = values[c["text"]]
                hyp.append({"text": c["text"], "pcfg": value, "parsed": ok})
                n += 1
                parsed += ok
            line(f, {"i": r["i"], "hyps": hyp})
    save(out/"pcfg.verified.json", {"scores_sha": sha(out/"pcfg.jsonl"), "candidates": n, "parsed": parsed,
                                   "grammar_sha": fingerprint, "source": REV, "dev_replay": DEV_SEAL})


def seal(*, create=False):
    a = settings()
    path = WORK/"seal.json"
    if not create and not path.is_file():
        raise FileNotFoundError("no complete three-domain test seal")
    files = inventory(False) | {"settings.json"} | {f"{d}/hnc.done.json" for d in N}
    if create:
        for d, n in N.items():
            gen = read(WORK/d/"generation.json")
            if (gen["n"], gen["domain"], gen["epoch"], gen["baseline_origin"], gen["beam4_match"]) != (
                    n, d, CONFIG[d][1], "fresh_beam4", None):
                raise ValueError(f"{d}: not a complete fresh neural baseline")
            seq = zip(source_input(d, a), rows(WORK/d/"sources.jsonl"), rows(WORK/d/"predictions.full.jsonl"), strict=True)
            for i, (original_row, src, p) in enumerate(seq):
                require_source(src, d, i, "test")
                if (src != original_row or any(p[k] != v for k, v in src.items())
                        or p["predictions"].keys() != systems(d, False)
                        or any(not isinstance(y, str) for y in p["predictions"].values())):
                    raise ValueError(f"{d}/{i}: incomplete output")
    info = {"split": "test", "protocol": "final_v1", "approval_sha": sha(PERMIT),
            "sha256": {key: sha(WORK/key) for key in sorted(files)}}
    if create:
        save(path, info)
        save(INPUT/"sealed_once.json", {"seal": sha(path), "revision": a["revision"]})
    if read(path) != info or read(INPUT/"sealed_once.json") != {"seal": sha(path), "revision": a["revision"]}:
        raise ValueError("invalid or altered global test seal")
    return sha(path)


def stage(name, d, workers):
    a = settings()
    if (WORK/"seal.json").exists() or (INPUT/"sealed_once.json").exists():
        raise ValueError("test predictions already sealed")
    opts = {"work": WORK, "n": N[d], "split": "test"}
    if name == "hnc":
        write_hnc(d, workers, WORK/d, source_input(d, a), "test")
    elif name == "generate":
        generate(d, **opts)
    elif name == "neural-b":
        neural_b(d, **opts)
    elif name == "pcfg":
        pcfg(d, opts, workers)
    elif name == "choose":
        choose(d, False, **opts)
    else:
        from scripts.c_features import causal, cpu
        out = WORK/d
        meta = read(out/"generation.json")
        items = list(rows(out/"pool.jsonl"))
        if len(items) != N[d] or meta["baseline_origin"] != "fresh_beam4" or meta["n"] != len(items):
            raise ValueError("incomplete fresh test pool")
        if name == "cpu":
            cpu(items, out, workers)
        else:
            causal(items, out, 24, str(CAUSAL))


if __name__ == "__main__":
    os.umask(0o077)
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("dev-gate", "open-approved-v1", "hnc", "generate", "neural-b", "pcfg",
                                      "cpu", "causal", "choose", "seal"))
    ap.add_argument("domain", choices=N, nargs="?")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    general = args.stage in ("dev-gate", "open-approved-v1", "seal")
    if (args.domain is None) != general or args.workers < 1:
        ap.error("use a domain for prediction stages only, and positive workers")
    if args.stage == "dev-gate":
        dev_gate()
    elif args.stage == "open-approved-v1":
        authorize()
    elif args.stage == "seal":
        print("global test seal", seal(create=True), flush=True)
    else:
        stage(args.stage, args.domain, args.workers)
