"""
KID-A WALKER  —  伪3D步行模拟器  v3.0  INFINITE MAP
风格：暗色赛博 / 深蓝荧光走廊
音乐：saw wave 降调(-8半音)+混响 无限循环
特性：《遗迹2》风格模块化随机拼接伪无限地图
      - 21×21 房间模块预置库（走廊/交叉口/大厅/迷宫/暗室等）
      - 玩家靠近区块边缘时自动生成相邻区块
      - 玩家附近 3×3 模块动态加载，离开后模块被重置
      - v3.0: 多线程预加载，异步渲染，开始/游戏状态完全分离
"""

import pygame
import math
import numpy as np
import threading
import random
import time
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Callable
import json
import os

# ─────────────────────── 常量 ─────────────────────────
W, H     = 1280, 720
FOV      = math.pi / 3
HALF_FOV = FOV / 2
NUM_RAYS = 240  # 射线数量
NUM_RAYS_HALF = NUM_RAYS // 2  # 用于查找表
MAX_DEPTH = 22
MOVE_SPEED = 0.055
ROT_SPEED  = 0.028

SR   = 44100
BPM  = 118
BEAT = 60.0 / BPM
VOL  = 0.13
SEMITONES_DOWN = -8
REVERB_DELAY   = 0.19
REVERB_DECAY   = 0.44

# ─── 游戏增强常量 ───
ENERGY_MAX         = 100.0   # 能量上限
ENERGY_DRAIN_MOVE  = 1.0     # 移动消耗：点/秒
ENERGY_DRAIN_RED   = 3.0     # 红色区消耗：点/秒
ENERGY_RECOVER_GLOW= 0.5     # 发光柱附近恢复：点/秒
INTERACT_RANGE     = 2.0     # 交互检测范围（世界单位）
FRAGMENT_TYPES     = 10      # 碎片种类数（deco_type 2~11）
EXPLORE_RADIUS     = 10      # 区块探索判定半径（世界单位）
ACID_DRAIN_RATE    = 15.0    # 酸液池接触消耗：点/秒
PORTAL_TELEPORT_CHANCE = 0.15  # 传送门触发概率
SPAWN_GRACE_PERIOD  = 3.0     # 出生保护期（秒），期间不消耗能量

# ─── 暗室房间模板名集合 ───
DARK_ROOM_TEMPLATES = {
    'make_room_dark_cross',
    'make_room_secret_ns',
}

# ─────────────────────── 游戏状态枚举 ─────────────────────────
class GameState(Enum):
    START_SCREEN = "start"      # 开始界面
    LOADING = "loading"         # 加载中
    PLAYING = "playing"         # 游戏运行
    PAUSED = "paused"           # 暂停
    GAME_OVER = "game_over"     # 死亡结算
    QUIT = "quit"               # 退出

# ─────────────────────── 预加载任务系统 ─────────────────────────
@dataclass
class LoadingTask:
    name: str
    progress: float  # 0.0 ~ 1.0
    status: str
    complete: bool = False

class PreloadManager:
    """后台预加载管理器，使用独立线程"""
    
    def __init__(self, progress_callback: Optional[Callable[[LoadingTask], None]] = None):
        self._lock = threading.Lock()
        self._tasks: dict[str, LoadingTask] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._canceled = False
        self._progress_callback = progress_callback
        self._world: Optional['WorldMap'] = None
        self._spawn: tuple[float, float] = (10.5, 10.5)
        
    def _update_task(self, name: str, progress: float, status: str):
        """更新任务进度（线程安全）"""
        with self._lock:
            self._tasks[name] = LoadingTask(name, progress, status)
        if self._progress_callback:
            self._progress_callback(self._tasks[name])
    
    def _run_loading(self):
        """后台加载线程的主逻辑"""
        import pygame
        
        self._update_task("world", 0.0, "Initializing world generator...")
        self._world = WorldMap(seed=None)
        time.sleep(0.05)  # 让界面有机会更新
        
        self._update_task("terrain", 0.0, "Generating terrain...")
        # 使用预加载范围常量生成初始区块
        preload_r = CHUNK_PRELOAD_RANGE
        for radius in range(preload_r + 1):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if abs(dx) == radius or abs(dy) == radius:
                        self._world._generate_chunk((dx, dy))
            self._update_task("terrain", (radius + 1) / (preload_r + 1), f"Generating chunks... ({radius}/{preload_r})")
            time.sleep(0.02)
        
        self._update_task("connections", 0.0, "Connecting regions...")
        self._world.ensure_neighbors(0, 0)
        self._update_task("connections", 1.0, "Connected!")
        
        self._update_task("audio", 0.5, "Preparing audio synth...")
        # 音频在主线程初始化
        
        self._update_task("spawn", 0.0, "Finding safe spawn...")
        self._spawn = self._world.find_safe_spawn(0, 0)
        self._update_task("spawn", 1.0, "Spawn found!")
        
        self._update_task("complete", 1.0, "Ready!")
        time.sleep(0.1)
        
        with self._lock:
            for task in self._tasks.values():
                task.complete = True
            self._tasks["complete"] = LoadingTask("complete", 1.0, "All done!", True)
    
    def start(self):
        """启动后台加载"""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._canceled = False
        self._thread = threading.Thread(target=self._run_loading, daemon=True)
        self._thread.start()
    
    def cancel(self):
        """取消加载"""
        self._canceled = True
        self._running = False
    
    def get_overall_progress(self) -> tuple[float, str]:
        """获取总体进度"""
        with self._lock:
            if not self._tasks:
                return 0.0, "Initializing..."
            total = sum(t.progress for t in self._tasks.values())
            count = len(self._tasks)
            if count == 0:
                return 0.0, "Initializing..."
            avg = total / count
            current_task = next((t for t in reversed(list(self._tasks.values())) if not t.complete), None)
            status = current_task.status if current_task else "Complete!"
            return avg, status
    
    def is_complete(self) -> bool:
        """检查是否加载完成"""
        with self._lock:
            return any(t.complete for t in self._tasks.values())
    
    def get_world(self) -> Optional['WorldMap']:
        """获取加载好的世界"""
        with self._lock:
            return self._world
    
    def get_spawn(self) -> tuple[float, float]:
        """获取出生点"""
        with self._lock:
            return self._spawn
    
    def get_task_status(self, name: str) -> Optional[LoadingTask]:
        """获取特定任务状态"""
        with self._lock:
            return self._tasks.get(name)

# ── 调色板（增加多种氛围色系） ──
# 冰霜蓝系
C_CEIL_TOP = (5,  10,  22)
C_CEIL_BOT = (12,  25,  55)
C_FLOOR_T  = (18,  35,  50)
C_FLOOR_B  = (8,   18,  35)
C_WALL_FAR = (5,   20,  55)
C_WALL_NR  = (15,  60, 120)
C_GLOW_COL = (50, 255, 200)
C_FOG      = (8,   12,  30)
C_GRID     = (15,  45, 90)
C_ACCENT   = (40, 200, 255)
C_HUD      = (50, 180, 230)
C_SCAN     = (0,   4,  12)

# 装饰物颜色
C_DECO_RED    = (180, 50,  50)   # 红色警示
C_DECO_GOLD   = (200, 170, 60)   # 金色装饰
C_DECO_GREEN  = (50,  200, 100)  # 绿色植物
C_DECO_PURPLE = (150, 80,  200)  # 紫色能量
C_DECO_ORANGE = (220, 120, 40)   # 橙色光源

# ─────────────────────── 预计算查找表（优化渲染）────────────────────────
# 预计算射线角度（从 -HALF_FOV 到 +HALF_FOV）
_RAY_ANGLES = np.array([(i / NUM_RAYS) * FOV - HALF_FOV for i in range(NUM_RAYS)], dtype=np.float32)
_RAY_SIN = np.sin(_RAY_ANGLES).astype(np.float32)
_RAY_COS = np.cos(_RAY_ANGLES).astype(np.float32)

# 预计算距离校正因子（fish-eye correction）
_RAY_CORRECTION = np.cos(_RAY_ANGLES).astype(np.float32)

# ─────────────────────── 模块化地图系统 ─────────────────────────
# 每个房间模块 21×21，用字符串列表表示
# '#' = 实体墙  '.' = 空地  '2' = 发光柱
# 各方向出口位置固定：N=col10,row0  S=col10,row20  W=row10,col0  E=row10,col20

CHUNK_SIZE = 21   # 每个区块的尺寸

# ── 房间模板库（含出口开口） ──
# 出口开口标记：每个出口占3格宽（中心±1），用 '.' 开放

# 辅助：生成21行21列的全墙模板，注意出口必须与开口对齐
def _mk_empty():
    g = [['#'] * CHUNK_SIZE for _ in range(CHUNK_SIZE)]
    return g

def _open_north(g):
    """在北侧中心开3格出口"""
    g[0][9] = g[0][10] = g[0][11] = '.'
    g[1][9] = g[1][10] = g[1][11] = '.'

def _open_south(g):
    g[20][9] = g[20][10] = g[20][11] = '.'
    g[19][9] = g[19][10] = g[19][11] = '.'

def _open_west(g):
    g[9][0]  = g[10][0]  = g[11][0]  = '.'
    g[9][1]  = g[10][1]  = g[11][1]  = '.'

def _open_east(g):
    g[9][20]  = g[10][20]  = g[11][20]  = '.'
    g[9][19]  = g[10][19]  = g[11][19]  = '.'

def _fill_rect(g, r1, c1, r2, c2, ch='.'):
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            g[r][c] = ch

def _grid_to_str(g):
    return [''.join(row) for row in g]

# ─── 各类房间模板生成函数 ───

def make_room_cross():
    """十字走廊：四向开口"""
    g = _mk_empty()
    # 水平走廊
    _fill_rect(g, 9, 0, 11, 20)
    # 竖直走廊
    _fill_rect(g, 0, 9, 20, 11)
    _open_north(g); _open_south(g)
    _open_west(g);  _open_east(g)
    return _grid_to_str(g)

def make_room_hall_ns():
    """南北大走廊"""
    g = _mk_empty()
    _fill_rect(g, 0, 7, 20, 13)
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_hall_ew():
    """东西大走廊"""
    g = _mk_empty()
    _fill_rect(g, 7, 0, 13, 20)
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_hall_ns_glow():
    """南北走廊（含发光柱）"""
    g = _mk_empty()
    _fill_rect(g, 0, 7, 20, 13)
    # 发光柱对称分布
    for r in [4, 8, 12, 16]:
        g[r][8]  = '2'
        g[r][12] = '2'
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_tee_n():
    """T形：N/W/E开口"""
    g = _mk_empty()
    _fill_rect(g, 0, 9, 11, 11)   # 北走廊
    _fill_rect(g, 9, 0, 11, 20)   # 东西走廊
    _open_north(g); _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_tee_s():
    """T形：S/W/E开口"""
    g = _mk_empty()
    _fill_rect(g, 9, 9, 20, 11)   # 南走廊
    _fill_rect(g, 9, 0, 11, 20)   # 东西走廊
    _open_south(g); _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_tee_e():
    """T形：N/S/E开口"""
    g = _mk_empty()
    _fill_rect(g, 0, 9, 20, 11)   # 南北走廊
    _fill_rect(g, 9, 9, 11, 20)   # 东走廊
    _open_north(g); _open_south(g); _open_east(g)
    return _grid_to_str(g)

def make_room_tee_w():
    """T形：N/S/W开口"""
    g = _mk_empty()
    _fill_rect(g, 0, 9, 20, 11)
    _fill_rect(g, 9, 0, 11, 11)
    _open_north(g); _open_south(g); _open_west(g)
    return _grid_to_str(g)

def make_room_corner_ne():
    """L形：N/E开口"""
    g = _mk_empty()
    _fill_rect(g, 0, 9, 10, 11)
    _fill_rect(g, 9, 9, 11, 20)
    _open_north(g); _open_east(g)
    return _grid_to_str(g)

def make_room_corner_nw():
    """L形：N/W开口"""
    g = _mk_empty()
    _fill_rect(g, 0, 9, 10, 11)
    _fill_rect(g, 9, 0, 11, 11)
    _open_north(g); _open_west(g)
    return _grid_to_str(g)

def make_room_corner_se():
    """L形：S/E开口"""
    g = _mk_empty()
    _fill_rect(g, 10, 9, 20, 11)
    _fill_rect(g, 9, 9, 11, 20)
    _open_south(g); _open_east(g)
    return _grid_to_str(g)

def make_room_corner_sw():
    """L形：S/W开口"""
    g = _mk_empty()
    _fill_rect(g, 10, 9, 20, 11)
    _fill_rect(g, 9, 0, 11, 11)
    _open_south(g); _open_west(g)
    return _grid_to_str(g)

def make_room_chamber():
    """中央大厅（四向开口）+ 柱子阵列"""
    g = _mk_empty()
    _fill_rect(g, 2, 2, 18, 18)
    _open_north(g); _open_south(g)
    _open_west(g);  _open_east(g)
    # 内部柱子
    for r in [5, 10, 15]:
        for c in [5, 10, 15]:
            g[r][c] = '#'
    # 发光柱
    g[5][5]   = '2'
    g[5][15]  = '2'
    g[15][5]  = '2'
    g[15][15] = '2'
    return _grid_to_str(g)

def make_room_maze_ns():
    """迷宫型：N/S开口，内部迷宫墙"""
    g = _mk_empty()
    _fill_rect(g, 0, 1, 20, 19)
    # 随机内墙（用固定种子保证每次相同结构）
    inner_walls = [
        (3,3,3,8),(3,12,3,17),(6,1,6,6),(6,10,6,15),
        (9,4,9,9),(9,11,9,16),(12,2,12,7),(12,13,12,18),
        (15,5,15,10),(15,11,15,16),
    ]
    for r1,c1,r2,c2 in inner_walls:
        _fill_rect(g, r1, c1, r2, c2, '#')
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_maze_ew():
    """迷宫型：E/W开口"""
    g = _mk_empty()
    _fill_rect(g, 1, 0, 19, 20)
    inner_walls = [
        (2,3,7,3),(2,9,7,9),(2,15,7,15),
        (13,3,18,3),(13,9,18,9),(13,15,18,15),
        (8,6,12,6),(8,12,12,12),
    ]
    for r1,c1,r2,c2 in inner_walls:
        _fill_rect(g, r1, c1, r2, c2, '#')
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_dark_cross():
    """暗室十字（发光柱 + 四向出口）"""
    g = _mk_empty()
    _fill_rect(g, 9, 0, 11, 20)
    _fill_rect(g, 0, 9, 20, 11)
    # 四角发光柱
    for r, c in [(3,3),(3,17),(17,3),(17,17)]:
        g[r][c] = '2'
    _open_north(g); _open_south(g)
    _open_west(g);  _open_east(g)
    return _grid_to_str(g)

def make_room_long_corridor_ns():
    """细长南北走廊（带侧龛）"""
    g = _mk_empty()
    _fill_rect(g, 0, 8, 20, 12)
    # 左右小龛
    for r in [3, 7, 13, 17]:
        _fill_rect(g, r, 5, r+1, 7)
        _fill_rect(g, r, 13, r+1, 15)
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_long_corridor_ew():
    """细长东西走廊（带上下凹槽）"""
    g = _mk_empty()
    _fill_rect(g, 8, 0, 12, 20)
    for c in [3, 7, 13, 17]:
        _fill_rect(g, 5, c, 7, c+1)
        _fill_rect(g, 13, c, 15, c+1)
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_secret_ns():
    """暗路南北室：弯曲路径，N/S开口"""
    g = _mk_empty()
    # S形路径
    _fill_rect(g, 0, 9, 7, 11)
    _fill_rect(g, 6, 6, 8, 14)
    _fill_rect(g, 7, 8, 14, 10)
    _fill_rect(g, 13, 8, 15, 15)
    _fill_rect(g, 14, 9, 20, 11)
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_hub():
    """中枢大厅：四向出口，宽阔空间"""
    g = _mk_empty()
    _fill_rect(g, 1, 1, 19, 19)
    # 中心岛
    _fill_rect(g, 8, 8, 12, 12, '#')
    g[10][10] = '2'
    # 走廊连接出口
    _fill_rect(g, 0, 9, 1, 11)
    _fill_rect(g, 19, 9, 20, 11)
    _fill_rect(g, 9, 0, 11, 1)
    _fill_rect(g, 9, 19, 11, 20)
    _open_north(g); _open_south(g)
    _open_west(g);  _open_east(g)
    return _grid_to_str(g)

def make_room_glow_hall():
    """发光走廊大厅（双向）"""
    g = _mk_empty()
    _fill_rect(g, 3, 0, 17, 20)
    for c in range(2, 19, 3):
        g[5][c]  = '2'
        g[15][c] = '2'
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_red_warning():
    """红色警示区（N/S）"""
    g = _mk_empty()
    _fill_rect(g, 0, 6, 20, 14)
    for r in [3, 8, 12, 17]:
        g[r][7] = '3'  # 红色装饰
        g[r][13] = '3'
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_golden_treasury():
    """金色宝库（十字）"""
    g = _mk_empty()
    _fill_rect(g, 9, 0, 11, 20)
    _fill_rect(g, 0, 9, 20, 11)
    # 金色装饰物
    for r, c in [(3,3),(3,17),(17,3),(17,17),(10,5),(10,15),(5,10),(15,10)]:
        g[r][c] = '4'
    _open_north(g); _open_south(g)
    _open_west(g);  _open_east(g)
    return _grid_to_str(g)

def make_room_garden():
    """绿色花园（N/S）"""
    g = _mk_empty()
    _fill_rect(g, 0, 4, 20, 16)
    # 绿色植被
    for r in [2, 6, 10, 14, 18]:
        for c in [5, 9, 13]:
            if random.random() > 0.4:
                g[r][c] = '5'
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_energy_core():
    """紫色能量核心（四向）"""
    g = _mk_empty()
    _fill_rect(g, 4, 4, 16, 16)
    # 中心能量柱
    g[10][10] = '6'
    for r, c in [(6,6),(6,14),(14,6),(14,14)]:
        g[r][c] = '6'
    _open_north(g); _open_south(g)
    _open_west(g);  _open_east(g)
    return _grid_to_str(g)

def make_room_fire_exit():
    """橙色出口（E/W）"""
    g = _mk_empty()
    _fill_rect(g, 6, 0, 14, 20)
    for c in [4, 8, 12, 16]:
        g[7][c] = '7'
        g[13][c] = '7'
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_debris():
    """碎石废墟区（N/S）"""
    g = _mk_empty()
    _fill_rect(g, 0, 5, 20, 15)
    # 随机碎石
    for _ in range(12):
        r = random.randint(2, 18)
        c = random.randint(6, 14)
        if g[r][c] == '.':
            g[r][c] = '8'  # 灰色碎石装饰
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_tech_nook():
    """科技角落（T形 N/S/E）"""
    g = _mk_empty()
    _fill_rect(g, 0, 9, 20, 11)
    _fill_rect(g, 9, 9, 11, 20)
    # 科技装饰
    for r in [3, 7, 14, 18]:
        g[r][10] = '9'  # 青色科技装饰
    _open_north(g); _open_south(g); _open_east(g)
    return _grid_to_str(g)

def make_room_crystal_cavern():
    """水晶洞穴（双向 E/W）"""
    g = _mk_empty()
    _fill_rect(g, 2, 0, 18, 20)
    # 水晶柱
    for r in [4, 8, 12, 16]:
        g[r][6] = 'A'  # 蓝色水晶
        g[r][14] = 'A'
    # 特殊水晶
    g[8][10] = 'B'  # 紫水晶
    g[12][10] = 'B'
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_shrine():
    """神秘祭坛（N/S）"""
    g = _mk_empty()
    _fill_rect(g, 0, 5, 20, 15)
    # 中央祭坛
    _fill_rect(g, 8, 8, 12, 12, '#')
    g[10][10] = '2'  # 中心发光
    # 周围的发光装饰
    for r, c in [(6,7),(6,13),(14,7),(14,13)]:
        g[r][c] = '2'
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_battle_arena():
    """战斗竞技场（四向）"""
    g = _mk_empty()
    _fill_rect(g, 2, 2, 18, 18)
    # 环形障碍物
    for angle in range(0, 360, 30):
        import math
        rad = math.radians(angle)
        r = int(8 + 2 * math.sin(angle * 0.3))
        c = int(10 + 6 * math.cos(rad))
        if 2 <= r <= 18 and 2 <= c <= 18:
            g[r][c] = '#'
    _open_north(g); _open_south(g)
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_data_temple():
    """数据神殿（T形 N/S/E）"""
    g = _mk_empty()
    _fill_rect(g, 0, 7, 20, 13)
    _fill_rect(g, 7, 7, 13, 20)
    # 矩阵装饰
    for r in [3, 6, 9, 12, 15, 18]:
        for c in [8, 10, 12]:
            if random.random() > 0.5:
                g[r][c] = '9'
    _open_north(g); _open_south(g); _open_east(g)
    return _grid_to_str(g)

def make_room_abandoned_lab():
    """废弃实验室（双向 E/W）"""
    g = _mk_empty()
    _fill_rect(g, 3, 0, 17, 20)
    # 实验台
    for c in [4, 8, 12, 16]:
        _fill_rect(g, 4, c, 7, c+2, '#')
        if random.random() > 0.3:
            g[5][c+1] = '8'  # 灰色碎石/破损设备
    # 警示标志
    for c in [6, 14]:
        g[10][c] = '3'  # 红色警示
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_floating_platforms():
    """悬浮平台（N/S）"""
    g = _mk_empty()
    _fill_rect(g, 0, 4, 20, 16)
    # 随机悬浮平台
    platforms = [(3,6),(5,10),(8,8),(11,14),(14,7),(16,12)]
    for r, c in platforms:
        _fill_rect(g, r, c, r, c+2, '#')
        if random.random() > 0.4:
            g[r-1][c+1] = '2'
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_bone_garden():
    """骨骸花园（S/E/W）"""
    g = _mk_empty()
    _fill_rect(g, 4, 0, 20, 20)
    # 骨骸装饰
    for _ in range(8):
        r = random.randint(6, 18)
        c = random.randint(4, 16)
        g[r][c] = '8'
        if c + 1 < 16:
            g[r][c+1] = '8'
    _open_south(g); _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_neon_highway():
    """霓虹高速（E/W）"""
    g = _mk_empty()
    _fill_rect(g, 0, 0, 20, 20)
    # 中心分隔带
    for r in [9, 10, 11]:
        for c in range(0, 20):
            if c % 4 == 0:
                g[r][c] = '9'  # 科技装饰
    # 霓虹灯柱
    for c in [3, 7, 11, 15, 19]:
        g[3][c] = '3'  # 红色
        g[17][c] = '2'  # 青色
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_forgotten_tomb():
    """遗忘陵墓（N/S）"""
    g = _mk_empty()
    _fill_rect(g, 0, 3, 20, 17)
    # 石棺
    _fill_rect(g, 8, 8, 12, 12, '#')
    g[10][10] = '6'  # 紫色能量
    # 石像
    for r, c in [(4,5),(4,14),(16,5),(16,14)]:
        g[r][c] = '8'
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_trader_outpost():
    """商人哨站（十字）"""
    g = _mk_empty()
    _fill_rect(g, 9, 0, 11, 20)
    _fill_rect(g, 0, 9, 20, 11)
    # 商店摊位
    for r, c in [(8,4),(8,15),(12,4),(12,15)]:
        _fill_rect(g, r, c, r+2, c+3, '#')
        g[r+1][c+1] = '4'  # 金色商品
    _open_north(g); _open_south(g)
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_acid_pool():
    """酸液池（N/S）"""
    g = _mk_empty()
    _fill_rect(g, 0, 5, 20, 15)
    # 酸液池
    for r in [6, 8, 10, 12, 14]:
        for c in [6, 8, 10, 12, 14]:
            if random.random() > 0.3:
                g[r][c] = '5'  # 绿色腐蚀
    # 安全路径
    _fill_rect(g, 6, 4, 14, 5, '.')
    _fill_rect(g, 6, 15, 14, 16, '.')
    _open_north(g); _open_south(g)
    return _grid_to_str(g)

def make_room_ancient_library():
    """远古图书馆（E/W）"""
    g = _mk_empty()
    _fill_rect(g, 1, 0, 19, 20)
    # 书架
    for r in [2, 4, 6, 8, 10, 12, 14, 16, 18]:
        for c in [3, 7, 11, 15, 19]:
            if c <= 18:
                g[r][c] = '#'
                if random.random() > 0.5:
                    g[r][c-1] = '4'  # 金色书籍
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_generator_room():
    """发电机室（T形 N/S/W）"""
    g = _mk_empty()
    _fill_rect(g, 0, 9, 20, 11)
    _fill_rect(g, 9, 0, 11, 11)
    # 大型发电机
    _fill_rect(g, 6, 3, 14, 7, '#')
    g[8][5] = '7'  # 橙色能量线
    g[8][6] = '7'
    g[12][5] = '7'
    g[12][6] = '7'
    _open_north(g); _open_south(g); _open_west(g)
    return _grid_to_str(g)

def make_room_pit_trap():
    """陷阱坑室（E/W）"""
    g = _mk_empty()
    _fill_rect(g, 0, 0, 20, 20)
    # 中央深坑
    _fill_rect(g, 7, 7, 13, 13, '8')
    # 围绕陷阱的窄桥
    _fill_rect(g, 10, 0, 10, 6, '.')
    _fill_rect(g, 10, 14, 10, 20, '.')
    _fill_rect(g, 0, 10, 6, 10, '.')
    _fill_rect(g, 14, 10, 20, 10, '.')
    _open_west(g); _open_east(g)
    return _grid_to_str(g)

def make_room_portal_gate():
    """传送门大厅（四向）- 稀有事件"""
    g = _mk_empty()
    _fill_rect(g, 4, 4, 16, 16)
    # 中心传送门
    g[10][10] = 'C'  # 传送门格子
    # 四角发光柱
    for r, c in [(6,6),(6,14),(14,6),(14,14)]:
        g[r][c] = '2'
    # 传送门周围科技装饰
    g[9][10] = '9'
    g[11][10] = '9'
    g[10][9] = '9'
    g[10][11] = '9'
    _open_north(g); _open_south(g)
    _open_west(g);  _open_east(g)
    return _grid_to_str(g)

# ─── 按出口方向分组的模板池 ───
# 键为出口方向集合的 frozenset，值为 list[生成函数]

_ALL_TEMPLATES = {
    frozenset({'N','S','E','W'}): [make_room_cross, make_room_chamber,
                                    make_room_dark_cross, make_room_hub,
                                    make_room_golden_treasury, make_room_energy_core,
                                    make_room_battle_arena, make_room_trader_outpost,
                                    make_room_portal_gate],
    frozenset({'N','S'}):         [make_room_hall_ns, make_room_hall_ns_glow,
                                    make_room_maze_ns, make_room_long_corridor_ns,
                                    make_room_secret_ns, make_room_red_warning,
                                    make_room_garden, make_room_debris,
                                    make_room_shrine, make_room_floating_platforms,
                                    make_room_forgotten_tomb, make_room_acid_pool],
    frozenset({'E','W'}):         [make_room_hall_ew, make_room_maze_ew,
                                    make_room_long_corridor_ew, make_room_glow_hall,
                                    make_room_fire_exit, make_room_crystal_cavern,
                                    make_room_neon_highway, make_room_ancient_library,
                                    make_room_abandoned_lab, make_room_pit_trap],
    frozenset({'N','E','W'}):     [make_room_tee_n, make_room_tech_nook,
                                    make_room_data_temple, make_room_generator_room],
    frozenset({'S','E','W'}):     [make_room_tee_s, make_room_bone_garden],
    frozenset({'N','S','E'}):     [make_room_tee_e],
    frozenset({'N','S','W'}):     [make_room_tee_w],
    frozenset({'N','E'}):         [make_room_corner_ne],
    frozenset({'N','W'}):         [make_room_corner_nw],
    frozenset({'S','E'}):         [make_room_corner_se],
    frozenset({'S','W'}):         [make_room_corner_sw],
}

_OPPOSITE = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}
_DIR_DELTA = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}

# ─────────────────────── 世界地图管理器 ─────────────────────────
# 区块视野范围设置
CHUNK_VIEW_RANGE = 1      # 视野范围：玩家周边 1 格
CHUNK_PRELOAD_RANGE = 1   # 预加载范围：仅玩家所在+周围1格（3×3）
CHUNK_CLEAN_RANGE = 2     # 清理范围：超出此范围的区块将被卸载

class WorldMap:
    """
    管理无限延伸的区块网格。
    区块坐标 (cx, cy)，世界坐标 = (cx*CHUNK_SIZE + local_x, cy*CHUNK_SIZE + local_y)。
    
    策略：玩家在哪，预加载周围区块；走远了的区块卸载。
    未加载区块cell()返回1（墙），防止空气墙。
    """
    def __init__(self, seed=None):
        self._rng = random.Random(seed or random.randint(0, 2**32))
        self._chunks: dict[tuple[int,int], list[str]] = {}   # (cx,cy) -> 21行字符串
        self._exits:  dict[tuple[int,int], set[str]]  = {}   # (cx,cy) -> {'N','S','E','W'}子集
        self._template_names: dict[tuple[int,int], str] = {} # (cx,cy) -> 模板函数名（暗室判定用）
        # 生成起始区块（四向出口）
        self._generate_chunk((0, 0), required_exits={'N','S','E','W'})

    def find_safe_spawn(self, cx, cy):
        """寻找安全出生点：空地、远离墙壁、周围无危险"""
        # 优先检查当前区块，找不到则检查周围区块
        for check_cx, check_cy in [(cx, cy)] + [(cx + dx, cy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]:
            spawn = self._find_spawn_in_chunk(check_cx, check_cy)
            if spawn is not None:
                return spawn
        # 最终回退：区块中心
        return (cx * CHUNK_SIZE + 10.5, cy * CHUNK_SIZE + 10.5)
    
    def _find_spawn_in_chunk(self, cx, cy):
        """在指定区块内寻找安全出生点"""
        chunk = self._chunks.get((cx, cy))
        if chunk is None:
            self._generate_chunk((cx, cy))
            chunk = self._chunks.get((cx, cy))
        if chunk is None:
            return None
        center = CHUNK_SIZE // 2
        
        def _check_cell(lx, ly):
            """检查格子是否适合作为出生点"""
            if not (0 <= lx < CHUNK_SIZE and 0 <= ly < CHUNK_SIZE):
                return False
            ch = chunk[ly][lx]
            if ch != '.':  # 必须为空地
                return False
            # 检查周围 5x5 范围内的危险和墙壁
            for dy2 in range(-2, 3):
                for dx2 in range(-2, 3):
                    nx, ny = lx + dx2, ly + dy2
                    if 0 <= nx < CHUNK_SIZE and 0 <= ny < CHUNK_SIZE:
                        neighbor = chunk[ny][nx]
                        # 危险类型：墙壁(1)、红色警示区(3)、酸液池(5)、传送门(C)
                        if neighbor in ('#', '3', '5', 'C'):
                            return False
            return True
        
        # 螺旋向外搜索，优先中心附近
        for radius in range(6):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if abs(dx) != radius and abs(dy) != radius:
                        continue
                    lx, ly = center + dx, center + dy
                    if _check_cell(lx, ly):
                        return (cx * CHUNK_SIZE + lx + 0.5, cy * CHUNK_SIZE + ly + 0.5)
        
        # 回退：找任意空地（不检查周围）
        for ly in range(CHUNK_SIZE):
            for lx in range(CHUNK_SIZE):
                if chunk[ly][lx] == '.':
                    return (cx * CHUNK_SIZE + lx + 0.5, cy * CHUNK_SIZE + ly + 0.5)
        return None

    # ── 出口查询 ──
    def _has_exit(self, cx, cy, direction) -> bool:
        return direction in self._exits.get((cx, cy), set())

    # ── 区块生成 ──
    def _generate_chunk(self, coord, required_exits=None):
        if coord in self._chunks:
            return
        cx, cy = coord

        # 由相邻区块推断必需出口
        forced = set(required_exits or [])
        for d, (dx, dy) in _DIR_DELTA.items():
            nb = (cx + dx, cy + dy)
            if nb in self._exits and _OPPOSITE[d] in self._exits[nb]:
                forced.add(d)

        # 从模板库中筛选并随机选择
        exits = self._pick_exits(forced)
        template_fn = self._pick_template(exits)
        grid = template_fn()
        self._chunks[coord] = grid
        self._exits[coord]  = set(exits)
        # 记录模板函数名（用于暗室判定）
        self._template_names[coord] = template_fn.__name__

    def _pick_exits(self, forced):
        """在 forced 基础上随机扩充出口（确保至少有一条路回来）"""
        exits = set(forced)
        # 随机添加额外出口
        for d in ['N', 'S', 'E', 'W']:
            if d not in exits and self._rng.random() < 0.45:
                exits.add(d)
        # 保证至少2个出口
        while len(exits) < 2:
            exits.add(self._rng.choice(['N', 'S', 'E', 'W']))
        return frozenset(exits)

    def _pick_template(self, exits):
        """从模板库中找最匹配的模板，优先精确匹配"""
        if exits in _ALL_TEMPLATES:
            return self._rng.choice(_ALL_TEMPLATES[exits])
        # 找超集（模板出口包含所有必需出口的）
        candidates = []
        for tmpl_exits, fns in _ALL_TEMPLATES.items():
            if exits.issubset(tmpl_exits):
                candidates.extend(fns)
        if candidates:
            return self._rng.choice(candidates)
        # 兜底：十字走廊
        return make_room_cross

    # ── 区块按需生成 + 远处卸载 ──
    def ensure_neighbors(self, cx: int, cy: int):
        """确保 (cx,cy) 及其预加载范围区块已生成，并卸载远处区块"""
        for dcy in range(-CHUNK_PRELOAD_RANGE, CHUNK_PRELOAD_RANGE + 1):
            for dcx in range(-CHUNK_PRELOAD_RANGE, CHUNK_PRELOAD_RANGE + 1):
                nb = (cx + dcx, cy + dcy)
                if nb not in self._chunks:
                    self._generate_chunk(nb)
        self._cleanup_distant_chunks(cx, cy)
    
    def _cleanup_distant_chunks(self, cx: int, cy: int):
        """卸载距离玩家超过 CHUNK_CLEAN_RANGE 的区块"""
        to_remove = []
        for chunk_coord in self._chunks.keys():
            dx = abs(chunk_coord[0] - cx)
            dy = abs(chunk_coord[1] - cy)
            if dx > CHUNK_CLEAN_RANGE or dy > CHUNK_CLEAN_RANGE:
                to_remove.append(chunk_coord)
        for coord in to_remove:
            del self._chunks[coord]
            if coord in self._exits:
                del self._exits[coord]
            if coord in self._template_names:
                del self._template_names[coord]

    # ── 世界坐标→格值 ──
    def cell(self, wx: float, wy: float) -> int:
        """返回世界坐标 (wx,wy) 处的格值：
        0=空  1=墙  2=发光柱  3=红色装饰  4=金色装饰
        5=绿色植被  6=紫色能量  7=橙色光源  8=灰色碎石  9=科技装饰
        A=蓝色水晶  B=紫水晶
        
        注意：未生成的区块返回1（墙壁），阻止玩家和射线进入虚空。
        """
        cx = int(math.floor(wx / CHUNK_SIZE))
        cy = int(math.floor(wy / CHUNK_SIZE))
        # 修复：使用相对坐标计算，避免 int() 对负数截断导致的错误
        lx = int(wx - cx * CHUNK_SIZE)
        ly = int(wy - cy * CHUNK_SIZE)
        # 边界夹紧
        lx = max(0, min(CHUNK_SIZE - 1, lx))
        ly = max(0, min(CHUNK_SIZE - 1, ly))
        chunk = self._chunks.get((cx, cy))
        if chunk is None:
            return 1   # 未生成区块视为墙壁，防止空气墙
        ch = chunk[ly][lx]
        if ch == '.': return 0
        if ch == '#': return 1
        if ch == '2': return 2
        if ch == '3': return 3
        if ch == '4': return 4
        if ch == '5': return 5
        if ch == '6': return 6
        if ch == '7': return 7
        if ch == '8': return 8
        if ch == '9': return 9
        if ch == 'A': return 10
        if ch == 'B': return 11
        if ch == 'C': return 12  # 传送门
        return 1
    
    def cell_blocking(self, wx: float, wy: float) -> bool:
        """检查指定坐标是否阻挡移动（只检查已生成的墙）"""
        return self.cell(wx, wy) == 1

    # ── 当前区块坐标 ──
    @staticmethod
    def world_to_chunk(wx: float, wy: float) -> tuple[int, int]:
        return (int(math.floor(wx / CHUNK_SIZE)),
                int(math.floor(wy / CHUNK_SIZE)))

    # ── 已生成区块列表 ──
    def generated_chunks(self):
        return list(self._chunks.keys())

    # ── 区块出口信息 ──
    def exits_of(self, cx, cy):
        return self._exits.get((cx, cy), set())

    # ── 暗室判定 ──
    def is_dark_room(self, cx: int, cy: int) -> bool:
        """判断指定区块是否为暗室（基于生成时使用的模板函数名）"""
        # 首先检查模板名标记
        template_name = self._template_names.get((cx, cy))
        if template_name and template_name in DARK_ROOM_TEMPLATES:
            return True
        # 备用方案：如果区块中有少量发光柱且无大面积空地，视为暗室
        chunk = self._chunks.get((cx, cy))
        if chunk is None:
            return False
        glow_count = 0
        open_count = 0
        for row in chunk:
            for ch in row:
                if ch == '2':
                    glow_count += 1
                elif ch == '.':
                    open_count += 1
        # 暗室特征：发光柱少且空地比例较低
        if glow_count <= 4 and open_count < 80:
            return True
        return False

    def chunk_zone(self, cx: int, cy: int) -> int:
        """返回区块的主导区域类型（统计装饰格子最多的类型）"""
        chunk = self._chunks.get((cx, cy))
        if chunk is None:
            return 2  # 默认空灵神秘
        counts: dict[int, int] = {}
        for row in chunk:
            for ch in row:
                if ch in ('.', '#'):
                    continue
                if '2' <= ch <= '9':
                    v = int(ch)
                elif ch == 'A': v = 10
                elif ch == 'B': v = 11
                elif ch == 'C': v = 12
                else:
                    continue
                if v >= 2:
                    counts[v] = counts.get(v, 0) + 1
        return max(counts, key=counts.get) if counts else 2

# ─────────────────────── 游戏增强系统类 ─────────────────────────

@dataclass
class EnergySystem:
    """玩家能量管理"""
    energy: float = ENERGY_MAX
    max_energy: float = ENERGY_MAX
    drain_move: float = 1.0       # 点/秒，移动时消耗
    drain_red: float = 3.0        # 点/秒，红色区消耗
    recover_glow: float = 0.5     # 点/秒，发光柱附近恢复
    shield_timer: float = 0.0     # 护盾剩余时间（秒）
    grace_timer: float = SPAWN_GRACE_PERIOD  # 出生保护期倒计时
    is_dead: bool = False

    def update(self, dt: float, is_moving: bool, zone_type: int,
               near_glow: bool, drain_mult: float = 1.0, shielded: bool = False):
        """每帧更新能量值
        dt: 帧间隔（秒）
        is_moving: 玩家是否在移动
        zone_type: 当前脚下格子类型（3=红色区）
        near_glow: 是否在发光柱(2)附近
        drain_mult: AbilitySystem 提供的消耗倍率
        shielded: 是否处于危险免疫状态（AbilitySystem全收集免疫）
        """
        # 护盾倒计时
        if self.shield_timer > 0:
            self.shield_timer -= dt

        # 出生保护期倒计时
        if self.grace_timer > 0:
            self.grace_timer -= dt
            # 保护期内仅发光柱恢复生效，不消耗能量
            if near_glow and self.energy < self.max_energy:
                self.energy += self.recover_glow * dt
                self.energy = min(self.max_energy, self.energy)
            return

        if shielded:
            return  # 危险免疫期完全不消耗

        # 护盾减半消耗（能量核心护盾，独立于AbilitySystem免疫）
        shield_mult = 0.5 if self.shield_timer > 0 else 1.0

        # 消耗计算
        # 红色区独立消耗（不论移动与否）
        if zone_type == 3:
            self.energy -= self.drain_red * dt * drain_mult * shield_mult
            if is_moving:
                self.energy -= self.drain_move * dt * drain_mult * shield_mult
        elif is_moving:
            self.energy -= self.drain_move * dt * drain_mult * shield_mult

        # 酸液池接触（格子5）
        if zone_type == 5:
            self.energy -= ACID_DRAIN_RATE * dt * shield_mult

        # 发光柱恢复
        if near_glow and self.energy < self.max_energy:
            self.energy += self.recover_glow * dt

        # 夹紧
        self.energy = max(0.0, min(self.max_energy, self.energy))
        if self.energy <= 0:
            self.is_dead = True

    def recover(self, amount: float):
        """恢复指定量能量"""
        self.energy = min(self.max_energy, self.energy + amount)

    def activate_shield(self, duration: float):
        """激活护盾指定秒数"""
        self.shield_timer = duration

    def is_shielded(self) -> bool:
        """是否处于护盾状态"""
        return self.shield_timer > 0

    def get_ratio(self) -> float:
        """获取能量比例 0.0~1.0"""
        return self.energy / self.max_energy if self.max_energy > 0 else 0.0

    def reset(self):
        """重置能量系统"""
        self.energy = self.max_energy
        self.shield_timer = 0.0
        self.grace_timer = SPAWN_GRACE_PERIOD
        self.is_dead = False


@dataclass
class FragmentSystem:
    """碎片收集系统"""
    collected: dict = None        # {deco_type: count}
    interacted: dict = None       # {(cx,cy,lx,ly): True}
    codex: dict = None            # 图鉴跨局保留 {deco_type: True}
    total_types: int = FRAGMENT_TYPES  # 10种碎片（类型2-11）

    def __post_init__(self):
        if self.collected is None:
            self.collected = {}
        if self.interacted is None:
            self.interacted = {}
        if self.codex is None:
            self.codex = {}

    def collect(self, deco_type: int, key: tuple) -> bool:
        """拾取碎片并标记已互动，返回是否成功"""
        if key in self.interacted:
            return False  # 已拾取过
        self.interacted[key] = True
        self.collected[deco_type] = self.collected.get(deco_type, 0) + 1
        # 图鉴记录（跨局保留）
        self.codex[deco_type] = True
        return True

    def has_interacted(self, key: tuple) -> bool:
        """是否已互动过"""
        return key in self.interacted

    def get_count(self, deco_type: int) -> int:
        """获取指定类型碎片数量"""
        return self.collected.get(deco_type, 0)

    def get_total(self) -> int:
        """获取碎片总数"""
        return sum(self.collected.values())

    def get_unique_types(self) -> int:
        """获取已收集的不同类型数"""
        return len(self.collected)

    def is_all_types_collected(self) -> bool:
        """是否收集齐所有类型"""
        return self.get_unique_types() >= self.total_types

    def reset(self):
        """重置（保留图鉴）"""
        self.collected.clear()
        self.interacted.clear()
        # 注意：codex 不清除，跨局保留


class InteractionSystem:
    """E键交互系统"""
    def __init__(self, interact_range: float = 2.0):
        self.interact_range = interact_range

    def find_nearest_deco(self, px, py, world, fragments) -> Optional[tuple]:
        """扫描玩家周围，找到最近的未互动装饰物
        返回 (wx, wy, deco_type, interact_key) 或 None
        """
        scan_r = int(self.interact_range) + 1
        best_dist = self.interact_range
        best = None
        for dy in range(-scan_r, scan_r + 1):
            for dx in range(-scan_r, scan_r + 1):
                wx = int(px) + dx
                wy = int(py) + dy
                ct = world.cell(wx + 0.5, wy + 0.5)
                if ct < 2 or ct >= 12:  # 传送门(12)不可交互收集
                    continue
                cx, cy = world.world_to_chunk(wx + 0.5, wy + 0.5)
                lx = int(wx + 0.5) % CHUNK_SIZE
                ly = int(wy + 0.5) % CHUNK_SIZE
                key = (cx, cy, lx, ly)
                if fragments.has_interacted(key):
                    continue
                dist = math.sqrt((wx + 0.5 - px)**2 + (wy + 0.5 - py)**2)
                if dist < best_dist:
                    best_dist = dist
                    best = (wx, wy, ct, key)
        return best

    def is_in_range(self, px, py, world, fragments) -> bool:
        """是否有可交互的装饰物在范围内"""
        return self.find_nearest_deco(px, py, world, fragments) is not None

    def interact(self, px, py, world, fragments, energy) -> Optional[dict]:
        """执行交互，返回交互结果字典"""
        result = self.find_nearest_deco(px, py, world, fragments)
        if result is None:
            return None
        wx, wy, deco_type, key = result
        # 传送门等非碎片类型不可交互
        if deco_type >= 12:
            return None
        # 拾取碎片
        fragments.collect(deco_type, key)
        # 根据类型执行特殊效果
        effects = {"type": deco_type}
        if deco_type == 2:   # 青绿发光柱 → 恢复20能量
            energy.recover(20)
            effects["energy_recover"] = 20
        elif deco_type == 4: # 金色宝库 → 高分碎片
            effects["bonus_score"] = True
        elif deco_type == 6: # 紫色能量核心 → 临时护盾10秒
            energy.activate_shield(10.0)
            effects["shield"] = 10.0
        elif deco_type == 7: # 橙色光源 → 恢复少量+标记
            energy.recover(5)
            effects["energy_recover"] = 5
            effects["marker"] = (wx, wy)
        return effects


class AbilitySystem:
    """能力增强系统"""
    def __init__(self, fragments: FragmentSystem):
        self.fragments = fragments
        self.danger_immune_timer = 0.0
        self._immune_cooldown: float = 0.0  # 全收集免疫冷却（防止永久免疫）

    def update(self, dt: float):
        """每帧更新能力状态"""
        if self.danger_immune_timer > 0:
            self.danger_immune_timer -= dt
        if self._immune_cooldown > 0:
            self._immune_cooldown -= dt
        # 检查全收集免疫
        self._check_passives()

    def _check_passives(self):
        """检查被动能力状态（每帧调用，实际检查在getter中）"""
        pass

    def get_depth_multiplier(self) -> float:
        """视野增强：5个同色碎片 → +15%"""
        for deco_type in range(2, 12):
            if self.fragments.get_count(deco_type) >= 5:
                return 1.15
        return 1.0

    def get_drain_multiplier(self) -> float:
        """耗能降低：5个同色碎片 → -20%"""
        for deco_type in range(2, 12):
            if self.fragments.get_count(deco_type) >= 5:
                return 0.8
        return 1.0

    def get_speed_multiplier(self) -> float:
        """移速增加：5个同色碎片 → +15%"""
        for deco_type in range(2, 12):
            if self.fragments.get_count(deco_type) >= 5:
                return 1.15
        return 1.0

    def is_danger_immune(self) -> bool:
        """10种各1 → 危险免疫5秒（含冷却60秒防止永久触发）"""
        # 仍在免疫期内
        if self.danger_immune_timer > 0:
            return True
        # 冷却中，不可再次触发
        if self._immune_cooldown > 0:
            return False
        # 全收集且冷却结束 → 激活免疫
        if self.fragments.is_all_types_collected():
            self.danger_immune_timer = 5.0
            self._immune_cooldown = 65.0  # 5秒免疫+60秒冷却
            return True
        return False

    def has_vision_bonus(self) -> bool:
        """是否有视野增强"""
        return self.get_depth_multiplier() > 1.0

    def has_drain_reduction(self) -> bool:
        """是否有耗能降低"""
        return self.get_drain_multiplier() < 1.0

    def has_speed_bonus(self) -> bool:
        """是否有移速增加"""
        return self.get_speed_multiplier() > 1.0


class ExplorationTracker:
    """区块探索追踪"""
    def __init__(self, explore_radius: int = 10):
        self.explored: dict[tuple, bool] = {}
        self.explore_radius = explore_radius

    def check_and_mark(self, px, py, world):
        """检查玩家是否在区块中心附近，标记已探索"""
        cx, cy = world.world_to_chunk(px, py)
        # 计算区块中心的世界坐标
        center_wx = cx * CHUNK_SIZE + CHUNK_SIZE // 2
        center_wy = cy * CHUNK_SIZE + CHUNK_SIZE // 2
        dist = math.sqrt((px - center_wx)**2 + (py - center_wy)**2)
        if dist <= self.explore_radius:
            self.explored[(cx, cy)] = True

    def is_explored(self, cx, cy) -> bool:
        """指定区块是否已探索"""
        return (cx, cy) in self.explored

    def get_explored_count(self) -> int:
        """获取已探索区块数"""
        return len(self.explored)

    def reset(self):
        """重置探索记录"""
        self.explored.clear()

# ─────────────────────── 区域音效系统 ─────────────────────────
PITCH_RATIO = 2.0 ** (SEMITONES_DOWN / 12.0)
# 不同装饰物类型对应不同的音效风格
# 2: 青绿发光 - 空灵神秘  |  3: 红色警示 - 紧张警告  |  4: 金色宝库 - 庄严神圣
# 5: 绿色植被 - 自然平和  |  6: 紫色能量 - 科幻能量  |  7: 橙色出口 - 温暖引导
# 8: 灰色碎石 - 废墟沉默  |  9: 科技装饰 - 电子数字  |  A: 蓝色水晶 - 冰冷空灵
# B: 紫水晶 - 神秘魔法

# ── 音符频率表 ──
NOTE_FREQS = {
    "C3":130.81,"D3":146.83,"E3":164.81,"F3":174.61,"G3":196.00,"A3":220.00,"B3":246.94,
    "C4":261.63,"D4":293.66,"E4":329.63,"F4":349.23,"G4":392.00,"A4":440.00,"B4":493.88,
    "C5":523.25,"D5":587.33,"E5":659.26,"F5":698.46,"G5":783.99,"A5":880.00,"B5":987.77,
    "C6":1046.50,
}

# ── 区域音效配置 ──
class ZoneSound:
    """每个区域的音效配置"""
    def __init__(self, notes, bpm, waveform, scale_mode, reverb_dense, pitch_shift):
        self.notes = notes          # 音符列表
        self.bpm = bpm              # 节拍速度
        self.waveform = waveform    # 波形类型
        self.scale_mode = scale_mode # 音阶模式
        self.reverb_dense = reverb_dense  # 混响浓度
        self.pitch_shift = pitch_shift    # 音高偏移


ZONE_CONFIGS = {
    2: ZoneSound(["C4","E4","G4","C5","G4","E4","C4","E4"], 90, "sine", "minor", 0.5, 0),    # 空灵神秘
    3: ZoneSound(["Eb4","Eb4","Bb3","Eb4","Eb4","Bb3","F4","Eb4"], 130, "sawtooth", "blues", 0.2, 2),  # 紧张警告
    4: ZoneSound(["C4","G4","C5","E5","G4","C5","A4","G4"], 70, "organ", "major", 0.6, -1),  # 庄严神圣
    5: ZoneSound(["A3","C4","E4","G4","E4","C4","A3","D4"], 85, "sine", "pentatonic_minor", 0.4, -2),  # 自然平和
    6: ZoneSound(["F4","F#4","G4","A4","C5","A4","G4","F#4"], 140, "square", "phrygian", 0.3, 3),  # 科幻能量
    7: ZoneSound(["C4","E4","G4","C5","E5","G4","C5","E4"], 100, "triangle", "major", 0.35, 0),  # 温暖引导
    8: ZoneSound(["D3","D3","A3","D3","D3","A3","G3","D3"], 60, "sine", "minor", 0.15, -3),   # 废墟沉默
    9: ZoneSound(["G4","A4","C5","D5","C5","A4","G4","F4"], 160, "square", "chromatic", 0.25, 4),  # 电子数字
    10: ZoneSound(["C5","F5","C5","A4","F4","C4","G4","C5"], 75, "sine", "crystal", 0.7, 1),  # 冰冷空灵
    11: ZoneSound(["Bb3","Eb4","G4","C5","G4","Eb4","Bb3","D4"], 95, "sine", "mystic", 0.55, 2),  # 神秘魔法
    12: ZoneSound(["C5","E5","G5","C6","G5","E5","C5","E5"], 110, "triangle", "major", 0.6, 5),  # 传送门
}

# ── 基础波形合成 ──
def _wave(freq, phase, waveform, duration):
    """生成单周期波形"""
    samples = []
    for i in range(int(SR * duration)):
        t = (phase + i) / SR
        if waveform == "sine":
            val = math.sin(2 * math.pi * freq * t)
        elif waveform == "sawtooth":
            val = 2.0 * ((freq * t) % 1.0) - 1.0
        elif waveform == "square":
            val = 1.0 if (freq * t) % 1.0 < 0.5 else -1.0
        elif waveform == "triangle":
            p = (freq * t) % 1.0
            val = 4.0 * p - 1.0 if p < 0.5 else 3.0 - 4.0 * p
        elif waveform == "organ":
            # 管风琴多谐波
            val = (math.sin(2*math.pi*freq*t)*0.5 +
                   math.sin(4*math.pi*freq*t)*0.3 +
                   math.sin(6*math.pi*freq*t)*0.15 +
                   math.sin(8*math.pi*freq*t)*0.05)
        else:
            val = math.sin(2 * math.pi * freq * t)
        samples.append(val)
    return samples

def _apply_envelope(samples, attack=0.01, decay=0.05, sustain=0.7, release=0.1):
    """ADSR包络"""
    n = len(samples)
    a = int(attack * n)
    d = int(decay * n)
    r = int(release * n)
    for i in range(n):
        t = i / n
        if i < a:
            samples[i] *= i / a if a > 0 else 1
        elif i < a + d:
            samples[i] *= 1.0 - (1.0 - sustain) * (i - a) / d if d > 0 else sustain
        elif i < n - r:
            samples[i] *= sustain
        else:
            samples[i] *= sustain * (n - i) / r if r > 0 else 0
    return samples

def _apply_reverb(samples, delay_ms=80, decay=0.3):
    """简单混响"""
    delay = int(delay_ms * SR / 1000)
    out = list(samples)
    for i in range(delay, len(out)):
        out[i] += out[i - delay] * decay
    return out


class ZoneSynth:
    """区域环境音效合成器，支持区域间渐变过渡"""
    def __init__(self):
        self._lock = threading.Lock()
        self._current_zone = 2  # 当前区域
        self._prev_zone = 2     # 前一区域
        self._fade = 1.0        # 渐变进度：0.0=纯旧区，1.0=纯新区
        self._fade_rate = 0.08  # 每拍渐变步进（~12拍≈6秒完成过渡）
        self._cur_beat = 0
        self._prev_beat = 0
        self._queue = []
        self._q_lock = threading.Lock()
        self.running = True
        self._frames = int(SR * BEAT * 0.5)
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def set_zone(self, zone_type):
        """切换到指定区域（触发渐变）"""
        with self._lock:
            if self._current_zone != zone_type:
                self._prev_zone = self._current_zone
                self._prev_beat = self._cur_beat  # 继承旧区节拍
                self._current_zone = zone_type
                self._cur_beat = 0
                self._fade = 0.0  # 开始渐变

    def _get_zone_config(self, zone=None):
        """获取指定区域的音效配置"""
        z = zone if zone is not None else self._current_zone
        return ZONE_CONFIGS.get(z, ZONE_CONFIGS[2])

    def _get_freq(self, note_name, pitch_shift=0):
        """获取音符频率"""
        base = NOTE_FREQS.get(note_name, 440.0)
        return base * (2.0 ** (pitch_shift / 12.0)) * PITCH_RATIO

    def _gen_zone_pcm(self, zone, beat):
        """生成指定区域、指定节拍的PCM"""
        cfg = self._get_zone_config(zone)
        beat_dur = 60.0 / cfg.bpm
        note_dur = beat_dur * 0.8
        note_name = cfg.notes[int(beat) % len(cfg.notes)]
        if note_name is None:
            return np.zeros(self._frames, dtype=np.float32)
        freq = self._get_freq(note_name, cfg.pitch_shift)
        samples = _wave(freq, 0.0, cfg.waveform, note_dur)
        samples = _apply_envelope(samples, attack=0.02, decay=0.1, sustain=0.6, release=0.15)
        samples = _apply_reverb(samples, delay_ms=int(50 + cfg.reverb_dense * 80),
                               decay=0.2 + cfg.reverb_dense * 0.3)
        raw = np.array(samples[:self._frames], dtype=np.float32)
        if len(raw) < self._frames:
            raw = np.pad(raw, (0, self._frames - len(raw)))
        raw = np.tanh(raw * 1.2).astype(np.float32)
        return (raw * VOL).astype(np.float32)

    def _gen_pcm(self):
        """生成一拍PCM，渐变期间混合新旧区域"""
        with self._lock:
            cur_zone = self._current_zone
            prev_zone = self._prev_zone
            fade = self._fade
            cur_beat = self._cur_beat
            self._cur_beat += 0.5
            if fade < 1.0:
                prev_beat = self._prev_beat
                self._prev_beat += 0.5
                self._fade = min(1.0, fade + self._fade_rate)

        cur_pcm = self._gen_zone_pcm(cur_zone, cur_beat)

        if fade < 1.0:
            prev_pcm = self._gen_zone_pcm(prev_zone, prev_beat)
            # 渐变混合：fade从0→1，旧区权重递减，新区权重递增
            mixed = (cur_pcm * fade + prev_pcm * (1.0 - fade))
            return mixed.astype(np.float32)

        return cur_pcm

    def _make_sound(self, pcm):
        i16 = np.clip(pcm * 32767.0, -32768, 32767).astype(np.int16)
        stereo = np.ascontiguousarray(np.column_stack([i16, i16]))
        return pygame.sndarray.make_sound(stereo)

    def _worker(self):
        while self.running:
            with self._q_lock:
                qlen = len(self._queue)
            if qlen < 6:
                pcm = self._gen_pcm()
                snd = self._make_sound(pcm)
                with self._q_lock:
                    self._queue.append(snd)
            else:
                time.sleep(0.005)

    def pop_sound(self):
        with self._q_lock:
            return self._queue.pop(0) if self._queue else None


# ── 保持向后兼容的 WalkerSynth 别名 ──
WalkerSynth = ZoneSynth

# ─────────────────────── 音效播放器（SFX） ─────────────────────────
class SFXPlayer:
    """简易音效播放器，不影响 ZoneSynth BGM"""
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._last_warning_t = 0.0  # 防止预警音效过于频繁

    def play_collect(self, deco_type: int):
        """拾取音效：根据碎片类型变化音高"""
        if not self.enabled:
            return
        try:
            freq = 400 + deco_type * 50
            snd = self._synth_beep(freq, 0.08)
            snd.play()
        except Exception:
            pass

    def play_warning(self):
        """危险预警蜂鸣"""
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last_warning_t < 1.0:
            return  # 每秒最多一次
        self._last_warning_t = now
        try:
            snd = self._synth_beep(200, 0.15)
            snd.play()
        except Exception:
            pass

    def play_shield(self):
        """护盾激活音效"""
        if not self.enabled:
            return
        try:
            snd = self._synth_beep(800, 0.2)
            snd.play()
        except Exception:
            pass

    def _synth_beep(self, freq: float, duration: float) -> pygame.mixer.Sound:
        """合成短促蜂鸣音"""
        n_samples = int(SR * duration)
        t = np.linspace(0, duration, n_samples, dtype=np.float32)
        wave = np.sin(2 * np.pi * freq * t).astype(np.float32)
        # 简单包络
        env = np.ones(n_samples, dtype=np.float32)
        fade = min(n_samples // 4, 200)
        if fade > 0:
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
        pcm = (wave * env * 0.3).astype(np.float32)
        i16 = np.clip(pcm * 32767, -32768, 32767).astype(np.int16)
        stereo = np.ascontiguousarray(np.column_stack([i16, i16]))
        return pygame.sndarray.make_sound(stereo)


# ─────────────────────── 粒子（支持多彩） ─────────────────────────
# 装饰物对应的粒子颜色
DECO_PARTICLE_COLORS = {
    2: [(0, 255, 200), (50, 200, 180)],
    3: [(220, 80, 60), (180, 50, 40)],
    4: [(230, 200, 100), (200, 160, 60)],
    5: [(80, 220, 140), (50, 180, 100)],
    6: [(200, 120, 255), (160, 80, 220)],
    7: [(255, 170, 80), (220, 130, 50)],
    8: [(140, 140, 150), (100, 100, 110)],
    9: [(100, 230, 255), (70, 190, 220)],
    10: [(120, 200, 255), (90, 160, 220)],
    11: [(180, 120, 240), (140, 80, 200)],
    12: [(255, 255, 100), (200, 200, 50)],   # 传送门 - 金色闪光
}

class Particle:
    __slots__ = ('x','y','vx','vy','life','max_life','r','col')

    def __init__(self, x, y, deco_type=2):
        self.x, self.y = float(x), float(y)
        a = random.uniform(0, 2 * math.pi)
        s = random.uniform(0.4, 2.2)
        self.vx = math.cos(a) * s
        self.vy = math.sin(a) * s
        self.life = self.max_life = random.randint(25, 70)
        self.r = random.randint(1, 3)
        colors = DECO_PARTICLE_COLORS.get(deco_type, DECO_PARTICLE_COLORS[2])
        self.col = random.choice(colors)

    def update(self):
        self.x += self.vx
        self.y += self.vy
        self.vy += 0.05
        self.vx *= 0.96
        self.life -= 1
        return self.life > 0

    def draw(self, surf):
        ratio = self.life / self.max_life
        if self.col and len(self.col) >= 3:
            col = tuple(max(0, min(255, int(c * ratio + 40))) for c in self.col[:3])
        else:
            col = (0, 255, 200)  # 默认青色
        pygame.draw.circle(surf, col, (int(self.x), int(self.y)), self.r)


# ─────────────────────── Raycasting ─────────────────────────
class Renderer:
    def __init__(self, surf):
        self.surf = surf
        self.col_w = max(1, W // NUM_RAYS)
        # 双缓冲：预创建的辅助表面
        self._bg_cache: Optional[pygame.Surface] = None
        self._zbuf_dirty = True
        
    def _ensure_bg_cache(self):
        """预渲染静态背景"""
        if self._bg_cache is None:
            self._bg_cache = pygame.Surface((W, H))
            # 预渲染天空和地板
            for y in range(0, H // 2, 3):
                t = y / (H // 2)
                pygame.draw.line(self._bg_cache, self._lerp(C_CEIL_TOP, C_CEIL_BOT, t), (0, y), (W, y))
            for y in range(H // 2, H, 3):
                t = (y - H // 2) / (H // 2)
                pygame.draw.line(self._bg_cache, self._lerp(C_FLOOR_T, C_FLOOR_B, t), (0, y), (W, y))
            self._zbuf_dirty = True

    @staticmethod
    def _lerp(a, b, t):
        t = max(0.0, min(1.0, t))
        return (
            int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t),
        )

    def render_bg(self, use_cache=True):
        """渲染背景，使用缓存优化"""
        if use_cache and self._bg_cache:
            self.surf.blit(self._bg_cache, (0, 0))
        else:
            # 无缓存版本（用于首次初始化）
            for y in range(0, H // 2, 3):
                t = y / (H // 2)
                pygame.draw.line(self.surf, self._lerp(C_CEIL_TOP, C_CEIL_BOT, t), (0, y), (W, y))
            for y in range(H // 2, H, 3):
                t = (y - H // 2) / (H // 2)
                pygame.draw.line(self.surf, self._lerp(C_FLOOR_T, C_FLOOR_B, t), (0, y), (W, y))

    def _cast(self, px, py, angle, world: WorldMap):
        rx = math.cos(angle)
        ry = math.sin(angle)
        mx, my = int(px), int(py)
        ddx = abs(1.0 / rx) if abs(rx) > 1e-9 else 1e18
        ddy = abs(1.0 / ry) if abs(ry) > 1e-9 else 1e18
        sx = 1 if rx > 0 else -1
        sy = 1 if ry > 0 else -1
        dist_x = (mx + 1 - px) * ddx if rx > 0 else (px - mx) * ddx
        dist_y = (my + 1 - py) * ddy if ry > 0 else (py - my) * ddy
        side = 0
        for _ in range(MAX_DEPTH * 4):
            if dist_x < dist_y:
                dist_x += ddx; mx += sx; side = 0
            else:
                dist_y += ddy; my += sy; side = 1
            ct = world.cell(mx, my)
            if ct > 0:
                d = (mx - px + (1 - sx) / 2) / rx if side == 0 \
                    else (my - py + (1 - sy) / 2) / ry
                return abs(d), ct, side
        return float(MAX_DEPTH), 0, 0

    def _cast_optimized(self, px: float, py: float, cos_val: float, sin_val: float, world: WorldMap):
        """优化射线投射：使用预计算值"""
        mx, my = int(px), int(py)
        ddx = abs(1.0 / cos_val) if abs(cos_val) > 1e-9 else 1e18
        ddy = abs(1.0 / sin_val) if abs(sin_val) > 1e-9 else 1e18
        sx = 1 if cos_val > 0 else -1
        sy = 1 if sin_val > 0 else -1
        dist_x = (mx + 1 - px) * ddx if cos_val > 0 else (px - mx) * ddx
        dist_y = (my + 1 - py) * ddy if sin_val > 0 else (py - my) * ddy
        side = 0
        for _ in range(MAX_DEPTH * 4):
            if dist_x < dist_y:
                dist_x += ddx; mx += sx; side = 0
            else:
                dist_y += ddy; my += sy; side = 1
            ct = world.cell(mx, my)
            if ct > 0:
                d = (mx - px + (1 - sx) / 2) / cos_val if side == 0 \
                    else (my - py + (1 - sy) / 2) / sin_val
                return abs(d), ct, side
        return float(MAX_DEPTH), 0, 0

    def render_walls(self, px, py, angle, zbuf, world: WorldMap, effective_max_depth: Optional[float] = None):
        """优化的墙面渲染：预计算 + 向量化
        effective_max_depth: 有效最大射线距离，暗室时会缩小
        """
        max_d = effective_max_depth if effective_max_depth is not None else MAX_DEPTH
        # 装饰物颜色映射（移到类级别避免重复创建）
        deco_colors = {
            2: ((0, 255, 200), (0, 200, 160)),
            3: ((200, 60, 60), (160, 40, 40)),
            4: ((220, 190, 80), (180, 150, 50)),
            5: ((60, 220, 120), (40, 180, 80)),
            6: ((180, 100, 240), (140, 70, 200)),
            7: ((255, 150, 60), (220, 120, 30)),
            8: ((120, 120, 130), (90, 90, 100)),
            9: ((80, 220, 255), (50, 180, 220)),
            10: ((100, 180, 255), (70, 140, 220)),
            11: ((160, 100, 220), (120, 70, 180)),
            12: ((255, 255, 100), (200, 200, 60)),   # 传送门 - 金色
        }
        
        for i in range(NUM_RAYS):
            # 使用预计算的角度
            ray_a = angle + _RAY_ANGLES[i]
            # 优化：使用预计算的 sin/cos
            cos_ray = _RAY_COS[i] * math.cos(angle) - _RAY_SIN[i] * math.sin(angle)
            sin_ray = _RAY_SIN[i] * math.cos(angle) + _RAY_COS[i] * math.sin(angle)
            
            raw_d, ct, side = self._cast_optimized(px, py, cos_ray, sin_ray, world)
            # 暗室视野缩短：超出有效距离的射线截断
            if raw_d > max_d:
                raw_d = max_d
                ct = 0
            zbuf[i] = raw_d

            # 优化：使用预计算的校正因子
            dist = max(0.001, raw_d * _RAY_CORRECTION[i])
            h = min(H, int(H / dist))
            top = (H - h) // 2

            # 预计算雾化
            fog_t  = min(1.0, raw_d / max_d) * 0.7
            dark   = 0.65 if side == 1 else 1.0
            bright = max(0.08, dark * (1.0 - raw_d * 0.025))

            # 根据装饰物类型选择颜色
            if ct >= 2:
                base_pair = deco_colors.get(ct, deco_colors[2])
                base = base_pair[0] if side == 0 else base_pair[1]
                fade = max(0.35, 1.0 - raw_d * 0.05)
                col  = tuple(int(c * fade) for c in base)
                col  = self._lerp(col, C_FOG, fog_t ** 1.3)
            else:
                near = tuple(int(c * bright) for c in C_WALL_NR)
                far  = tuple(int(c * bright) for c in C_WALL_FAR)
                col  = self._lerp(near, far, fog_t)
                col  = self._lerp(col, C_FOG, fog_t ** 1.5)

            x = i * self.col_w
            pygame.draw.rect(self.surf, col, (x, top, self.col_w + 1, h))

    def render_scanlines(self):
        # 减弱扫描线效果：间距加大，透明度降低
        for y in range(0, H, 10):
            alpha_surf = pygame.Surface((W, 2), pygame.SRCALPHA)
            alpha_surf.fill((C_SCAN[0], C_SCAN[1], C_SCAN[2], 25))
            self.surf.blit(alpha_surf, (0, y))


# ─────────────────────── HUD ─────────────────────────
# 区域类型名称映射
ZONE_NAMES = {
    2: "AETHER GLOW",     # 青绿发光
    3: "RED ALERT",       # 红色警示
    4: "TREASURY",        # 金色宝库
    5: "VERDANT GARDEN",  # 绿色植被
    6: "ENERGY CORE",     # 紫色能量
    7: "FIRE EXIT",       # 橙色出口
    8: "RUINS",           # 灰色碎石
    9: "TECH NOOK",       # 科技装饰
    10: "CRYSTAL CAVE",   # 蓝色水晶
    11: "MYSTIC ALTAR",   # 紫水晶
    12: "PORTAL GATE",    # 传送门
}

# 区域颜色
ZONE_COLORS = {
    2: (0, 255, 200),
    3: (220, 60, 60),
    4: (220, 180, 60),
    5: (60, 200, 100),
    6: (180, 80, 240),
    7: (255, 150, 60),
    8: (140, 140, 150),
    9: (80, 220, 255),
    10: (100, 180, 255),
    11: (160, 80, 220),
    12: (255, 255, 100),
}

def draw_hud(surf, f_sm, f_bg, tick, pos, chunk_coord, current_zone=2,
             energy=None, fragments=None, interaction_in_range=False,
             shielded=False, danger_near=False):
    """增强版HUD：能量条、碎片计数、危险提示、交互提示"""
    bar_h = 48
    pygame.draw.rect(surf, (0, 4, 18), (0, H - bar_h, W, bar_h))
    pygame.draw.line(surf, C_ACCENT,   (0, H - bar_h), (W, H - bar_h), 1)

    ts    = tick // 60
    t_str = f"{ts // 60:02d}:{ts % 60:02d}"
    cx, cy = chunk_coord
    zone_name = ZONE_NAMES.get(current_zone, "UNKNOWN")
    zone_color = ZONE_COLORS.get(current_zone, C_ACCENT)
    coord = f"[{pos[0]:.1f}, {pos[1]:.1f}]  SECTOR ({cx},{cy})"

    lbl1 = f_sm.render(f"POS {coord}", True, C_HUD)
    lbl2 = f_sm.render(f"KID-A WALKER  //  {t_str}", True, C_ACCENT)
    zone_lbl = f_sm.render(f"ZONE: {zone_name}", True, zone_color)
    lbl3 = f_sm.render("WASD MOVE | TAB CODEX | E INTERACT | ESC QUIT", True, (0, 70, 110))

    surf.blit(lbl1, (16, H - bar_h + 5))
    surf.blit(lbl2, (W // 2 - lbl2.get_width() // 2, H - bar_h + 5))
    surf.blit(zone_lbl, (W // 2 - zone_lbl.get_width() // 2, H - bar_h + 20))
    surf.blit(lbl3, (W - lbl3.get_width() - 16, H - bar_h + 5))

    # ─── 能量条 ───
    if energy is not None:
        ebar_x, ebar_y = 16, H - bar_h + 24
        ebar_w, ebar_h = 200, 12
        # 背景
        pygame.draw.rect(surf, (15, 15, 30), (ebar_x, ebar_y, ebar_w, ebar_h))
        # 填充（渐变色：青绿→黄→红）
        ratio = energy.get_ratio()
        fill_w = int(ebar_w * ratio)
        if fill_w > 0:
            for i in range(fill_w):
                t = i / ebar_w  # 0~1 从左到右
                if t < 0.5:
                    # 青绿(0,255,200) → 黄(255,255,0)
                    r = int(0 + 255 * (t * 2))
                    g = 255
                    b = int(200 * (1 - t * 2))
                else:
                    # 黄(255,255,0) → 红(255,50,50)
                    t2 = (t - 0.5) * 2
                    r = 255
                    g = int(255 - 205 * t2)
                    b = int(50 * t2)
                pygame.draw.line(surf, (r, g, b),
                                 (ebar_x + i, ebar_y + 1),
                                 (ebar_x + i, ebar_y + ebar_h - 1))
        # 护盾高亮
        if shielded:
            pulse = abs(math.sin(tick * 0.15))
            shield_col = (100, int(200 + 55 * pulse), 255)
            pygame.draw.rect(surf, shield_col, (ebar_x, ebar_y, ebar_w, ebar_h), 2)
        else:
            pygame.draw.rect(surf, (40, 80, 100), (ebar_x, ebar_y, ebar_w, ebar_h), 1)

        # 能量数值
        e_text = f_sm.render(f"NRG {int(energy.energy)}", True, (180, 220, 240))
        surf.blit(e_text, (ebar_x + ebar_w + 8, ebar_y - 2))

        # 出生保护期提示
        if energy.grace_timer > 0:
            grace_sec = int(energy.grace_timer) + 1
            pulse = abs(math.sin(tick * 0.1))
            grace_col = (int(50 + 200 * pulse), 255, int(180 + 75 * pulse))
            grace_text = f_sm.render(f"GRACE {grace_sec}s", True, grace_col)
            surf.blit(grace_text, (ebar_x + ebar_w + 8, ebar_y + 10))

    # ─── 碎片计数（右上角） ───
    if fragments is not None:
        frag_text = f_sm.render(
            f"FRAGMENTS: {fragments.get_total()} ({fragments.get_unique_types()}/{FRAGMENT_TYPES})",
            True, (200, 200, 255))
        surf.blit(frag_text, (W - frag_text.get_width() - 16, 8))

    # ─── 区域危险提示（中央偏下） ───
    if danger_near and current_zone == 3:
        pulse = abs(math.sin(tick * 0.12))
        warn_col = (int(200 + 55 * pulse), int(40 + 30 * pulse), int(40 + 30 * pulse))
        warn_text = f_sm.render("⚠ DANGER ZONE ⚠", True, warn_col)
        surf.blit(warn_text, (W // 2 - warn_text.get_width() // 2, H // 2 + 80))

    # ─── 交互提示 ───
    if interaction_in_range:
        pulse = abs(math.sin(tick * 0.1))
        col = (int(0 + 100 * pulse), int(255 - 55 * pulse), int(200))
        hint = f_sm.render("[E] INTERACT", True, col)
        surf.blit(hint, (W // 2 - hint.get_width() // 2, H // 2 + 50))

    # ─── 危险预警边框闪烁 ───
    if danger_near:
        pulse = abs(math.sin(tick * 0.15))
        border_alpha = int(80 * pulse)
        border_surf = pygame.Surface((W, H), pygame.SRCALPHA)
        # 四边红色半透明边框
        for thickness in range(3):
            pygame.draw.rect(border_surf, (220, 40, 40, border_alpha),
                             (thickness, thickness, W - thickness * 2, H - thickness * 2), 2)
        surf.blit(border_surf, (0, 0))

    if tick < 360:
        alpha = min(255, tick * 2)
        t = f_bg.render("KID-A WALKER", True, C_ACCENT)
        t.set_alpha(alpha)
        surf.blit(t, (W // 2 - t.get_width() // 2, 22))

    # 准星
    hcx, hcy = W // 2, H // 2
    for dx, dy, ln in [(-1,0,9),(1,0,9),(0,-1,9),(0,1,9)]:
        pygame.draw.line(surf, C_ACCENT,
                         (hcx + dx * 3, hcy + dy * 3),
                         (hcx + dx * (3 + ln), hcy + dy * (3 + ln)), 1)


def draw_codex(surf, f_sm, fragments):
    """碎片图鉴页（Tab切换）：展示10种碎片收集进度"""
    overlay = pygame.Surface((W, H), pygame.SRCALPHA)
    overlay.fill((1, 3, 15, 240))
    surf.blit(overlay, (0, 0))

    # 标题
    title = f_sm.render("FRAGMENT CODEX", True, C_ACCENT)
    surf.blit(title, (W // 2 - title.get_width() // 2, 40))

    # 碎片类型信息
    type_info = [
        (2, "AETHER GLOW",    (0, 255, 200)),
        (3, "RED ALERT",      (220, 60, 60)),
        (4, "TREASURY",       (220, 180, 60)),
        (5, "VERDANT GARDEN", (60, 200, 100)),
        (6, "ENERGY CORE",    (180, 80, 240)),
        (7, "FIRE EXIT",      (255, 150, 60)),
        (8, "RUINS",          (140, 140, 150)),
        (9, "TECH NOOK",      (80, 220, 255)),
        (10, "CRYSTAL CAVE",  (100, 180, 255)),
        (11, "MYSTIC ALTAR",  (160, 80, 220)),
    ]

    start_y = 80
    row_h = 50
    col_w = W // 2

    for idx, (dtype, name, color) in enumerate(type_info):
        col_idx = idx % 2
        row_idx = idx // 2
        bx = 60 + col_idx * col_w
        by = start_y + row_idx * row_h

        count = fragments.get_count(dtype)
        in_codex = dtype in fragments.codex
        has_any = count > 0

        # 类型颜色标记块
        display_col = color if has_any else (40, 40, 60)
        pygame.draw.rect(surf, display_col, (bx, by, 20, 20))
        pygame.draw.rect(surf, (80, 80, 100), (bx, by, 20, 20), 1)

        # 名称和计数
        name_col = color if has_any else (60, 60, 80)
        name_text = f_sm.render(f"{name}", True, name_col)
        surf.blit(name_text, (bx + 28, by))

        count_text = f_sm.render(f"x{count}", True, (200, 200, 255) if has_any else (60, 60, 80))
        surf.blit(count_text, (bx + 28, by + 16))

        # 收集进度条（满5为被动能力）
        bar_x = bx + 140
        bar_y = by + 8
        bar_w = 120
        bar_h = 8
        pygame.draw.rect(surf, (20, 20, 35), (bar_x, bar_y, bar_w, bar_h))
        fill = min(count, 5)
        fill_w = int(bar_w * fill / 5)
        if fill_w > 0:
            for i in range(fill_w):
                t = i / bar_w
                fc = tuple(min(255, int(c * (0.5 + 0.5 * t))) for c in color)
                pygame.draw.line(surf, fc, (bar_x + i, bar_y + 1), (bar_x + i, bar_y + bar_h - 1))
        pygame.draw.rect(surf, (60, 60, 80), (bar_x, bar_y, bar_w, bar_h), 1)

        # 被动能力标记
        if count >= 5:
            passive = f_sm.render("PASSIVE", True, (255, 255, 100))
            surf.blit(passive, (bar_x + bar_w + 8, bar_y - 2))

        # 图鉴标记（跨局）
        if in_codex and not has_any:
            codex_mark = f_sm.render("[DISCOVERED]", True, (80, 80, 100))
            surf.blit(codex_mark, (bx + 140, by + 16))

    # 底部统计
    total = fragments.get_total()
    unique = fragments.get_unique_types()
    all_collected = fragments.is_all_types_collected()

    stats_y = start_y + 5 * row_h + 10
    stats_text = f_sm.render(f"TOTAL: {total}  |  TYPES: {unique}/10", True, C_HUD)
    surf.blit(stats_text, (W // 2 - stats_text.get_width() // 2, stats_y))

    if all_collected:
        bonus = f_sm.render("ALL TYPES COLLECTED - DANGER IMMUNE!", True, (255, 255, 100))
        surf.blit(bonus, (W // 2 - bonus.get_width() // 2, stats_y + 20))

    hint = f_sm.render("TAB / ESC close", True, (0, 70, 110))
    surf.blit(hint, (W - hint.get_width() - 20, H - 30))


# ─────────────────────── 暗角遮罩（减弱版本） ─────────────────────────
def make_vignette(w, h):
    v = pygame.Surface((w, h), pygame.SRCALPHA)
    cx, cy = w // 2, h // 2
    max_r = int(math.sqrt(cx*cx + cy*cy))
    for radius in range(max_r, max_r - 100, -6):
        t = (max_r - radius) / 100.0
        alpha = int(min(40, t * 40))  # 最大透明度从90减到40
        pygame.draw.circle(v, (0, 0, 0, alpha), (cx, cy), radius, 6)
    return v


# ─────────────────────── 预加载界面 ─────────────────────────
class LoadingScreen:
    """开始界面 + 预加载进度界面"""

    TIPS = [
        "探索无限的世界，发现不同的区域类型...",
        "按 WASD 或方向键移动，Tab 查看大地图",
        "每个区域都有独特的音效氛围",
        "世界是随机生成的，每次都是新旅程",
        "寻找安全的路径，避开死胡同",
    ]

    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.clock = pygame.time.Clock()
        try:
            self.font_title: pygame.font.Font = pygame.font.SysFont("Courier New", 72, bold=True)
            self.font_sub: pygame.font.Font = pygame.font.SysFont("Courier New", 24, bold=True)
            self.font_tip: pygame.font.Font = pygame.font.SysFont("Courier New", 16)
            self.font_progress: pygame.font.Font = pygame.font.SysFont("Courier New", 18)
        except Exception:
            self.font_title = pygame.font.Font(None, 100)
            self.font_sub = pygame.font.Font(None, 32)
            self.font_tip = pygame.font.Font(None, 22)
            self.font_progress = pygame.font.Font(None, 24)
        self.tip_idx = 0
        self.tip_timer = 0
        self.phase = "start"  # "start" | "loading"
        self.progress = 0.0
        self.status_text = ""
        self._animate = 0.0
        self._bg_parts: list[tuple[int, int, float, float, int]] | None = None

    def wait_for_start(self) -> bool:
        """显示开始界面，等待玩家按键开始，返回是否继续游戏"""
        pygame.mouse.set_visible(True)
        while True:
            dt = self.clock.tick(60) / 1000.0
            self._animate += dt

            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    return False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_ESCAPE):
                        if ev.key == pygame.K_ESCAPE:
                            return False
                        pygame.mouse.set_visible(False)
                        return True
                elif ev.type == pygame.MOUSEBUTTONDOWN:
                    pygame.mouse.set_visible(False)
                    return True

            self._draw_start_screen()
            pygame.display.flip()

    def update_progress(self, progress: float, status: str = "") -> None:
        """更新加载进度，0.0 ~ 1.0"""
        self.progress = max(0.0, min(1.0, progress))
        self.status_text = status
        self._draw_loading_screen()
        pygame.display.flip()

    def _draw_start_screen(self) -> None:
        W, H = self.screen.get_size()
        self.screen.fill((5, 5, 15))

        # 动态背景粒子效果
        self._draw_bg_particles_start()

        # 标题
        title = self.font_title.render("KID-A WALKER", True, (0, 255, 200))
        title_shadow = self.font_title.render("KID-A WALKER", True, (0, 150, 120))
        tx = W // 2 - title.get_width() // 2
        ty = H // 3 - title.get_height() // 2
        self.screen.blit(title_shadow, (tx + 3, ty + 3))
        self.screen.blit(title, (tx, ty))

        # 副标题
        sub = self.font_sub.render("[ INFINITE WORLD ]", True, (100, 200, 255))
        sx = W // 2 - sub.get_width() // 2
        self.screen.blit(sub, (sx, ty + title.get_height() + 15))

        # 旋转提示（动画）
        pulse = abs(math.sin(self._animate * 2.0))
        hint_col = (int(150 + 105 * pulse), int(200 + 55 * pulse), int(255))
        hint = self.font_sub.render("— PRESS ANY KEY TO START —", True, hint_col)
        hx = W // 2 - hint.get_width() // 2
        hy = H * 2 // 3
        self.screen.blit(hint, (hx, hy))

        # 底部提示
        self.tip_timer += self.clock.get_rawtime()
        if self.tip_timer > 4000:
            self.tip_timer = 0
            self.tip_idx = (self.tip_idx + 1) % len(self.TIPS)
        tip = self.font_tip.render(self.TIPS[self.tip_idx], True, (100, 100, 130))
        tip_x = W // 2 - tip.get_width() // 2
        self.screen.blit(tip, (tip_x, H - 50))

        # 版本信息
        ver = self.font_tip.render("v1.0 | Procedural Exploration", True, (60, 60, 80))
        self.screen.blit(ver, (10, H - 30))

    def _draw_bg_particles_start(self) -> None:
        """开始界面的装饰粒子"""
        import random
        particles = getattr(self, '_bg_parts', None)
        if particles is None:
            self._bg_parts = [
                (random.randint(0, 800), random.randint(0, 600),
                 random.uniform(-0.3, 0.3), random.uniform(-0.3, 0.3),
                 random.randint(2, 5))
                for _ in range(40)
            ]
            particles = self._bg_parts

        W, H = self.screen.get_size()
        if particles is None:
            return
        new_parts: list[tuple[int, int, float, float, int]] = []
        for i, (x, y, vx, vy, r) in enumerate(particles):
            x_new = (x + vx) % W
            y_new = (y + vy) % H
            alpha = int(80 + 40 * math.sin(self._animate + i))
            col = (0, 180 + int(75 * math.sin(self._animate * 0.7 + i)), 150)
            pygame.draw.circle(self.screen, col, (int(x_new), int(y_new)), r)
            new_parts.append((int(x_new), int(y_new), vx, vy, r))
        self._bg_parts = new_parts

    def _draw_loading_screen(self) -> None:
        W, H = self.screen.get_size()
        self.screen.fill((5, 5, 15))

        # 顶部装饰线
        for i in range(0, W, 8):
            alpha = int(30 * math.sin(i * 0.02 + self._animate))
            if alpha > 0:
                pygame.draw.line(self.screen, (0, 200, 150, alpha), (i, 0), (i + 4, 0))

        # 标题
        title = self.font_title.render("LOADING", True, (0, 255, 200))
        tx = W // 2 - title.get_width() // 2
        ty = H // 3 - title.get_height() // 2
        self.screen.blit(title, (tx, ty))

        # 进度条背景
        bar_w, bar_h = 500, 24
        bar_x = W // 2 - bar_w // 2
        bar_y = H // 2
        pygame.draw.rect(self.screen, (20, 20, 35), (bar_x, bar_y, bar_w, bar_h), border_radius=4)

        # 进度条填充
        fill_w = int(bar_w * self.progress)
        if fill_w > 0:
            # 渐变效果模拟
            for i in range(fill_w):
                t = i / bar_w
                r = int(0 + 50 * t)
                g = int(200 + 55 * (1 - abs(t - 0.5) * 2))
                b = int(150 + 50 * t)
                pygame.draw.line(self.screen, (r, g, b), (bar_x + i, bar_y + 2), (bar_x + i, bar_y + bar_h - 2))
            pygame.draw.rect(self.screen, (0, 255, 200), (bar_x, bar_y, fill_w, bar_h), 1, border_radius=4)

        # 百分比文字
        pct = self.font_progress.render(f"{int(self.progress * 100)}%", True, (200, 220, 255))
        px = W // 2 - pct.get_width() // 2
        py = bar_y + bar_h + 15
        self.screen.blit(pct, (px, py))

        # 状态文字
        if self.status_text:
            st = self.font_tip.render(self.status_text, True, (120, 150, 180))
            sx = W // 2 - st.get_width() // 2
            sy = py + 30
            self.screen.blit(st, (sx, sy))

        # 底部装饰
        for i in range(0, W, 12):
            alpha = int(20 * math.sin(i * 0.015 + self._animate * 1.5))
            if alpha > 0:
                pygame.draw.line(self.screen, (100, 0, 200, alpha), (i, H - 2), (i + 6, H - 2))

    def draw_complete(self) -> None:
        """显示加载完成画面"""
        W, H = self.screen.get_size()
        self.screen.fill((5, 5, 15))

        title = self.font_title.render("READY!", True, (0, 255, 200))
        tx = W // 2 - title.get_width() // 2
        ty = H // 2 - title.get_height() // 2
        self.screen.blit(title, (tx, ty))

        hint = self.font_sub.render("Entering the world...", True, (150, 200, 255))
        hx = W // 2 - hint.get_width() // 2
        self.screen.blit(hint, (hx, ty + title.get_height() + 20))

        pygame.display.flip()


# ─────────────────────── 结算画面 ─────────────────────────
class DeathScreen:
    """游戏结算画面"""

    @staticmethod
    def calculate_score(fragments, survival_time, explored_count) -> int:
        """计算最终评分：碎片×100 + 区块数×50 + 存活秒×2 + 全类型×500"""
        score = 0
        score += fragments.get_total() * 100
        score += explored_count * 50
        score += int(survival_time) * 2
        if fragments.is_all_types_collected():
            score += 500
        return score

    @staticmethod
    def draw(screen, f_sm, f_bg, fragments, survival_time, explored_count, tick):
        """绘制结算画面"""
        score = DeathScreen.calculate_score(fragments, survival_time, explored_count)
        sw, sh = screen.get_size()

        # 半透明遮罩
        overlay = pygame.Surface((sw, sh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        screen.blit(overlay, (0, 0))

        # 标题
        title = f_bg.render("SYSTEM OFFLINE", True, (220, 60, 60))
        screen.blit(title, (sw // 2 - title.get_width() // 2, sh // 4))

        # 统计信息
        y = sh // 4 + 80
        stats = [
            f"DATA FRAGMENTS: {fragments.get_total()} ({fragments.get_unique_types()}/10 TYPES)",
            f"SURVIVAL TIME: {int(survival_time) // 60:02d}:{int(survival_time) % 60:02d}",
            f"SECTORS EXPLORED: {explored_count}",
            f"FINAL SCORE: {score}",
        ]
        for s in stats:
            lbl = f_sm.render(s, True, (200, 220, 255))
            screen.blit(lbl, (sw // 2 - lbl.get_width() // 2, y))
            y += 30

        # 碎片类型详情
        y += 10
        type_names = {
            2: "GLOW", 3: "RED", 4: "GOLD", 5: "VERD", 6: "NRG",
            7: "FIRE", 8: "DEBR", 9: "TECH", 10: "CRYS", 11: "MYST",
        }
        type_colors = {
            2: (0, 255, 200), 3: (220, 60, 60), 4: (220, 180, 60),
            5: (60, 200, 100), 6: (180, 80, 240), 7: (255, 150, 60),
            8: (140, 140, 150), 9: (80, 220, 255), 10: (100, 180, 255),
            11: (160, 80, 220),
        }
        frag_detail_x = sw // 2 - 200
        for dt_idx, dt_type in enumerate(range(2, 12)):
            fx = frag_detail_x + (dt_idx % 5) * 85
            fy = y + (dt_idx // 5) * 22
            count = fragments.get_count(dt_type)
            name = type_names.get(dt_type, "????")
            col = type_colors.get(dt_type, (200, 200, 200))
            has = count > 0
            lbl = f_sm.render(f"{name}:{count}", True, col if has else (60, 60, 80))
            screen.blit(lbl, (fx, fy))

        # 重启提示
        pulse = abs(math.sin(tick * 0.05))
        col = (int(150 + 105 * pulse), 255, int(200 + 55 * pulse))
        hint = f_sm.render("[R] RESTART  |  [ESC] QUIT", True, col)
        screen.blit(hint, (sw // 2 - hint.get_width() // 2, sh * 3 // 4))

    @staticmethod
    def draw_with_highscore(screen, f_sm, f_bg, fragments, survival_time, explored_count, tick, highscore=0):
        """绘制结算画面（含最高分）"""
        DeathScreen.draw(screen, f_sm, f_bg, fragments, survival_time, explored_count, tick)
        if highscore > 0:
            score = DeathScreen.calculate_score(fragments, survival_time, explored_count)
            sw, sh = screen.get_size()
            hs_col = (255, 255, 100) if score >= highscore else (120, 120, 140)
            hs_text = f_sm.render(f"BEST: {highscore}", True, hs_col)
            screen.blit(hs_text, (sw // 2 - hs_text.get_width() // 2, sh * 3 // 4 - 30))


# ─────────────────────── 最高分记录（P2-4） ─────────────────────────
HIGHSCORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "highscore.json")

def load_highscore() -> int:
    """从本地JSON加载最高分"""
    try:
        if os.path.exists(HIGHSCORE_FILE):
            with open(HIGHSCORE_FILE, 'r') as f:
                data = json.load(f)
                return data.get("highscore", 0)
    except Exception:
        pass
    return 0

def save_highscore(score: int):
    """保存最高分到本地JSON"""
    try:
        current = load_highscore()
        if score > current:
            with open(HIGHSCORE_FILE, 'w') as f:
                json.dump({"highscore": score}, f)
    except Exception:
        pass


# ─────────────────────── 主循环（状态机版本）─────────────────────────
class Game:
    """游戏主控制器，使用状态机管理不同阶段"""
    
    def __init__(self):
        pygame.init()
        pygame.mixer.pre_init(frequency=SR, size=-16, channels=2, buffer=2048)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(32)
        
        self.screen = pygame.display.set_mode((W, H))
        pygame.display.set_caption("KID-A WALKER  [INFINITE]")
        pygame.mouse.set_visible(False)
        
        # 初始化字体
        try:
            self.f_sm = pygame.font.SysFont("Courier New", 14, bold=True)
            self.f_bg = pygame.font.SysFont("Courier New", 32, bold=True)
        except Exception:
            self.f_sm = pygame.font.Font(None, 18)
            self.f_bg = pygame.font.Font(None, 40)
        
        # 状态机
        self.state = GameState.START_SCREEN
        self.clock = pygame.time.Clock()
        
        # 加载界面
        self.loading_screen = LoadingScreen(self.screen)
        
        # 预加载管理器
        self.preload_manager: Optional[PreloadManager] = None
        
        # 游戏核心组件（延迟初始化）
        self.world: Optional[WorldMap] = None
        self.synth: Optional[ZoneSynth] = None
        self.renderer: Optional[Renderer] = None
        self.vignette: Optional[pygame.Surface] = None
        
        # 主菜单音乐（固定音乐）
        self.menu_synth: Optional[ZoneSynth] = None
        self.menu_active_sounds: list = []
        self.last_menu_sound_t = 0.0
        
        # 玩家状态
        self.px, self.py, self.pang = 10.5, 10.5, 0.0
        self.show_codex = False
        self.tick = 0
        self.survival_time = 0.0
        self.particles: list[Particle] = []
        self.part_timer = 0
        self.last_sound_t = 0.0
        self.last_chunk = (0, 0)
        self.active_sounds = []
        self.zbuf = [0.0] * NUM_RAYS
        self.dominant_zone = 2
        self._danger_near = False
        self._last_zone_chunk: tuple[int, int] = (0, 0)

        # ─── 增强系统 ───
        self.energy = EnergySystem()
        self.fragments = FragmentSystem()
        self.interaction = InteractionSystem(INTERACT_RANGE)
        self.abilities = AbilitySystem(self.fragments)
        self.exploration = ExplorationTracker(EXPLORE_RADIUS)
        self.sfx = SFXPlayer(enabled=True)
        self.markers: list[tuple] = []  # 橙色光源标记点
        
        # 加载完成动画计时
        self._complete_show_time = 0
        
        # 开始界面动画时间
        self._start_animate = 0.0
        
    def run(self):
        """主游戏循环"""
        # 初始化主菜单音乐（固定空灵神秘风格）
        self.menu_synth = ZoneSynth()
        self.menu_synth.set_zone(2)  # zone 2 = 空灵神秘
        
        running = True
        while running:
            dt = self.clock.tick(60) / 1000.0
            
            # 状态处理
            if self.state == GameState.START_SCREEN:
                self._handle_start_screen(dt)
            elif self.state == GameState.LOADING:
                self._handle_loading()
            elif self.state == GameState.PLAYING:
                self._handle_playing(dt)
            elif self.state == GameState.GAME_OVER:
                self._handle_game_over(dt)
            elif self.state == GameState.QUIT:
                running = False
            
            pygame.display.flip()
        
        # 清理
        if self.synth:
            self.synth.running = False
        pygame.quit()
    
    def _handle_start_screen(self, dt: float):
        """处理开始界面"""
        self._start_animate += dt
        
        # 播放主菜单固定音乐
        if self.menu_synth:
            now = time.monotonic()
            if now - self.last_menu_sound_t >= BEAT * 0.46:
                snd = self.menu_synth.pop_sound()
                if snd is not None:
                    snd.play()
                    self.last_menu_sound_t = now
                    self.menu_active_sounds.append(snd)
                    if len(self.menu_active_sounds) > 16:
                        self.menu_active_sounds = self.menu_active_sounds[-16:]
        
        # 事件处理
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.state = GameState.QUIT
                return
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    pygame.mouse.set_visible(False)
                    self._start_loading()
                    return
                elif ev.key == pygame.K_ESCAPE:
                    self.state = GameState.QUIT
                    return
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                pygame.mouse.set_visible(False)
                self._start_loading()
                return
        
        # 绘制开始界面
        self.loading_screen._animate = self._start_animate
        self.loading_screen._draw_start_screen()
    
    def _start_loading(self):
        """开始后台加载"""
        # 停止主菜单音乐
        if self.menu_synth:
            self.menu_synth.running = False
            self.menu_synth = None
            self.menu_active_sounds = []
        
        self.state = GameState.LOADING
        self._complete_show_time = 0
        
        # 创建预加载管理器（带进度回调）
        def on_progress(task: LoadingTask):
            # 在主线程更新加载界面
            pass
        
        self.preload_manager = PreloadManager(on_progress)
        self.preload_manager.start()
    
    def _handle_loading(self):
        """处理加载状态"""
        # 事件处理（允许取消加载返回开始界面）
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.state = GameState.QUIT
                return
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    # 取消加载，返回开始界面
                    if self.preload_manager:
                        self.preload_manager.cancel()
                        self.preload_manager = None
                    self.state = GameState.START_SCREEN
                    self._start_animate = 0.0
                    pygame.mouse.set_visible(True)
                    return
        
        # 检查加载进度
        if self.preload_manager:
            progress, status = self.preload_manager.get_overall_progress()
            self.loading_screen._animate += 0.016  # 保持动画
            self.loading_screen.update_progress(progress, status)
            
            # 检查是否完成
            if self.preload_manager.is_complete():
                self._complete_show_time += 1
                if self._complete_show_time == 1:
                    self.loading_screen.draw_complete()
                elif self._complete_show_time > 50:  # 显示约0.8秒后进入游戏
                    self._initialize_game()
                    self.state = GameState.PLAYING
                    pygame.mouse.set_visible(False)
    
    def _initialize_game(self):
        """初始化游戏核心组件（在加载完成后调用）"""
        if not self.preload_manager:
            return
        
        # 获取预加载的数据
        world = self.preload_manager.get_world()
        if world is None:
            return
        self.world = world
        spawn = self.preload_manager.get_spawn()
        
        # 初始化音频
        self.synth = ZoneSynth()
        
        # 初始化渲染器
        self.renderer = Renderer(self.screen)
        self.renderer._ensure_bg_cache()  # 预渲染背景
        
        self.vignette = make_vignette(W, H)
        
        # 设置玩家位置并确保周围区块已生成
        self.px, self.py = spawn
        self.pang = 0.0
        self.last_chunk = self.world.world_to_chunk(self.px, self.py)
        self._last_zone_chunk = self.last_chunk

        # 初始区域音效（随机选择）
        self.dominant_zone = random.randint(2, 12)
        self.synth.set_zone(self.dominant_zone)
        
        # 预生成玩家周围的区块（会清理远处区块增强随机性）
        self.world.ensure_neighbors(self.last_chunk[0], self.last_chunk[1])
    
    def _handle_playing(self, dt: float):
        """处理游戏运行状态（集成增强系统）"""
        self.tick += 1
        self.survival_time += dt
        
        # 确保游戏组件已初始化
        if self.world is None or self.renderer is None:
            return
        
        # 事件处理
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.state = GameState.QUIT
                return
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    self.state = GameState.QUIT
                    return
                elif ev.key == pygame.K_TAB:
                    # Tab切换图鉴页
                    self.show_codex = not self.show_codex
                elif ev.key == pygame.K_e:
                    # E键交互
                    effects = self.interaction.interact(
                        self.px, self.py, self.world, self.fragments, self.energy)
                    if effects is not None:
                        deco_type = effects.get("type", 0)
                        # SFX
                        self.sfx.play_collect(deco_type)
                        if "shield" in effects:
                            self.sfx.play_shield()
                        if "marker" in effects:
                            self.markers.append(effects["marker"])
        
        # ─── 1. 移动计算（含速度倍率） ───
        keys = pygame.key.get_pressed()
        speed_mult = self.abilities.get_speed_multiplier()
        rot_spd = ROT_SPEED  # 旋转速度不随能力变化
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            self.pang -= rot_spd
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            self.pang += rot_spd
        
        is_moving = False
        mv = 0.0
        if keys[pygame.K_UP] or keys[pygame.K_w]:
            mv = MOVE_SPEED * speed_mult
            is_moving = True
        if keys[pygame.K_DOWN] or keys[pygame.K_s]:
            mv = -MOVE_SPEED * speed_mult
            is_moving = True
        
        # 移动和碰撞检测
        if mv:
            est_nx = self.px + math.cos(self.pang) * mv * 1.5
            est_ny = self.py + math.sin(self.pang) * mv * 1.5
            est_cx = int(math.floor(est_nx / CHUNK_SIZE))
            est_cy = int(math.floor(est_ny / CHUNK_SIZE))
            cur_cx = int(math.floor(self.px / CHUNK_SIZE))
            cur_cy = int(math.floor(self.py / CHUNK_SIZE))
            for dcx in range(min(cur_cx, est_cx) - 1, max(cur_cx, est_cx) + 2):
                for dcy in range(min(cur_cy, est_cy) - 1, max(cur_cy, est_cy) + 2):
                    self.world.ensure_neighbors(dcx, dcy)
        
        if mv:
            nx = self.px + math.cos(self.pang) * mv
            ny = self.py + math.sin(self.pang) * mv
            if self.world.cell(nx, self.py) == 0:
                self.px = nx
            if self.world.cell(self.px, ny) == 0:
                self.py = ny

        # ─── 传送门事件（P2-5） ───
        current_cell = self.world.cell(self.px, self.py)
        if current_cell == 12:  # 传送门
            if random.random() < PORTAL_TELEPORT_CHANCE:
                # 随机传送到远处（5~15个区块距离）
                offset_cx = random.choice([-1, 1]) * random.randint(5, 15)
                offset_cy = random.choice([-1, 1]) * random.randint(5, 15)
                cur_c = self.world.world_to_chunk(self.px, self.py)
                target_cx = cur_c[0] + offset_cx
                target_cy = cur_c[1] + offset_cy
                # 确保目标区块已生成
                self.world.ensure_neighbors(target_cx, target_cy)
                # 寻找安全出生点
                spawn = self.world.find_safe_spawn(target_cx, target_cy)
                self.px, self.py = spawn
                self.last_chunk = (target_cx, target_cy)
                # 传送音效
                self.sfx.play_shield()
        
        # 区块边缘检测
        cur_chunk = self.world.world_to_chunk(self.px, self.py)
        if cur_chunk != self.last_chunk:
            self.last_chunk = cur_chunk
            self.world.ensure_neighbors(cur_chunk[0], cur_chunk[1])
        
        # ─── 2. 能量更新 ───
        zone_type = self.world.cell(self.px, self.py)
        # 检查附近是否有发光柱(2)
        near_glow = False
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                if self.world.cell(self.px + dx, self.py + dy) == 2:
                    near_glow = True
                    break
            if near_glow:
                break
        
        drain_mult = self.abilities.get_drain_multiplier()
        shielded = self.abilities.is_danger_immune()
        self.energy.update(dt, is_moving, zone_type, near_glow, drain_mult, shielded)
        
        # 能量耗尽 → 死亡
        if self.energy.is_dead:
            self.state = GameState.GAME_OVER
            return
        
        # ─── 3. 探索追踪 ───
        self.exploration.check_and_mark(self.px, self.py, self.world)
        
        # ─── 4. 能力更新 ───
        self.abilities.update(dt)
        
        # ─── 5. 危险检测 ───
        self._danger_near = False
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                ct = self.world.cell(self.px + dx, self.py + dy)
                if ct == 3:  # 红色区
                    self._danger_near = True
                    break
            if self._danger_near:
                break
        
        # 危险预警蜂鸣
        if self._danger_near and not shielded:
            self.sfx.play_warning()
        
        # ─── 6. 区域/音频/粒子 ───
        self._update_zone()
        self._update_audio()
        self._update_particles()
        
        # ─── 7. 渲染 ───
        self._render()
    
    def _update_zone(self):
        """基于当前区块检测区域音效（切换区块时随机选择音乐）"""
        if self.world is None or self.synth is None:
            return
        cx, cy = self.world.world_to_chunk(self.px, self.py)
        if (cx, cy) != self._last_zone_chunk:
            self._last_zone_chunk = (cx, cy)
            # 随机选择音乐区域（2-12），每次进入新区块都随机
            zone = random.randint(2, 12)
            self.dominant_zone = zone
            self.synth.set_zone(zone)
    
    def _update_audio(self):
        """调度音频"""
        if self.synth is None:
            return
        now = time.monotonic()
        if now - self.last_sound_t >= BEAT * 0.46:
            snd = self.synth.pop_sound()
            if snd is not None:
                snd.play()
                self.last_sound_t = now
                self.active_sounds.append(snd)
                if len(self.active_sounds) > 16:
                    self.active_sounds = self.active_sounds[-16:]
    
    def _update_particles(self):
        """更新粒子"""
        if self.world is None:
            return
        self.part_timer += 1
        if self.part_timer % 8 == 0:
            scan_range = 5
            base_wx = int(self.px) - scan_range
            base_wy = int(self.py) - scan_range
            for dy_i in range(scan_range * 2 + 1):
                for dx_i in range(scan_range * 2 + 1):
                    wx_i = base_wx + dx_i
                    wy_i = base_wy + dy_i
                    ct = self.world.cell(wx_i + 0.5, wy_i + 0.5)
                    if ct >= 2:
                        ddx = wx_i + 0.5 - self.px
                        ddy = wy_i + 0.5 - self.py
                        dist2 = ddx * ddx + ddy * ddy
                        if dist2 < 20 and random.random() < 0.3:
                            rel_ang = math.atan2(ddy, ddx) - self.pang
                            dist = math.sqrt(dist2) + 0.01
                            sx = int(W / 2 + math.tan(rel_ang) * (W / 2))
                            sy = int(H / 2 + random.uniform(-60, 60) / dist)
                            if 0 <= sx < W and 0 <= sy < H:
                                self.particles.append(Particle(sx, sy, ct))
        
        # ─── 漂浮拾取物效果（P2-3） ───
        # 在可交互装饰物附近额外生成浮动粒子
        if self.part_timer % 16 == 0:
            nearest = self.interaction.find_nearest_deco(
                self.px, self.py, self.world, self.fragments)
            if nearest is not None:
                wx, wy, deco_type, _ = nearest
                ddx = wx + 0.5 - self.px
                ddy = wy + 0.5 - self.py
                rel_ang = math.atan2(ddy, ddx) - self.pang
                dist = math.sqrt(ddx * ddx + ddy * ddy) + 0.01
                sx = int(W / 2 + math.tan(rel_ang) * (W / 2))
                sy = int(H / 2 - 30 / dist)
                if 0 <= sx < W and 0 <= sy < H:
                    # 生成浮动上升粒子
                    for _ in range(2):
                        p = Particle(sx + random.randint(-8, 8),
                                     sy + random.randint(-5, 5), deco_type)
                        p.vy = -random.uniform(0.5, 1.5)  # 向上浮动
                        p.vx = random.uniform(-0.3, 0.3)
                        self.particles.append(p)
        
        self.particles = [p for p in self.particles if p.update()]
    
    def _render(self):
        """渲染画面（集成增强系统）"""
        if self.world is None or self.renderer is None:
            return

        # 计算有效视野深度
        depth_mult = self.abilities.get_depth_multiplier()
        cur_cx, cur_cy = self.world.world_to_chunk(self.px, self.py)
        is_dark = self.world.is_dark_room(cur_cx, cur_cy)
        dark_factor = 0.6 if is_dark else 1.0
        effective_max_depth = MAX_DEPTH * depth_mult * dark_factor

        # 使用缓存的背景
        self.renderer.render_bg(use_cache=True)
        self.renderer.render_walls(self.px, self.py, self.pang, self.zbuf, self.world, effective_max_depth)
        
        for p in self.particles:
            p.draw(self.screen)
        
        self.renderer.render_scanlines()
        if self.vignette:
            self.screen.blit(self.vignette, (0, 0))
        
        # 图鉴页（Tab切换）
        if self.show_codex:
            draw_codex(self.screen, self.f_sm, self.fragments)
        
        # HUD（含能量/碎片/交互提示）
        cur_chunk = self.world.world_to_chunk(self.px, self.py)
        interaction_in_range = self.interaction.is_in_range(
            self.px, self.py, self.world, self.fragments)
        draw_hud(self.screen, self.f_sm, self.f_bg, self.tick,
                 (self.px, self.py), cur_chunk, self.dominant_zone,
                 energy=self.energy, fragments=self.fragments,
                 interaction_in_range=interaction_in_range,
                 shielded=self.energy.is_shielded(),
                 danger_near=self._danger_near)

    def _handle_game_over(self, dt: float):
        """处理死亡结算状态"""
        self.tick += 1

        # 事件处理
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.state = GameState.QUIT
                return
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_r:
                    self._restart()
                    return
                elif ev.key == pygame.K_ESCAPE:
                    self.state = GameState.QUIT
                    return

        # 渲染游戏场景（冻结）
        if self.world is not None and self.renderer is not None:
            self.renderer.render_bg(use_cache=True)
            self.renderer.render_walls(self.px, self.py, self.pang, self.zbuf, self.world)
            self.renderer.render_scanlines()
            if self.vignette:
                self.screen.blit(self.vignette, (0, 0))

        # 计算并保存最高分
        score = DeathScreen.calculate_score(
            self.fragments, self.survival_time, self.exploration.get_explored_count())
        best = load_highscore()
        save_highscore(score)
        new_best = max(best, score)

        # 叠加结算画面（含最高分）
        DeathScreen.draw(self.screen, self.f_sm, self.f_bg,
                         self.fragments, self.survival_time,
                         self.exploration.get_explored_count(), self.tick)
        # 最高分显示
        if new_best > 0:
            sw = self.screen.get_width()
            hs_col = (255, 255, 100) if score >= best else (120, 120, 140)
            hs_text = self.f_sm.render(f"BEST: {new_best}", True, hs_col)
            self.screen.blit(hs_text, (sw // 2 - hs_text.get_width() // 2, H * 3 // 4 - 30))

    def _restart(self):
        """重置所有系统，返回开始界面"""
        # 重置增强系统（图鉴不清除）
        self.energy.reset()
        self.fragments.reset()
        self.exploration.reset()
        self.abilities = AbilitySystem(self.fragments)
        self.markers.clear()

        # 重置玩家状态
        self.px, self.py, self.pang = 10.5, 10.5, 0.0
        self.survival_time = 0.0
        self.tick = 0
        self.particles.clear()
        self.dominant_zone = 2
        self._danger_near = False
        self._last_zone_chunk = (0, 0)
        self.show_codex = False

        # 重新生成世界
        self.world = None
        self.synth = None
        self.renderer = None
        self.vignette = None
        self.preload_manager = None

        # 返回开始界面
        self.state = GameState.START_SCREEN
        self._start_animate = 0.0
        pygame.mouse.set_visible(True)


def main():
    """入口函数"""
    game = Game()
    game.run()


if __name__ == "__main__":
    main()
