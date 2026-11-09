# Сборка Cascade Control в один Windows-exe.
# Запускать на Windows с Python 3.11+, Node.js и WebView2 Runtime.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m pip install -r requirements.txt pyinstaller pywebview
npm --prefix web ci
npm --prefix web run build

pyinstaller --noconfirm --clean cascade-panel.spec
Write-Host "Готово: dist\CascadeControl.exe"
