# ROCK 4B+ 光谱仪诊断飞行记录器实施计划

日期：2026-07-29

依据：`docs/superpowers/specs/2026-07-29-diagnostic-flight-recorder-design.md`

状态：已实现，待 ROCK 4B+ 实机验收

## 执行约束

1. 只修改 `D:\codex\光谱仪 - Linux转移版` 及其中的 `deploy/rock4bplus`。
2. 不修改、暂存或提交原 Windows 项目。
3. 不修改、覆盖、删除或暂存用户现有 Excel、DOCX 和产品简介 PDF。
4. 诊断功能只能旁路观察，不得改变“完整帧先持久化、显示再限帧”的采集主路径。
5. 默认诊断包不得包含完整光谱值、`.zgs`、`.part`、CSV 或 Excel。
6. 诊断记录失败必须降级，不能导致采集任务失败。
7. 每个阶段先建立可复现测试，再做最小实现。

## 阶段 1：诊断数据模型、路径和会话清单

新增或修改：

- `spectrometer/diagnostics/__init__.py`
- `spectrometer/diagnostics/models.py`
- `spectrometer/diagnostics/paths.py`
- `spectrometer/services/platform_paths.py`
- `tests/test_diagnostic_models.py`
- `tests/test_diagnostic_paths.py`

实现：

- 定义诊断格式版本、运行 ID、采集 ID、事件等级和稳定 JSON 字段。
- 定义运行清单、采集摘要、设备快照、性能样本和帧摘要的序列化结构。
- 新增诊断根目录：
  `~/.local/state/ZGCAI/Spectrometer/diagnostics/`。
- 所有时间同时记录带时区 ISO 8601 和 `monotonic_ns`。
- 路径生成只接受程序生成的安全 ID，禁止绝对路径、`..` 和目录穿越。
- 清单使用临时文件加 `os.replace` 原子更新。

验证：

```powershell
python -m pytest -q tests/test_diagnostic_models.py tests/test_diagnostic_paths.py tests/test_linux_paths.py
```

## 阶段 2：非阻塞记录器与持久化时间线

新增：

- `spectrometer/diagnostics/recorder.py`
- `tests/test_diagnostic_recorder.py`

实现：

- `DiagnosticRecorder` 使用固定容量队列和专用 Python 写入线程。
- 调用方使用非阻塞 `record_event()`、`record_sample()` 和
  `record_frame_summary()`。
- 关键事件保持独立记录；同类性能样本在压力下只保留最新值。
- 队列满时累计：
  - `coalesced_samples`
  - `dropped_diagnostic_events`
- 下一次成功写入时把累计丢弃数量写入时间线。
- JSONL 按 UTF-8 追加，不做每条 `fsync`；正常结束时 flush、fsync 并关闭。
- 运行清单区分 `active`、`complete` 和 `incomplete`。
- 启动时识别上次未正常关闭的目录，但不改写其原始时间线。
- 诊断目录不可写时发出一次降级通知，后续调用快速返回。

验证：

```powershell
python -m pytest -q tests/test_diagnostic_recorder.py
```

关键断言：

- 生产者在慢写入模拟下不会等待消费者。
- 性能样本允许合并，采集关键事件丢弃可见。
- 多次启动/停止不会泄漏线程或文件句柄。

## 阶段 3：系统、进程和设备状态采样

新增或修改：

- `spectrometer/diagnostics/system_sampler.py`
- `spectrometer/acquisition/process_proxy.py`
- `spectrometer/device/device_manager.py`
- `tests/test_system_sampler.py`
- `tests/test_process_proxy.py`

实现：

- 从 `/proc` 读取系统负载、内存以及主进程/采集子进程：
  - CPU 时间；
  - RSS；
  - 线程数；
  - 进程状态；
  - 存活标志。
- 从 `/sys/class/thermal/thermal_zone*/temp` 读取可用温度并保留来源。
- 使用 `shutil.disk_usage` 记录数据目录和诊断目录剩余空间。
- Windows 测试环境中不可用字段返回 `null`，不得抛出平台错误。
- `AcquisitionProcessProxy` 只公开采集子进程 PID，不增加高频 IPC。
- `DeviceManager` 提供设备身份、协议版本、像素和当前采集参数快照。
- 1 Hz 采样由主程序定时器触发，实际文件写入仍交给记录线程。

验证：

```powershell
python -m pytest -q tests/test_system_sampler.py tests/test_process_proxy.py tests/test_device_identity.py
```

## 阶段 4：采集时间线、速率统计和帧隐私摘要

新增或修改：

- `spectrometer/diagnostics/acquisition_observer.py`
- `spectrometer/acquisition/process_worker.py`
- `spectrometer/acquisition/process_proxy.py`
- `spectrometer/acquisition/controller.py`
- `spectrometer/device/device_manager.py`
- `tests/test_diagnostic_acquisition.py`
- `tests/test_acquisition_process.py`
- `tests/test_acquisition_controller.py`

实现：

- 采集控制器建立/结束诊断采集会话，并记录：
  - 配置、开始、停止命令；
  - ACK 成功、失败和超时；
  - 封存、受控停止和导出结果。
- 每秒把采集子进程现有累计计数送入观察器。
- 观察器计算相邻样本增量和实际时间跨度，生成：
  - 完整帧 FPS；
  - 持久化 FPS；
  - 显示 FPS；
  - 显示覆盖 FPS；
  - 缺帧、拒绝候选和重同步增量。
- 计数器重置或采集进程重启时建立新基线，不生成负增量。
- 采集进程在不复制连续全量数据的前提下，始终维护最近 10 个原始帧的易失环形缓存。
- 首段、中段、末段代表帧只生成：
  - 长度、序号和像素布局；
  - min/max/mean/std；
  - 原始像素 SHA-256。
- 采集进程通过低频诊断事件发送摘要；完整最近帧仅在用户明确导出时按一次性请求返回。
- 完整帧样本请求不得读取 `.zgs`，不得改变持久化计数和显示队列。

验证：

```powershell
python -m pytest -q tests/test_diagnostic_acquisition.py tests/test_acquisition_process.py tests/test_acquisition_controller.py
```

关键断言：

- 诊断开启前后，输入完整帧数与持久化帧数一致。
- 默认路径不把完整像素写入诊断目录。
- 环形缓存始终最多 10 帧，返回后仍不影响采集。

## 阶段 5：保留策略与安全清理

新增：

- `spectrometer/diagnostics/retention.py`
- `tests/test_diagnostic_retention.py`

实现：

- 清理顺序：
  1. 删除超过 7 天的已识别诊断运行目录；
  2. 若仍超过 200 MB，按完成时间删除最旧目录。
- 活跃运行目录不参与清理。
- 只删除诊断根目录直接子级中带有效程序清单的运行目录。
- 对符号链接、路径解析失败、清单损坏和根目录越界采用跳过并记录。
- 只处理诊断包 `.tmp` 和诊断会话，明确排除 `.zgs`、`.part`、CSV、Excel。
- 清理在启动完成后和会话结束后低频执行，不在采集热路径运行。

验证：

```powershell
python -m pytest -q tests/test_diagnostic_retention.py
```

## 阶段 6：一致性 ZIP 导出器

新增：

- `spectrometer/diagnostics/bundle_exporter.py`
- `tests/test_diagnostic_bundle.py`

实现：

- 选择最近一次采集；无采集时退回当前/最近运行会话。
- 导出开始时冻结各 JSONL 文件的可读字节边界。
- ZIP 内容遵循设计文档的固定成员名称。
- `startup.log` 只截取受限尾部并过滤明显的敏感环境信息。
- 默认只包含帧摘要。
- 勾选后向采集代理请求最近 10 帧，写入 `frames.npy` 和
  `metadata.json`；采集进程已退出时在清单记录不可用。
- `bundle-manifest.json` 记录成员大小、SHA-256、缺失项和
  `live_snapshot`。
- 工作线程先写 `.tmp`，关闭后重新打开 ZIP、验证所有成员和哈希，再
  `os.replace` 为 `.zip`。
- 失败时删除本次 `.tmp`，不修改源诊断目录。

验证：

```powershell
python -m pytest -q tests/test_diagnostic_bundle.py
```

关键断言：

- 默认 ZIP 中找不到完整像素和采集数据文件。
- 可选样本恰好为可用的最近最多 10 帧。
- 活跃会话导出只包含冻结边界前的一致前缀。
- 损坏或空间不足不会留下伪成功 `.zip`。

## 阶段 7：诊断页和主窗口集成

新增或修改：

- `spectrometer/ui/diagnostics.py`
- `spectrometer/ui/main_window.py`
- `tests/test_diagnostics_ui.py`
- `tests/test_ui_smoke.py`
- `tests/test_main_window_acquisition.py`

实现：

- 诊断页增加：
  - “导出诊断包”按钮；
  - “包含最近 10 个完整原始帧”复选框；
  - 导出状态和最终路径。
- 使用 `QFileDialog.getSaveFileName` 选择 ZIP 路径，默认文件名含设备序列号、采集时间和短会话 ID。
- 导出工作在线程中执行；按钮在单次导出期间禁用，采集控制不禁用。
- 主窗口 `_log()` 同时更新界面和结构化记录器。
- 程序启动后建立运行会话，关闭窗口时有界等待记录器 flush；超时则留下可识别的不完整会话。
- 设备连接、参数更新、采集诊断和存储事件接入记录器。
- 诊断失败仅显示一次警告，不弹出循环错误对话框。

验证：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/test_diagnostics_ui.py tests/test_ui_smoke.py tests/test_main_window_acquisition.py
```

## 阶段 8：部署同步、完整回归和性能验证

运行：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q
python -m compileall -q main.py spectrometer tools
git diff --check
git status --short
```

部署目录必须新增诊断模块，但不得加入测试、开发文档或本地生成的诊断包。

性能测试补充：

- 构造高于实机约 90 FPS 的连续完整帧输入。
- 对比关闭/开启记录器时的：
  - 输入帧数；
  - 完整帧数；
  - 持久化帧数；
  - 测试总耗时。
- 必须保证帧计数完全一致；诊断开销异常时只允许合并性能样本。

## 板端实机验收

1. 桌面图标启动程序，确认连接 SN003、版本和 3648 有效像素。
2. 关闭自动保存连续采集至少 2 分钟，采集中导出实时诊断包。
3. 停止后导出最近一次诊断包，核对完整帧等于持久化帧。
4. 勾选“最近 10 帧”再次导出，确认 ZIP 包含样本且程序仍可继续采集。
5. 开启自动保存重复测试，确认 CSV/Excel 导出过程完整记录。
6. 拔出 USB，确认断线、未完成会话和自动重连均有时间线。
7. 重新启动程序，确认仍可导出上一次异常/未完成会话。
8. 连续运行足够时间后确认保留策略只清理诊断目录。

完成标准：

- 一键 ZIP 能还原采集、持久化、显示和系统负载的时间关系。
- 默认包不包含完整光谱数据；可选包最多包含最近 10 帧。
- 诊断功能在任何失败或压力情况下都不阻塞采集。
- 启用诊断后所有完整合法帧仍进入 spool，计数无变化。
- 部署副本与运行源码一致，完整自动测试通过。
- 用户的 Excel、DOCX 和产品简介 PDF 保持未提交状态。
