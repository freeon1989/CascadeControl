#!/bin/bash
# Публикует релиз из уже собранного архива: стабильные имена + .sha256.
# Использование: scripts/publish_release.sh build/CascadeControl-windows-x64.zip [заметки.md]
# Нужен вошедший в систему gh. Запускать только после проверки тестов и скана секретов.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ZIP="${1:?путь к CascadeControl-windows-x64.zip}"
VERSION="$(python3 -c "import re,pathlib;print(re.search(r'__version__ = \"([^\"]+)\"', pathlib.Path('cascade_panel/__init__.py').read_text()).group(1))")"
TAG="v$VERSION"
NOTES="${2:-docs/release-notes/$TAG.md}"
[ "$(basename "$ZIP")" = "CascadeControl-windows-x64.zip" ] || { echo "имя архива должно быть CascadeControl-windows-x64.zip" >&2; exit 1; }
STAGE="$(mktemp -d)"
cp "$ZIP" "$STAGE/CascadeControl-windows-x64.zip"
(cd "$STAGE" && sha256sum CascadeControl-windows-x64.zip > CascadeControl-windows-x64.zip.sha256 && sha256sum -c CascadeControl-windows-x64.zip.sha256)
gh release create "$TAG" "$STAGE/CascadeControl-windows-x64.zip" "$STAGE/CascadeControl-windows-x64.zip.sha256" \
  --title "Cascade Control $TAG" --notes-file "$NOTES" --target main
