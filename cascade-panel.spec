# -*- mode: python ; coding: utf-8 -*-
# Собирается интерпретатором Windows (нативно или под Wine).
import os

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hidden = []

for package in (
    "cascade_panel",
    "certifi",
    "truststore",
    "uvicorn",
    "fastapi",
    "starlette",
    "anyio",
    "cryptography",
    "paramiko",
    "qrcode",
    "PIL",
    "sqlalchemy",
    "pydantic",
    "webview",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hidden += pkg_hidden

if os.path.isdir("web/dist"):
    datas.append(("web/dist", "web/dist"))

agent = os.path.join("agent", "dist", "cascade-agent")
if os.path.isfile(agent):
    datas.append((agent, "agent"))

try:
    import certifi

    datas.append((certifi.where(), "certifi"))
except Exception:
    pass

hidden += [
    "certifi",
    "truststore",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "sqlite3",
    "h11",
    "websockets",
]

a = Analysis(
    ["cascade_panel/webview_host.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pytest", "sitecustomize"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CascadeControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CascadeControl",
)
