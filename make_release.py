# -*- coding: utf-8 -*-
"""release 打包：每游戏一个 zip（gitee 单文件发布用）+ 全量 zip（本地/网盘分发）。"""
import shutil
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
REL = ROOT / "release"
REL.mkdir(exist_ok=True)

# 1) 每游戏 zip
for gdir in sorted((DIST / "games").iterdir()):
    if not gdir.is_dir():
        continue
    out = REL / ("arcade-%s-v1.0.0-win64.zip" % gdir.name)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in gdir.rglob("*"):
            if f.is_file():
                z.write(f, gdir.name + "/" + str(f.relative_to(gdir)))
    print("%s  %.1f MB" % (out.name, out.stat().st_size / 1e6))

# 2) 全量 zip（菜单 + 全部游戏）
full = REL / "arcade-collection-v1.0.0-win64-full.zip"
with zipfile.ZipFile(full, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in DIST.rglob("*"):
        if p.is_file():
            z.write(p, str(p.relative_to(DIST)))
print("%s  %.1f MB" % (full.name, full.stat().st_size / 1e6))
print("完成")
