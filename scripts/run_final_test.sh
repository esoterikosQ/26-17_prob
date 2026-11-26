#!/usr/bin/env bash
set -euo pipefail
umask 077
root=outputs/final_test_v1
export TMPDIR="$PWD/.cache/tmp" HF_HOME="$PWD/.cache/huggingface" XDG_CACHE_HOME="$PWD/.cache"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export CUDA_CACHE_PATH="$PWD/.cache/cuda" TORCHINDUCTOR_CACHE_DIR="$PWD/.cache/torchinductor"
py=.venv/bin/python
gpu=.venv-nlm/bin/python
if [[ "${1:-}" != "approved-v1" ]]; then echo "Explicit approved-v1 run required" >&2; exit 2; fi
$py scripts/final_test.py open-approved-v1
exec >"$root/predict.log" 2>&1
trap 'echo "$?" >"$root/predict.status"' EXIT
for d in korean_learner lang8 union; do
    echo "$d HNC"
    $py scripts/final_test.py hnc "$d"
    if [[ "$d" == korean_learner ]]; then
        $py scripts/final_test.py pcfg "$d" & pid=$!
        p=0; q=0; $gpu scripts/final_test.py neural-b "$d" || p=$?; wait "$pid" || q=$?
        if ((p || q)); then echo "PCFG/B failed: $q/$p"; exit 1; fi
    fi
    echo "$d KoBART"
    $gpu scripts/final_test.py generate "$d"
    $py scripts/final_test.py cpu "$d" & pid=$!
    p=0; q=0; $gpu scripts/final_test.py causal "$d" || p=$?; wait "$pid" || q=$?
    if ((p || q)); then echo "causal/CPU features failed: $p/$q"; exit 1; fi
    $py scripts/final_test.py choose "$d"
done
$py scripts/final_test.py seal
