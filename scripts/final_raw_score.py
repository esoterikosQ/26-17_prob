"""Score the unchanged distributed M², including its paragraph boundaries and excluded offsets.

This post-seal adapter is for transfer domains only. It never regenerates decisions or edits a reference.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import os
import re
import runpy
import subprocess
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import Counts, aggregate, surface
from scripts.c_eval import lift_ci
from scripts.c_metric import per_candidate
from scripts.core_ablation_table import agree
from scripts.final_test import N, read, seal
from scripts.final_test_score import RESULT, annotate_test_pairs, open_gold, reference
from scripts.rehearsal_aux import edit_groups, typed_counts, typed_partition
from scripts.rehearsal_predict import save, sha, systems
from scripts.rehearsal_score import CONTRASTS, write_lines

CLI = Path("tools/m2scorer/scripts/m2scorer.py").resolve()


def official_annotations(path, tmp):
    # The pinned upstream CLI has no import guard. Load its real reader on a synthetic valid input.
    g, h = tmp/"fixture.m2", tmp/"fixture.txt"
    g.write_text("S a b\nA -1 -1|||noop|||-NONE-|||REQUIRED|||-NONE-|||0\n\n")
    h.write_text("a b\n")
    with patch.object(sys, "argv", [str(CLI),str(h),str(g)]), contextlib.redirect_stdout(io.StringIO()):
        loader = runpy.run_path(str(CLI))["load_annotation"]
    return loader(str(path))


def raw_rows(path, blocks, tmp):
    sys.path.insert(0, str(CLI.parent))
    from util import paragraphs
    sources, edits = official_annotations(path, tmp)
    sizes, excluded, repeated = Counter(), [], []
    records = []
    for para in paragraphs(path.read_text(encoding="utf-8").splitlines(keepends=True)):
        texts, events = [], []
        for row in para.splitlines():
            if row.startswith("S "):
                texts.append(row[2:].strip())
            elif row.startswith("A "):
                pos,t,cor,_,_,annotator = row[2:].split("|||")
                if annotator!="0" or not texts:
                    raise ValueError("unexpected raw annotation or source order")
                if t!="noop":
                    a,b=map(int,pos.split())
                    corr=[v.strip() if v!="-NONE-" else "" for v in cor.split("||")]
                    events.append((len(texts)-1,a,b,t,corr))
                    sizes["distributed_nonnoop"]+=1
                    if not 0<=a<=b<=len(texts[-1].split()):
                        excluded.append({"i":len(records)+len(texts)-1,"range":[a,b],"type":t})
            elif not row.startswith("I "):
                raise ValueError("unexpected raw M2 line")
        joined=" ".join(texts).split()
        limit=0
        if len(texts)>1:
            repeated.append(list(range(len(records),len(records)+len(texts))))
        for j,x in enumerate(texts):
            i=len(records)
            limit+=len(x.split())
            retained=[e for e in events if 0<=e[1]<=limit and 0<=e[2]<=limit]
            expected=[(a,b," ".join(joined[a:b]),corr) for _,a,b,_,corr in retained]
            if edits[i]!={0:expected} or sources[i]!=x or surface(blocks[i][0][2:])!=surface(x):
                raise ValueError(f"{i}: cannot attribute the pinned raw CLI's annotations")
            records.append((x, [e[3] for e in retained], edits[i][0]))
            sizes["official_effective"]+=len(retained)
    if len(records)!=len(sources) or len(records)!=len(blocks):
        raise ValueError("raw official source count mismatch")
    return records, {**sizes,"invalid_physical_edits":excluded,"multi_source_paragraphs":repeated}


def verify_cli(hyp, raw, evidence, j):
    run=subprocess.run([sys.executable,str(CLI),"-v",str(hyp),str(raw)],capture_output=True,text=True,check=True)
    totals={}
    for key,label in (("correct","correct"),("proposed","proposed"),("gold","gold")):
        cumulative=[int(x) for x in re.findall(rf"^# {label}\s*:\s*(\d+)$",run.stdout,re.M)]
        if len(cumulative)!=len(evidence):
            raise ValueError("raw CLI did not return one count per source")
        prev=0
        for i,(v,row) in enumerate(zip(cumulative,evidence,strict=True)):
            h=row["hyps"][j]
            target={"correct":len(h["tp"]),"proposed":h["proposed"],"gold":row["ngold"]}[key]
            if v-prev!=target:
                raise ValueError(f"{i}: official per-source {key} mismatch")
            prev=v
        totals[key]=prev
    vals=dict(re.findall(r"^(Precision|Recall|F_0\.5)\s*:\s*([0-9.]+)$",run.stdout,re.M))
    agree(Counts(**totals).metrics(),{k:float(vals[v]) for k,v in (("P","Precision"),("R","Recall"),("F0.5","F_0.5"))},
          "unaltered distributed M2 CLI")


def official_report(d, ps, recs, raw, parts, tmp):
    names=sorted(systems(d,False))
    ev=[]
    for p,(x,types,gold) in zip(ps,raw,strict=True):
        unique={y:per_candidate(y,x,gold) for y in set(p["predictions"].values())}
        ev.append({"ngold":len(gold),"types":types,"hyps":[unique[p["predictions"][k]] for k in names]})
    hyp=tmp/"system.txt"
    for j,k in enumerate(names):
        write_lines(hyp,(p["predictions"][k] for p in ps))
        verify_cli(hyp,reference(d),ev,j)
    result={}
    for part,ids in parts.items():
        totals={}; contrasts={}; audit={}
        denom=Counter(t for i in ids for t in ev[i]["types"])
        for j,k in enumerate(names):
            v=aggregate([Counts(len(ev[i]["hyps"][j]["tp"]),ev[i]["hyps"][j]["proposed"],ev[i]["ngold"]) for i in ids])
            hits=Counter(ev[i]["types"][idx] for i in ids for idx in ev[i]["hyps"][j]["tp"])
            totals[k]={**v.metrics(),"tp_by_reference_type":{t:{"tp":hits[t],"gold":n,"recall":hits[t]/n} for t,n in denom.items()}}
        for a,b in CONTRASTS:
            if a in names and b in names:
                contrasts[f"{b}-{a}"]={"delta":totals[b]["F0.5"]-totals[a]["F0.5"],
                        "ci95":lift_ci(ev,ids,recs,[names.index(a)]*len(ps),[names.index(b)]*len(ps))}
        for k in ("r9","r11"):
            z=Counter(e["z"] for i in ids for e in ps[i]["audit"][k])
            audit[k]={"changed_regions":z.total(),"map":dict(z),"unclassified":z["UNCLASSIFIED"],
                      "backoff_only":sum(e["backoff_only"] for i in ids for e in ps[i]["audit"][k])}
        result[part]={"n":len(ids),"metrics":totals,"paired":contrasts,"automatic_audit":audit}
    return result


def typed_report(d, ps, bs, parts, bad):
    names=sorted(systems(d,False))
    pairs=list(dict.fromkeys((surface(p["x"]),surface(p["predictions"][k])) for p in ps for k in names))
    out=RESULT/f"kagas.raw.{d}"
    out.mkdir()
    proposals,failed=annotate_test_pairs(out,pairs)
    evidence=[]; types=set()
    for i,(p,b) in enumerate(zip(ps,bs,strict=True)):
        if i in bad:
            evidence.append(dict.fromkeys(names))
            continue
        gold=edit_groups(b); types.update(gold[2]); row={}
        for k in names:
            pair=surface(p["x"]),surface(p["predictions"][k])
            if pair in failed:
                row[k]=None
            else:
                prop=proposals[pair]; types.update(prop[2]); row[k]=typed_counts(gold,prop)
        evidence.append(row)
    parts_out={}
    for part,ids in parts.items():
        typed={}; missing={}
        for k in names:
            typed[k],ix=typed_partition(evidence,ids,k,types)
            if ix: missing[k]=ix
        parts_out[part]={"n":len(ids),"typed":typed,"unavailable":missing}
    return {"partitions":parts_out,"invalid_reference_occurrences":sorted(bad),"distinct_pairs":len(pairs),
            "failed_distinct_pairs":len(failed),"required_occurrences":len(ps)*len(names),
            "annotator_failures":list(failed.values())}


def export_tables():
    fingerprint = seal()
    summary = read(RESULT/"summary.json")
    if summary["seal"] != fingerprint:
        raise ValueError("summary does not match the approved seal")
    with (RESULT/"test_scores.csv").open("x",encoding="utf-8",newline="") as a, \
            (RESULT/"test_contrasts.csv").open("x",encoding="utf-8",newline="") as b:
        cells, contrasts = csv.writer(a), csv.writer(b)
        fields = ("correct","proposed","gold","P","R","F0.5")
        cells.writerow(("domain","partition","n","system",*fields,"gleu_x100","typed_complete"))
        contrasts.writerow(("domain","partition","n","contrast","delta_F0.5","ci_low","ci_high"))
        for d in N:
            reports = {}
            for kind, digest in summary["domains"][d]["report_sha"].items():
                p = RESULT/f"{kind}.{d}.json"
                if sha(p) != digest:
                    raise ValueError("report differs from validated test summary")
                reports[kind] = read(p)["partitions"]
            for part,m in reports["m2"].items():
                g,t = reports["gleu"][part],reports["kagas"][part]
                if (m["n"]!=g["n"] or m["n"]!=t["n"] or
                        not m["metrics"].keys()==g["gleu_x100"].keys()==t["typed"].keys()):
                    raise ValueError("metric denominators or systems differ")
                for k,v in m["metrics"].items():
                    cells.writerow([d,part,m["n"],k,*[v[x] for x in fields],g["gleu_x100"][k],t["typed"][k] is not None])
                for k,v in m["paired"].items():
                    contrasts.writerow([d,part,m["n"],k,v["delta"],*v["ci95"]])


if __name__=="__main__":
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument("kind",choices=("m2","kagas","export")); p.add_argument("domain",nargs="?",choices=("lang8","union"))
    a=p.parse_args()
    if (a.domain is None)!=(a.kind=="export"):
        p.error("export covers all domains; other modes need a transfer domain")
    if a.kind=="export":
        export_tables()
        sys.exit(0)
    fingerprint,ps,recs,bs,parts=open_gold(a.domain)
    with TemporaryDirectory(dir=RESULT) as folder:
        tmp=Path(folder)
        raw,audit=raw_rows(reference(a.domain),bs,tmp)
        if a.kind=="m2":
            output={"partitions":official_report(a.domain,ps,recs,raw,parts,tmp),"official_cli_verified":True,
                    "verified_against":"unaltered raw CLI all per-source correct/proposed/gold"}
        else:
            output=typed_report(a.domain,ps,bs,parts,{r["i"] for r in audit["invalid_physical_edits"]})
    save(RESULT/f"{a.kind}.{a.domain}.json",{**output,"split":"test","domain":a.domain,"seal":fingerprint,
         "official_m2_sha":sha(reference(a.domain)),"annotation_audit":audit,"score_adapter_sha":sha(Path(__file__))})
