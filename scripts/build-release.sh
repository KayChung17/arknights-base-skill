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

bash "$ROOT_DIR/scripts/validate-all.sh" | tee "$DEST.validation.tmp"
mkdir -p "$DEST" "$STAGE/$NAME"
rsync -a \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude 'output/' \
  --exclude 'release/' \
  --exclude '*.local.*' \
  "$ROOT_DIR/" "$STAGE/$NAME/"

mv "$DEST.validation.tmp" "$DEST/${NAME}-validation.txt"
(
  cd "$STAGE"
  zip -qr "$DEST/${NAME}.zip" "$NAME"
  tar -czf "$DEST/${NAME}.tar.gz" "$NAME"
)
(
  cd "$DEST"
  sha256sum "${NAME}.zip" "${NAME}.tar.gz" "${NAME}-validation.txt" > "${NAME}.sha256"
)

echo "发布包已生成：$DEST"
