#!/bin/bash
# Кросс-сборка Windows x64 панели на Linux: Wine + официальный CPython amd64 + PyInstaller.
# Рядом с exe кладётся веб-интерфейс и linux/amd64 бинарник агента (его панель
# копирует на входной узел, на Windows он не запускается).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WINEPREFIX="${WINEPREFIX:-$HOME/.wine-cascade}"
export WINEPREFIX WINEARCH=win64 WINEDEBUG="${WINEDEBUG:--all}"
export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-mscoree,mshtml=}"
PY_WIN="C:\\Python312\\python.exe"
DIST_DIR="$ROOT/dist/CascadeControl"
STAGE="$ROOT/build/windows-zip"
# Имя архива стабильное: приложение само ищет именно его в последнем релизе.
ZIP_PATH="${1:-$ROOT/build/CascadeControl-windows-x64.zip}"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "нет команды: $1" >&2; exit 1; }
}

need wine
need go
need npm
need xvfb-run

echo "== веб-интерфейс =="
if [ ! -d web/node_modules ]; then
  npm --prefix web ci
fi
npm --prefix web run build
test -f web/dist/index.html

VERSION="$(python3 -c "import re,pathlib;print(re.search(r'__version__ = \"([^\"]+)\"', pathlib.Path('cascade_panel/__init__.py').read_text()).group(1))")"
echo "версия $VERSION"

echo "== агент linux/amd64 =="
mkdir -p agent/dist
(
  cd agent
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags "-s -w -X main.Version=$VERSION" -o "$ROOT/agent/dist/cascade-agent" ./cmd/cascade-agent
)
python3 - << 'PYEOF'
# ELF64, little-endian, x86-64 (без утилиты file, её может не быть)
head = open("agent/dist/cascade-agent", "rb").read(20)
assert head[:4] == b"\x7fELF" and head[4] == 2 and head[5] == 1 and head[18:20] == b"\x3e\x00", "агент не linux/amd64"
PYEOF

echo "== Windows Python в Wine =="
if ! xvfb-run -a wine "$PY_WIN" -c "import sys; print(sys.version)"; then
  echo "Windows Python не установлен в $WINEPREFIX. Сначала: scripts/setup_wine_python.sh" >&2
  exit 1
fi

echo "== зависимости PyInstaller =="
# CPython 3.12.10 вызывает CopyFile2, которого нет в Wine 9. Подмена copy2
# нужна только этому интерпретатору сборки, в exe она не попадает.
SITE_CUSTOM="$WINEPREFIX/drive_c/Python312/Lib/site-packages/sitecustomize.py"
if [ ! -f "$SITE_CUSTOM" ]; then
  cat > "$SITE_CUSTOM" << 'EOF'
import shutil
_orig_copytree = shutil.copytree

def _copy2(src, dst, *args, **kwargs):
    return shutil.copy(src, dst)

def _copytree(src, dst, symlinks=False, ignore=None, copy_function=None,
              ignore_dangling_symlinks=False, dirs_exist_ok=False):
    return _orig_copytree(
        src, dst, symlinks=symlinks, ignore=ignore, copy_function=shutil.copy,
        ignore_dangling_symlinks=ignore_dangling_symlinks, dirs_exist_ok=dirs_exist_ok,
    )

shutil.copy2 = _copy2
shutil.copytree = _copytree
EOF
fi
xvfb-run -a wine "$PY_WIN" -m pip install --disable-pip-version-check -r requirements.txt "pyinstaller==6.11.1"
xvfb-run -a wine "$PY_WIN" -m pip install --disable-pip-version-check bottle pythonnet clr_loader
xvfb-run -a wine "$PY_WIN" -m pip install --disable-pip-version-check --no-build-isolation proxy_tools
xvfb-run -a wine "$PY_WIN" -m pip install --disable-pip-version-check --no-deps "pywebview==5.4"

echo "== PyInstaller (onedir) =="
rm -rf build/cascade-panel build/cascade-updater dist/CascadeControl dist/CascadeUpdater.exe
xvfb-run -a wine "$PY_WIN" -m PyInstaller --noconfirm --clean cascade-panel.spec
test -f "$DIST_DIR/CascadeControl.exe"
echo "== установщик обновлений (CascadeUpdater.exe) =="
xvfb-run -a wine "$PY_WIN" -m PyInstaller --noconfirm --clean cascade-updater.spec
test -f dist/CascadeUpdater.exe
cp dist/CascadeUpdater.exe "$DIST_DIR/CascadeUpdater.exe"

echo "== zip =="
rm -rf "$STAGE"
mkdir -p "$STAGE/CascadeControl" "$(dirname "$ZIP_PATH")"
cp -a "$DIST_DIR"/. "$STAGE/CascadeControl/"
cp packaging/README_RU.txt "$STAGE/CascadeControl/README_RU.txt"
# В архиве не должно быть базы и ключа панели.
if find "$STAGE" -iname '*.db' -o -iname 'master.key' -o -iname 'id_rsa' | grep -q .; then
  echo "в каталог сборки попали секреты" >&2
  exit 1
fi
rm -f "$ZIP_PATH"
(
  cd "$STAGE"
  zip -r -q "$ZIP_PATH" CascadeControl
)
# Формат «<sha256>  <имя файла>» без пути: его читает само приложение и sha256sum -c.
(cd "$(dirname "$ZIP_PATH")" && sha256sum "$(basename "$ZIP_PATH")" | tee "$(basename "$ZIP_PATH").sha256")
echo "готово: $ZIP_PATH"
