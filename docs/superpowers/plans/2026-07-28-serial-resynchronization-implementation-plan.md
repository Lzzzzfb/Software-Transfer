# ROCK 4B+ 串口帧重新同步实施计划

日期：2026-07-28

依据：`docs/superpowers/specs/2026-07-28-serial-resynchronization-design.md`

状态：待执行

## 执行约束

1. 只修改 Linux 专用副本及其 `deploy/rock4bplus` 运行副本。
2. 不修改、暂存或提交用户现有 Excel 和产品 PDF。
3. 使用针对性测试先复现错误流，再实现最小修复。
4. 不放宽 ACK 校验，不改变数据帧校验的现有固件兼容约定。
5. 不在本次修改自动保存、背景扣除和绘图策略。

## 阶段 1：锁定拆包器恢复行为

修改：

- `tests/test_frame_decoder.py`
- `spectrometer/communication/frame_decoder.py`

测试场景：

- 普通帧校验失败后恢复同一字节流中的合法 ACK。
- 伪帧声明巨大长度时不阻塞后续合法帧。
- 已知像素数时拒绝长度不符的 `0x80` 候选帧。
- 像素数据包含 `0x24` 时仍按完整数据帧输出。

实现：

- 增加合法命令、单帧长度和期望数据帧长度检查。
- 普通帧在消费前校验。
- 非法候选只丢弃当前帧头并继续扫描。
- 增加校验失败、长度失败和重新同步计数。

验证：

```powershell
python -m pytest -q tests/test_frame_decoder.py tests/test_protocol.py
```

## 阶段 2：命令统一经过串口工作线程入口

修改：

- `tests/test_qt_transport.py`
- `tests/test_serial_frame_gate.py`
- `spectrometer/device/device_manager.py`
- `spectrometer/communication/serial_port.py`

实现：

- `send_to_device()` 调用 `do_send_command(cmd, params)`。
- `set_pixel_info()` 把期望像素数同步给拆包器。
- 开始采集时重置发布时钟和主动跳帧计数。
- 控制 ACK 永远绕过显示帧门控。
- 将重新同步错误按批次汇总，避免诊断信号洪泛。

验证：

```powershell
python -m pytest -q tests/test_qt_transport.py tests/test_serial_frame_gate.py tests/test_device_manager_ack.py
```

## 阶段 3：回归与部署副本

执行：

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer tools
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
git diff --check
git status --short
```

核对：

- `spectrometer` 与 `deploy/rock4bplus/app/spectrometer` 中相关运行文件一致。
- Excel 保持用户原有未提交修改。
- 产品 PDF 保持未跟踪。
- 原 Windows 项目没有写入。

## 阶段 4：板端实机验收

1. 完全退出旧程序，拔插光谱仪 USB 以终止残留连续输出。
2. 传输本次变更文件并重启程序。
3. 关闭自动保存，连续执行三轮“采集 30 秒—停止—再次开始”。
4. 每轮确认曲线持续更新，`0x21`、开始命令和 `0x52` 均收到 ACK。
5. 记录重新同步计数、接收帧数和主进程 CPU；若仍失步，再采集原始串口证据，不以跳过 ACK 校验掩盖问题。
