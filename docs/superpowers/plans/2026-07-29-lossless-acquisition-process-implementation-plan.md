# ROCK 4B+ 上位机尽力无损采集实施计划

日期：2026-07-29

依据：`docs/superpowers/specs/2026-07-28-lossless-acquisition-process-design.md`

状态：待执行

## 执行约束

1. 只修改 Linux 专用副本及 `deploy/rock4bplus`。
2. 原 Windows 项目只读，不修改、不暂存。
3. 不修改、覆盖或暂存用户现有 Excel、DOCX 和产品 PDF。
4. 每个阶段先建立可复现测试，再做最小实现。
5. 正常数据解析继续遵循已实测的 U16 包号格式，同时保留并会话锁定 U32 新版兼容格式。
6. 不声称恢复 MCU 未发送或到达上位机前已丢失的数据。

## 阶段 1：协议会话锁定与零拷贝像素视图

修改或新增：

- `spectrometer/communication/protocol.py`
- `spectrometer/communication/frame_decoder.py`
- `spectrometer/domain/models.py`
- `tests/test_protocol.py`
- `tests/test_frame_decoder.py`
- `tests/test_domain_models.py`
- `tests/test_performance_throughput.py`

实现：

- 定义明确的 `legacy_u16` 和 `u32` 数据协议描述。
- 首个合法数据帧按精确长度识别协议，随后锁定本次设备连接。
- 锁定后只接受对应物理长度；断开重连才清除锁定。
- 实机 U16 格式严格验证：`nPixel=3694`、`nLength=7391`、物理长度 7395。
- 使用 `numpy.frombuffer(dtype=">u2")` 解析原始像素，避免 `struct.unpack → list → tuple` 的逐点 Python 转换。
- 显示帧只复制裁剪后的 3648 个有效像素；持久化通道保留完整 3694 像素。
- 数据 CheckSum 继续消费但不验证。

验证：

```powershell
python -m pytest -q tests/test_protocol.py tests/test_frame_decoder.py tests/test_domain_models.py tests/test_performance_throughput.py
```

## 阶段 2：可流式恢复的会话临时文件 v2

修改或新增：

- `spectrometer/storage/spool.py`
- `spectrometer/storage/recovery.py`
- `tests/test_spool.py`
- `tests/test_recovery_export.py`

实现：

- 临时文件 v2 顺序记录原始总像素、包号位宽、时间戳、协议格式和设备参数。
- 每条记录包含记录长度及上位机 CRC32，检测临时文件自身损坏。
- 直接写入 NumPy/原始像素字节，不用 `struct.pack(*pixels)` 展开数千个 Python 参数。
- 提供流式迭代器，禁止恢复和导出时一次性把全部帧读入内存。
- 定期 `flush`，按有界时间或帧数执行 `fsync`。
- 提供封存清单，记录完整帧数、首末包号、缺帧数、协议格式和文件大小。
- 保持 v1 `.part` 只读恢复兼容。

验证：

```powershell
python -m pytest -q tests/test_spool.py tests/test_recovery_export.py
```

## 阶段 3：独立采集进程与父进程代理

新增或修改：

- `spectrometer/acquisition/process_worker.py`
- `spectrometer/acquisition/process_proxy.py`
- `spectrometer/communication/serial_port.py`
- `spectrometer/device/device_manager.py`
- `tests/test_acquisition_process.py`
- `tests/test_qt_transport.py`
- `tests/test_device_manager_ack.py`

采集进程负责：

- 独占打开串口并运行自己的 Qt Core 事件循环。
- 完成读取、拆包、协议锁定、序号检查和 ACK 解析。
- 接收父进程的命令请求，并返回带请求标识的响应事件。
- 任务开始前创建会话临时文件；每个合法完整帧同步追加后才增加“已持久化”计数。
- 最多每 50 ms 发布最新显示帧；显示队列容量为 1，只覆盖旧显示帧。
- 汇总发送诊断，不逐错误字节轰炸 GUI。

父进程代理负责：

- 使用 `multiprocessing` 的 `spawn` 上下文创建和监管采集进程。
- 将子进程事件转换为现有 Qt 信号，尽量保持 `DeviceManager` 和控制器接口稳定。
- 检测进程退出、控制管道断开和心跳超时。
- 应用退出时先停止设备、封存会话，再回收子进程。

进程间不传输全部帧对象。全量数据只走子进程本地顺序文件；IPC 只传控制事件、低频诊断和最新显示帧。

验证：

```powershell
python -m pytest -q tests/test_acquisition_process.py tests/test_qt_transport.py tests/test_device_manager_ack.py tests/test_device_handshake.py
```

## 阶段 4：全量会话生命周期与停止排空

修改：

- `spectrometer/acquisition/controller.py`
- `spectrometer/device/device_manager.py`
- `spectrometer/storage/session_manager.py`
- `tests/test_acquisition_controller.py`
- `tests/test_storage_session_manager.py`
- `tests/test_simulated_session.py`

实现：

- 在发送开始命令前先成功创建会话临时文件。
- 开始 ACK 成功后才进入正式采集状态。
- 停止时发送 `0x52`，继续接收尾部数据，等待 ACK 和静默窗口。
- 封存临时文件后才将采集任务标记为数据接收完成。
- 即使未选择即时 CSV/Excel 导出，也保留完整会话临时数据及清单，不静默删除。
- 采集进程或持久化失败立即请求受控停止。
- 保存最终化与设备停止保持异步，GUI能够显示“设备已停止、数据最终化中”。

验证：

```powershell
python -m pytest -q tests/test_acquisition_controller.py tests/test_storage_session_manager.py tests/test_simulated_session.py
```

## 阶段 5：从封存会话流式生成 CSV/Excel

修改：

- `spectrometer/storage/process_worker.py`
- `spectrometer/storage/coordinator.py`
- `spectrometer/storage/csv_exporter.py`
- `spectrometer/storage/xlsx_exporter.py`
- `tests/test_storage_process.py`
- `tests/test_storage_coordinator.py`
- `tests/test_batch_exporters.py`

实现：

- 停止当前“采集时把全部 `SpectrumFrame` 放入线程队列、批次导出并等待 `.result()`”的路径。
- 保存进程只从已经封存的 spool 流式读取。
- 按冻结的处理快照执行背景扣除、参考、强度校准和自定义处理。
- 最终 CSV/Excel 只写处理后结果，符合既有决定。
- 大会话按有界批次处理，内存占用不随采集时长线性增长。
- 导出成功后按明确保留策略处理 spool；导出失败保留 spool 和 `.part` 恢复信息。
- 导出帧数必须与封存清单中的合法帧数一致，否则导出失败并保留源数据。

验证：

```powershell
python -m pytest -q tests/test_storage_process.py tests/test_storage_coordinator.py tests/test_batch_exporters.py tests/test_recovery_export.py
```

## 阶段 6：异常阈值与诊断页

修改或新增：

- `spectrometer/acquisition/health_monitor.py`
- `spectrometer/acquisition/sequence_tracker.py`
- `spectrometer/ui/diagnostics.py`
- `spectrometer/ui/main_window.py`
- `tests/test_acquisition_health.py`
- `tests/test_sequence_tracker.py`
- `tests/test_ui_smoke.py`

实现：

- 分别统计原始字节、完整帧、已持久化帧、显示帧和显示覆盖帧。
- 显示包号缺失、重复、回退、回绕、拒绝候选和重新同步。
- 显示协议格式、`nPixel`、起始/有效像素和物理帧长度。
- 滚动 10 秒窗口内异常比例达到 5%，或连续 5 帧异常时受控停止。
- 串口溢出、文件写入失败、磁盘不足、采集进程退出立即停止。
- 单个缺帧或一次重新同步继续运行并记录。
- 诊断事件按时间窗口汇总，不因日志信号制造新的 GUI 队列压力。

验证：

```powershell
python -m pytest -q tests/test_acquisition_health.py tests/test_sequence_tracker.py tests/test_ui_smoke.py
```

## 阶段 7：删除重复热路径

修改：

- `spectrometer/device/device_manager.py`
- `spectrometer/ui/main_window.py`
- `spectrometer/acquisition/coordinator.py`
- 相关测试

实现：

- 移除未使用的 `data_received` 信号。
- 移除旧 `DeviceManager._process_timer`、`_latest_frames` 和无人消费的 `data_arrived` 路径。
- 移除主窗口中只写不读的 `_latest_frames`。
- 保留一套明确的最新显示帧合并器。
- 确保 GUI 处理量只与显示 FPS 有关，不与原始采集 FPS 成正比。

验证：

```powershell
python -m pytest -q tests/test_main_window_acquisition.py tests/test_ui_smoke.py tests/test_performance_throughput.py
```

## 阶段 8：完整回归、部署与板端压力测试

本地执行：

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer tools
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
git diff --check
git status --short
```

板端验收：

1. 关闭自动导出，连续采集至少 10 分钟，确认完整帧数等于已持久化帧数。
2. 开启 CSV/Excel，连续采集至少 10 分钟，确认 GUI 流畅且采集进程计数不受导出影响。
3. 使用不同积分时间和采集间隔，覆盖高于当前约 90 FPS 的输入情况。
4. 人为拖动窗口、缩放曲线和持续操作界面，确认仅显示帧被覆盖。
5. 正常停止三次，确认 `0x21`、开始命令和 `0x52` ACK 正常。
6. 拔插 USB，验证断线、`.part` 保留和自动重连。
7. 模拟磁盘不足、写入失败和采集进程退出，确认受控停止且已有数据可恢复。
8. 核对 spool 清单、CSV 和 Excel 的合法帧数一致。
9. 记录主进程、采集进程和导出进程的 CPU、内存及磁盘吞吐。

完成标准：

- 所有完整合法帧均进入 spool，或者任务明确失败并受控停止。
- GUI、绘图和导出负载不会改变持久化帧数。
- 没有静默丢帧。
- Excel、DOCX 和产品 PDF 仍保持用户原有未提交状态。
- 原 Windows 项目没有任何写入。
