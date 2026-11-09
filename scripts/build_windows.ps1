# Сборка Cascade Control на Windows: каталог dist\CascadeControl\CascadeControl.exe.
# Нужны Python 3.11+, Node.js, Go и WebView2 Runtime.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m pip install -r requirements.txt pyinstaller pywebview
npm --prefix web ci
npm --prefix web run build

New-Item -ItemType Directory -Force -Path agent\dist | Out-Null
$env:CGO_ENABLED = "0"
$env:GOOS = "linux"
$env:GOARCH = "amd64"
Push-Location agent
go build -ldflags "-s -w" -o dist\cascade-agent .\cmd\cascade-agent
Pop-Location

pyinstaller --noconfirm --clean cascade-panel.spec
Write-Host "Готово: dist\CascadeControl\CascadeControl.exe"
