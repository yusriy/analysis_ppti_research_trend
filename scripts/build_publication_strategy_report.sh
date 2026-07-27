#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONDA_BIN="$HOME/miniforge3/bin/conda"
NODE_BIN="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
DELIVER_SCRIPT=""

for candidate in "$HOME"/.codex/plugins/cache/openai-curated-remote/data-analytics/*/skills/build-report/scripts/deliver_portable_artifact.mjs; do
  if [ -f "$candidate" ]; then
    DELIVER_SCRIPT="$candidate"
  fi
done

if [ ! -x "$CONDA_BIN" ]; then
  echo "Conda executable not found: $CONDA_BIN" >&2
  exit 1
fi

if [ ! -x "$NODE_BIN" ]; then
  NODE_BIN="$(command -v node || true)"
fi

if [ -z "$NODE_BIN" ] || [ ! -x "$NODE_BIN" ]; then
  echo "Node.js executable not found." >&2
  exit 1
fi

if [ -z "$DELIVER_SCRIPT" ]; then
  echo "Portable report delivery script not found in the installed Data Analytics plugin." >&2
  exit 1
fi

"$CONDA_BIN" run -n openai_ppti5 \
  python "$REPO_DIR/publication_strategy_report/build_report.py"

"$NODE_BIN" "$DELIVER_SCRIPT" \
  --input "$REPO_DIR/publication_strategy_report/artifact.json" \
  --output "$REPO_DIR/ppti_publication_strategy_report.html"

test -s "$REPO_DIR/ppti_publication_strategy_report.html"
echo "Built $REPO_DIR/ppti_publication_strategy_report.html"
