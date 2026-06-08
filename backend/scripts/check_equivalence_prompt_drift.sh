#!/usr/bin/env bash
# mongo-canonical-retirement T9 step 4: prompt-drift 守门
#
# 设计稿 docs/.../prompts/semantic-equivalence-candidate.md 与
# 运行时 backend/.../prompts/semantic_equivalence.md 的 ## 模板正文 段必须 1:1 一致.
# 任一缺失或正文不同 → 退出 1, 阻止 commit.

set -e

DESIGN="docs/superpowers/specs/2026-05-19-mongo-canonical-retirement/prompts/semantic-equivalence-candidate.md"
RUNTIME="backend/prompts/semantic_equivalence.md"

if [ ! -f "$DESIGN" ]; then
  echo "[prompt-drift] design prompt missing: $DESIGN" >&2
  exit 1
fi

if [ ! -f "$RUNTIME" ]; then
  echo "[prompt-drift] runtime prompt missing: $RUNTIME" >&2
  exit 1
fi

if ! diff \
    <(sed -n '/^## 模板正文/,$p' "$DESIGN") \
    <(sed -n '/^## 模板正文/,$p' "$RUNTIME") > /dev/null; then
  echo "[prompt-drift] design vs runtime '## 模板正文' diff non-empty" >&2
  diff \
    <(sed -n '/^## 模板正文/,$p' "$DESIGN") \
    <(sed -n '/^## 模板正文/,$p' "$RUNTIME") >&2 || true
  exit 1
fi
