#!/usr/bin/env bash
set -euo pipefail
umask 077
root=outputs/t5_followup_v1
overlay="$PWD/data/t5_followup_v1/runtime"
export TMPDIR="$PWD/.cache/tmp" HF_HOME="$PWD/.cache/huggingface" XDG_CACHE_HOME="$PWD/.cache"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export CUDA_CACHE_PATH="$PWD/.cache/cuda" TORCHINDUCTOR_CACHE_DIR="$PWD/.cache/torchinductor"
py=.venv/bin/python
gpu=.venv-nlm/bin/python
case "${1:-}" in
    predict)
        "$py" scripts/t5_followup.py freeze
        for d in korean_learner lang8 union; do
            PYTHONPATH="$overlay" "$gpu" -u scripts/t5_followup.py dev "$d"
        done
        for d in korean_learner lang8 union; do
            PYTHONPATH="$overlay" "$gpu" -u scripts/t5_followup.py test "$d"
            "$py" -u scripts/t5_followup.py cpu "$d" & pid=$!
            p=0; q=0
            "$gpu" -u scripts/t5_followup.py causal "$d" || p=$?
            wait "$pid" || q=$?
            if ((p || q)); then echo "$d: causal/CPU feature error: $p/$q" >&2; exit 1; fi
            "$py" scripts/t5_followup.py choose "$d"
        done
        "$py" scripts/t5_followup.py seal ;;
    score)
        pids=()
        for d in korean_learner lang8 union; do
            (set -e; "$py" -u scripts/t5_followup_score.py m2 "$d"
             .venv-kagas/bin/python -u scripts/t5_followup_score.py gleu "$d"
             "$py" -u scripts/t5_followup_score.py kagas "$d") >"$root/score.$d.log" 2>&1 &
            pids+=("$!")
        done
        status=0
        for pid in "${pids[@]}"; do wait "$pid" || status=1; done
        if ((status)); then echo 'At least one domain failed; see score.<domain>.log' >&2; exit 1; fi
        "$py" scripts/t5_followup_score.py summary ;;
    *) echo 'Use predict or score (new follow-up only)' >&2; exit 2 ;;
esac
