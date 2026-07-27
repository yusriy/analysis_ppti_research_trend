#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONDA_BIN="$HOME/miniforge3/bin/conda"

if [ ! -x "$CONDA_BIN" ]; then
  echo "Conda executable not found: $CONDA_BIN" >&2
  exit 1
fi

"$CONDA_BIN" run -n openai_ppti5 \
  python "$REPO_DIR/podcast/build_podcast.py"

test -s "$REPO_DIR/podcast/latest/script.txt"
test -s "$REPO_DIR/podcast/latest/episode_metadata.json"
echo "Built $REPO_DIR/podcast/latest"
