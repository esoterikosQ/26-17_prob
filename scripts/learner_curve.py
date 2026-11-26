"""Independent matched-budget experiment on Learner train/dev only. No frozen full-data GEC resources."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing as mp
import subprocess
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc import jamo
from hnc.align import Span, align_pair
from hnc.candidates_c import surface
from hnc.channel_edits import sentence_ops, substring_counts, substring_edits
from hnc.jamo_channel import JamoChannel, estimate
from hnc.morph import MorphAnalyzer
from hnc.morph_channel import MorphChannel, is_punct
from hnc.morph_lm import MorphLM, lm_tokens
from hnc.surface import split_punct
from scripts.core_ablation_controls import make_decoder
from scripts.decode import m2_blocks, m2score, write_m2
from scripts.extract_morph_edits import analyze_region
from scripts.population_dev import data_paths, is_selection
from scripts.rehearsal_pcfg import MODEL, score_texts
from scripts.rehearsal_predict import CAUSAL, line, rows, save, sha
from scripts.rehearsal_score import write_lines

ROOT = Path("outputs/learner_curve_v1")
TRAIN = Path("datasets/kor_gec/korean_learner_train.jsonl")
SOURCE = Path("data/kor_gec/korean_learner/korean_learner_val_original.txt")
FRACTIONS = (10, 25, 50, 100)
BASE = Path(".cache/huggingface/hub/models--skt--kobart-base-v1/snapshots/3276dc875c0c26e1a697808649d1ba506e5643a1")


def budgets(ids):
    if len(set(ids)) != len(ids) or not ids:
        raise ValueError("training IDs must be unique")
    ranked = sorted(ids, key=lambda key: hashlib.sha256(f"hnc-budget-v1:{key}".encode()).digest())
    return {f: set(ranked[:(len(ids)*f+50)//100]) for f in FRACTIONS}


def prepare():
    records = list(rows(TRAIN))
    if len(records) != 19898 or any(r["unit_id"] != f"korean_learner.train.{i}" or not r["x"].strip()
                                   or not r["y"].strip() for i, r in enumerate(records)):
        raise ValueError("unexpected Learner training inventory")
    ROOT.mkdir(exist_ok=False)
    for f, ids in budgets([r["unit_id"] for r in records]).items():
        out = ROOT/str(f)
        out.mkdir()
        with (out/"train.jsonl").open("x", encoding="utf-8") as w:
            for r in records:
                if r["unit_id"] in ids:
                    line(w, {k: r[k] for k in ("unit_id", "x", "y")})
        save(out/"budget.json", {"n": len(ids), "fraction": f, "train_sha": sha(out/"train.jsonl"), "parent_sha": sha(TRAIN)})


def subset(out):
    receipt = json.loads((out/"budget.json").read_text())
    data = list(rows(out/"train.jsonl"))
    if len(data) != receipt["n"] or sha(out/"train.jsonl") != receipt["train_sha"]:
        raise ValueError("supervised training budget changed")
    return data


def fit(out):
    records = subset(out)
    freq = Counter(core for r in records for w in r["y"].split() if (core := split_punct(w)[1]))
    ana = MorphAnalyzer(num_workers=3)
    uni, bi, edits, alpha = Counter(), Counter(), Counter(), Counter()
    regions, others = [], set()
    with (out/"lm.txt").open("x", encoding="utf-8") as lm:
        for s in range(0, len(records), 500):
            batch = records[s:s+500]
            ys = list(ana.kiwi.tokenize([r["y"] for r in batch]))
            for r, toks, xs in zip(batch, ys, ana.spans_many([r["x"] for r in batch]), strict=True):
                x, y = r["x"], r["y"]
                ys_spans = [(t.form, t.tag, t.start, t.start+t.len) for t in toks]
                lm.write(" ".join(lm_tokens(ana._to_morphs(y, toks)))+"\n")
                lt = "BOS"
                for t in toks:
                    if not is_punct((t.form, t.tag)):
                        uni[t.form, t.tag] += 1
                        if t.tag[0] in "JE":
                            bi[lt, t.form, t.tag] += 1
                        lt = t.tag
                sp = [Span(*v) for v in ys_spans]
                projected, start = [], 0
                for g in align_pair(y, x, sp):
                    text = g.x_seg
                    if g.kind == "edit":
                        es, z, _, _, _ = analyze_region(y, x, g, ys_spans, xs, lambda word: freq[word], frozenset())
                        regions.append({"edits": es})
                        if z != "SPELL":
                            text = g.y_seg
                    projected.extend((y[start:g.y_start], text))
                    start = g.y_end
                xp = "".join([*projected, y[start:]])
                ops = sentence_ops(y, align_pair(y, xp, sp))
                edits.update((a, b) for a, b, _ in substring_edits(ops))
                alpha.update(substring_counts([v for _, v, _ in ops if v]))
                others.update(set(jamo.decompose(x+y)) - set(jamo.ALPHABET))
    params = estimate(({"a": a, "b": b, "c": n} for (a, b), n in edits.items()),
                      ({"a": a, "c": n} for a, n in alpha.items()))
    morph = MorphChannel.fit(regions, uni, bi)
    for filename, text in (("jamo.json", params.to_json()), ("morph.json", morph.to_json())):
        with (out/filename).open("x", encoding="utf-8") as dest:
            dest.write(text)
    save(out/"other.json", sorted(others))
    save(out/"vocab.json", sorted(w for w, n in freq.items() if n >= 5))
    bins = Path("tools/kenlm/build/bin")
    with (out/"lm.txt").open("rb") as text, (out/"lm.arpa").open("xb") as arpa:
        subprocess.run([str(bins/"lmplz"), "-o", "5", "-S", "1G", "-T", ".cache/tmp", "--discount_fallback"],
                       stdin=text, stdout=arpa, check=True)
    subprocess.run([str(bins/"build_binary"), "trie", str(out/"lm.arpa"), str(out/"lm.bin")], check=True)
    save(out/"fit.json", {"train": sha(out/"train.jsonl"), "sentences": len(records), "morph_regions": len(regions),
                          "artifacts": {p: sha(out/p) for p in ("jamo.json", "morph.json", "vocab.json", "other.json", "lm.bin")}})


def one(x):
    h = _decoder.search_nbest(x, _decoder.prepare(x), n_best=8)
    if not h:
        raise ValueError("empty low-resource beam")
    return [{"text": c.text, "lm": c.lm, "ch": c.ch} for c in h]


def decode(out, workers):
    from hnc.jamo_channel import ChannelParams
    global _decoder
    fitted = json.loads((out/"fit.json").read_text())
    if any(sha(out/p) != value for p, value in fitted["artifacts"].items()):
        raise ValueError("fitted budget model changed")
    load = lambda p: json.loads((out/p).read_text())
    _decoder = make_decoder(set(load("vocab.json")), MorphLM(out/"lm.bin"), MorphAnalyzer(num_workers=1),
                            MorphChannel.from_json((out/"morph.json").read_text()),
                            JamoChannel(ChannelParams.from_json((out/"jamo.json").read_text()), load("other.json")))
    xs = SOURCE.read_text().splitlines()
    if len(xs) != 4264:
        raise ValueError("wrong development source")
    with mp.get_context("fork").Pool(workers) as pool, (out/"h8.jsonl").open("x", encoding="utf-8") as w:
        for i,h in enumerate(pool.imap(one, xs, chunksize=8)):
            line(w, {"i": i, "x": xs[i], "hyps": h})


def all_texts():
    return sorted({c["text"] for f in FRACTIONS for r in rows(ROOT/str(f)/"h8.jsonl") for c in r["hyps"]})


def priors(kind, workers):
    texts = all_texts()
    if kind == "pcfg":
        scores, _ = score_texts(texts, workers, Path("data/models/pcfg_best.sha256"))
        scores = {t: v for t, (v, _) in scores.items()}
    else:
        import torch
        from scripts.nlm_score import causal_scores
        vals = causal_scores(texts, str(CAUSAL), 64, "cuda", torch.bfloat16)
        scores = {}
        for t,v in zip(texts, vals, strict=True):
            if v is None or not math.isfinite(v["lp"]):
                raise ValueError("missing causal LM score")
            scores[t] = v["lp"]
    save(ROOT/f"{kind}.json", scores)


def evaluate():
    from scripts.c_metric import annotations, per_candidate, stats
    from scripts.c_eval import lift_ci
    from hnc.candidates_c import aggregate
    from scripts.core_ablation_table import agree
    sys.path.insert(0, str(Path("tools/m2scorer/scripts").resolve()))
    pcfg, causal = [json.loads((ROOT/f"{k}.json").read_text()) for k in ("pcfg", "causal")]
    dev, gold = data_paths("korean_learner")
    recs, blocks, src = list(rows(dev)), m2_blocks(gold), SOURCE.read_text().splitlines()
    ids = {"all_dev": list(range(len(src))), "check": [i for i,r in enumerate(recs) if not is_selection("korean_learner", r)]}
    report = []
    with TemporaryDirectory(dir=ROOT) as tmp:
        for f in FRACTIONS:
            out = ROOT/str(f)
            n = len(subset(out))
            neural = json.loads((out/"kobart.json").read_text())
            if neural["train_sha"] != sha(out/"train.jsonl") or neural["source_sha"] != sha(SOURCE):
                raise ValueError("seq2seq budget/source mismatch")
            ev = []
            for i,(x,r,b,h,s) in enumerate(zip(src,recs,blocks,rows(out/"h8.jsonl"),rows(out/"kobart.jsonl"),strict=True)):
                if (not surface(x)==surface(r["x"])==surface(b[0][2:]) or h["x"]!=x or s["x"]!=x or s["i"]!=i or h["i"]!=i):
                    raise ValueError("misaligned curve")
                hs=h["hyps"]
                def prior(c):
                    return c["lm"]-c["ch"]+.3*pcfg[c["text"]]
                choices=[max(hs,key=prior)["text"],max(hs,key=lambda c:prior(c)+causal[c["text"]])["text"],s["yhat"]]
                original,_,edits=annotations(b)
                ev.append({"ngold":len(edits),"hyps":[per_candidate(y,original,edits) for y in choices]})
            for part, ix in ids.items():
                gp,hp=Path(tmp)/"gold.m2",Path(tmp)/"hyp.txt"
                write_m2([blocks[i] for i in ix],gp)
                scalars={}
                for j,k in enumerate(("A","B","KoBART")):
                    v=aggregate([stats(ev[i],ev[i]["hyps"][j]) for i in ix]).metrics()
                    write_lines(hp,(ev[i]["hyps"][j]["text"] for i in ix))
                    agree(m2score(hp,gp),v,f"{f}/{part}/{k}")
                    scalars[k]=v
                ci=lift_ci(ev,ix,recs,[2]*len(ev),[1]*len(ev))
                report.append({"percent":f,"train_n":n,"part":part,"dev_n":len(ix),"scores":scalars,
                               "B_minus_KoBART":scalars["B"]["F0.5"]-scalars["KoBART"]["F0.5"],"ci95":ci})
    save(ROOT/"curve.json",{"split":"dev","seed":0,"parser_sha":sha(MODEL),"points":report})
    with (ROOT/"curve.csv").open("x",encoding="utf-8",newline="") as w:
        writer=csv.writer(w); writer.writerow(("percent","train_pairs","partition","n","system","P","R","F0.5"))
        for r in report:
            for k,v in r["scores"].items():
                writer.writerow([r["percent"],r["train_n"],r["part"],r["dev_n"],k,v["P"],v["R"],v["F0.5"]])


if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("stage",choices=("prepare","fit","decode","pcfg","causal","evaluate"))
    p.add_argument("--fraction",type=int,choices=FRACTIONS)
    p.add_argument("--workers",type=int,default=5)
    a=p.parse_args()
    if (a.stage in ("fit","decode")) != (a.fraction is not None) or a.workers<1:
        p.error("fit/decode require one budget; use positive workers")
    if a.stage=="prepare": prepare()
    elif a.stage=="fit": fit(ROOT/str(a.fraction))
    elif a.stage=="decode": decode(ROOT/str(a.fraction),a.workers)
    elif a.stage=="evaluate": evaluate()
    else: priors(a.stage,a.workers)
