# ROCK 4B+ PyQtGraph 实时绘图后端实施计划

日期：2026-07-29

依据：`docs/superpowers/specs/2026-07-29-pyqtgraph-plot-backend-design.md`

状态：已实现，待 ROCK 4B+ 实机验收

## 约束

- 只修改 `D:\codex\光谱仪 - Linux转移版`。
- 不修改原 Windows 项目。
- 不暂存或覆盖用户现有的 Excel、DOCX、PDF 和诊断 ZIP。
- 不改变协议解析、全量 `.part/.zgs` 持久化、CSV 或 Excel 数据内容。
- PyQtGraph 只用于实时光谱页；历史数据页继续使用现有绘图控件。
- OpenGL 默认关闭。

## 阶段 1：显示队列覆盖计数

修改：

- `spectrometer/acquisition/process_worker.py`
- `tests/test_acquisition_process.py`

先增加失败测试，构造容量为 1 的显示队列并连续覆盖两个已经发布的显示
事件。验证新事件的 `intentionally_skipped` 等于：

```text
新事件自身主动省略 + 旧事件累计主动省略 + 旧事件本身 1 帧
```

随后修改 `_replace_latest()`，仅对 `frame` 事件合并最后一个字段。控制事件和
全量采集计数不受影响。

## 阶段 2：绘图后端工厂

新增：

- `spectrometer/ui/plot_backend.py`
- `tests/test_plot_backend.py`

实现：

- `ZGCAI_PLOT_BACKEND=legacy` 强制现有 `SpectrumPlotWidget`。
- `ZGCAI_PLOT_BACKEND=pyqtgraph` 强制新后端，缺包时抛出明确错误。
- 未设置时 Linux 优先 PyQtGraph，缺包则返回 legacy 和降级原因。
- 未设置时非 Linux 使用 legacy，保持本机回归测试和 Windows 副本维护边界。
- 工厂返回控件以及包含请求后端、实际后端、降级原因的选择结果。

测试不依赖本机安装 PyQtGraph；通过依赖注入或导入函数替身覆盖全部选择
分支。

## 阶段 3：PyQtGraph 控件

新增：

- `spectrometer/ui/pyqtgraph_plot_widget.py`
- `tests/test_pyqtgraph_plot_widget.py`

实现与现有实时控件一致的公开接口和信号。关键约束：

- 每台设备只创建一个 `PlotDataItem`，后续仅调用 `setData()`。
- 输入转成连续的一维 `float64` NumPy 数组，并拒绝形状不一致或非有限值。
- `antialias=False`、无 symbol、`clipToView=True`、
  `autoDownsample=True`、`downsampleMethod="peak"`。
- OpenGL 不启用。
- 自动范围不在每帧执行；固定 Y 和手动范围优先。
- 自定义 `ViewBox` 实现左键框选、滚轮和双击复位。
- `InfiniteLine` 与限频鼠标代理实现十字光标。
- PNG 导出不改变视图范围。
- 通过视口绘制事件，只对尚未报告的新数据代次发出
  `data_frame_painted`。

本机未安装 PyQtGraph 时跳过真实控件契约测试；工厂和 legacy 回归测试仍
必须运行。ROCK 4B+ 安装自检确保目标机不会走缺包路径。

## 阶段 4：主窗口接入与隐藏页优化

修改：

- `spectrometer/ui/main_window.py`
- `tests/test_main_window_acquisition.py`
- `tests/test_ui_smoke.py`
- `tests/test_diagnostic_acquisition.py`

实现：

- 主窗口通过工厂创建实时绘图控件。
- 启动后把请求后端、实际后端和降级原因写入诊断记录。
- 系统样本增加 `plot_backend`。
- 实时页隐藏时仍调用 `take_latest_frames()` 丢弃旧显示帧，但不执行处理器和
  `setData()`。
- 隐藏期间为每台设备保留最新显示帧；切回实时页后立即处理该最新帧。
- 切页不影响全量采集、序号检查和持久化。

测试验证诊断页激活时处理器与绘图控件不被调用，切回后只显示最新帧。

## 阶段 5：部署

修改：

- `deploy/rock4bplus/install.sh`
- `tests/test_rock4bplus_deploy.py`
- 由 `tools/build_rock4bplus_deploy.py` 同步的 `deploy/rock4bplus/app`

实现：

- APT 安装列表新增 `python3-pyqtgraph`。
- venv 自检导入 `pyqtgraph` 并打印版本。
- 部署源码包含两个新绘图模块。
- 不向部署目录加入测试、文档、诊断包或开发工具。

## 阶段 6：验证与交付

执行：

```powershell
python -m pytest -q tests/test_acquisition_process.py
python -m pytest -q tests/test_plot_backend.py
python -m pytest -q tests/test_pyqtgraph_plot_widget.py
python -m pytest -q tests/test_main_window_acquisition.py tests/test_ui_smoke.py
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
python -m compileall -q main.py spectrometer tools
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q
git diff --check
```

实机 A/B：

```bash
ZGCAI_PLOT_BACKEND=pyqtgraph /opt/zgcai-spectrometer/run.sh
ZGCAI_PLOT_BACKEND=legacy /opt/zgcai-spectrometer/run.sh
```

每个后端采集至少 2 分钟并导出诊断包。完成标准：

- PyQtGraph 后端实际绘制不低于 15 FPS。
- 完整帧等于持久化帧，约 90 FPS。
- 显示覆盖不再产生虚假缺帧 WARN。
- 主进程不再长期占满单核。
- 曲线、缩放、固定 Y、参考、处理模式、停止采集和 PNG 保存正常。
