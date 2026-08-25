# -*- mode: python ; coding: utf-8 -*-
# arcade.spec —— 街机合集 onedir 打包（2026-08-25）
# games/ 整个目录作为数据打入包（每个游戏独立进程，经 --run 重入执行）

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('games', 'games')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['scipy', 'moviepy', 'pygame_gui'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='arcade',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='arcade',
)
