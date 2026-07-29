#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python - <<'PY' "$ROOT_DIR/pyproject.toml"
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding='utf-8')
print(re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1))
PY
)"
NAME="arknights-base-skill-v${VERSION}"
DEST="${1:-$ROOT_DIR/release}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$DEST" "$STAGE/$NAME"
DEST="$(cd "$DEST" && pwd)"
bash "$ROOT_DIR/scripts/validate-all.sh" | tee "$DEST/${NAME}-validation.txt"
rsync -a \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude '__pycache__/' \
  --exclude 'output/' \
  --exclude 'arkbase-output/' \
  --exclude 'work/' \
  --exclude 'release/' \
  --exclude 'dist/' \
  --exclude 'build/' \
  --exclude '*.local.*' \
  --exclude 'project.json' \
  --exclude 'project.*.json' \
  --exclude '*.xlsx' \
  --exclude '*.xls' \
  --exclude 'roster.xlsx' \
  --exclude 'roster.tsv' \
  --exclude 'roster.csv' \
  --exclude '干员练度表*.xlsx' \
  --exclude '干员练度表*.txt' \
  "$ROOT_DIR/" "$STAGE/$NAME/"

bash "$STAGE/$NAME/scripts/validate-all.sh" | tee "$DEST/${NAME}-staged-validation.txt"
(
  cd "$STAGE"
  zip -qr "$DEST/${NAME}.zip" "$NAME"
  tar -czf "$DEST/${NAME}.tar.gz" "$NAME"
)
(
  cd "$DEST"
  sha256sum \
    "${NAME}.zip" \
    "${NAME}.tar.gz" \
    "${NAME}-validation.txt" \
    "${NAME}-staged-validation.txt" > "${NAME}.sha256"
)

echo "发布包已生成：$DEST"
