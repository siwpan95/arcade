# kid_a_walker

KID-A WALKER：暗色赛博风格的第一人称 2D 步行模拟器（伪无限地图，模块化随机拼接）。

## 语言/类型
Python 3（pygame），桌面 GUI 游戏，单文件程序。

## 目录要点
- `kid_a_walker.py`：主程序（pygame 渲染，21×21 房间模块预置库，走近边缘自动生成相邻区块）
- `kid_a_walker.spec`：PyInstaller 打包配置
- `tests/test_map.py`：地图系统回归测试（见下）

## 运行方式与依赖
```
<PATH>/python.exe kid_a_walker.py     # 装有 pygame、numpy 的解释器
```
依赖：`pygame`、`numpy`。GUI 窗口游戏，运行后会阻塞等待交互。

## 测试状态
`py_compile` 语法检查通过（1 个 `.py`）；GUI（pygame）未做自动运行。
`tests/test_map.py` 覆盖地图系统三个不变量：

```
<PATH>/python.exe tests/test_map.py
```

- 模版出口一致性（43 模版"声明出口 == 网格开口"）
- 门洞互通（同模版各门内格互相连通——防"通而不达"/死路）
- 区块出口互连 + 出生点可达（防单边出口 / 出生点牢笼）

> 2026-10-09：以上测试上线时即抓到并修复三类真实缺陷——分组错放
> （tech_nook / data_temple / generator_room 声明出口与实现不符）、
> 单边出口（随机扩充不检查已生成邻居）、门洞通而不达
> （energy_core / portal_gate 四门与大厅之间隔着两行墙、
> generator_room 西侧门廊被发电机块封死）。
