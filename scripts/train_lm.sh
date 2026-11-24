#!/bin/bash
# A-2 형태소 n-gram LM 학습 (KenLM, 저장소 안에서만 임시 파일 사용).
#   bash scripts/train_lm.sh <tag> [order] [prune]     # datasets/lm/<tag>/train.txt → lm<order>.bin
# prune: lmplz --prune 값(차수별 계수 하한). 기본 "0 1 2" = 2-gram 단독 제거, 3-gram 이상 2회 이하 제거.
#   (v0은 "0 0 1"로 학습함. 신문 포함 v1은 ARPA가 공용 디스크를 압박하지 않도록 강화, 2026-11-24)
# 이진 파일은 trie + 8비트 양자화(-q 8 -b 8).
set -euo pipefail
TAG=${1:-v1}; ORDER=${2:-5}; PRUNE=${3:-"0 1 2"}
D=datasets/lm/$TAG
BIN=tools/kenlm/build/bin
mkdir -p .cache/tmp
export TMPDIR=$PWD/.cache/tmp
# shellcheck disable=SC2086
"$BIN/lmplz" -o "$ORDER" -S 40% -T .cache/tmp --prune $PRUNE --discount_fallback < "$D/train.txt" > "$D/lm$ORDER.arpa"
"$BIN/build_binary" -T .cache/tmp -q 8 -b 8 trie "$D/lm$ORDER.arpa" "$D/lm$ORDER.bin"
ls -la "$D/lm$ORDER.arpa" "$D/lm$ORDER.bin"
rm -f "$D/lm$ORDER.arpa"
