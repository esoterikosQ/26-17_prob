"""Post-seal evaluation of the four T5-dependent rows. Source attribution is never a model input."""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface
from scripts.c_strong_dev import ORDER, union_pairs
from scripts.decode import m2_blocks
from scripts.final_raw_score import official_report, raw_rows, typed_report
from scripts.final_test import INPUT, N, read
from scripts.final_test_score import reference
from scripts.rehearsal_predict import C_NAMES, rows, save, sha
from scripts.rehearsal_score import write_lines
from scripts.t5_followup import COMPARISONS, ROOT, seal, source_beam

OUT=ROOT/"scores"


def open_test(d):
    stamp=seal()
    src,_=source_beam(d,"test")
    ps=list(rows(ROOT/"test"/d/"predictions.jsonl"))
    recs,blocks=[],[]
    for component in (ORDER if d=="union" else (d,)):
        path=Path("datasets/kor_gec")/f"{component}_test.jsonl"
        batch=m2_blocks(reference(component))
        for j,(r,b) in enumerate(zip(rows(path),batch,strict=True)):
            if r["unit_id"]!=f"{component}.test.{j}" or surface(r["x"])!=surface(b[0][2:]):
                raise ValueError(f"{component}/{j}: official test identity mismatch")
            recs.append({"x":r["x"],"y":r["y"],"component":component})
        blocks.extend(batch)
    if d=="union":
        if blocks!=m2_blocks(reference(d)):
            raise ValueError("Union source-ordered annotation blocks differ")
        distributed=union_pairs((INPUT/"gold/union_test.txt").read_text().splitlines())
        if any((surface(r["x"]),surface(r["y"]))!=pair for r,pair in zip(recs,distributed,strict=True)):
            raise ValueError("Union source/reference order differs from component mix")
    for r,p,x in zip(recs,ps,src,strict=True):
        if set(p["predictions"])!=set(C_NAMES) or any(p[k]!=v for k,v in x.items()) or surface(x["x"])!=surface(r["x"]):
            raise ValueError("sealed T5 observations do not match official sources")
    if len(ps)!=N[d]:
        raise ValueError("incomplete test")
    parts={"all_test":list(range(len(ps)))}
    if d=="union":
        for group in ORDER:
            parts[group]=[i for i,r in enumerate(recs) if r["component"]==group]
        seen={surface(r["y"]) for r in rows(Path("datasets/kor_gec/native_train.jsonl"))}
        parts["native_novel"]=[i for i in parts["native"] if surface(recs[i]["y"]) not in seen]
    if not all(parts.values()):
        raise ValueError("empty T5 evaluation partition")
    return stamp,ps,recs,blocks,parts


def gleu(ps,recs,parts):
    # This stage runs in the pinned metric interpreter, not the neural environment.
    package=Path("tools/gleu_final_v1").resolve()
    blobs=subprocess.check_output(["git","hash-object",str(package/"metric/gleu.py"),str(package/"metric/gleumodule.py")],
                                  text=True).splitlines()
    if blobs!=["42976ea997d5e8c3cf6d3abb5020732213cf0da3","ab280799d737f957ac244a04bde4e3f437259aa2"]:
        raise ValueError("GLEU source changed")
    sys.path.insert(0,str(package))
    from metric.gleumodule import run_gleu
    result={}
    with TemporaryDirectory(dir=OUT) as folder:
        p=Path(folder)
        for name,ids in parts.items():
            write_lines(p/"src",(ps[i]["x"] for i in ids))
            write_lines(p/"ref",(recs[i]["y"] for i in ids))
            values={}
            for k in C_NAMES:
                write_lines(p/"hyp",(ps[i]["predictions"][k] for i in ids))
                values[k]=100*float(run_gleu(source=p/"src",reference=p/"ref",hypothesis=p/"hyp"))
                if not 0<=values[k]<=100:
                    raise ValueError("invalid GLEU")
            result[name]={"n":len(ids),"gleu_x100":values}
    return {"partitions":result,"evaluator_blobs":blobs}


def summary():
    stamp=seal()
    domains={}
    with (OUT/"test_scores.csv").open("x",encoding="utf-8",newline="") as f, \
            (OUT/"paired.csv").open("x",encoding="utf-8",newline="") as pairs:
        scores,cis=csv.writer(f),csv.writer(pairs)
        fields=("correct","proposed","gold","P","R","F0.5")
        scores.writerow(("domain","partition","sentences","system",*fields,"gleu_x100","typed_complete"))
        cis.writerow(("domain","partition","contrast","delta_F0.5","ci_low","ci_high"))
        for d in N:
            reports={}; hashes={}
            for m in ("m2","gleu","kagas"):
                p=OUT/f"{m}.{d}.json"
                v=read(p)
                if v["seal"]!=stamp or v["domain"]!=d or v["split"]!="test":
                    raise ValueError("mixed results or seals")
                reports[m]=v["partitions"];hashes[m]=sha(p)
            m,g,t=(reports[k] for k in ("m2","gleu","kagas"))
            if not m.keys()==g.keys()==t.keys():
                raise ValueError("inconsistent test subsets")
            for part,group in m.items():
                if (not group["n"]==g[part]["n"]==t[part]["n"] or
                        not group["metrics"].keys()==g[part]["gleu_x100"].keys()==t[part]["typed"].keys()==set(C_NAMES)):
                    raise ValueError("unequal T5 metric/system denominators")
                for k in C_NAMES:
                    a=group["metrics"][k]
                    scores.writerow([d,part,group["n"],k,*[a[n] for n in fields],g[part]["gleu_x100"][k],t[part]["typed"][k] is not None])
                for k,v in group["paired"].items():
                    cis.writerow([d,part,k,v["delta"],*v["ci95"]])
            allm=m["all_test"]
            domains[d]={"n":allm["n"],"m2":{k:{v:a[v] for v in fields} for k,a in allm["metrics"].items()},
                        "paired":allm["paired"],"gleu_x100":g["all_test"]["gleu_x100"],
                        "missing_typed_cells":{p:sorted(v["unavailable"]) for p,v in t.items()},"report_sha":hashes}
    save(OUT/"summary.json",{"experiment":"t5_followup_v1","seal":stamp,"post_primary":True,"domains":domains})


if __name__=="__main__":
    os.umask(0o077)
    ap=argparse.ArgumentParser()
    ap.add_argument("metric",choices=("m2","gleu","kagas","summary"))
    ap.add_argument("domain",nargs="?",choices=N)
    a=ap.parse_args()
    if (a.domain is None)!=(a.metric=="summary"):
        ap.error("summary covers all domains; other stages require a domain")
    if a.metric=="summary":
        summary()
    else:
        s,ps,recs,blocks,parts=open_test(a.domain)
        OUT.mkdir(exist_ok=True)
        if a.metric=="gleu":
            result=gleu(ps,recs,parts)
        else:
            with TemporaryDirectory(dir=OUT) as folder:
                tmp=Path(folder)
                raw,audit=raw_rows(reference(a.domain),blocks,tmp)
                if audit["official_effective"]!={"korean_learner":8878,"lang8":38658,"union":52073}[a.domain]:
                    raise ValueError("changed distributed reference denominator")
                if a.metric=="m2":
                    result={"partitions":official_report(a.domain,ps,recs,raw,parts,tmp,names=C_NAMES,comparisons=COMPARISONS),
                            "official_raw_cli_verified_per_source":True}
                else:
                    result=typed_report(a.domain,ps,blocks,parts,{v["i"] for v in audit["invalid_physical_edits"]},
                                        names=C_NAMES,output=OUT)
                result["annotation_audit"]=audit
        save(OUT/f"{a.metric}.{a.domain}.json",{**result,"domain":a.domain,"split":"test","seal":s,
                                               "official_m2_sha":sha(reference(a.domain))})
