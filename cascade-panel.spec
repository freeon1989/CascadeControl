# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("uvicorn") + collect_submodules("fastapi") + [
    "paramiko",
    "cryptography",
    "qrcode",
    "sqlalchemy.dialects.sqlite",
    "webview",
]

a = Analysis(
    ["cascade_panel/webview_host.py"],
    pathex=["."],
    binaries=[],
    datas=[("web/dist", "web/dist")],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CascadeControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
