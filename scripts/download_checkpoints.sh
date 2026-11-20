#!/usr/bin/env bash
# Download SAM 2.1 tiny weights used by the default CLI path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/checkpoints"
CKPT="$ROOT/checkpoints/sam2.1_hiera_tiny.pt"
URL="https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt"
if [[ -f "$CKPT" ]]; then
  echo "Already present: $CKPT"
  exit 0
fi
curl -L -o "$CKPT" "$URL"
echo "Wrote $CKPT"
