# ROCK 4B+ 显示节流与缺帧误报修复实施计划

日期：2026-07-29

依据：`docs/superpowers/specs/2026-07-29-display-throttle-missing-frame-fix-design.md`

状态：执行中

## 约束

- 只修改 Linux 转移版和 `deploy/rock4bplus`。
- 不修改或暂存用户 Excel、DOCX、PDF 和诊断 ZIP。
- 不改变完整帧先写入 `.part/.zgs` 的权威采集路径。
- 显示抽帧只能影响 GUI，不得影响持久化帧数。

## 阶段 1：复现与协议字段

修改：

- `spectrometer/acquisition/process_worker.py`
- `spectrometer/acquisition/process_proxy.py`
- `tests/test_acquisition_process.py`

先增加失败测试，验证 90 FPS 等价输入经 20 FPS 显示门后：

- 每个显示事件携带自上次显示以来的主动省略帧数。
- 主动省略数在成功发布后清零。
- 人为包号缺口不被主动省略掩盖。
- 完整帧数等于持久化帧数。

## 阶段 2：移除主进程第二时间门

修改：

- `spectrometer/acquisition/coordinator.py`
- `spectrometer/ui/main_window.py`
- `tests/test_acquisition_coordinator.py`
- `tests/test_main_window_acquisition.py`

实现 `take_latest_frames()`：GUI 的 50 ms `QTimer` 每次直接消费最新帧，不再由协调器比较另一个 50 ms 时间门。保留旧接口供兼容测试，不再用于 Linux 主窗口绘图。

验证主动省略帧经过 `SequenceTracker` 后不产生缺帧 WARN，真实缺口仍然可见。

## 阶段 3：最终封存计数

修改：

- `spectrometer/device/device_manager.py`
- `tests/test_acquisition_process.py`
- `tests/test_main_window_acquisition.py`

采集子进程发出 `session_sealed` 时，`DeviceManager` 先把封存结果作为最终
`acquisition_diagnostics` 发布，再通知控制器完成任务。这样任务结束时飞行记录器拿到最终帧数。

## 阶段 4：GUI 诊断指标

修改：

- `spectrometer/ui/main_window.py`
- `tests/test_diagnostic_acquisition.py`
- `tests/test_ui_smoke.py`

实现：

- 系统诊断样本加入 GUI 实际绘图 FPS。
- 页签切换记录页签名称和索引。
- 真实缺帧日志按一秒窗口汇总，避免任何异常形成逐帧日志风暴。

## 阶段 5：部署与回归

执行：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
python -m compileall -q main.py spectrometer tools
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q
git diff --check
```

实机完成标准：

- 完整/持久化约 90 FPS 且一致。
- 不再持续出现“缺少 4 帧”。
- 子进程发布约 18～20 FPS。
- GUI 实际绘图目标不低于 15 FPS。
- 最终诊断摘要等于 `.zgs` 封存帧数。

