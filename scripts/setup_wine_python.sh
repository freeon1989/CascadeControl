#!/bin/bash
# Ставит официальный CPython 3.12 amd64 внутрь отдельного префикса Wine.
set -euo pipefail

WINEPREFIX="${WINEPREFIX:-$HOME/.wine-cascade}"
export WINEPREFIX WINEARCH=win64 WINEDEBUG="${WINEDEBUG:--all}"
# Не ждать установщики Mono/Gecko: панели они не нужны.
export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-mscoree,mshtml=}"
CACHE="${CACHE:-$HOME/.cache/cascade-wine}"
PY_VERSION="${PY_VERSION:-3.12.10}"
INSTALLER="$CACHE/python-${PY_VERSION}-amd64.exe"
URL="https://www.python.org/ftp/python/${PY_VERSION}/python-${PY_VERSION}-amd64.exe"

mkdir -p "$CACHE"
if [ ! -f "$INSTALLER" ]; then
  echo "скачиваю $URL"
  curl -fL --retry 3 -o "$INSTALLER.partial" "$URL"
  mv "$INSTALLER.partial" "$INSTALLER"
fi

if [ ! -d "$WINEPREFIX" ]; then
  mkdir -p "$WINEPREFIX"
  xvfb-run -a wineboot --init
fi

echo "тихая установка Python в C:\\Python312"
xvfb-run -a wine "$INSTALLER" /quiet InstallAllUsers=1 PrependPath=0 Include_test=0 Include_launcher=0 Include_pip=1 "TargetDir=C:\\Python312"
# Инсталлятор иногда возвращает код до записи файлов на диск Wine.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if xvfb-run -a wine "C:\\Python312\\python.exe" -c "import sys; print(sys.version)"; then
    SITE_CUSTOM="$WINEPREFIX/drive_c/Python312/Lib/site-packages/sitecustomize.py"
    if [ ! -f "$SITE_CUSTOM" ]; then
      mkdir -p "$(dirname "$SITE_CUSTOM")"
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
    exit 0
  fi
  sleep 3
done
echo "Python под Wine не запустился" >&2
exit 1
