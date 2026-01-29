# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import os
import sys
from PyInstaller.utils.hooks import collect_submodules

# Bundle templates and static assets needed by FastAPI/Jinja2
# (copied into the dist folder on build).
datas = [
    ("scoreboard/templates", "scoreboard/templates"),
    ("scoreboard/static", "scoreboard/static"),
]

hiddenimports = []
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("jinja2")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("serial")
# Local modules used via subprocess or dynamic import
hiddenimports += ["scoreboard.comms", "scoreboard.utils"]

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SwimmingScoreboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="SwimmingScoreboard",
)
