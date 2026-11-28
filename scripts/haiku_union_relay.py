"""Verify the frozen 450 direct submissions and 550 direct text replies, then score the same Union sample."""

from __future__ import annotations

import argparse
import csv
import math
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import aggregate, surface
from scripts.c_eval import lift_ci
from scripts.c_metric import per_candidate, stats
from scripts.decode import m2_blocks
from scripts.domain_report import SEALS, examples
from scripts.final_raw_score import raw_rows
from scripts.final_test import original, read
from scripts.final_test_score import reference
from scripts.haiku_union_sample import (IDS_SHA, PROMPT, SIZE, SOURCE_SHA, WIDTH, batch_keys, order,
                                       parse_batch, sample)
from scripts.rehearsal_aux import GLEU, PYTHON
from scripts.rehearsal_predict import line, rows, save, sha
from scripts.rehearsal_score import write_lines

ROOT = Path("outputs/haiku_union_relay_v1")
PRIOR = Path("data/haiku_union_1k_v1")
MODEL = "claude-haiku-5.5"
CUTOFF = "fa378ffe5c244bc7f817a4a0a6c381edbb93bc1059d9d64e7d01e3b03c93efc9"
SHARDS = {"a": (4,5,6), "b": (7,8,9), "c": (15,16,17), "d": (18,19)}


def collect():
    ids, _ = sample()
    shuffled = order()
    conf = read(ROOT/"protocol.json")
    cutoff = read(ROOT/"accepted_receipt.json")
    host = read(ROOT/"responses/routing_check.json")
    setup = read(ROOT/"initial_settings.json")
    if (conf["sample_sha"] != IDS_SHA or sha(ROOT/"sample_ids.txt") != IDS_SHA
            or conf["prompt"] != setup["prompt"] or setup["prompt"] != PROMPT
            or conf["model"] != MODEL or conf["reasoning_effort"] != "low"
            or conf["accepted_first"] != CUTOFF or cutoff["out_sha"] != CUTOFF
            or sha(ROOT/"accepted450.jsonl") != CUTOFF or cutoff["n"] != 450 or not cutoff["blind"]
            or {k:tuple(v) for k,v in conf["remaining_shards"].items()} != SHARDS):
        raise ValueError("fixed sample, initial cutoff, or authorized continuation changed")
    verified = {}
    for r in host["first_stage"].values():
        if (r["requested_model"] != MODEL or r["started_model"] != MODEL or r["runtime_effort"] != "low"
                or set(r["message_models"]) != {MODEL}):
            raise ValueError("initial response not attributable to Haiku Low")
        for event in r["accepted"]:
            name = f"{event['batch']:02}.tsv"
            if name in verified or event["rows"] != WIDTH:
                raise ValueError("duplicate or incomplete cutoff batch")
            verified[name] = event["sha"]
    if verified != cutoff["batch_sha"]:
        raise ValueError("previously saved rows differ from host-acknowledged writes")
    values = {}
    for r in rows(ROOT/"accepted450.jsonl"):
        i = int(r["unit_id"].removeprefix("union:test:"))
        if i in values or not isinstance(r["prediction"], str):
            raise ValueError("invalid initial saved record")
        values[i] = r["prediction"]
    first_ids = {i for n in verified for i in shuffled[int(n[:2])*WIDTH:(int(n[:2])+1)*WIDTH]}
    if set(values) != first_ids or len(values) != 450 or set(host["text_only"]) != set(SHARDS):
        raise ValueError("handoff did not partition the original sample")
    responses = {}
    for shard, bs in SHARDS.items():
        r = host["text_only"][shard]
        inp, reply = ROOT/"sources"/f"{shard}.tsv", ROOT/"responses"/f"{shard}.tsv"
        codes = [k for b in bs for k in batch_keys(b)]
        if (any(r[k]!=MODEL for k in ("requested_model","started_model","first_dispatched","completed_model"))
                or r["runtime_effort"]!="low" or r["cancelled"] or r["rows"]!=len(codes)
                or r["response_sha256"]!=sha(reply) or conf["source_sha"][shard]!=sha(inp)
                or any(c != {"tool":"view","source":f"{shard}.tsv"} for c in r["reads"]) or not r["reads"]):
            raise ValueError(f"{shard}: unverified source-only final message")
        verified_sources = parse_batch(inp.read_text(encoding="utf-8"), codes)
        if sha(original("union")) != SOURCE_SHA:
            raise ValueError("source changed")
        originals = original("union").read_text(encoding="utf-8").splitlines()
        predictions = parse_batch(reply.read_text(encoding="utf-8"), codes)
        indexes = [i for b in bs for i in shuffled[b*WIDTH:(b+1)*WIDTH]]
        for i,x,y in zip(indexes, verified_sources, predictions, strict=True):
            if i in values or x != originals[i]:
                raise ValueError("duplicate occurrence or source-order mismatch")
            values[i] = y
        responses[shard] = sha(reply)
    if set(values) != set(ids) or len(values) != SIZE:
        raise ValueError("incomplete final predictions")
    return ids,values,responses


def seal(create=False):
    ids, values, responses = collect()
    export = ROOT/"predictions.jsonl"
    text = ROOT/"predictions.txt"
    if create:
        with export.open("x", encoding="utf-8") as f, text.open("x", encoding="utf-8") as t:
            for i in ids:
                line(f,{"unit_id":f"union:test:{i}", "prediction":values[i]})
                t.write(values[i] + "\n")
    if (list(rows(export)) != [{"unit_id":f"union:test:{i}","prediction":values[i]} for i in ids]
            or text.read_text(encoding="utf-8").splitlines() != [values[i] for i in ids]):
        raise ValueError("export differs from unmodified verified Haiku outputs")
    info = {"model":MODEL, "reasoning_effort":"low", "n":len(ids), "ids_sha":IDS_SHA, "unchanged_cutoff_sha":CUTOFF,
            "response_sha":responses, "route_sha":sha(ROOT/"responses/routing_check.json"),
            "protocol_sha":sha(ROOT/"protocol.json"), "jsonl_sha":sha(export), "txt_sha":sha(text)}
    if create:
        save(ROOT/"seal.json", info)
    if read(ROOT/"seal.json") != info:
        raise ValueError("Haiku prediction seal changed")
    return sha(ROOT/"seal.json")


def scores():
    stamp = seal()
    ids,_ = sample()
    previous = read(PRIOR/"previous/summary.json")
    if previous["sample"]["sample_ids_sha"]!=IDS_SHA or sha(PRIOR/"previous/sample_ids.txt")!=IDS_SHA:
        raise ValueError("baseline selection differs")
    selected = [r for r in examples("union")]
    predictions = {MODEL:[r["prediction"] for r in rows(ROOT/"predictions.jsonl")]}
    names = [MODEL]
    for family,name in (("kobart","union/predictions.full.jsonl"),("t5","test/union/predictions.jsonl")):
        base = PRIOR/family
        if sha(base/"seal.json")!=SEALS[family] or sha(base/name)!=read(base/"seal.json")["sha256"][name]:
            raise ValueError("previous benchmark seal mismatch")
        ps = list(rows(base/name))
        for k in ("r9","s_only","r10","r11"):
            key = family+"/"+k
            if not all(ps[i]["unit_id"]==f"union:test:{i}" and surface(ps[i]["x"])==selected[i]["x"] for i in ids):
                raise ValueError("prior prediction ID mismatch")
            names.append(key)
            predictions[key]=[ps[i]["predictions"][k] for i in ids]
    gold_path = reference("union")
    if sha(gold_path)!=previous["official_gold_sha"]:
        raise ValueError("different official Union reference")
    recs=[selected[i] for i in ids]
    with TemporaryDirectory(dir=ROOT) as folder:
        tmp=Path(folder)
        raw,_=raw_rows(gold_path,m2_blocks(gold_path),tmp)
        ev,xs,gs=[],[],[]
        for j,i in enumerate(ids):
            x,_,edits=raw[i]
            if surface(x)!=recs[j]["x"]:
                raise ValueError("source/reference mismatch")
            xs.append(x);gs.append({0:edits})
            v={y:per_candidate(y,x,edits) for y in {predictions[k][j] for k in names}}
            ev.append({"ngold":len(edits),"hyps":[v[predictions[k][j]] for k in names]})
        import levenshtein as official
        values={}
        for j,k in enumerate(names):
            v=aggregate([stats(r,r["hyps"][j]) for r in ev]).metrics()
            cli=official.batch_multi_pre_rec_f1(predictions[k],xs,gs,2,.5)
            if v["gold"]!=2250 or any(not math.isclose(v[m],q,abs_tol=1e-12,rel_tol=0)
                                      for m,q in zip(("P","R","F0.5"),cli,strict=True)):
                raise ValueError("pinned official micro counts disagree")
            if k!=MODEL:
                old=previous["scores"][k]
                if any(old[m]!=v[m] for m in v):
                    raise ValueError("same-sample baseline parity failed")
                v["GLEU_x100"]=old["GLEU_x100"]
            values[k]=v
        blobs=subprocess.check_output(["git","hash-object",str(GLEU/"metric/gleu.py"),
                                       str(GLEU/"metric/gleumodule.py")],text=True).splitlines()
        if blobs!=["42976ea997d5e8c3cf6d3abb5020732213cf0da3","ab280799d737f957ac244a04bde4e3f437259aa2"]:
            raise ValueError("wrong pinned GLEU")
        write_lines(tmp/"x", (r["x"] for r in recs)); write_lines(tmp/"y", (r["y"] for r in recs))
        write_lines(tmp/"h",predictions[MODEL])
        cmd=("import sys; sys.path.insert(0,sys.argv[1]); from metric.gleumodule import run_gleu; "
             "print(100*float(run_gleu(source=sys.argv[2],reference=sys.argv[3],hypothesis=sys.argv[4])))")
        g=float(subprocess.check_output([str(PYTHON),"-c",cmd,str(GLEU.resolve()),str(tmp/"x"),str(tmp/"y"),
                                         str(tmp/"h")],text=True))
        if not 0<=g<=100: raise ValueError("nonfinite or invalid GLEU")
        values[MODEL]["GLEU_x100"]=g
    contrasts={}
    for j,k in enumerate(names[1:],1):
        ci=lift_ci(ev,range(SIZE),recs,[j]*SIZE,[0]*SIZE)
        contrasts[k]={"delta_F0.5":values[MODEL]["F0.5"]-values[k]["F0.5"],"ci95":ci,
                      "delta_GLEU_x100":g-values[k]["GLEU_x100"]}
    save(ROOT/"summary.json",{"model":MODEL,"effort":"low","seal":stamp,"sample":previous["sample"],"n":SIZE,
         "gold_sha":sha(gold_path),"gold_edits":2250,"values":values,"haiku_minus":contrasts,
         "changed":sum(surface(y)!=r["x"] for r,y in zip(recs,predictions[MODEL],strict=True)),
         "empty":sum(not y.strip() for y in predictions[MODEL]),"first_450_then_text_only_550":True})
    fields=("correct","proposed","gold","P","R","F0.5","GLEU_x100")
    with (ROOT/"comparison.csv").open("x",encoding="utf-8",newline="") as f:
        w=csv.writer(f);w.writerow(["system","sample_n",*fields,"Haiku_minus_F0.5_points","ci_low_points","ci_high_points"])
        for k in names:
            v=values[k]; c=contrasts.get(k)
            w.writerow([k,SIZE,*[v[m] for m in fields],
                        *([100*c["delta_F0.5"],*[100*z for z in c["ci95"]]] if c else [None,None,None])])
    print({"scores":values[MODEL],"changed":read(ROOT/"summary.json")["changed"],"Haiku_minus_T5":contrasts["t5/r9"]})


if __name__=="__main__":
    os.umask(0o077)
    ap=argparse.ArgumentParser();ap.add_argument("stage",choices=("seal","score"));a=ap.parse_args()
    if a.stage=="seal":
        print(seal(create=True))
    else:
        scores()
