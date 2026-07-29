# ROCK 4B+ PyQtGraph 实时绘图后端迁移设计

## 1. 背景与目标

最新实机诊断表明：

- 完整接收和持久化约 89.1 FPS，计数完全一致。
- 采集子进程向 GUI 发布约 17.75 FPS。
- 实时光谱页实际绘制仅约 3.64 FPS。
- 实时光谱页主进程约占满一个 CPU 核心（98.6%）。
- 切到诊断页、不执行可见曲线绘制时，主进程降至约 32.7%。

现有自绘后端在每次 paint 中通过 Python 列表创建大量 `QPointF`，在
ROCK 4B+ 的 X11 软件绘制路径上成为主要瓶颈。

目标是在不改变采集、解析、处理和全量持久化路径的前提下，把 Linux
实时光谱绘图迁移到 PyQtGraph，并达到：

- 单台 3648 点曲线实际可见绘制不低于 15 FPS。
- 完整帧和持久化帧仍约 90 FPS 且完全一致。
- 保留窄峰、缩放、固定范围、参考曲线、十字光标和 PNG 保存。
- PyQtGraph 不可用时能显式降级到旧后端，而不是阻止软件启动。

## 2. 依赖选择

目标系统使用 Debian 12 Bookworm ARM64、Python 3.11 和系统 PyQt6。
安装脚本新增 Debian 官方包：

```text
python3-pyqtgraph
```

Bookworm 提供 `python3-pyqtgraph 0.13.1-4`，是架构无关的纯 Python
包，依赖 NumPy，并支持以 PyQt6 作为 Qt 绑定：

- https://packages.debian.org/bookworm/amd64/python3-pyqtgraph

应用 venv 已使用 `--system-site-packages`，因此不在 pip 依赖中重复安装
PyQtGraph。安装自检必须导入 PyQt6、PyQtGraph、NumPy 并打印版本。

本阶段不启用 OpenGL。普通 PyQtGraph 路径达到验收值后保持 CPU/Qt
GraphicsView 后端；只有普通路径仍不合格时才单独评估 ROCK 4B+ Mali
驱动上的 OpenGL。

## 3. 后端边界

保留现有 `SpectrumPlotWidget` 公开行为，新增后端工厂：

```text
spectrometer/ui/plot_backend.py
spectrometer/ui/pyqtgraph_plot_widget.py
spectrometer/ui/plot_widget.py          # 现有 legacy 后端
```

`plot_backend.create_spectrum_plot_widget()` 根据环境选择：

- `ZGCAI_PLOT_BACKEND=pyqtgraph`：强制 PyQtGraph；不可导入时返回明确错误。
- `ZGCAI_PLOT_BACKEND=legacy`：强制现有 QPainter 后端。
- 未设置：
  - Linux 优先 PyQtGraph；
  - PyQtGraph 不可用时降级 legacy，并公开降级原因；
  - 非 Linux 测试环境保持 legacy，避免改变原 Windows 项目的维护边界。

主窗口只通过共同接口操作绘图控件，不直接导入 PyQtGraph。
诊断飞行记录器记录：

- 请求的后端；
- 实际启用的后端；
- 降级原因；
- 实际完成新数据绘制的 FPS。

## 4. 共同接口

PyQtGraph 后端实现当前主窗口使用的全部接口：

- `update_device_curve(device_id, x, y, label)`
- `remove_device_curve(device_id)`
- `add_reference_curve(x, y, label)`
- `clear_reference_curves()`
- `set_baseline_curve(device_id, x, y, visible)`
- `set_axis_labels(x_label, y_label)`
- `set_line_width(width)`
- `set_device_color(device_id, color)`
- `enable_auto_range(enabled)`
- `set_fixed_y_range(minimum, maximum)`
- `disable_fixed_y()`
- `reset_initial_view()`
- `set_x_range()`、`set_y_range()`、`set_view_range()`
- `save_image(path)`
- `format_axis_value()`

信号：

- `user_zoomed`
- `view_reset`
- `data_frame_painted`

主窗口、状态栏、处理模式和采集控制器不感知具体后端。

## 5. PyQtGraph 配置

全局和曲线配置：

- 白色背景、深色前景，与现有界面一致。
- `antialias=False`。
- 实线，默认宽度 1；用户设置大于 1 时允许应用，但诊断记录性能提示。
- 曲线数据直接传入一维连续 NumPy 数组。
- 不显示每点 symbol。
- `clipToView=True`。
- `autoDownsample=True`。
- 降采样方法使用 `peak`，保留区间最小值和最大值，避免漏掉窄峰。
- 输入在处理管线已经保证为有限数时启用 `skipFiniteCheck=True`。

这些选项对应 PyQtGraph 官方的实时绘图优化能力：

- https://pyqtgraph.readthedocs.io/en/pyqtgraph-0.13.3/api_reference/graphicsItems/plotdataitem.html

每台设备只创建一个 `PlotDataItem`；后续帧只调用 `setData()`，不得删除并
重建曲线、坐标轴、图例或 ViewBox。

## 6. 交互行为

### 6.1 缩放

- 左键框选放大。
- 滚轮围绕光标缩放。
- 左键双击恢复初始范围。
- 用户缩放后发出 `user_zoomed`，关闭自动范围。
- 复位后发出 `view_reset`。

使用自定义 `ViewBox` 子类适配以上事件，不在主窗口重复实现。

### 6.2 自动与固定范围

- 自动范围只在新设备加入、用户明确复位或数据范围确需变化时更新。
- 不允许每个显示帧都调用完整 `autoRange()`。
- 固定 Y 范围时只自动更新 X 范围。
- 手动缩放时保持用户当前视图。

### 6.3 十字光标

使用两条 `InfiniteLine` 和一个文本标签。鼠标移动事件通过
`SignalProxy` 限制更新频率，不允许十字光标产生比曲线更高的刷新压力。

### 6.4 参考与基线

设备曲线、参考曲线和基线使用独立的 `PlotDataItem`。参考曲线数量继续
限制为 64，图例最多展示当前既有上限，清除参考不得影响实时设备曲线。

### 6.5 PNG

优先使用 PyQtGraph 图像导出器；导出失败时返回 `False` 并由主窗口记录
错误。导出不得改变当前缩放范围。

## 7. 隐藏页面行为

当实时光谱页不可见时：

- 主窗口不执行显示光谱处理和 `setData()`。
- 采集协调器继续把待显示槽更新为最新帧。
- 全量采集、序号检查和 `.zgs` 持久化继续运行。
- 切回实时光谱页时立即消费最新帧并恢复绘制。

这样诊断页和历史页不会浪费主线程 CPU，同时不会积压显示队列。

## 8. 显示覆盖缺帧误报补充修复

采集子进程的显示队列容量为 1。若新显示事件覆盖旧显示事件：

- 新事件的 `intentionally_skipped` 加上：
  - 被覆盖事件已经累计的 `intentionally_skipped`；
  - 被覆盖的显示事件本身 1 帧。

即：

```text
new_skipped += old_skipped + 1
```

因此不论 GUI 阻塞多久，下一次送达主线程的显示帧都能解释完整包号跨度。
Linux 权威全量诊断仍来自采集子进程，显示覆盖不得增加真实缺帧。

## 9. 错误与回退

- 自动模式下 PyQtGraph 导入失败：启用 legacy，启动后在诊断页记录 WARN。
- 强制 `pyqtgraph` 模式导入失败：显示明确启动错误，便于发现部署缺包。
- PyQtGraph 曲线更新异常：记录设备号和异常，不影响采集/持久化。
- OpenGL 默认关闭，避免驱动失败造成黑屏。
- legacy 后端保留到 PyQtGraph 通过完整实机验收；本阶段不删除旧实现。

## 10. 测试

### 10.1 接口契约

对两个后端运行同一组契约测试：

- 添加、更新、隐藏和移除设备曲线。
- 参考曲线上限与清除。
- 自动范围、固定 Y、手动缩放和复位。
- 轴标签与颜色。
- PNG 保存。
- `data_frame_painted` 只在新数据实际绘制后计数。

### 10.2 数据与峰值

- 输入 3648 点数据，包含单像素窄峰。
- 自动 `peak` 降采样后可见范围仍包含峰值最大值。
- 原始 NumPy 数组不被绘图后端修改。
- NaN/Inf 在进入 `skipFiniteCheck` 前由处理管线拒绝。

### 10.3 运行路径

- Linux 默认选择 PyQtGraph。
- `ZGCAI_PLOT_BACKEND=legacy` 正确回退。
- 自动模式缺包时正确回退并给出原因。
- 隐藏实时页时不调用处理器和 `setData()`；切回后显示最新帧。
- 多级显示队列覆盖后，`intentionally_skipped` 能解释全部跨度。

### 10.4 部署

- 安装脚本包含 `python3-pyqtgraph`。
- venv 自检输出 PyQtGraph 版本。
- 部署目录只包含运行文件。
- 完整测试和部署一致性检查通过。

## 11. 实机验收

同一次安装中进行 A/B：

```bash
ZGCAI_PLOT_BACKEND=pyqtgraph /opt/zgcai-spectrometer/run.sh
ZGCAI_PLOT_BACKEND=legacy /opt/zgcai-spectrometer/run.sh
```

每种后端连续采集至少 2 分钟并导出诊断包。

PyQtGraph 完成标准：

- 完整帧等于持久化帧，约 90 FPS。
- 真实缺帧、拒帧和重同步为 0（除非 MCU/USB 实际产生缺口）。
- 不再出现显示覆盖导致的缺帧 WARN。
- 实时光谱页实际绘制不低于 15 FPS。
- 主进程 CPU 相比 legacy 明显下降，不再长期占满单核。
- 页面切换、缩放、复位、固定 Y、参考曲线和 PNG 保存正常。

若普通 PyQtGraph 路径达到以上条件，本阶段不启用 OpenGL。

## 12. 非目标

- 不修改 MCU 协议。
- 不改变采集子进程、全量 spool、CSV 或 Excel 数据内容。
- 不在原 Windows 项目中引入 PyQtGraph。
- 不在本阶段删除 legacy 后端。
- 不以降低完整数据保存率换取显示 FPS。
