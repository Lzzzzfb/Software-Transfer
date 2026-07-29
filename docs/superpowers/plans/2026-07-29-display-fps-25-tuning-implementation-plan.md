# ROCK 4B+ 实时绘图 25 FPS 调优实施计划

日期：2026-07-29

依据：`docs/superpowers/specs/2026-07-29-display-fps-25-tuning-design.md`

状态：待实现

## 约束

- 只修改 Linux 转移版和 `deploy/rock4bplus`。
- 不修改原 Windows 项目。
- 不暂存或覆盖用户 Excel、DOCX、PDF 和诊断 ZIP。
- 只调整显示通道，不改变串口解析、全量持久化和导出数据。
- 不启用 OpenGL，不实现运行时或自适应 FPS。

## 阶段 1：失败测试

修改：

- `tests/test_ui_smoke.py`
- `tests/test_acquisition_process.py`

先把主窗口契约改为：

```text
display_fps == 25
plot_timer.interval() == 40
```

再增加约 90 FPS、持续一秒的采集核心测试，要求：

- 完整帧和持久化帧均为 90。
- 显示帧为 22～23。
- 显示覆盖不增加真实缺帧。

在修改生产常量前运行测试并确认失败。

## 阶段 2：同步显示参数

修改：

- `spectrometer/ui/main_window.py`
- `spectrometer/acquisition/process_worker.py`
- `README.md`

实现：

```text
DISPLAY_FPS = 25
DISPLAY_INTERVAL_NS = 40_000_000
```

README 的实时显示目标同步更新为 25 FPS，并说明 90 FPS 数据源下实际显示
通常约 22～23 FPS。

不修改非 Linux `SerialWorker` 的兼容显示门，因为 ROCK 4B+ 使用
`AcquisitionProcessProxy` 和采集子进程路径。

## 阶段 3：部署与回归

执行：

```powershell
python -m pytest -q tests/test_acquisition_process.py tests/test_ui_smoke.py
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
python -m compileall -q main.py spectrometer tools
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q
git diff --check
```

确认 `deploy/rock4bplus/app` 中的两个运行常量与源码一致。

## 阶段 4：实机验收

重新安装部署副本后，以 PyQtGraph 后端连续采集至少两分钟并导出诊断包。

通过标准：

- GUI 实际绘制中位数不低于 21 FPS。
- 完整帧等于持久化帧，仍约 90 FPS。
- 缺帧、拒帧和重同步为 0。
- 停止采集及封存正常。
- 实时页主进程单核平均低于 80%。
- 温度最高低于 82°C。

不符合任意稳定性或资源标准时恢复 20 FPS/50 ms，不尝试 30 FPS。
