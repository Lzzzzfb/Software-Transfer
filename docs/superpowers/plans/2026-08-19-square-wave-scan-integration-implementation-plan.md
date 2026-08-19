# ROCK 4B+ 方波控制与扫描联动实施计划

- 日期：2026-08-19
- 依据：`docs/superpowers/specs/2026-08-19-square-wave-scan-integration-design.md`
- 设计提交：`1d75529`
- 当前分支：`codex/lk-md2202-integration`
- 状态：主机实现与部署包已完成；等待 STM32 修改/烧录和 ROCK 4B+ 实机验收
- 当前定向基线：`41 passed in 3.55s`
- 当前完整基线：`516 passed, 5 warnings in 12.84s`
- 说明：本会话没有 `writing-plans` 技能，本文件按仓库既有格式提供等价的测试优先实施计划

## 实施结果（2026-08-19）

- 方波协议、设置、发现、专用 Qt 串口线程、只读探针和业务控制器已完成；
- 主界面紧凑控制行和设置弹窗已完成，联动开关每次启动默认关闭；
- 纯电机/光谱仪逐轮联动、故障停止、状态未知保护和清单/诊断记录已完成；
- ROCK 4B+ 运行副本已同步并通过 109 个运行文件一致性检查；
- 全量自动测试：`591 passed, 5 warnings in 11.53s`；
- 定向部署与方波回归：`104 passed in 1.25s`；
- 尚未执行：用户修改/烧录 STM32，以及方波发生器和光谱仪实机验收。

对应阶段提交：`21e0b44`、`a9e045b`、`76f26b3`、`a360152`、
`7a43d18`、`840e7f8`、`12786a6`、`4ed77b1`、`b0af2a1`。

## 1. 工作区保护、修改范围与提交策略

当前工作区已有用户自己的 Office、CSV、PDF、诊断包、`tmp/` 和
`docs/motor/rock4bplus-update-1df16e3.md` 改动。实施期间不得清理、还原、移动、覆盖或
批量暂存这些文件。所有 `git add` 和提交必须列出本功能的明确路径。

开始每个阶段前记录：

```powershell
git status --short
git diff --name-only
git log -10 --oneline
```

主工程允许新增或修改：

```text
spectrometer/square_wave/                    新增方波业务模块
spectrometer/ui/square_wave_panel.py         新增紧凑主界面控件
spectrometer/ui/square_wave_settings_dialog.py
spectrometer/ui/motor_panel.py               嵌入方波控件
spectrometer/ui/main_window.py               装配、端口互斥、日志和关闭
spectrometer/motor/scan_controller.py        每轮联动状态机
spectrometer/motor/manifest.py               有清单时记录方波摘要
tests/square_wave/                            新增模块测试
tests/motor/test_scan_controller.py          精确联动时序
tests/motor/test_motor_panel.py               界面装配和默认关闭
tests/test_ui_smoke.py                        主窗口与关闭回归
tests/test_rock4bplus_deploy.py               部署一致性
docs/square-wave/                             协议和 STM32 修改说明
docs/motor/rock4bplus-acceptance-checklist.md 后续实机验收项
deploy/rock4bplus/app/                        由构建工具生成的运行副本
deploy/rock4bplus/README.md                   板端识别和回退说明
```

`D:\codex\fangbo` 和 `D:\codex\fangbo\上位机` 只读参考。本轮不直接修改或提交该目录，
也不复制 Tkinter、PySerial、Windows 注册表、`build/`、`dist/` 或 EXE。用户依据生成的
固件修改说明自行修改、编译和烧录。

建议阶段提交：

1. `docs: document square wave protocol and firmware patch`
2. `feat: add square wave serial controller`
3. `feat: add square wave controls to motor workspace`
4. `feat: link square wave to scan rounds`
5. `test: cover square wave diagnostics and shutdown`
6. `build: sync square wave integration to rock4bplus`
7. `docs: package square wave rock4bplus update`

任一阶段失败只回退该阶段明确文件，不回退用户资料、光谱仪数据或已稳定的电机进程。

## 2. 阶段一：冻结协议并生成 STM32 修改说明

新增：

- `docs/square-wave/protocol.md`
- `docs/square-wave/stm32-id-status-modification.md`

协议文档建立事实表，明确：

| 命令 | 响应 | 副作用 |
|---|---|---|
| `ID?` | `ID ZGCAI_SQUARE_WAVE protocol=1` | 无 |
| `STATUS?` | `STATUS running=<0|1> freq=<1..10> width=<1..9999>` | 无 |
| `pulse_freq=<1..10>` | `OK` | 改频率 |
| `pulse_width=<1..9999>` | `OK` | 改脉宽 |
| `START` | `OK` | 开输出 |
| `STOP` | `OK` | 关输出 |

明确 `START:<frequency>,<width>` 不是合法命令，`0483:5740` 只是 USB 候选身份，最终身份
必须由 `ID?` 确认。

STM32 修改说明必须给出可复制的精确代码块和修改位置：

1. 在 `USB_Command.h` 添加设备身份、协议版本和“自定义响应已发送”状态；
2. 在 `USB_Command_ProcessRxData()` 的状态分派中让自定义响应不再追加第二个 `OK`；
3. 在 `USB_Command_ParseAndExecute()` 添加 `ID?` 和 `STATUS?`；
4. 把 `pulse_freq` 判断改为 `freq >= 1 && freq <= 10`；
5. 保持 `pulse_width` 为 `width >= 1 && width <= 9999`；
6. 保持 10 Hz、5 μs 默认值、PB10、TIM2 和现有波形输出逻辑不变；
7. 列出 Keil Build、ST-LINK 下载、复位和串口最小验证步骤；
8. 提供原文件备份和逐文件回退步骤。

文档自检：

```powershell
rg -n "START:|1–9999|1–10|ID\?|STATUS\?" docs/square-wave
git diff --check -- docs/square-wave
```

验收：用户无需猜测 C 枚举、响应格式或修改位置；原固件工程不会被 Codex 自动改动。

## 3. 阶段二：纯模型、协议与设置存储

新增：

- `spectrometer/square_wave/__init__.py`
- `spectrometer/square_wave/models.py`
- `spectrometer/square_wave/protocol.py`
- `spectrometer/square_wave/settings_store.py`
- `tests/square_wave/__init__.py`
- `tests/square_wave/test_models.py`
- `tests/square_wave/test_protocol.py`
- `tests/square_wave/test_settings_store.py`

先写失败测试：

1. 默认参数精确为 10 Hz、5 μs；
2. 频率 1 和 10 接受，0、11、浮点、布尔值和空值拒绝；
3. 脉宽 1 和 9999 接受，0、10000、浮点、布尔值和空值拒绝；
4. 脉宽必须小于本次频率的周期；
5. `OutputOwner.NONE/MANUAL/SCAN` 和“已停/正在输出/未知”状态不可混用；
6. 每条命令生成精确 ASCII 和 `\r\n`；
7. ID、STATUS、OK 和错误响应严格解析；
8. 一行拆成多次输入、多行一次输入、CR/LF 和半行都正确；
9. 缺字段、重复字段、未知身份、未知协议和越界状态拒绝；
10. 设置原子写入，损坏 JSON 回退默认值并留下可诊断结果；
11. 只保存最后成功设备、自动/手动端口偏好、波特率、频率和脉宽；
12. 设置文件不存在“扫描联动方波”字段。

最小实现要求：

- 模型使用冻结 dataclass/Enum，不依赖 Qt 控件；
- 协议层不打开串口、不等待、不重试；
- 行解析器有明确缓冲上限；
- 设置写入使用同目录临时文件、flush、原子替换；
- 损坏文件不覆盖原文件，控制器决定何时报告和重新保存。

定向测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q `
  tests\square_wave\test_models.py `
  tests\square_wave\test_protocol.py `
  tests\square_wave\test_settings_store.py
```

## 4. 阶段三：候选发现、Qt 串口传输和只读探针

新增：

- `spectrometer/square_wave/discovery.py`
- `spectrometer/square_wave/transport.py`
- `spectrometer/square_wave/probe.py`
- `tests/square_wave/test_discovery.py`
- `tests/square_wave/test_transport.py`
- `tests/square_wave/test_probe.py`

先写失败测试：

1. `QSerialPortInfo` 记录规范化端口名、系统位置、VID、PID、序列号和描述；
2. 排除 `ttyFIQ*`、光谱仪、电机以及调用方明确排除的端口；
3. `0483:5740` 排到候选前部，但没有 `ID?` 响应时不能确认设备；
4. 自动发现对候选只发送 `ID?`，不能发送 STATUS、参数、START 或 STOP；
5. 最近成功 USB 序列号优先，但仍重新做协议握手；
6. 串口在工作线程内创建、打开、读写和关闭；
7. 9600、8N1 和无流控配置精确；
8. 同时只存在一个等待响应事务；
9. 任意分片和粘包正确分派到当前事务；
10. 只读 ID/STATUS 超时可有限重试；
11. START 和参数写入超时不自动重发；
12. ResourceError/拔出立即关闭、清空等待事务并报告连接丢失；
13. 关闭线程有界，不遗留 QThread 或串口句柄；
14. `probe` 默认只执行 ID 和 STATUS，输出 JSON，绝不启动信号。

传输实现采用两个对象：

- GUI 线程中的 `SquareWaveTransport` 外观；
- 移入专用 `QThread` 的 `_SquareWaveSerialWorker`，独占 `QSerialPort`。

测试通过注入假串口和虚拟时钟，不打开开发机真实串口、不真实等待。禁止直接在 GUI
线程轮询 `waitForReadyRead()` 或调用 `time.sleep()`。

定向测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests\square_wave\test_discovery.py `
  tests\square_wave\test_transport.py `
  tests\square_wave\test_probe.py `
  tests\test_qt_compat.py
```

## 5. 阶段四：方波控制器与所有权

新增：

- `spectrometer/square_wave/controller.py`
- `tests/square_wave/test_controller.py`

先写失败测试：

1. 自动连接逐个只读握手，只接受身份与协议版本完全匹配的候选；
2. 手动连接也必须执行 ID，不允许绕过身份确认；
3. 参数事务严格为频率 OK、脉宽 OK、STATUS 精确回读；
4. START 严格为 OK 后 STATUS running=1；
5. STOP 严格为 OK 后 STATUS running=0；
6. 参数或启停任一步失败时状态不能伪造成功；
7. 输出过程中拒绝改参数；断开请求必须先停止确认，再关闭串口；
8. 手动输出取得 MANUAL 所有权，扫描不能静默接管；
9. `prepare_scan_round()` 只在已连接、已停止且参数有效时取得 SCAN 所有权；
10. `finish_scan_round()` 停止确认后释放 SCAN 所有权；
11. 串口断开时运行状态改为未知并保留设备身份用于提示；
12. 诊断事件包含设备、参数、操作、ACK、状态、耗时和错误层级；
13. `shutdown()` 正在输出时尽力 STOP、有限等待、记录未知状态并回收线程；
14. 只有成功应用的参数写入 `square-wave-settings.json`。

公开 Qt 信号至少包括：

```text
candidates_changed
connection_changed
status_changed
parameters_applied
operation_failed
diagnostic_event
scan_round_prepared
scan_round_finished
```

控制器不引用 `MainWindow`、电机或光谱仪对象。扫描总控只订阅其公开状态和轮次完成信号。

定向测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\square_wave\test_controller.py
```

## 6. 阶段五：主界面控件、设置弹窗和端口互斥

新增或修改：

- 新增 `spectrometer/ui/square_wave_panel.py`
- 新增 `spectrometer/ui/square_wave_settings_dialog.py`
- 修改 `spectrometer/ui/motor_panel.py`
- 修改 `spectrometer/ui/main_window.py`
- 新增 `tests/square_wave/test_panel.py`
- 新增 `tests/square_wave/test_settings_dialog.py`
- 修改 `tests/motor/test_motor_panel.py`
- 修改 `tests/test_ui_smoke.py`

先写失败测试：

1. 主界面方波行包含状态、频率、脉宽、连接/断开、应用、启动/停止、联动和设置；
2. 初始值 10 Hz、5 μs，联动复选框每个新窗口都为关闭；
3. 即使设置文件或上次会话记录为启用，联动仍不恢复；
4. 连接、参数、运行和故障状态映射到唯一按钮状态；
5. 正在输出时参数和连接操作锁定；
6. 扫描期间所有方波手动控件锁定；
7. 设置弹窗展示自动识别、手动端口、波特率、身份、协议、USB 序列号和候选；
8. 主界面不增加独立滚动区或通信日志框；
9. 电机面板现有按钮、焦点、扫描输入和无滚动布局测试不退化；
10. MainWindow 支持注入假方波控制器，现有测试不访问真实串口；
11. 光谱仪扫描排除已连接方波端口，电机发现排除方波端口，方波发现排除另两类端口；
12. 方波连接完成后状态栏不残留“正在识别”提示。

实现边界：

- `SquareWavePanel` 只收集输入和发信号，不打开串口；
- `MainWindow` 创建控制器、连接信号并提供统一的占用端口集合；
- `MotorPanel` 只负责把紧凑方波控件嵌入现有“电机与扫描”区域；
- 不修改绘图区、侧栏、顶部功能区、处理控件和现有光谱仪样式；
- 不引入 Tkinter、PySerial 或第二套 Qt 绑定。

定向测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q `
  tests\square_wave\test_panel.py `
  tests\square_wave\test_settings_dialog.py `
  tests\motor\test_motor_panel.py `
  tests\test_ui_smoke.py
```

## 7. 阶段六：扫描总控精确联动

修改：

- `spectrometer/motor/scan_controller.py`
- `spectrometer/motor/manifest.py`
- `spectrometer/ui/main_window.py`
- `spectrometer/ui/motor_panel.py`
- `tests/motor/test_scan_controller.py`
- `tests/motor/test_motor_panel.py`

先扩展假方波控制器，让测试记录请求、单调时间、同步/异步成功、失败、断开和状态未知。
新增失败测试：

1. 联动关闭时现有扫描事件和调用顺序完全不变；
2. 联动开启但方波未连接、正在手动输出或状态未知时，扫描在锁电机前被拒绝；
3. 电机轮次预检成功后才写方波参数；
4. 方波参数回读和 START 确认完成后才允许启动光谱仪；
5. `task_started` 后才执行第一段运动；
6. 纯电机联动在 START 确认后直接执行运动；
7. 每轮只写一次冻结参数、只 START 一次、只 STOP 一次；
8. 方波在全部扫描段和步时期间持续输出；
9. 最后扫描段和电机严格终点校验完成后才停止光谱仪；
10. 光谱仪尾帧、停止 ACK、完整性核对和 `scan_capture_sealed` 完成后才 STOP 方波；
11. 方波 STOP 并由 STATUS 确认后才开始第一段回程；
12. 后台 CSV/Excel 延期导出期间方波保持关闭；
13. 多轮扫描在回程结束后重新进行下一轮方波启停；
14. 光谱仪启动失败时关闭已启动的方波，不发送电机扫描路径；
15. 方波启动、停止或状态查询失败时不回程、不进入下一轮；
16. 方波串口断开触发扫描故障并停止电机和光谱仪；
17. 无光谱仪时同样执行“开方波—扫描—关方波—回程”；
18. 清单存在时记录联动、设备身份、参数和启停结果；纯电机仍不为此强行创建光谱清单。

精确事件断言：

```text
signal_start_confirmed < acquisition_started < first_motor_move
last_scan_move < acquisition_sealed < signal_stop_confirmed < first_return_move
```

新增状态：

```text
STARTING_SIGNAL
STOPPING_SIGNAL
```

状态机使用一个明确的“方波停止后的下一动作”枚举，区分正常回程、用户停止和故障收尾，
防止同步信号重入造成重复 STOP、重复回程或重复完成。

定向测试：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests\motor\test_scan_controller.py
```

## 8. 阶段七：用户停止、诊断和应用关闭

修改或新增测试：

- `spectrometer/motor/scan_controller.py`
- `spectrometer/ui/main_window.py`
- `tests/motor/test_scan_controller.py`
- `tests/square_wave/test_controller.py`
- `tests/test_diagnostic_core.py`
- `tests/test_diagnostic_bundle.py`
- `tests/test_ui_smoke.py`

先写失败测试：

1. 用户停止立即停电机；
2. 已启动采集时先等待光谱仪受控停止和封存，再关方波；
3. 光谱仪收尾有界超时后仍关方波，并记录“尾部覆盖无法确认”；
4. 无采集时用户停止直接关方波；
5. 方波停止无法确认时显示“方波输出状态未知”，不伪造已停止；
6. 方波故障不会触发自动回程或下一轮；
7. 关闭主窗口时按扫描停止、光谱停止收尾、方波尽力停止、设备线程关闭顺序执行；
8. 关闭后没有遗留方波 QThread；
9. 方波事件进入现有 `timeline.jsonl` 和诊断 ZIP；
10. 诊断默认不增加独立日志文件，不记录无界高频 STATUS 轮询；
11. 日志包含串口、USB 序列号、参数、ACK/STATUS、单调时间、耗时和故障原因；
12. 现有电机进程、采集进程、存储和诊断关闭测试不退化。

在 `MainWindow.closeEvent()` 中增加方波关闭，但不能改变现有数据封存和存储关闭保证。
如果设备物理断开导致 STOP 无法送达，记录明确警告并按设计提示给板断电。

## 9. 阶段八：完整回归与静态检查

按层运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'

.\.venv\Scripts\python.exe -m pytest -q tests\square_wave
.\.venv\Scripts\python.exe -m pytest -q tests\motor
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_main_window_acquisition.py `
  tests\test_ui_smoke.py `
  tests\test_diagnostic_core.py `
  tests\test_diagnostic_bundle.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q main.py spectrometer tools
git diff --check
```

回归失败不得通过放宽光谱协议、数据完整性、电机限位、行程、扫描终点校验或方波身份
校验解决。性能比较至少确认：

- 纯电机且联动关闭的段间时间不退化；
- 光谱联动且方波开启后，方波串口线程不进入电机进程；
- 方波日志不会产生高频 GUI 事件；
- 完整帧、持久化帧和导出帧计数保证不变。

## 10. 阶段九：部署镜像、板端更新和回退包

自动测试通过后运行：

```powershell
.\.venv\Scripts\python.exe tools\build_rock4bplus_deploy.py
.\.venv\Scripts\python.exe tools\build_rock4bplus_deploy.py --check
.\.venv\Scripts\python.exe -m pytest -q tests\test_rock4bplus_deploy.py
```

构建工具应自动复制 `spectrometer/square_wave/` 和新增 UI 文件。不要手工维护部署源码。

更新 `deploy/rock4bplus/README.md`，列出：

- Debian 12 ARM64、PyQt6 QtSerialPort 自检；
- `/dev/ttyACM*`、`/dev/serial/by-id`、`lsusb` 和 `dialout` 检查；
- 只读 `python -m spectrometer.square_wave.probe` 示例；
- 应用升级前停止 GUI 和工作进程；
- 时间戳备份、逐文件安装、SHA-256、`py_compile` 和回退命令。

没有板端枚举和权限证据前，不修改 `99-zgcai-spectrometer.rules`。若实机证明 Debian 默认
规则不足，再单独审阅并增加精确 `0483:5740`、`GROUP="dialout"`、`MODE="0660"`、
`TAG+="uaccess"` 规则；不得使用 `0666` 或共享唯一别名。

生成新的更新说明，记录设计提交、计划提交、实现提交、部署文件哈希和本地测试结果。

## 11. 用户执行的 STM32 修改与验证

上位机实现完成后，用户按 `docs/square-wave/stm32-id-status-modification.md` 操作：

1. 备份 `USB_Command.c/.h`；
2. 在 Keil5 修改并 Build；
3. 解决全部编译错误和警告；
4. 使用 ST-LINK 烧录；
5. 重新上电；
6. 先在 Windows 或 ROCK 4B+ 执行只读 `ID?`、`STATUS?`；
7. 验证越界频率 0/11 和脉宽 0/10000 返回 `INVALID PARAM`；
8. 再验证 1/10 Hz、1/5/9999 μs 和 START/STOP；
9. 用示波器核对实际频率、脉宽和停止后的低电平；
10. 回传输出或诊断包后再进入联动验收。

首次验证不直接开始扫描，且 STOP 指令和方波板断电手段必须随时可用。

## 12. 后续 ROCK 4B+ 实机验收矩阵

当前没有可用光谱仪，本轮自动化完成后状态只能是“可部署，待实机”。后续依次验收：

1. 方波 USB 枚举、权限、协议身份和序列号；
2. 自动识别不会占用光谱仪或电机串口；
3. 手动参数、启动、停止和软件重启默认联动关闭；
4. 方波参数的示波器实测；
5. 纯电机单轮/多轮方波联动；
6. 光谱仪单轮/多轮联动，并用诊断单调时间证明覆盖关系；
7. 用户停止、急停、电机故障和光谱仪故障；
8. 方波 USB 断开、重连、先 STOP 后恢复；
9. 应用退出和设备重新上电；
10. 长时间运行后的电机流畅度、光谱完整性、CPU、内存和线程清理。

关键验收关系：

```text
方波确认开启时间 < 光谱任务开始时间 < 第一段电机运动时间
最后扫描段完成时间 < 光谱封存完成时间 < 方波确认关闭时间 < 第一段回程时间
```

## 13. 完成与回退条件

软件实现完成需要同时满足：

- 新测试和现有 516 项基线全部通过；
- 联动关闭路径与现有行为一致；
- 联动开启路径满足严格时序和故障互锁；
- 方波线程不进入电机或光谱采集进程；
- 诊断随现有包导出且无独立无界日志；
- 部署副本校验通过；
- 本轮每个提交都只包含明确文件；
- ROCK 4B+ 更新包包含备份、安装、校验和回退步骤。

如自动测试失败，回退到设计提交 `1d75529` 之后的最后一个通过阶段。如板端失败，使用更新
前时间戳备份恢复本轮应用文件；STM32 使用用户保存的原始 `USB_Command.c/.h` 重新编译
烧录。任何回退均不得覆盖光谱数据、设置文件或用户现有未提交资料。
