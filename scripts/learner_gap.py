"""Post-primary diagnosis using archived development choices only, without human or test records."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import Counts, surface
from scripts.c_edit_select import GROUPS, margin
from scripts.c_eval import candidate_score, pick
from scripts.c_metric import annotations, per_candidate
from scripts.decode import m2_blocks
from scripts.final_test import DEV_SEAL, GATE
from scripts.population_dev import data_paths, is_selection
from scripts.rehearsal_predict import rows, save, sha

INPUT = Path("data/learner_gap_v1")
OUT = Path("outputs/learner_gap_v1")
DOMAIN = "korean_learner"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run():
    for folder in ("proposal","edit","controls"):
        subprocess.run(["sha256sum","--quiet","--strict","-c","INPUTS.sha256"],cwd=INPUT/folder,check=True)
    if sha(GATE/"seal.full.json") != DEV_SEAL:
        raise ValueError("wrong dev rehearsal")
    lock=read(GATE/"seal.full.json")["sha256"]
    for file in ("features.jsonl","causal.jsonl","predictions.full.jsonl"):
        if sha(GATE/DOMAIN/file) != lock[f"{DOMAIN}/{file}"]:
            raise ValueError("archived dev choices/features changed")
    rp,gp=data_paths(DOMAIN)
    recs=list(rows(rp)); blocks=m2_blocks(gp)
    selected=[i for i,r in enumerate(recs) if not is_selection(DOMAIN,r)]
    ev={r["i"]:r for r in rows(INPUT/"proposal"/DOMAIN/"check_edits.jsonl")}
    if sorted(ev)!=selected or len(selected)!=2133:
        raise ValueError("wrong dev check population")
    previous=read(INPUT/"proposal"/DOMAIN/"summary.json")["partitions"]["check"]
    controls=read(INPUT/"controls/summary.json")["partitions"]["check"]
    select_only=read(INPUT/"edit"/DOMAIN/"summary.json")["partitions"]["select"]
    causal={r["text"]:r["lp"] for r in rows(GATE/DOMAIN/"causal.jsonl")}
    total=defaultdict(Counter); by_type=defaultdict(Counter); dominant=defaultdict(Counter)
    variant_flags=Counter()
    for i,(r,f,p,b) in enumerate(zip(recs,rows(GATE/DOMAIN/"features.jsonl"),rows(GATE/DOMAIN/"predictions.full.jsonl"),
                                      blocks,strict=True)):
        if i not in ev:
            continue
        e=ev[i]; x,types,gold=annotations(b)
        if (r["unit_id"]!=e["unit_id"] or f["i"]!=i or p["i"]!=i or surface(f["x"])!=x
                or surface(r["x"])!=x or types!=e["types"]):
            raise ValueError("development identity mismatch")
        hs=f["hyps"]
        for h,m in zip(hs,e["hyps"],strict=True):
            if (h["s"],h["h"])!=(m["s"],m["h"]):
                raise ValueError("archived candidate order changed")
            v=per_candidate(h["text"],x,gold)
            if v["tp"]!=m["tp"] or v["proposed"]!=m["proposed"]:
                raise ValueError("reference-edit identity mismatch")
            h.update(causal=causal[h["text"]],prior=.5*h["lm"]+causal[h["text"]],tp=set(m["tp"]),proposed=m["proposed"])
        winners={"archived":next(j for j,h in enumerate(hs) if h["s"]==0),"s_fixed":pick(hs,"s",.5),
                 "union_fixed":pick(hs,"union",.5),"union_no_s2s":pick(hs,"union",0.)}
        for k,field in (("union_no_channel","ch"),("no_lm_prior","prior")):
            winners[k]=pick([{**h,field:0.} for h in hs],"union",.5)
        if any(e["chosen"][k]!=j for k,j in winners.items() if k in e["chosen"]):
            raise ValueError("original frozen decisions changed")
        w=hs[winners["union_fixed"]]
        if w["text"]!=p["predictions"]["r11"]:
            raise ValueError("winner not reproduced")
        rank=sorted(range(len(hs)),key=lambda j:(-candidate_score(hs[j],.5),hs[j]["text"]))
        for k,j in winners.items():
            h=hs[j]
            total[k].update(correct=len(h["tp"]),proposed=h["proposed"],gold=len(gold))
        for t,typ in enumerate(types):
            c=by_type[typ]; c["gold"]+=1
            for name,j in winners.items():
                c[name]+=t in hs[j]["tp"]
            rivals=[j for j in rank if t in hs[j]["tp"]]
            if not rivals:
                c["absent"]+=1
            elif t not in w["tp"]:
                c["covered_missed"]+=1
                v=margin(w,hs[rivals[0]])
                if v["total"] < -1e-8:
                    raise ValueError("candidate covering the reference outranks its winner")
                groups=[g for g in GROUPS if math.isclose(v[g],max(v[o] for o in GROUPS),rel_tol=0,abs_tol=1e-10)]
                key=groups[0] if len(groups)==1 and v["total"]>1e-10 else "tie"
                dominant[typ][key]+=1
        if w["proposed"]>len(w["tp"]):
            variant_flags["sentences_with_reference_unmatched_proposals"]+=1
            variant_flags["such_sentences_with_MAP_VARIANT_region"]+=any(v["z"]=="VARIANT" for v in w["edits"])
    scores={k:Counts(c["correct"],c["proposed"],c["gold"]).metrics() for k,c in total.items()}
    if any(scores[k]!=previous["systems"][k] for k in scores if k in previous["systems"]):
        raise ValueError("historical sufficient statistics disagree")
    fate=Counter()
    for typ,c in by_type.items():
        old=previous["by_type"][typ]
        if ((c["gold"],c["absent"],c["covered_missed"],c["union_fixed"])
                !=(old["gold"],old["neither"],old["covered_not_selected"],old["union_fixed"])):
            raise ValueError(f"{typ}: coverage decomposition disagrees with archive")
        fate.update(c)
    OUT.mkdir(exist_ok=False)
    with (OUT/"by_type.csv").open("x",encoding="utf-8",newline="") as w:
        cols=["gold","absent","covered_missed",*scores.keys()]
        writer=csv.writer(w); writer.writerow(["gold_type",*cols,*GROUPS,"tie"])
        for typ,c in sorted(by_type.items()):
            writer.writerow([typ,*[c[k] for k in cols],*[dominant[typ][k] for k in (*GROUPS,"tie")]])
    leaders=sum(dominant.values(),Counter())
    if leaders.total()!=fate["covered_missed"]:
        raise ValueError("one dominant-term classification per covered missed edit required")
    save(OUT/"summary.json",{"split":"dev/check","sentences":len(selected),"dev_seal":DEV_SEAL,
         "fates":dict(fate),"scores":scores,"dominant_term_for_covered_misses":dict(leaders),
         "oracle":previous["proposal"]["oracle_m2"],"source_coverage":previous["proposal"]["gold_edit"],
         "pool_switches":previous["switches"],"S_to_union_gained":previous["all_edits"]["gained"],
         "S_to_union_lost":previous["all_edits"]["lost"],"possible_variation_not_human_labels":dict(variant_flags),
         "separate_A_layer_hits":controls["type_hits"],"dev_selection_only":select_only,
         "input_manifests":{f:sha(INPUT/f/"INPUTS.sha256") for f in ("proposal","edit","controls")}})
    print("Completed dev-only Learner diagnosis",len(selected),fate["gold"],dict(leaders),flush=True)


if __name__=="__main__":
    run()
