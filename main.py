# -*- coding: utf-8 -*-
"""arcade-collection —— 街机合集选单壳（2026-08-25 玩具整合）。

纪律：壳只做「展示 + 启动」，每个小游戏独立子进程、独立 cwd（子进程隔离），
崩一个不影响其他与壳本身；游戏文件原样保留在 games/<name>/（复制优于共享）。
"""
import subprocess
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "games"

GAMES = [
    ("俄罗斯方块", "tetris", "main.py"),
    ("井字棋", "tictactoe", "井字棋8.py"),
    ("摸鱼下棋", "moyuxiaqi", "moyuxiaqi.py"),
    ("KID-A 步行模拟器", "kid-a-walker", "kid_a_walker.py"),
    ("ASCII 滑雪", "ski", "main.py"),
    ("拼图", "puzzle", "main.py"),
    ("2048（终端自动对弈）", "2048", "main.py"),
    ("Calculator ∞ Alchemia 解密终端", "calculator", "main.py"),
    ("Line Rider Pro 画线骑行", "rider", "main.py"),
    ("六一零食大闯关", "sixone", "main.py"),
]


def launch(sub, entry):
    d = ROOT / sub
    if not (d / entry).exists():
        print("[arcade] 找不到入口:", d / entry)
        return
    subprocess.Popen([sys.executable, str(d / entry)], cwd=str(d))


def main():
    win = tk.Tk()
    win.title("街机合集 arcade-collection")
    win.geometry("360x420")
    win.configure(bg="#10141a")
    tk.Label(win, text="街机合集", bg="#10141a", fg="#7fd0ff",
             font=("Microsoft YaHei", 20, "bold")).pack(pady=(24, 4))
    tk.Label(win, text="每个游戏独立子进程运行（子进程隔离）",
             bg="#10141a", fg="#8291a8", font=("Microsoft YaHei", 9)).pack(pady=(0, 14))
    for name, sub, entry in GAMES:
        d = ROOT / sub
        state = "normal" if (d / entry).exists() else "disabled"
        tk.Button(
            win, text=name, width=24, font=("Microsoft YaHei", 12),
            bg="#1c2637", fg="#cfe0f5", activebackground="#243350",
            activeforeground="#ffffff", relief="flat", bd=0, pady=8, cursor="hand2",
            command=lambda s=sub, e=entry: launch(s, e), state=state,
        ).pack(pady=4)
    win.mainloop()


if __name__ == "__main__":
    main()
