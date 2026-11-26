"""Fixed, post-primary T5/HNC comparison; never modifies or authorizes another final_v1 run."""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface
from scripts.c_eval import pick
from scripts.final_test import DEV_SEAL, LOCK, N, original, read
from scripts.rehearsal_predict import CAUSAL, CONFIG, C_NAMES, line, require_source, rows, save, sha

ROOT = Path("outputs/t5_followup_v1")
DATA = Path("data/t5_followup_v1")
SETUP = ROOT/"setup"
PARENTS = {"dev": ("seal.full.json", DEV_SEAL),
           "test": ("seal.json", "9645165a3fbbaacc1ec57dd215067ab7f01d350c06722943e0cc0d6d3c545034")}
BEST = {"korean_learner": (6, 59.9094), "lang8": (2, 39.1224), "union": (4, 48.9943)}
MD5 = {"korean_learner": "a6c79d295e7d26e7eb92de74217d73e3", "lang8": "8c31aebe2f15ad0be21eb550c159db44",
       "union": "3c6af90316299574e04f058089620d6d"}
MODEL_FILES = {"best_info.json", "config.json", "generation_config.json", "model.safetensors",
               "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"}
RUNTIME = {"transformers": "4.57.6", "tokenizers": "0.22.2", "torch": "2.11.0+cu128"}
COMPARISONS = (("r9", "r11"), ("r9", "s_only"), ("s_only", "r11"), ("r10", "r11"))


def manifest(path):
    pairs = [l.split(maxsplit=1) for l in path.read_text().splitlines()]
    by_name = {name: digest for digest, name in pairs}
    if len(pairs) != len(by_name) or any(Path(n).name != n for n in by_name):
        raise ValueError(f"invalid or duplicate member of {path}")
    return by_name


def model_receipt(d, *, verify_bytes):
    hashes = manifest(SETUP/f"{d}.sha256")
    md5 = manifest(SETUP/f"{d}.md5")
    if set(hashes) != MODEL_FILES or set(md5) != MODEL_FILES or md5["model.safetensors"] != MD5[d]:
        raise ValueError("wrong Drive checkpoint inventory")
    path = DATA/"models"/d
    if verify_bytes and any(sha(path/name) != h for name,h in hashes.items()):
        raise ValueError(f"{d}: T5 bytes differ from verified transfer")
    archive = DATA/"archive"/d
    recipe = read(archive/"run_info.json")
    expected = {"model": "paust/pko-t5-large", "seed": 0, "data": d, "lr": .0001, "kind": "seq2seq",
                "num_beams": 4, "gen_batch_size": 32, "gen_len_ratio": 2., "gen_len_add": 10,
                "max_gen_len": 512, "precision": "bf16-mixed"}
    ep, val = BEST[d]
    epochs = list(rows(archive/"epochs.jsonl"))
    info = read(path/"best_info.json")
    historical = max(epochs, key=lambda r:r["val_gleu"])
    if (any(recipe["args"][key] != v for key,v in expected.items())
            or any(recipe["versions"][key] != v for key,v in RUNTIME.items())
            or historical["epoch"] != epochs[-1]["best_epoch"] or historical["epoch"] != info["epoch"]
            or info["epoch"] != ep or any(abs(x-val) > .00011 for x in (info["val_gleu"], historical["val_gleu"]))):
        raise ValueError(f"{d}: not the original scenario-1 dev-selected seed-0 T5")
    return {"files": hashes, "args": expected, "epoch": ep, "val_gleu": val, "training_commit": recipe["git_commit"]}


def source_beam(d, split):
    base = DATA/"h8"/split
    name, digest = PARENTS[split]
    if sha(base/name) != digest:
        raise ValueError(f"{split}: wrong parent seal")
    frozen = read(base/name)["sha256"]
    files = [base/d/f for f in ("sources.jsonl", "hnc.jsonl")]
    if any(sha(p) != frozen[str(p.relative_to(base))] for p in files):
        raise ValueError("source-only H8 inputs do not match parent seal")
    src, hs = (list(rows(p)) for p in files)
    if len(src) != (N[d] if split == "test" else CONFIG[d][0]):
        raise ValueError("wrong source count")
    for i,(r,h) in enumerate(zip(src,hs,strict=True)):
        require_source(r,d,i,split)
        if any(h[k] != v for k,v in r.items()) or not 1 <= len(h["hyps"]) <= 8:
            raise ValueError("source/H8 occurrence mismatch")
    if split == "test" and [r["x"] for r in src] != original(d).read_text(encoding="utf-8").splitlines():
        raise ValueError("test stream differs from the original source-only file")
    return src, [[c["text"] for c in h["hyps"]] for h in hs]


def freeze():
    if (SETUP/"settings.json").exists() or (DATA/"test_sealed.json").exists():
        raise FileExistsError("T5 run already frozen or completed")
    subprocess.run(["sha256sum","--quiet","--strict","-c",str(LOCK)],check=True)
    receipts = {}
    for d in N:
        dev = DATA/"archive"/d
        subprocess.run(["sha256sum","--quiet","--strict","-c",str((SETUP/f"{d}.dev.sha256").resolve())],cwd=dev,check=True)
        receipts[d] = model_receipt(d,verify_bytes=True)
        for split in PARENTS:
            source_beam(d,split)
    revision = subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    subprocess.run(["git","diff","--quiet",revision,"--","hnc","scripts","evaluation"],check=True)
    save(SETUP/"settings.json",{"experiment":"t5_followup_v1","post_primary":True,"parents":PARENTS,
         "revision":revision,"runtime":RUNTIME,"models":receipts,"model_lock":sha(LOCK),
         "rerank":{"morph_lm":.5,"causal":1.,"channel_cost":-1.,"lambda4":[0.,.5],"H":8,"S":8},
         "primary":"Learner r11-r9","contrasts":COMPARISONS,"partial_typed":"entire affected cell NA"})


def settings():
    a = read(SETUP/"settings.json")
    if (a["experiment"]!="t5_followup_v1" or a["model_lock"]!=sha(LOCK) or not a["post_primary"]
            or {k:tuple(v) for k,v in a["parents"].items()}!=PARENTS):
        raise ValueError("wrong frozen follow-up settings")
    subprocess.run(["git","diff","--quiet",a["revision"],"--","hnc","scripts","evaluation"],check=True)
    return a


def dev_gate():
    a = settings()
    for d in N:
        out = ROOT/"dev"/d
        proof = read(out/"parity.json")
        if (proof["domain"] != d or proof["matched"] != CONFIG[d][0] or proof["n"] != CONFIG[d][0]
                or proof["prediction_sha"] != sha(out/"beam4.jsonl") or proof["settings_sha"] != sha(SETUP/"settings.json")
                or proof["model_sha"] != a["models"][d]["files"]["model.safetensors"]):
            raise ValueError(f"{d}: exact dev beam-4 parity required before test")


def choices(hyps):
    idx = [j for j,h in enumerate(hyps) if h["s"] == 0]
    if len(idx) != 1 or len({h["text"] for h in hyps}) != len(hyps):
        raise ValueError("ambiguous fresh T5 baseline or duplicate surface")
    return {"r9": idx[0], "s_only": pick(hyps,"s",.5), "r10": pick(hyps,"union",0.),
            "r11": pick(hyps,"union",.5)}


def choose(d):
    out = ROOT/"test"/d
    src,_ = source_beam(d,"test")
    c = {r["text"]:r["lp"] for r in rows(out/"causal.jsonl")}
    with (out/"predictions.jsonl").open("x",encoding="utf-8") as w:
        for r,f,b in zip(src,rows(out/"features.jsonl"),rows(out/"beam4.jsonl"),strict=True):
            if any(f[k]!=v for k,v in r.items()) or b["i"]!=r["i"]:
                raise ValueError("scoring inputs out of order")
            for h in f["hyps"]:
                h["prior"] = .5*h["lm"] + c[h["text"]]
                if not all(math.isfinite(h[k]) for k in ("prior","s2s","ch")):
                    raise ValueError("nonfinite reranking input")
            js = choices(f["hyps"])
            ys = {k:f["hyps"][j]["text"] for k,j in js.items()}
            if ys["r9"] != b["text"]:
                raise ValueError("S[0] is not the freshly generated beam-4 output")
            line(w,{**r,"predictions":ys,"choices":js,
                    "audit":{k:[{"z":e["z"],"backoff_only":e["backoff_only"]} for e in f["hyps"][js[k]]["edits"]]
                             for k in ("r9","r11")}})


def seal(*, create=False):
    dev_gate()
    path = ROOT/"seal.json"
    if not create and not path.is_file():
        raise FileNotFoundError("score only after the complete T5 test seal")
    files = [SETUP/"settings.json"]
    for d in N:
        dev, test = ROOT/"dev"/d, ROOT/"test"/d
        files.extend([dev/"beam4.jsonl", dev/"parity.json", SETUP/f"{d}.sha256"])
        files.extend(test/n for n in ("beam4.jsonl","generation.json","pool.jsonl","features.jsonl",
                                      "causal.jsonl","predictions.jsonl"))
        if create:
            src,_ = source_beam(d,"test")
            meta = read(test/"generation.json")
            if meta["n"] != N[d] or meta["split"]!="test" or meta["domain"]!=d or meta["baseline"]!="fresh_beam4":
                raise ValueError("not a complete fresh T5 test run")
            for r,p in zip(src,rows(test/"predictions.jsonl"),strict=True):
                if any(p[k]!=v for k,v in r.items()) or set(p["predictions"])!=set(C_NAMES):
                    raise ValueError("incomplete T5 prediction inventory")
    info = {"experiment":"t5_followup_v1","split":"test","parent":PARENTS["test"][1],
            "sha256":{str(p.relative_to(ROOT)):sha(p) for p in files}}
    receipt = DATA/"test_sealed.json"
    if create:
        if receipt.exists():
            raise FileExistsError("test follow-up already sealed")
        save(path,info)
        save(receipt,{"seal":sha(path)})
    if read(path)!=info or read(receipt)!={"seal":sha(path)}:
        raise ValueError("changed global T5 seal")
    return sha(path)


def run(stage,d):
    if stage=="freeze":
        freeze()
    elif stage=="seal":
        print("T5 global seal",seal(create=True),flush=True)
    else:
        settings()
        if (ROOT/"seal.json").exists() or (DATA/"test_sealed.json").exists():
            raise ValueError("do not regenerate sealed outputs")
        if stage!="dev":
            dev_gate()
        if stage in ("dev","test"):
            from scripts.t5_generate import measure
            measure(d,stage)
        elif stage=="choose":
            choose(d)
        else:
            from scripts.c_features import causal,cpu
            out=ROOT/"test"/d
            pool=list(rows(out/"pool.jsonl"))
            if read(out/"generation.json")["pool_sha"]!=sha(out/"pool.jsonl") or len(pool)!=N[d]:
                raise ValueError("pool incomplete or changed")
            if stage=="cpu":
                cpu(pool,out,10)
            else:
                causal(pool,out,24,str(CAUSAL))


if __name__=="__main__":
    os.umask(0o077)
    ap=argparse.ArgumentParser()
    ap.add_argument("stage",choices=("freeze","dev","test","cpu","causal","choose","seal"))
    ap.add_argument("domain",choices=N,nargs="?")
    a=ap.parse_args()
    if (a.stage in ("freeze","seal")) != (a.domain is None):
        ap.error("generation and feature stages require a domain")
    run(a.stage,a.domain)
