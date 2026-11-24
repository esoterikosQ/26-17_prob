#!/bin/bash
# A-2 형태소 n-gram LM 학습 (KenLM, 저장소 안에서만 임시 파일 사용).
#   bash scripts/train_lm.sh <tag> [order]     # datasets/lm/<tag>/train.txt → lm<order>.bin
set -euo pipefail
TAG=${1:-v1}; ORDER=${2:-5}
D=datasets/lm/$TAG
BIN=tools/kenlm/build/bin
mkdir -p .cache/tmp
export TMPDIR=$PWD/.cache/tmp
"$BIN/lmplz" -o "$ORDER" -S 40% -T .cache/tmp --prune 0 0 1 --discount_fallback < "$D/train.txt" > "$D/lm$ORDER.arpa"
"$BIN/build_binary" -T .cache/tmp trie "$D/lm$ORDER.arpa" "$D/lm$ORDER.bin"
ls -la "$D/lm$ORDER.arpa" "$D/lm$ORDER.bin"
rm -f "$D/lm$ORDER.arpa"
