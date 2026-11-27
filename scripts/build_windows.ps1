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
$version = (Select-String -Path cascade_panel\__init__.py -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
go build -ldflags "-s -w -X main.Version=$version" -o dist\cascade-agent .\cmd\cascade-agent
Pop-Location

pyinstaller --noconfirm --clean cascade-panel.spec
pyinstaller --noconfirm --clean cascade-updater.spec
Copy-Item dist\CascadeUpdater.exe dist\CascadeControl\CascadeUpdater.exe
Copy-Item packaging\README_RU.txt dist\CascadeControl\README_RU.txt
Write-Host "Готово: dist\CascadeControl\CascadeControl.exe"

# Стабильные имена для релиза: CascadeControl-windows-x64.zip и .sha256 рядом.
New-Item -ItemType Directory -Force -Path build | Out-Null
$zip = "build\CascadeControl-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\CascadeControl -DestinationPath $zip
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText("$zip.sha256", "$hash  CascadeControl-windows-x64.zip`n")
Write-Host "Архив: $zip"
