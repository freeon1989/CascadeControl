# -*- mode: python ; coding: utf-8 -*-
# Маленький установщик обновлений: один exe, только стандартная библиотека.
a = Analysis(
    ["updater/cascade_updater.py"],
    pathex=["."],
    excludes=["tkinter", "matplotlib", "numpy", "pytest", "sitecustomize", "fastapi", "uvicorn",
              "sqlalchemy", "pydantic", "paramiko", "cryptography", "PIL", "webview"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="CascadeUpdater",
    debug=False, strip=False, upx=False,
    console=False,
)
