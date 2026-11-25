#!/usr/bin/env bash
# Run in a dedicated tmux session rooted at phdq_prob, with the dev manifests already prepared.
set -euo pipefail
umask 077
ROOT=outputs/c_gec2_dev_v1
export TMPDIR="$PWD/.cache/tmp" XDG_CACHE_HOME="$PWD/.cache" HF_HOME="$PWD/.cache/huggingface"
export CUDA_CACHE_PATH="$PWD/.cache/cuda" TORCHINDUCTOR_CACHE_DIR="$PWD/.cache/torchinductor"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1
trap 'echo "$?" > "$ROOT/queue.status"' EXIT

wait_input() {
    local file="$1" status="$2" until_time=$((SECONDS + 21600))
    while [[ ! -s "$file" ]]; do
        if [[ -s "$status" || $SECONDS -ge $until_time ]]; then
            echo "Required input not ready: $file (producer: $status)" >&2
            return 1
        fi
        sleep 10
    done
}
for d in korean_learner lang8 union; do
    dest="$ROOT/$d"
    wait_input "$ROOT/restore/$d.sha256" "$ROOT/restore/retry.status"
    if [[ "$d" != korean_learner ]]; then
        wait_input "$ROOT/beam/lang8.jsonl" "$ROOT/beam/status"
    fi
    if [[ "$d" == union ]]; then
        wait_input "$ROOT/beam/native.jsonl" "$ROOT/beam/status"
    fi
    echo "$d: smoke"
    .venv-nlm/bin/python -u scripts/c_strong_generate.py "$d" --smoke
    echo "$d: generation"
    .venv-nlm/bin/python -u scripts/c_strong_generate.py "$d"
    echo "$d: CPU and causal features"
    # Do not proceed to evaluation if either child fails.
    .venv/bin/python -u scripts/c_features.py --stage cpu --out "$dest" --workers 8 &
    cpu_pid=$!
    .venv-nlm/bin/python -u scripts/c_features.py --stage causal --out "$dest" --batch 24 &
    gpu_pid=$!
    cpu_rc=0; gpu_rc=0
    wait "$cpu_pid" || cpu_rc=$?
    wait "$gpu_pid" || gpu_rc=$?
    if ((cpu_rc || gpu_rc)); then
        echo "Feature stage failed (CPU=$cpu_rc GPU=$gpu_rc)" >&2
        exit 1
    fi
    .venv/bin/python -u scripts/c_strong_dev.py fixed "$d"
done
