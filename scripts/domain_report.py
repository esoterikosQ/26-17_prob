"""Post-hoc Lang8/Union diagnostics on archived decisions. No training, generation or weight selection."""

from __future__ import annotations

import argparse
import csv
import math
import multiprocessing as mp
import sys
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import Counts, aggregate, surface
from scripts.c_edit_select import GROUPS, margin
from scripts.c_eval import candidate_score, lift_ci
from scripts.c_metric import matched_edits, per_candidate, proposal_evidence, stats
from scripts.c_strong_dev import ORDER, union_pairs
from scripts.decode import m2_blocks
from scripts.final_raw_score import raw_rows
from scripts.final_test import INPUT, N, read
from scripts.final_test_score import reference
from scripts.rehearsal_predict import require_source, rows, save, sha
from scripts.t5_followup import choices

DATA = Path("data/domain_report_v1")
OUT = Path("outputs/domain_report_v1")
CORPUS = Path("datasets/kor_gec")
SEALS = {"kobart": "9645165a3fbbaacc1ec57dd215067ab7f01d350c06722943e0cc0d6d3c545034",
         "t5": "26568e193078a38b36f272590e2ca08a40772b449a42151e2486b5b3717ff28e"}
NAMES = ("r9", "s_only", "r10", "r11")


def references(d):
    blocks = m2_blocks(reference(d))
    with TemporaryDirectory(dir=OUT) as tmp:
        aligned, audit = raw_rows(reference(d), blocks, Path(tmp))
    return aligned, audit


def examples(d):
    result = []
    for part in ORDER if d == "union" else (d,):
        for i,r in enumerate(rows(CORPUS/f"{part}_test.jsonl")):
            if r["unit_id"] != f"{part}.test.{i}":
                raise ValueError("component occurrence order changed")
            result.append({"x": surface(r["x"]), "y": surface(r["y"]), "component": part})
    if d == "union":
        pairs = union_pairs((INPUT/"gold/union_test.txt").read_text().splitlines())
        if any((r["x"], r["y"]) != xy for r,xy in zip(result, pairs, strict=True)):
            raise ValueError("Union does not match component source/reference occurrences")
    return result


def training(d):
    refs = defaultdict(set)
    n = 0
    for r in rows(CORPUS/f"{d}_train.jsonl"):
        refs[surface(r["x"])].add(surface(r["y"]))
        n += 1
    return n, refs


def overlap(refs, y):
    if not refs:
        return "unseen_x"
    if y not in refs:
        return "only_other_y"
    return "same_and_other_y" if len(refs) > 1 else "only_same_y"


def profile():
    report = {}
    for d in ORDER:
        n_train, targets = training(d)
        recs = examples(d)
        raw, audit = references(d)
        ys_train = set().union(*targets.values())
        by_x = defaultdict(Counter)
        counts, relation, types = Counter(), Counter(), Counter()
        for r,(x,labels,edits) in zip(recs,raw,strict=True):
            if surface(x) != r["x"]:
                raise ValueError("corpus profile source mismatch")
            x,y = r["x"],r["y"]
            relation[overlap(targets.get(x), y)] += 1
            by_x[x][y] += 1
            counts.update(sentences=1, src_tokens=len(x.split()), target_tokens=len(y.split()),
                          identity=x==y, target_in_train=y in ys_train, edits=len(edits), zero_edits=not edits,
                          at_least_three_edits=len(edits)>=3,
                          multiword_edits=sum(b-a>1 or any(len(z.split())>1 for z in corr) for a,b,_,corr in edits))
            types.update(labels)
        counts["same_test_source_multiple_targets_occurrences"] = sum(v.total() for v in by_x.values() if len(v)>1)
        counts["deterministic_exact_match_ceiling_n"] = sum(max(v.values()) for v in by_x.values())
        report[d] = {"train": n_train, "train_unique_x": len(targets), "test_unique_x": len(by_x),
                     "counts": dict(counts), "overlap": dict(relation), "types": dict(types), "annotation": audit}
    save(OUT/"corpus.json", report)


def archived(family,d):
    root = DATA/family
    if sha(root/"seal.json") != SEALS[family]:
        raise ValueError("wrong experiment seal")
    lock = read(root/"seal.json")["sha256"]
    p = root/d if family == "kobart" else root/"test"/d
    pred = p/("predictions.full.jsonl" if family=="kobart" else "predictions.jsonl")
    for f in (pred,p/"features.jsonl",p/"causal.jsonl"):
        if sha(f) != lock[str(f.relative_to(root))]:
            raise ValueError(f"candidate inputs differ from sealed {family}/{d}")
    official = read(root/"scores"/f"m2.{d}.json")
    if official["seal"] != SEALS[family] or official["official_m2_sha"] != sha(reference(d)):
        raise ValueError("official score belongs to a different run or gold file")
    return p,pred,official["partitions"]


def jobs(family,d,p,pred,recs,raw):
    causal = {r["text"]:r["lp"] for r in rows(p/"causal.jsonl")}
    for i,(f,decision,r,(x,types,gold)) in enumerate(zip(rows(p/"features.jsonl"),rows(pred),recs,raw,strict=True)):
        src = {k:f[k] for k in ("x","i","unit_id")}
        require_source(src,d,i,"test")
        if any(decision[k]!=v for k,v in src.items()) or not surface(x)==r["x"]==surface(f["x"]):
            raise ValueError("source mismatch")
        hs = [{k:h[k] for k in ("text","s","h","s2s","ch","lm")} for h in f["hyps"]]
        for h in hs:
            h["causal"] = causal[h["text"]]
            h["prior"] = .5*h["lm"]+h["causal"]
        js = choices(hs)
        if (js != decision["c_choices" if family=="kobart" else "choices"] or
                any(hs[j]["text"]!=decision["predictions"][k] for k,j in js.items())):
            raise ValueError(f"{family}/{d}/{i}: not the original decisions")
        yield i,x,types,gold,hs,js


def measure(job):
    i,x,types,gold,hs,js = job
    metric = [dict(per_candidate(h["text"],x,gold), s=h["s"], h=h["h"]) for h in hs]
    rank = sorted(range(len(hs)),key=lambda j:(-candidate_score(hs[j],.5),hs[j]["text"]))
    win = js["r11"]
    if rank[0] != win:
        raise ValueError("winner differs from frozen score")
    terms = {}
    for t in set().union(*(h["tp"] for h in metric)) - set(metric[win]["tp"]):
        rival = next(j for j in rank if t in metric[j]["tp"])
        m = margin(hs[win],hs[rival])
        mx = max(m[g] for g in GROUPS)
        largest = [g for g in GROUPS if math.isclose(m[g],mx,abs_tol=1e-10,rel_tol=0)]
        terms[t] = largest[0] if len(largest)==1 and m["total"]>1e-10 else "tie"
    return {"i":i, "types":types, "ngold":len(gold), "hyps":metric, "js":js, "terms":terms}


def sums(metric,ids,k):
    return aggregate([stats(metric[i],metric[i]["hyps"][metric[i]["js"][k]]) for i in ids]).metrics()


def partition(metric,recs,ids,expected):
    proposal = proposal_evidence(metric,recs,ids)
    fate = defaultdict(Counter)
    switches = Counter()
    for i in ids:
        r = metric[i]
        hs,js = r["hyps"],r["js"]
        s,h = [matched_edits([c for c in hs if c[field] is not None]) for field in ("s","h")]
        tp = {k:set(hs[j]["tp"]) for k,j in js.items()}
        if js["s_only"]!=js["r11"]:
            w = hs[js["r11"]]
            if w["s"] is not None or w["h"] is None:
                raise ValueError("adding H changed the ranking within S")
            switches["to_H_only"] += 1
            switches["to_source_copy"] += w["text"] == recs[i]["x"]
        for idx,typ in enumerate(r["types"]):
            v = fate[typ]
            before,after = idx in tp["r9"],idx in tp["r11"]
            v.update(gold=1, absent=idx not in s|h, covered_missed=idx in s|h and not after, hit=after,
                     h_only=idx in h-s, r9_hit=before, s_hit=idx in tp["s_only"],
                     gained_vs_r9=after and not before,lost_vs_r9=before and not after,
                     gained_vs_s=after and idx not in tp["s_only"],lost_vs_s=idx in tp["s_only"] and not after)
            if idx in r["terms"]:
                v[r["terms"][idx]] += 1
    total = sum(fate.values(),Counter())
    for v in [*fate.values(),total]:
        if (v["gold"]!=v["absent"]+v["covered_missed"]+v["hit"] or
                v["hit"]-v["r9_hit"]!=v["gained_vs_r9"]-v["lost_vs_r9"] or
                v["hit"]-v["s_hit"]!=v["gained_vs_s"]-v["lost_vs_s"] or
                v["covered_missed"]!=sum(v[g] for g in (*GROUPS,"tie"))):
            raise ValueError("edit accounting does not close")
    results = {k:sums(metric,ids,k) for k in NAMES}
    if expected["n"]!=len(ids) or any(v[key]!=expected["metrics"][k][key] for k,v in results.items() for key in v):
        raise ValueError("diagnostic disagrees with archived official M2")
    for k,field in (("r9","r9_hit"),("s_only","s_hit"),("r11","hit")):
        official = expected["metrics"][k]["tp_by_reference_type"]
        if (set(fate)!=set(official) or any(v["gold"]!=official[t]["gold"] or v[field]!=official[t]["tp"]
                                           for t,v in fate.items())):
            raise ValueError("type denominator or TP mismatch")
    if total["absent"]!=proposal["gold_edit"]["neither"] or results["r11"]["F0.5"]>proposal["oracle_m2"]["union"]["F0.5"]:
        raise ValueError("coverage or oracle mismatch")
    return {"n":len(ids),"scores":results,"fate":dict(total),"by_type":dict(fate),"proposal":proposal,"switches":dict(switches)}


def run_pools(family,d,workers):
    p,pred,expected = archived(family,d)
    recs = examples(d)
    raw,_ = references(d)
    if len(recs)!=N[d]:
        raise ValueError("wrong test size")
    with mp.get_context("fork").Pool(workers) as pool:
        ev = list(pool.imap(measure,jobs(family,d,p,pred,recs,raw),chunksize=12))
    if len(ev)!=len(recs) or any(r["i"]!=i for i,r in enumerate(ev)):
        raise ValueError("incomplete candidate analysis")
    all_ids = list(range(len(recs)))
    groups = {"all_test":all_ids}
    if d=="union":
        for k in ORDER:
            groups[k] = [i for i,r in enumerate(recs) if r["component"]==k]
        train_y=set().union(*training("native")[1].values())
        groups["native_novel"]=[i for i in groups["native"] if recs[i]["y"] not in train_y]
    parts={k:partition(ev,recs,ids,expected[k]) for k,ids in groups.items()}
    overlap_sets={}
    if d!="union":
        targets=training(d)[1]
        ix=defaultdict(list)
        for i,r in enumerate(recs):
            ix[overlap(targets.get(r["x"]),r["y"])].append(i)
        for k,ids in ix.items():
            scores={}
            for name in ("r9","r11"):
                ys=[ev[i]["hyps"][ev[i]["js"][name]]["text"] for i in ids]
                alt=sum(y!=recs[i]["y"] and y in targets.get(recs[i]["x"],()) for i,y in zip(ids,ys,strict=True))
                scores[name]={**sums(ev,ids,name),"other_train_target_exact":alt}
            overlap_sets[k]={"n":len(ids),"scores":scores}
    save(OUT/f"{family}.{d}.json",{"seal":SEALS[family],"gold_sha":sha(reference(d)),
         "partitions":parts,"by_train_relation":overlap_sets})
    print(f"{family}/{d}: {len(ev)} verified occurrences, {parts['all_test']['proposal']['union']} candidates",flush=True)


def mix_terms(parts):
    before,after=[parts["all_test"]["scores"][k] for k in ("r9","r11")]
    den=4*after["proposed"]+after["gold"]
    result={}
    for d in ORDER:
        v=parts[d]["scores"]
        a,b=v["r9"],v["r11"]
        if a["gold"]!=b["gold"]:
            raise ValueError("a paired comparison must keep reference counts fixed")
        dt,dp=b["correct"]-a["correct"],b["proposed"]-a["proposed"]
        result[d]={"tp_delta":dt,"proposed_delta":dp,"fp_delta":dp-dt,
                   "contribution_points":100*(5*dt-4*before["F0.5"]*dp)/den}
    for k,overall in (("r9",before),("r11",after)):
        if any(sum(parts[d]["scores"][k][field] for d in ORDER)!=overall[field] for field in ("correct","proposed","gold")):
            raise ValueError("Union component micro counts do not add up")
    if not math.isclose(sum(v["contribution_points"] for v in result.values()),100*(after["F0.5"]-before["F0.5"]),abs_tol=1e-10):
        raise ValueError("Union F0.5 accounting does not close")
    return result


def standardized_recall(learner,lang8,k):
    labels={"PARTICLE","ENDING","SPELL","WS","INSERTION","DELETION"}
    hit={"r9":"r9_hit","r11":"hit"}[k]
    def group(data):
        out=defaultdict(Counter)
        for typ,v in data["by_type"].items():
            out[typ if typ in labels else "OTHER"].update({"n":v["gold"],"tp":v[hit]})
        return out
    a,b=group(learner),group(lang8)
    if set(a)!=set(b):
        raise ValueError("cannot standardize across groups without common support")
    n=sum(v["n"] for v in b.values())
    matched=sum(v["n"]/n*a[t]["tp"]/a[t]["n"] for t,v in b.items())
    return {"learner":learner["scores"][k]["R"],"learner_under_lang8_type_mix":matched,"lang8":lang8["scores"][k]["R"]}


def same_reference(family):
    sys.path.insert(0,str(Path("tools/m2scorer/scripts").resolve()))
    _,pred,_=archived(family,"union")
    combined=iter(rows(pred))
    views={}; pooled={k:[] for k in ("r9","r11")}
    for d in ORDER:
        recs=examples(d); aligned,_=references(d)
        direct=list(rows(archived(family,d)[1])) if d in N else None
        v=[]
        for i,(r,(x,t,edits)) in enumerate(zip(recs,aligned,strict=True)):
            u=next(combined)
            if surface(x)!=r["x"] or surface(u["x"])!=r["x"] or (direct and surface(direct[i]["x"])!=r["x"]):
                raise ValueError("component sources differ")
            ys=[u["predictions"][k] for k in ("r9","r11")]
            if direct:
                ys.extend(direct[i]["predictions"][k] for k in ("r9","r11"))
            cache={y:per_candidate(y,x,edits) for y in set(ys)}
            v.append({"ngold":len(t),"hyps":[cache[y] for y in ys]})
        entry={}
        for j,k in enumerate(("r9","r11")):
            c=aggregate([stats(row,row["hyps"][j]) for row in v])
            pooled[k].append(c); entry[k]={"union":c.metrics()}
            if direct:
                local=aggregate([stats(row,row["hyps"][2+j]) for row in v]).metrics()
                original=read(OUT/f"{family}.{d}.json")["partitions"]["all_test"]["scores"][k]
                if local!=original: raise ValueError("source-matched individual metric mismatch")
                entry[k].update(individual=local,union_minus_individual=c.f-local["F0.5"],
                                paired_ci95=lift_ci(v,range(len(v)),recs,[2+j]*len(v),[j]*len(v)))
        views[d]=entry
    if next(combined,None) is not None:
        raise ValueError("extra Union prediction")
    return {"paired_by_source":views,"separate_reference_micro":{k:aggregate(v).metrics() for k,v in pooled.items()}}


def finish():
    columns=("gold","absent","covered_missed","hit","h_only","r9_hit","s_hit","gained_vs_r9","lost_vs_r9",
             "gained_vs_s","lost_vs_s",*GROUPS,"tie")
    analysis={}
    with (OUT/"edit_fates.csv").open("x",encoding="utf-8",newline="") as f:
        w=csv.writer(f); w.writerow(["family","model_domain","partition","reference_type",*columns])
        for family in SEALS:
            models={d:read(OUT/f"{family}.{d}.json")["partitions"] for d in N}
            for d,parts in models.items():
                for part,p in parts.items():
                    for t,v in {"__ALL__":p["fate"],**p["by_type"]}.items():
                        w.writerow([family,d,part,t,*[v.get(c,0) for c in columns]])
            analysis[family]={"union_contributions":mix_terms(models["union"]),"recall_mix":{
                k:standardized_recall(models["korean_learner"]["all_test"],models["lang8"]["all_test"],k)
                for k in ("r9","r11")},**same_reference(family)}
    save(OUT/"cross_domain.json",analysis)


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("stage",choices=("profile","pool","finish"))
    ap.add_argument("--family",choices=SEALS); ap.add_argument("--domain",choices=N)
    ap.add_argument("--workers",type=int,default=8)
    args=ap.parse_args()
    if ((args.family is not None)!=(args.stage=="pool") or
            (args.domain is not None)!=(args.stage=="pool") or args.workers<1):
        ap.error("pool requires both a family and a domain; use positive workers")
    OUT.mkdir(exist_ok=True)
    if args.stage=="profile": profile()
    elif args.stage=="pool": run_pools(args.family,args.domain,args.workers)
    else: finish()
