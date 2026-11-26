#!/usr/bin/env bash
# No reference or previous system output is an argument to any prediction stage.
set -euo pipefail
umask 077
ROOT=outputs/final_rehearsal_v1
case "${1:-}" in
    full) mode=full; flags=() ;;
    --partial-no-pcfg) mode=partial; flags=(--partial-no-pcfg) ;;
    *) echo 'Specify full or --partial-no-pcfg (dev rehearsal only).' >&2; exit 2 ;;
esac
export TMPDIR="$PWD/.cache/tmp" XDG_CACHE_HOME="$PWD/.cache" HF_HOME="$PWD/.cache/huggingface"
export CUDA_CACHE_PATH="$PWD/.cache/cuda" TORCHINDUCTOR_CACHE_DIR="$PWD/.cache/torchinductor"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1
trap 'echo "$?" > "$ROOT/predict.$mode.status"' EXIT
sha256sum --strict --quiet -c evaluation/final_v1.sha256
if [[ "$mode" == full ]]; then
    test -s "$ROOT/setup/grammar.sha256" || { echo 'Trusted v3 grammar receipt required' >&2; exit 1; }
fi
for d in korean_learner lang8 union; do
    echo "$d: H beam"; .venv/bin/python -u scripts/rehearsal_predict.py hnc "$d"
    if [[ "$d" == korean_learner ]]; then
        if [[ "$mode" == full ]]; then
            .venv/bin/python -u scripts/rehearsal_pcfg.py
        fi
        echo "$d: B scores"; .venv-nlm/bin/python -u scripts/rehearsal_predict.py neural-b "$d"
    fi
    echo "$d: fresh beam 4, beam 8 and teacher forcing"
    .venv-nlm/bin/python -u scripts/rehearsal_predict.py generate "$d"
    .venv/bin/python -u scripts/rehearsal_predict.py cpu "$d" --workers 10 & cpu=$!
    .venv-nlm/bin/python -u scripts/rehearsal_predict.py causal "$d" & gpu=$!
    c=0; g=0; wait "$cpu" || c=$?; wait "$gpu" || g=$?
    if ((c || g)); then echo "feature failure: CPU=$c GPU=$g" >&2; exit 1; fi
    .venv/bin/python -u scripts/rehearsal_predict.py choose "$d" "${flags[@]}"
    echo "$d: choices written"
done
.venv/bin/python -u scripts/rehearsal_seal.py "${flags[@]}"
