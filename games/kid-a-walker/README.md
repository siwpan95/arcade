# kid_a_walker

KID-A WALKER：暗色赛博风格的第一人称 2D 步行模拟器（伪无限地图，模块化随机拼接）。

## 语言/类型
Python 3（pygame），桌面 GUI 游戏，单文件程序。

## 目录要点
- `kid_a_walker.py`：主程序（pygame 渲染，21×21 房间模块预置库，走近边缘自动生成相邻区块）
- `kid_a_walker.spec`：PyInstaller 打包配置

## 运行方式与依赖
```
F:\Python314\python.exe kid_a_walker.py
```
依赖：`pygame`、`numpy`。GUI 窗口游戏，运行后会阻塞等待交互。

## 测试状态
`py_compile` 语法检查通过（1 个 `.py`）；GUI（pygame）未做自动运行。
