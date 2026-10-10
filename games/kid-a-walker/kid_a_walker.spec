# -*- mode: python ; coding: utf-8 -*-
# 入口脚本按 spec 所在目录推导（原为本机一条已失效的绝对路径，见硬编码门禁清单）
#
# 代码已拆分为同目录的 kaw/ 包；pathex 必须显式带上 SPECPATH，
# 否则 Analysis 只按入口脚本目录找顶层模块，包内相对导入可能漏收。
import os

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# moderngl / glcontext：glcontext 在函数内按平台动态导入后端（如 glcontext.wgl 是
# 已编译 .pyd），静态分析容易漏。显式收集其子模块与二进制，保证冻结后仍能创建 GL 上下文。
_gl_hidden = collect_submodules('moderngl') + collect_submodules('glcontext')
_gl_binaries = collect_dynamic_libs('glcontext') + collect_dynamic_libs('moderngl')


a = Analysis(
    [os.path.join(SPECPATH, 'kid_a_walker.py')],
    pathex=[SPECPATH],
    binaries=_gl_binaries,
    datas=[],
    hiddenimports=[
        'kaw.config',
        'kaw.state',
        'kaw.preload',
        'kaw.highscore',
        'kaw.game',
        'kaw.world.worldmap',
        'kaw.world.pool',
        'kaw.world.templates',
        'kaw.systems.energy',
        'kaw.systems.fragments',
        'kaw.systems.interaction',
        'kaw.systems.ability',
        'kaw.systems.exploration',
        'kaw.audio.synth',
        'kaw.audio.sfx',
        'kaw.render.particles',
        'kaw.render.raycast',
        'kaw.render.enhanced',
        'kaw.render.textures',
        'kaw.render.camera',
        'kaw.render.meshgen',
        'kaw.render.gl',
        'kaw.ui.hud',
        'kaw.ui.vignette',
        'kaw.ui.loading',
        'kaw.ui.death',
    ] + _gl_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='kid_a_walker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
