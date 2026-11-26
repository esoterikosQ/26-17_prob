"""Fresh pko-t5-large hypotheses and conditional log probabilities in the original gec2 T5 runtime."""

from __future__ import annotations

import math
from importlib.metadata import version

import torch
from transformers import AutoTokenizer, T5ForConditionalGeneration
from transformers.modeling_outputs import BaseModelOutput

from hnc.candidates_c import surface, union_pool
from scripts.c_kobart import seq_logprob
from scripts.rehearsal_predict import line, rows, save, sha
from scripts.t5_followup import DATA, ROOT, RUNTIME, SETUP, model_receipt, settings, source_beam


def tokenize(tok, texts, *, target=False):
    enc = tok(text_target=texts) if target else tok(texts)
    ids = enc["input_ids"]
    if len(ids)!=len(texts) or any(not seq or seq[-1]!=tok.eos_token_id for seq in ids):
        raise ValueError("T5 requires its native EOS-terminated tokenization, including the empty target")
    return ids


def padded(seqs, value, device):
    return torch.nn.utils.rnn.pad_sequence([torch.tensor(s,dtype=torch.long) for s in seqs],
                                           batch_first=True,padding_value=value).to(device)


def encoder_inputs(xs,device):
    return {"input_ids":padded(xs,0,device),"attention_mask":padded([[1]*len(x) for x in xs],0,device)}


def conditional(model, hidden, mask, owners, ys):
    ids = torch.tensor(owners,device=hidden.device)
    labels = padded(ys,-100,hidden.device)
    shifted = model.prepare_decoder_input_ids_from_labels(labels=labels)
    scores = model(encoder_outputs=BaseModelOutput(last_hidden_state=hidden.index_select(0,ids)),
                   attention_mask=mask.index_select(0,ids),decoder_input_ids=shifted,use_cache=False).logits
    values = seq_logprob(scores,labels).tolist()
    if not all(math.isfinite(v) and v <= 0 for v in values):
        raise ValueError("nonfinite or positive T5 sequence log probability")
    return values


def verify_conditional(tok,model,x):
    ys = tokenize(tok,["가 나",""],target=True)
    xs = encoder_inputs([x],model.device)
    hidden = model.get_encoder()(**xs).last_hidden_state
    joint = conditional(model,hidden,xs["attention_mask"],[0,0],ys)
    error = 0.
    for score,y in zip(joint,ys,strict=True):
        prefix = [model.config.decoder_start_token_id]
        total = 0.
        for token in y:
            logits = model(**xs,decoder_input_ids=padded([prefix],0,model.device),use_cache=False).logits[0,-1]
            total += logits.float().log_softmax(-1)[token].item()
            prefix.append(token)
        error = max(error,abs(total-score))
    if error > .001:
        raise ValueError(f"T5 batched log P disagrees with prefix sum including EOS: {error}")
    return error


def load(d):
    runtime = {k:version(k) for k in RUNTIME}
    if runtime != RUNTIME:
        raise ValueError(f"run T5 using the original pinned runtime, got {runtime}")
    recipe = model_receipt(d,verify_bytes=True)
    if recipe != settings()["models"][d]:
        raise ValueError("checkpoint or training metadata changed after freezing")
    path = DATA/"models"/d
    tok = AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    model = T5ForConditionalGeneration.from_pretrained(path,local_files_only=True,use_safetensors=True).to("cuda").eval()
    gen = model.generation_config
    ids = tok.bos_token_id,tok.eos_token_id,tok.pad_token_id,gen.decoder_start_token_id,gen.pad_token_id,gen.eos_token_id
    if (ids!=(None,1,0,0,0,1) or gen.forced_eos_token_id is not None or model.config.model_type!="t5"
            or tok("가")["input_ids"][-1]!=1 or tokenize(tok,[""],target=True)!=[[1]]):
        raise ValueError("not the original EOS-only BPE T5 tokenizer/start format")
    return tok,model,recipe


def generate_texts(tok,model,xs,beam,cap,nret):
    with torch.autocast("cuda",dtype=torch.bfloat16):
        dec = model.generate(**encoder_inputs(xs,model.device),num_beams=beam,num_return_sequences=nret,do_sample=False,
                             early_stopping=True,use_cache=True,max_new_tokens=cap)
    return [surface(t) for t in tok.batch_decode(dec[:,1:],skip_special_tokens=True)]


@torch.inference_mode()
def measure(d,split):
    src,hs = source_beam(d,split)
    tok,model,recipe = load(d)
    xs = tokenize(tok,[r["x"] for r in src])
    torch.set_float32_matmul_precision("highest")
    error = verify_conditional(tok, model, xs[0])
    n = len(xs)
    order = sorted(range(n),key=lambda i:-len(xs[i]))
    first = [None]*n
    pools = [None]*n
    batch = recipe["args"]["gen_batch_size"]
    out = ROOT/split/d
    out.mkdir(parents=True,exist_ok=False)
    for k in range(0,n,batch):
        group = order[k:k+batch]
        enc = [xs[i] for i in group]
        cap = min(512,2*max(map(len,enc))+10)
        torch.set_float32_matmul_precision("high")  # original mixed-precision beam generation
        for i,text in zip(group,generate_texts(tok,model,enc,4,cap,1),strict=True):
            first[i]=text
        if split=="test":
            for j in range(0,len(group),8):
                sub = group[j:j+8]
                texts = generate_texts(tok,model,[xs[i] for i in sub],8,cap,8)
                if len(texts)!=8*len(sub):
                    raise ValueError("incomplete beam 8")
                for t,i in enumerate(sub):
                    s=list(dict.fromkeys([first[i],*texts[8*t:8*t+8]]))[:8]
                    pools[i]=union_pool(hs[i],s)
            # One encoder pass per source; all H-only targets receive exactly the same decoder scoring as S targets.
            torch.set_float32_matmul_precision("highest")
            inputs = encoder_inputs(enc,model.device)
            hidden = model.get_encoder()(**inputs).last_hidden_state
            entries=[(j,h) for j,i in enumerate(group) for h in pools[i]]
            targets=tokenize(tok,[h["text"] for _,h in entries],target=True)
            ranks=sorted(range(len(entries)),key=lambda t:len(targets[t]))
            for start in range(0,len(ranks),24):
                ranks_chunk=ranks[start:start+24]
                vals=conditional(model,hidden,inputs["attention_mask"],[entries[t][0] for t in ranks_chunk],
                                 [targets[t] for t in ranks_chunk])
                for t,v in zip(ranks_chunk,vals,strict=True):
                    entries[t][1]["s2s"]=v
        if k%1024==0:
            print(split,d,min(k+batch,n),"/",n,flush=True)
    with (out/"beam4.jsonl").open("x",encoding="utf-8") as w:
        for i,y in enumerate(first):
            if not isinstance(y,str):
                raise ValueError("missing T5 baseline output")
            line(w,{"i":i,"text":y})
    meta={"domain":d,"split":split,"n":n,"baseline":"fresh_beam4","epoch":recipe["epoch"],
          "model_sha":recipe["files"]["model.safetensors"],"prefix_abs_error":error,
          "prediction_sha":sha(out/"beam4.jsonl"),"settings_sha":sha(SETUP/"settings.json"),"runtime":RUNTIME}
    if split=="dev":
        old=(DATA/"archive"/d/"dev_hypothesis.txt").read_text(encoding="utf-8").splitlines()
        matched=sum(r["text"]==surface(y) for r,y in zip(rows(out/"beam4.jsonl"),old,strict=True))
        save(out/"parity.json",{**meta,"matched":matched,"archived_sha":sha(DATA/"archive"/d/"dev_hypothesis.txt")})
        if matched!=n:
            raise ValueError(f"{d}: fresh T5 dev beam4 {matched}/{n} matched; test blocked")
        print(f"{d}: dev T5 baseline exactly reproduced ({n}/{n})",flush=True)
    else:
        with (out/"pool.jsonl").open("x",encoding="utf-8") as w:
            for r,p in zip(src,pools,strict=True):
                if not p:
                    raise ValueError("empty T5+HNC pool")
                line(w,{**r,"hyps":p})
        save(out/"generation.json",{**meta,"pool_sha":sha(out/"pool.jsonl"),"total_union":sum(map(len,pools)),
                                    "n_s":sum(h["s"] is not None for p in pools for h in p),
                                    "n_h":sum(h["h"] is not None for p in pools for h in p)})
