"""Reconstruct the pinned PCFG K4-5 release from its original DP train split, not from GEC outputs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rehearsal_pcfg import MODEL, PACKAGE, REV
from scripts.rehearsal_predict import WORK, save, sha

STAGE = Path("data/pcfg_recovery_v3")
INPUTS = {
    "DP_json.zip": ("NIKL_DP_v2.0", ("NXDP1902103231", "SXDP2002103180")),
    "DP2024.zip": ("NIKL_DP_2024_v1.0", ("NXDP2402503041", "SXDP2402503041")),
    "DP2025.zip": ("NIKL_DP_2025_v1.0", ("NXDP2502512313", "SXDP2502512312")),
}


def run():
    if MODEL.exists():
        raise FileExistsError(MODEL)
    rev = subprocess.check_output(["git", "-C", str(PACKAGE), "rev-parse", "HEAD"], text=True).strip()
    if rev != REV or subprocess.check_output(["git", "-C", str(PACKAGE), "diff", "HEAD", "--", "pcfg", "scripts", "setup.py"]):
        raise ValueError("pinned parser checkout changed")
    sys.path.insert(0, str(PACKAGE.resolve()))
    from kiwipiepy import Kiwi
    from pcfg.korean import assign_morphemes, dp_files, dp_to_tree, is_projective, iter_dp_sentences, single_root, split_of
    from pcfg.tree import to_bracket

    subprocess.run(["sha256sum", "--strict", "-c", "INPUTS.sha256"], cwd=STAGE/"raw", check=True)
    subprocess.run(["sha256sum", "--strict", "-c", "BUILDS.sha256"], cwd=STAGE, check=True)
    historic = json.loads((STAGE/"outputs/k5v3/build.log").read_text().splitlines()[0])
    target = json.loads((STAGE/"outputs/k2/build.log").read_text().splitlines()[0])["split"]["train"]
    docs = STAGE/"json"
    docs.mkdir(exist_ok=False)
    for archive, (name, stems) in INPUTS.items():
        dest = docs/name
        dest.mkdir()
        with zipfile.ZipFile(STAGE/"raw"/archive) as z:
            for stem in stems:
                filename = stem + ".json"
                member, = [m for m in z.infolist() if m.filename.rsplit("/",1)[-1] == filename
                           and "__MACOSX" not in m.filename.split("/")]
                with z.open(member) as src, (dest/filename).open("xb") as out:
                    shutil.copyfileobj(src, out)
    kiwi = Kiwi(num_workers=8)
    trees = STAGE/"train.trees"
    n = morphs = 0
    with trees.open("x", encoding="utf-8") as f:
        def flush(batch):
            nonlocal n, morphs
            for s, tokens in zip(batch, kiwi.tokenize([s.form for s in batch]), strict=True):
                ms = assign_morphemes(s.words, tokens)
                if ms is not None:
                    f.write(to_bracket(dp_to_tree(s.heads, s.labels, ms)) + "\n")
                    n += 1
                    morphs += sum(map(len, ms))
        batch = []
        for s in iter_dp_sentences(dp_files(docs)):
            if split_of(s.doc_id) != "train":
                continue
            if single_root(s.heads) and is_projective(s.heads) and all(
                    s.form[b:e] == w for (b, e), w in zip(s.words, s.word_forms, strict=True)):
                batch.append(s)
                if len(batch) == 4000:
                    flush(batch)
                    batch.clear()
        if batch:
            flush(batch)
    if {"sent": n, "morph": morphs} != target or n != historic["train_trees"]:
        raise ValueError(f"train-only transformation does not reproduce K2: {n}/{morphs}")
    rebuilt = STAGE/"reconstructed.pkl"
    if rebuilt.exists():
        raise FileExistsError(rebuilt)
    res = subprocess.run([sys.executable, str(PACKAGE/"scripts/build_scorer.py"), "--train", str(trees),
                          "--parent-v", "3", "--out", str(rebuilt)], check=True, capture_output=True, text=True)
    result = json.loads(res.stdout)
    for k in ("config", "train_trees", "nonterminals", "vocab"):
        if result[k] != historic[k]:
            raise ValueError(f"reconstructed K4-5 inventory differs: {k}")
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    os.link(rebuilt, MODEL)  # never replace a concurrently delivered original
    fingerprint = sha(MODEL)
    setup = WORK/"setup"
    save(setup/"grammar_provenance.json", {"method": "reconstructed_from_original_DP_train", "parser_commit": REV,
         "raw_manifest_sha": sha(STAGE/"raw/INPUTS.sha256"), "training_receipts_sha": sha(STAGE/"BUILDS.sha256"),
         "trees_sha": sha(trees), "train_trees": n, "train_morphs": morphs, "config": result["config"],
         "nonterminals": result["nonterminals"], "vocab": result["vocab"], "historic_bytes": historic["bytes"],
         "recovered_bytes": MODEL.stat().st_size, "model_sha": fingerprint, "historic_binary_sha_known": False})
    with (setup/"grammar.sha256").open("x") as f:
        f.write(f"{fingerprint}  {MODEL}\n")
    print("Reconstruction staged, not yet replay-verified:", n, morphs, fingerprint, flush=True)


if __name__ == "__main__":
    run()
