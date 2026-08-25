# 街机合集 arcade-collection

单文件玩具整合项目（2026-08-25 用户拍板）：6 个小游戏收进一个选单壳，每玩法保留一个现行版，重复重做版已删除。

## 纪律

- 壳只做「展示 + 启动」；每个游戏**独立子进程 + 独立 cwd**（子进程隔离，崩一个不影响其他）；
- 游戏文件原样保留在 `games/<name>/`（复制优于共享，不融合代码）；
- 平台登记：`platform.yaml`（game 分类，入口 `python main.py`）。

## 收录

| 名称 | 目录 | 入口 |
|---|---|---|
| 俄罗斯方块 | games/tetris | main.py |
| 井字棋 | games/tictactoe | 井字棋8.py |
| 摸鱼下棋 | games/moyuxiaqi | moyuxiaqi.py |
| KID-A 步行模拟器 | games/kid-a-walker | kid_a_walker.py |
| ASCII 滑雪 | games/ski | main.py |
| 拼图 | games/puzzle | main.py |
| 2048（终端自动对弈） | games/2048 | main.py |
| Calculator ∞ Alchemia 解密终端 | games/calculator | main.py |
| Line Rider Pro 画线骑行 | games/rider | main.py |
| 六一零食大闯关 | games/sixone | main.py |

## 运行

```bash
cd G:\games\arcade-collection
python main.py
```

## 发行版（2026-08-25 打包跑通）

```bash
pip install pyinstaller pywin32 pywin32-ctypes   # pywin32-ctypes>=0.2.3（0.2.0 在 Py3.14 坏）
python -m PyInstaller arcade.spec --noconfirm
# 产物: dist/arcade/arcade.exe（onedir，整个 dist/arcade 目录即发行版）
# release/arcade-collection-v1.0.0-win64.zip = 打包好的压缩分发件
```

- 打包模式游戏仍走独立进程：`arcade.exe --run <sub> <entry>`（exe 携带解释器重入执行）；
- 数据文件（games/ 全目录）经 spec datas 打入 `_internal/games`，`sys._MEIPASS` 解析；
- 冒烟已验证：exe 选单启动正常 + 打包态 --run 井字棋正常。

## 整合背景

详见《单文件玩具整合方案_2026-08-25》：吸血鬼幸存者/公路驾驶/俄罗斯轮盘/贪吃蛇/文字传奇/挂机族等玩法重复重做已收敛（重复版删除，保留版独立成项目并登记平台）。
