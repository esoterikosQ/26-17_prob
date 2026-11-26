"""Fresh pretrained KoBART at each Learner supervision budget; fixed ten epochs, no validation selection."""

from __future__ import annotations

import argparse
import math
import random
import subprocess
import sys
from pathlib import Path

import torch
from transformers import AutoTokenizer, BartForConditionalGeneration, get_cosine_schedule_with_warmup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hnc.candidates_c import surface
from scripts.c_strong_generate import encode, inputs
from scripts.learner_curve import BASE, FRACTIONS, ROOT, SOURCE, subset
from scripts.rehearsal_predict import line, save, sha


def train(fraction):
    out = ROOT/str(fraction)
    if (out/"kobart.json").exists() or (out/"epochs.jsonl").exists():
        raise FileExistsError("a run at this budget already exists")
    base_lock = Path("data/low_resource_v1/base.sha256").resolve()
    subprocess.run(["sha256sum", "--quiet", "--strict", "-c", str(base_lock)], cwd=BASE, check=True)
    data = subset(out)
    random.seed(0)
    torch.manual_seed(0)
    torch.cuda.manual_seed_all(0)
    tok = AutoTokenizer.from_pretrained(BASE, local_files_only=True)
    model = BartForConditionalGeneration.from_pretrained(BASE, local_files_only=True, use_safetensors=True).to("cuda")
    if (tok.bos_token_id,tok.eos_token_id,tok.pad_token_id,model.config.decoder_start_token_id)!=(0,1,3,1):
        raise ValueError("wrong base tokenizer or BART config")
    wrap = tok("가")["input_ids"][-1] != tok.eos_token_id
    xs=encode(tok,[r["x"] for r in data],wrap)
    ys=encode(tok,[r["y"] for r in data],wrap,target=True)
    samples=SOURCE.read_text(encoding="utf-8").splitlines()
    dev=encode(tok,samples,wrap)
    if max(len(t) for seqs in (xs,ys,dev) for t in seqs)>model.config.max_position_embeddings:
        raise ValueError("model context exceeded; do not truncate or drop examples")
    norm = {id(v) for m in model.modules() if isinstance(m,torch.nn.LayerNorm) for v in m.parameters()}
    decay,nodecay=[],[]
    for name,p in model.named_parameters():
        (nodecay if name.endswith("bias") or id(p) in norm else decay).append(p)
    opt=torch.optim.AdamW([{"params":decay,"weight_decay":.01},{"params":nodecay,"weight_decay":0.}],lr=5e-5)
    steps=10*math.ceil(len(xs)/64)
    scheduler=get_cosine_schedule_with_warmup(opt,int(.1*steps),steps)
    rng=torch.Generator().manual_seed(0)
    with (out/"epochs.jsonl").open("x",encoding="utf-8") as log:
        for epoch in range(10):
            model.train()
            order=torch.randperm(len(xs),generator=rng).tolist()
            total=0.
            for start in range(0,len(order),64):
                ix=order[start:start+64]
                target=[ys[i] for i in ix]
                length=max(map(len,target))
                labels=torch.tensor([y+[-100]*(length-len(y)) for y in target],device="cuda")
                opt.zero_grad(set_to_none=True)
                with torch.autocast("cuda",dtype=torch.bfloat16):
                    loss=model(**inputs([xs[i] for i in ix]),labels=labels,use_cache=False).loss
                if not torch.isfinite(loss):
                    raise ValueError("nonfinite training loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                opt.step()
                scheduler.step()
                total+=loss.item()*len(ix)
            line(log,{"epoch":epoch+1,"sentences":len(order),"train_loss":total/len(order)})
            log.flush()
    model.eval()
    order=sorted(range(len(dev)),key=lambda i:-len(dev[i]))
    pred={}
    with torch.inference_mode(), torch.autocast("cuda",dtype=torch.bfloat16):
        for j in range(0,len(dev),64):
            ix=order[j:j+64]
            result=model.generate(**inputs([dev[i] for i in ix]),num_beams=4,do_sample=False,early_stopping=True,
                                  max_new_tokens=min(512,2*max(len(dev[i]) for i in ix)+10),use_cache=True)
            for i,y in zip(ix,tok.batch_decode(result[:,1:],skip_special_tokens=True),strict=True):
                pred[i]=surface(y)
    with (out/"kobart.jsonl").open("x",encoding="utf-8") as w:
        for i,x in enumerate(samples):
            line(w,{"i":i,"x":x,"yhat":pred[i]})
    model.save_pretrained(out/"checkpoint")
    save(out/"kobart.json",{"fraction":fraction,"train_n":len(xs),"train_sha":sha(out/"train.jsonl"),
         "epochs":10,"steps":steps,"seed":0,"base_lock":sha(base_lock),"base_revision":BASE.name,
         "source_sha":sha(SOURCE),"weights_sha":sha(out/"checkpoint/model.safetensors"),
         "pred_sha":sha(out/"kobart.jsonl")})
    print(f"{fraction}%: {len(xs)} training pairs, {steps} updates, {len(pred)} dev predictions",flush=True)


if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("fraction",type=int,choices=FRACTIONS)
    train(ap.parse_args().fraction)
