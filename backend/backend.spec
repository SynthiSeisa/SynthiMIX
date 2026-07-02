# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent

# Include ffmpeg / yt-dlp if they sit next to the project root
binaries = []
for name in ('ffmpeg.exe', 'ffprobe.exe', 'yt-dlp.exe'):
    p = ROOT / name
    if p.exists():
        binaries.append((str(p), '.'))
    p2 = ROOT / 'bin' / name
    if p2.exists():
        binaries.append((str(p2), '.'))

a = Analysis(
    ['main.py'],
    pathex=[str(ROOT / 'backend')],
    binaries=binaries,
    datas=[],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.lifespan', 'uvicorn.lifespan.off', 'uvicorn.lifespan.on',
        'websockets', 'websockets.legacy', 'websockets.legacy.server',
        'h11', 'anyio', 'anyio._backends._asyncio',
        'mutagen', 'mutagen.mp3', 'mutagen.flac', 'mutagen.mp4',
        'mutagen.id3', 'mutagen.oggvorbis',
        'PIL', 'PIL.Image',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,         # no console window in production
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='backend',
)
