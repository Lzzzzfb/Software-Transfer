# LK-MD2202 两轴电机控制集成实施计划

日期：2026-08-10

设计依据：`docs/superpowers/specs/2026-08-10-lk-md2202-motor-integration-design.md`

目标分支：`codex/lk-md2202-integration`

修改前备份：`backup/pre-lk-md2202-20260810` -> `399d500894e6846858f68dfbb4113138fb4e51ab`

## 1. 实施原则

1. 唯一修改工程为 `D:\codex\光谱仪 - Linux转移版`。
2. 不修改现有光谱仪设备协议、采集、完整帧持久化、保存和数据处理逻辑。
3. 不暂存、覆盖、移动或清理用户已有 Word、Excel、CSV、PDF 和诊断包；每次只对明确路径执行 `git add`。
4. 采用测试先行：先写失败测试，确认失败原因正确，再做最小实现，然后运行相关测试和完整回归。
5. 每个阶段形成独立提交；通信内核、控制器、界面、扫描和部署可以分别回退。
6. LK-MD2202 由 ROCK 4B+ 通过 USB-RS485 直接控制；不再开发、编译、烧录或部署 STM32 电机固件。
7. 旧 `firmware/TMC2209` 保持完整并标记为历史方案；不为目录整洁冒险移动大量已发布文件。
8. 产品目标 Qt 绑定为 PyQt6。Windows 开发环境可临时使用兼容层回退绑定，但正式依赖、ROCK 4B+ 安装和验收必须使用 PyQt6/QtSerialPort。
9. 动作写命令不得自动重试；读事务可有限重试；写动作超时必须进入“结果未知”而不是假定未执行。
10. 自动化测试不能代替 USB-RS485、限位、电流、方向、真实行程和 ROCK 4B+ GUI 实机验收。

## 2. 当前基线

### 2.1 Git 与工作区

- 当前设计提交：`9805f98 docs: design LK-MD2202 motor integration`；
- 开发分支：`codex/lk-md2202-integration`；
- 原 TMC2209 集成分支：`codex/motor-scan-integration`；
- 原版回退标签：`backup/pre-lk-md2202-20260810`；
- 用户未提交的两个 Office 文件和多个未跟踪数据/文档继续保持原状。

实施每一阶段前后运行：

```powershell
git status --short --branch
git diff --name-only
git diff --cached --name-only
git log -6 --oneline --decorate
```

### 2.2 现有自动化基线

Windows 开发机已运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor tests/test_motor_firmware_contract.py tests/test_rock4bplus_deploy.py
```

结果：`57 passed in 4.15s`。测试进程退出后出现 pytest 临时目录访问权限提示，发生在 `atexit` 清理阶段，不影响用例结论；实施中不通过删除用户临时目录处理该提示。

### 2.3 已知替换点

| 当前实现 | 新实现 |
|---|---|
| STM32 ASCII 行协议 | LK-MD2202 Modbus-RTU 二进制协议 |
| 115200 baud USB CDC | 默认 9600 baud、地址 1、8N1 RS-485 |
| X/Y/Z 三轴 | X/M1、Y/M2 两轴 |
| 640 pulse/mm | 320 pulse/mm |
| 主机估算并持久化绝对坐标 | 读取驱动器计数＋会话内软件零点偏移 |
| PB3～PB6 STM32 限位 | M1-K、M2-K 闭合接地零点限位 |
| 固件生成 STEP/DIR | LK-MD2202 自己驱动电机 |

## 3. 阶段 0：迁移事实表、知识库与历史方案保护

### 3.1 新增迁移基线记录

新增：

- `docs/motor/lk-md2202-migration-baseline.md`

记录：

- 当前分支、提交、标签和远端；
- 用户未提交文件清单；
- LK-MD2202 PDF 的路径、SHA-256 和版本 V2.0；
- 当前 Python、Qt 绑定、pytest 基线；
- ROCK 4B+ 已知环境和仍需板端采集的项目；
- USB-RS485 适配器 VID/PID、序列号和内核驱动标记为“待实机采集”，不得猜测。

验证：

```powershell
Get-FileHash -Algorithm SHA256 'D:\codex\电机控制软件\LK-MD2202两路微型步进电机驱动器使用说明V2.0.pdf'
git rev-parse refs/tags/backup/pre-lk-md2202-20260810
git status --short
```

### 3.2 更新知识库和协议文档

新增：

- `docs/motor/lk-md2202-modbus-protocol.md`

修改：

- `docs/motor/motor-control-knowledge-base.md`
- `docs/motor/motor-protocol.md`
- `docs/motor/rock4bplus-acceptance-checklist.md`

要求：

- 新协议文档建立寄存器、功能码、端序、CRC、动作重试和异常码事实表；
- `motor-protocol.md` 顶部明确标记为 STM32/TMC2209 历史协议，并链接新协议；
- 知识库把活动硬件改为 LK-MD2202，仅在历史章节保留 STM32/TMC2209；
- 删除活动方案中 640 pulse/mm、Z 轴、PB3～PB6 和烧录 HEX 的说明；
- 保留上一代知识，不把历史事实改写成新硬件事实。

### 3.3 标记旧固件为历史方案

新增：

- `firmware/TMC2209/LEGACY.md`

内容：

- 说明旧工程对应 STM32/TMC2209，不适用于 LK-MD2202；
- 记录最后可用提交和 HEX SHA-256；
- 明确新版本不构建、不烧录、不部署该目录；
- 给出 Git 标签回退方法。

不移动或删除现有固件，不修改其 C、Keil 工程或 HEX。

### 3.4 阶段验证与提交

```powershell
rg -n "LK-MD2202|320 pulse/mm|4800" docs/motor
git diff --check -- docs/motor firmware/TMC2209/LEGACY.md
git status --short
```

预期提交：

```text
docs: establish LK-MD2202 migration baseline
```

## 4. 阶段 1：纯 Python Modbus-RTU 内核

这一阶段不接 Qt、不打开串口、不修改现有控制器，先建立可以独立证明的二进制协议基础。

### 4.1 先写失败测试

新增：

- `tests/motor/test_modbus_rtu.py`

测试至少覆盖：

1. 标准 CRC16-Modbus，输出顺序为低字节、高字节；
2. 说明书示例请求：`01 10 00 01 00 02 04 00 0A 01 02 92 30`；
3. 说明书示例响应：`01 10 00 01 00 02 10 08`；
4. `0x03` 读保持寄存器请求与变长响应；
5. `0x06` 写单寄存器请求和固定 8 字节回显；
6. `0x10` 写多个连续寄存器及固定 8 字节响应；
7. 功能码最高位置位的 5 字节异常响应；
8. 地址、功能码、字节数、长度和 CRC 不匹配；
9. uint16、uint32、int32 的高寄存器/低寄存器转换；
10. `-1`、int32 最小/最大值和边界溢出；
11. 响应被任意分片、多帧连到一起、前导噪声和半帧保留；
12. 缓冲区上限，损坏数据后可以恢复下一合法响应。

先运行并确认因模块不存在而失败：

```powershell
python -m pytest -q tests/motor/test_modbus_rtu.py
```

### 4.2 最小实现

新增：

- `spectrometer/motor/modbus_rtu.py`

实现：

- `crc16_modbus(data)`；
- `append_crc(frame)` 和 `verify_crc(frame)`；
- `build_read_holding(...)`；
- `build_write_single(...)`；
- `build_write_multiple(...)`；
- `parse_response(...)` 和结构化异常；
- 16/32 位有符号、无符号寄存器转换；
- 根据当前在途事务约束解析预期响应长度的有界缓冲器。

约束：

- 业务层不得自行拼 CRC 或 32 位寄存器；
- 32 位动作参数必须通过一个 `0x10` 请求一次写入；
- 解析器只消费满足地址、功能码、长度和 CRC 全部约束的帧；
- 协议异常与传输超时使用不同错误类型。

### 4.3 验证与提交

```powershell
python -m pytest -q tests/motor/test_modbus_rtu.py
python -m pytest -q tests/motor/test_motor_protocol.py
git diff --check -- spectrometer/motor/modbus_rtu.py tests/motor/test_modbus_rtu.py
```

旧 ASCII 协议测试此时继续通过，证明新内核尚未扰动当前运行链路。

预期提交：

```text
feat: add LK-MD2202 Modbus RTU codec
```

## 5. 阶段 2：LK-MD2202 寄存器语义和主机设置

### 5.1 先写失败测试

新增：

- `tests/motor/test_lk_md2202.py`
- `tests/motor/test_motor_settings_store.py`

扩充：

- `tests/motor/test_motor_models.py`

测试：

1. 身份读取使用软件版本 0x0000 和设备名 0x0006～0x000F；
2. 设备名按寄存器字节解码并严格识别 LK-MD2202，不只凭 VID/PID；
3. M1 使用 0x0010～0x0029，M2 使用 0x0030～0x0049；
4. 地址、波特率使用 0x0050、0x0051；
5. 图片默认值准确映射：1.8°、8细分、50%、零点限位、4800 pulse、无锁定、加减速10000、位置速度10000 pps；
6. 15 mm、4800 pulse、320 pulse/mm 和最小 0.003125 mm；
7. 相对移动使用 int32，负向脉冲正确编码；
8. X/M1、Y/M2 轴映射，拒绝 Z；
9. 限位、运行状态和当前位置解码；
10. 软件方向反转只改变普通运动符号，不改变驱动板内部回零命令；
11. 主机设置默认地址 1、9600、自动端口、X/Y不反转；
12. JSON 设置原子保存，损坏文件回到安全默认值；
13. 非法地址、波特率、终点、限位模式和跨字段冲突被拒绝。

### 5.2 实现设备语义层

新增：

- `spectrometer/motor/lk_md2202.py`
- `spectrometer/motor/settings_store.py`

修改：

- `spectrometer/motor/models.py`（先增加新数据类，不在本阶段切断旧控制器）
- `spectrometer/motor/__init__.py`

建议数据类型：

- `Axis`：最终只允许 X、Y；
- `ConnectionSettings`：自动/手动端口、地址、波特率；
- `AxisDriveConfiguration`：驱动板轴参数；
- `DeviceConfiguration`：M1、M2、通信配置和身份；
- `HostMotorSettings`：稳定端口记录、地址、波特率、X/Y方向反转；
- `AxisRuntimeStatus`：原始脉冲、软件坐标、校准、限位和运行状态。

设备适配函数负责生成和解释寄存器级请求，控制器不得硬编码地址。

### 5.3 配置一致性

- 限位模式强制为零点限位；
- 行程固定 15 mm；
- 默认终点 4800 pulse；
- 默认换算 320 pulse/mm；
- 若步距角或细分变化，返回“需要重新确认脉冲换算”的显式校验结果；
- 不读取设备时把软件默认值标记为“建议值”，不能标记为硬件实值。

### 5.4 验证与提交

```powershell
python -m pytest -q tests/motor/test_lk_md2202.py tests/motor/test_motor_settings_store.py tests/motor/test_motor_models.py
git diff --check -- spectrometer/motor tests/motor
```

预期提交：

```text
feat: model LK-MD2202 registers and motor settings
```

## 6. 阶段 3：二进制串口事务层与安全自动识别

### 6.1 先写失败测试

重写/扩充：

- `tests/motor/test_motor_transport.py`
- `tests/motor/test_motor_discovery.py`

使用 Qt 假串口 worker 覆盖：

1. 打开 9600、8N1、无校验；
2. 单事务队列严格串行；
3. `readyRead` 任意分片和多响应处理；
4. 读命令超时最多重试两次；
5. 相对移动、回零和配置动作不得自动重发；
6. 停止命令可以再次发送；
7. 动作响应超时产生 `result_unknown`；
8. Modbus 异常码、CRC错误和协议错误分别报告；
9. 连接丢失使活动事务和队列安全失败；
10. worker 关闭、线程退出和重复连接不泄漏对象；
11. 自动识别优先保存端口，其次尝试出厂 9600/地址1；
12. 自动识别只发送 0x03 身份读取；
13. 光谱仪已占用串口不会被尝试打开；
14. 同型号 USB 适配器不能只凭 VID/PID 判为驱动板；
15. 无可靠 USB 序列号时，把持久身份降级为当前会话并显示端口信息。

### 6.2 改造传输层

修改：

- `spectrometer/motor/transport.py`
- `spectrometer/motor/discovery.py`

实现：

- worker 发出原始 `bytes_received`，不再按 CR/LF 拆行；
- 事务包含地址、请求帧、预期功能码、响应长度规则、超时、重试策略和动作风险等级；
- transport 使用第 4 阶段的 Modbus 缓冲器；
- 读事务重试由事务本身显式允许；
- 动作事务超时只上报未知状态；
- ResourceError 后立即释放串口并通知控制器；
- 诊断记录端口、地址、寄存器范围、耗时、重试和错误，不默认无限记录完整十六进制帧。

为了保持阶段提交可回归，可以先新增 `ModbusSerialTransport`，保留旧 `MotorSerialTransport` 到下一阶段完成控制器切换；切换后删除旧行协议类。

### 6.3 增加只读探针

新增：

- `spectrometer/motor/probe.py`
- `tools/lk_md2202_probe.py`
- `tests/motor/test_lk_md2202_probe.py`

`spectrometer/motor/probe.py` 提供可通过 `python -m spectrometer.motor.probe` 调用的只读核心，随应用部署；`tools/lk_md2202_probe.py` 只是 Windows 开发机入口。探针默认只执行：列出候选串口、读取身份、读取配置、读取状态。任何写操作都不在该工具首版范围内。Windows 和 ROCK 4B+ 都可运行，输出 JSON 便于保存证据。

### 6.4 验证与提交

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_transport.py tests/motor/test_motor_discovery.py tests/motor/test_lk_md2202_probe.py
python tools/lk_md2202_probe.py --help
python -m spectrometer.motor.probe --help
git diff --check -- spectrometer/motor tools/lk_md2202_probe.py tests/motor
```

预期提交：

```text
feat: add queued Modbus transport and safe motor discovery
```

## 7. 阶段 4：两轴控制器、坐标和主面板切换

这一阶段完成运行链路切换。工作树中可能需要同时改模型、控制器和面板，但提交前相关测试和完整导入必须全部恢复通过。

### 7.1 先改测试表达新行为

重写/扩充：

- `tests/motor/test_motor_controller.py`
- `tests/motor/test_motor_models.py`
- `tests/motor/test_motor_panel.py`
- `tests/test_main_window_acquisition.py`

删除或改写旧实现测试：

- `tests/motor/test_motor_protocol.py`：由 Modbus 和设备适配测试取代；
- `tests/motor/test_motor_state_store.py`：改为软件零点会话状态和主机设置测试。

必须覆盖：

1. 运行时只有 X、Y；
2. 连接后依次读取身份、配置、运行状态、限位和位置，不自动写寄存器；
3. 当前位置来自驱动板 int32 脉冲计数；
4. 软件清零只改变会话内偏移，不写设备；
5. 软件坐标允许为负；
6. 软件回零使用相对脉冲回到软件 0；
7. 机械回零按轴执行 0x0025/0x0045，成功后机械和软件坐标归零；
8. 断线或应用重启后坐标标记未校准，不用旧文件伪装机械位置；
9. 已校准时执行 0～4800 pulse 行程检查；
10. 未校准时允许手动运动；
11. 1 mm 写 320 pulse，15 mm 写 4800 pulse；
12. X/Y方向反转；
13. 动作完成后读取状态和位置并核对增量；
14. 已校准且目标恰为机械 0 时，预期限位视为成功；
15. 未校准、目标大于 0、非运动轴限位或位置异常视为故障；
16. 动作超时不重发并进入结果未知；
17. 急停分别停止 M1、M2；
18. 主界面不存在 Z 控件。

### 7.2 改造领域模型与控制器

修改：

- `spectrometer/motor/models.py`
- `spectrometer/motor/controller.py`
- `spectrometer/motor/__init__.py`

删除活动实现：

- `spectrometer/motor/protocol.py`
- 主机估算绝对坐标的 `state_store.py` 运行依赖；若文件保留，只能有历史说明且不得被导入。

控制器状态机：

```text
DISCONNECTED
  -> PROBING
  -> READING_CONFIGURATION
  -> READY_UNCALIBRATED / READY_CALIBRATED
  -> MOVING / HOMING / APPLYING_CONFIGURATION
  -> RESULT_UNKNOWN / FAULT
```

控制器负责：

- 发现、连接和断开；
- 设备配置快照；
- 软件零点偏移；
- mm/pulse 换算和软件方向映射；
- 动作互斥和状态轮询；
- 预期限位判定；
- 结构化诊断事件；
- 有界关闭 worker。

### 7.3 改造主面板与装配

修改：

- `spectrometer/ui/motor_panel.py`
- `spectrometer/ui/main_window.py`
- `spectrometer/ui/styles.qss`

行为：

- 仅显示 X/Y；
- 最小距离改为 0.003125 mm；
- 显示软件坐标、限位和“已校准/未校准”；
- 保留连接、断开、速度、正负移动、清零、软件回零、机械回零和急停；
- `MainWindow` 不再构造旧 `MotorStateStore`；
- 光谱仪现有信号连接和控制器代码不改。

### 7.4 验证与提交

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_models.py tests/motor/test_motor_controller.py tests/motor/test_motor_panel.py tests/test_main_window_acquisition.py
python -m compileall -q spectrometer
rg -n "Axis\.Z|640|TMC2209|ID\?|MOVE[123]|PB3|PB4|PB5|PB6" spectrometer/motor spectrometer/ui/motor_panel.py
git diff --check -- spectrometer tests/motor tests/test_main_window_acquisition.py
```

活动电机代码的 `rg` 结果应为空；历史文档和 firmware 目录不纳入该断言。

预期提交：

```text
feat: switch motor runtime to LK-MD2202 two-axis control
```

## 8. 阶段 5：电机设置弹窗与安全配置写入

### 8.1 先写失败测试

新增：

- `tests/motor/test_motor_settings_dialog.py`

扩充：

- `tests/motor/test_motor_controller.py`
- `tests/motor/test_motor_panel.py`

测试：

1. 主面板存在“电机设置”，但不平铺次要参数；
2. 弹窗包含连接页和 X/M1、Y/M2 参数页；
3. 建议默认值与用户图片一致；
4. 连接后显示驱动板实值，不自动覆盖；
5. “恢复建议默认值”只修改表单；
6. 限位模式固定为零点限位且不可编辑；
7. 显示 4800 pulse、15 mm、320 pulse/mm 的一致性；
8. X/Y方向反转默认关闭并由主机设置保存；
9. 应用前显示差异；
10. 运动或扫描期间控制器和界面都拒绝写配置；
11. 只写实际变化字段；
12. 32 位字段一次写两个连续寄存器；
13. 隐藏寄存器不会被软件默认值覆盖；
14. 写后整块回读逐字段校验；
15. 地址、波特率最后写，使用新参数重连验证；
16. 部分失败显示成功/失败/未知字段并尝试恢复快照；
17. 只有全部回读一致才显示成功。

### 8.2 实现弹窗

新增：

- `spectrometer/ui/motor_settings_dialog.py`

修改：

- `spectrometer/ui/motor_panel.py`
- `spectrometer/ui/main_window.py`
- `spectrometer/ui/styles.qss`
- `spectrometer/motor/controller.py`
- `spectrometer/motor/settings_store.py`

弹窗使用 Qt 事件循环异步接收读取/写入结果，不在 GUI 线程阻塞等待串口。关闭弹窗时如果正在写配置，先提示并禁止直接销毁活动事务。

控制器增加配置应用状态机和扫描占用标志。即使按钮状态错误，控制器仍必须拒绝扫描期间写配置。

### 8.3 验证与提交

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_settings_dialog.py tests/motor/test_motor_controller.py tests/motor/test_motor_panel.py
python -m pytest -q tests/test_ui_smoke.py tests/test_main_window_acquisition.py
git diff --check -- spectrometer tests/motor
```

预期提交：

```text
feat: add verified LK-MD2202 motor settings dialog
```

## 9. 阶段 6：扫描脉冲计划、未回零策略和采集联动

### 9.1 先写失败测试

重写/扩充：

- `tests/motor/test_scan_plan.py`
- `tests/motor/test_scan_controller.py`
- `tests/motor/test_motor_controller.py`
- `tests/test_main_window_acquisition.py`

测试：

1. 每轮 X 完整行程 `n+1`、Y 行程 `n`；
2. X 方向正、负交替，Y 始终逻辑正向；
3. 全段距离先量化为整脉冲，再用商和余数分配子步，所有子步之和严格等于目标脉冲；
4. 子步脉冲数最多相差 1，不允许逐子步浮点舍入产生累计误差；
5. 步时只存在于相邻扫描子步之间，最后一个扫描子步后为 0；
6. 返回起点不采集、不拆分、不使用扫描步时；
7. 未校准时只验证 `x <= 15`、`y*n <= 15`，允许用户确认后启动；
8. 未校准确认框可继续或取消，且每次新扫描只确认一次；
9. 已校准时检查当前机械起点和 15 mm 边界；
10. 光谱仪在每轮第一个运动前启动连续采集；
11. 最后一个扫描子步完成后停止并保存采集，再返回起点；
12. 每轮单独生成采集任务和文件；
13. 停止、串口断开、光谱仪失败和异常限位会停止两轴与采集，不自动返回；
14. 预期在机械 0 点闭合的限位不会误报；
15. 扫描期间不能打开配置写入；
16. manifest 保存驱动板身份、设备配置快照、320 pulse/mm、方向反转、校准状态和停止原因。

固定示例：

```text
x=10, y=1, n=10, m=2, sx=10, sy=2, t=1
```

断言每轮 X 子步 110、Y 子步 20、扫描子步 130、等待 129；两轮分别采集和保存。

### 9.2 实现扫描计划

修改：

- `spectrometer/motor/scan.py`
- `spectrometer/motor/scan_controller.py`
- `spectrometer/motor/manifest.py`
- `spectrometer/ui/main_window.py`
- `spectrometer/ui/motor_panel.py`

关键实现：

- 扫描计划内部以整数脉冲为权威，mm 仅是输入和显示；
- 未校准计划使用相对原点构造路径，不伪造实际机械起点；
- `ScanController.start(..., allow_uncalibrated=True)` 只在用户确认后调用；
- 扫描状态进入/退出时设置控制器占用标志；
- 保持现有 `AcquisitionController` 的扫描 owner、连续采集、停止、保存和任务清单接口，不修改光谱仪采集实现。

### 9.3 验证与提交

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_scan_plan.py tests/motor/test_scan_controller.py tests/test_main_window_acquisition.py
python -m pytest -q tests/test_acquisition_controller.py tests/test_acquisition_coordinator.py tests/test_storage_coordinator.py
git diff --check -- spectrometer/motor spectrometer/ui tests/motor tests/test_main_window_acquisition.py
```

预期提交：

```text
feat: integrate LK-MD2202 scan with spectrometer acquisition
```

## 10. 阶段 7：PyQt6 主绑定、诊断与用户文档

### 10.1 PyQt6 收口

修改：

- `pyproject.toml`
- `requirements.txt`
- `spectrometer/qt.py`
- `tests/test_qt_compat.py`

实现：

- 项目依赖从 PySide6 改为 PyQt6；
- 所有平台优先加载 PyQt6；
- 兼容回退只用于已有开发环境，不作为发布依赖；
- QtSerialPort 与主 Qt 绑定必须相同；
- 不引入 Tkinter、Matplotlib 或第二套电机界面；
- 在诊断中记录实际 `QT_API`，ROCK 4B+ 验收要求为 PyQt6。

若当前 Windows 解释器仍只有 PyQt5，允许用它执行非发布测试，但必须另外在安装 PyQt6 的环境或 ROCK 4B+ 上完成 PyQt6 测试，不能把 PyQt5 结果标记为产品验收。

### 10.2 结构化诊断

修改：

- `spectrometer/motor/transport.py`
- `spectrometer/motor/controller.py`
- `spectrometer/motor/scan_controller.py`
- `spectrometer/ui/main_window.py`
- 相关诊断测试

记录：

- 端口稳定路径、USB 元数据、地址、波特率；
- 请求类型、寄存器范围、耗时、重试和异常码；
- 动作 ID、目标脉冲、开始/结束位置、限位和校准状态；
- 配置差异、回读和恢复结果；
- 扫描轮次、子步、采集任务 ID、文件和结束原因。

高频状态只累计并限频汇总；完整十六进制帧仅在诊断模式保留有界样本。

### 10.3 用户文档

修改：

- `docs/user-guide.md`
- `docs/motor/rock4bplus-acceptance-checklist.md`

新增或补充：

- RS-485、M1-K/M2-K 接地限位接线；
- 默认配置及“读取实值/应用建议值”区别；
- 未回零扫描风险；
- 软件坐标允许负数；
- 卡滞丢步无法检测及机械回零恢复；
- 电机设置弹窗；
- USB 串口权限和诊断包位置；
- 旧 STM32 HEX 不再烧录。

### 10.4 验证与提交

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/test_qt_compat.py tests/test_diagnostic_core.py tests/test_diagnostic_bundle.py tests/motor
rg -n "Tkinter|tkinter|matplotlib" spectrometer
git diff --check -- pyproject.toml requirements.txt spectrometer docs tests
```

预期提交：

```text
docs: finalize PyQt6 motor diagnostics and operating guide
```

## 11. 阶段 8：ROCK 4B+ 部署载荷与无硬件验证

### 11.1 部署测试先行

修改：

- `tests/test_rock4bplus_deploy.py`

测试：

1. 部署副本包含新的 Modbus、LK-MD2202、设置弹窗和设置存储模块；
2. 部署不包含 `firmware/TMC2209`、测试、PDF、用户数据和诊断包；
3. 安装脚本安装并导入 `python3-pyqt6.qtserialport`；
4. 依赖自检断言 pyqtgraph 使用 PyQt6；
5. 旧 STM32 USB CDC 规则不再作为活动电机规则；
6. udev 不使用 0666 或宽泛 tty 匹配；
7. 在实际 USB-RS485 VID/PID 未采集前，不猜测并写入错误规则；普通用户先通过 `dialout` 访问。

### 11.2 更新部署源

修改：

- `deploy/rock4bplus/install.sh`
- `deploy/rock4bplus/run.sh`（仅在需要时）
- `deploy/rock4bplus/99-zgcai-spectrometer.rules`
- `deploy/rock4bplus/requirements-rock4bplus.txt`（若实际依赖变化）
- `tools/build_rock4bplus_deploy.py`（只在同步规则需要变化时）

不要手工逐个编辑 `deploy/rock4bplus/app`。当前同步脚本只复制新文件，无法移除已经从源码删除的旧 `protocol.py`、`state_store.py` 等文件；本阶段必须把它改为先在仓库内受控临时目录生成完整载荷、校验文件集合，再替换明确的 `deploy/rock4bplus/app` 目标。替换前解析并验证目标绝对路径仍位于仓库的 `deploy/rock4bplus` 下，禁止对未知目录递归删除。

由构建脚本从受控源码同步：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
```

### 11.3 Windows 无硬件验证

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m compileall -q spectrometer deploy/rock4bplus/app
python -m pytest -q tests/test_rock4bplus_deploy.py tests/test_ui_smoke.py tests/motor
python tools/build_rock4bplus_deploy.py --check
git diff --check
```

新增：

- `docs/motor/lk-md2202-host-validation.md`

记录测试命令、提交、Python/Qt 绑定、结果和“未执行硬件”的边界。

预期提交：

```text
build: package LK-MD2202 support for ROCK 4B+
```

## 12. 阶段 9：完整自动化回归和发布候选

### 12.1 完整测试

Windows 开发机：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q
python -m compileall -q main.py spectrometer tools
python tools/build_rock4bplus_deploy.py --check
git diff --check
git status --short
```

检查：

- 所有原光谱仪测试继续通过；
- 没有活动代码引用 Z、640 pulse/mm、STM32 ASCII 命令或主机绝对坐标恢复；
- 部署副本与源码一致；
- 暂存区不含用户文件；
- 设计、计划、代码、测试和文档使用同一组默认参数。

### 12.2 回退演练

只读核对标签内容：

```powershell
git show --stat --oneline backup/pre-lk-md2202-20260810
git diff --stat backup/pre-lk-md2202-20260810..HEAD
```

不在当前脏工作区执行 checkout/reset。需要实际演练时使用新的临时 clone 或独立 Git worktree，验证旧版本能构建，不破坏用户当前文件。

### 12.3 发布候选提交

若完整回归只产生验收文档更新：

```text
test: validate LK-MD2202 host integration
```

未完成实机验收前，不创建“迁移完成”标签，不把状态写成已完成。

## 13. 阶段 10：ROCK 4B+ 与真实硬件验收

此阶段需要用户提供 USB-RS485、LK-MD2202、电机、限位开关和可安全运动的机构。所有动作从低速、短距离开始。

### 13.1 板端环境与 USB 证据

在 ROCK 4B+ 的 SSH Bash 执行：

```bash
cat /etc/os-release
uname -m
python3 --version
ldd --version
id
df -h
lsusb
lsusb -t
ls -l /dev/serial/by-id /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

对实际 USB-RS485 设备执行：

```bash
udevadm info --query=property --name=/dev/ttyUSB0
dmesg --since "10 minutes ago"
```

用实际 VID/PID、序列号和驱动更新验收记录。只有证据明确后才增加精确 udev 规则；不使用 `MODE="0666"`。

### 13.2 上传与安装

在 Windows PowerShell 构建和上传到用户临时目录，不直接覆盖 `/opt`：

```powershell
python tools/build_rock4bplus_deploy.py --check
scp -r '.\deploy\rock4bplus' 'radxa@<IP>:/home/radxa/update-lk-md2202'
```

在 ROCK 4B+ SSH Bash 安装：

```bash
cd /home/radxa/update-lk-md2202/rock4bplus
sudo ./install.sh
```

安装后重新登录以使 `dialout` 组生效。GUI 从板端本地图形桌面启动；SSH 只用于查看日志、USB、CPU、温度和磁盘，不硬编码 `DISPLAY=:0`。

### 13.3 无动作通信验收

先不接电机或确保机构断电：

```bash
cd /opt/zgcai-spectrometer/app
../.venv/bin/python -m spectrometer.motor.probe --json
```

若工具安装路径不同，以部署清单实际路径为准。验证：

- 自动识别包含协议身份而非只看 tty；
- 地址 1、9600、8N1；
- 读取软件版本、设备名和两轴实际配置；
- 没有写寄存器或电机动作；
- 实际 Qt 绑定为 PyQt6。

### 13.4 接线与低风险动作验收

1. 核对 M1/X、M2/Y 电机相线；
2. X 零点开关接 M1-K/GND，Y 接 M2-K/GND；
3. 手动闭合开关，确认界面限位状态；
4. 将位置速度临时设低，在可立即断电条件下执行短距离正反移动；
5. 验证软件方向反转；
6. 确认 LK-MD2202 内部复位方向实际朝零点，再执行单轴回零；
7. 验证零点闭合时不能继续朝零点、可以反向离开；
8. 测试 1 mm = 320 pulse；
9. 测试 15 mm = 4800 pulse；
10. 测量 50% 运行电流对应的实际电流及驱动板/电机温升。

任何方向不符、异常噪声、过流、温升或机构卡滞立即停止，不通过继续试跑“观察是否会好”来排障。

### 13.5 扫描与采集验收

先执行小范围、`t=0` 的单轮扫描，再执行固定示例：

```text
x=10 mm, y=1 mm, n=10, m=2,
sx=10, sy=2, t=1 s
```

验证：

- 未回零时提示风险但允许继续；
- 每轮 X 子步110、Y子步20、等待129；
- 每轮先启动采集、最后子步后停止保存、再返回起点；
- 两轮产生两份任务记录；
- X 准确返回机械 0 时预期限位不误报；
- 电机、光谱仪、串口状态和文件清单一致。

### 13.6 故障与恢复验收

- 扫描中拔掉 USB-RS485：采集停止、任务故障、不自动重发、不自动返回；
- 动作写入后制造响应超时：软件显示结果未知，不重复运动；
- 限位意外闭合：停止扫描与采集；
- 模拟机构卡滞：确认软件不能可靠识别实际丢步，人工排障后机械回零恢复；
- 重插 USB：重新枚举和身份验证，不自动续接旧扫描；
- 应用退出：worker 和串口在有界时间内释放。

### 13.7 验收记录

修改：

- `docs/motor/rock4bplus-acceptance-checklist.md`

新增：

- `docs/motor/lk-md2202-hardware-validation-2026-08.md`

每项只使用：`通过`、`失败`、`待确认`、`不适用`。记录环境、操作、预期、实际、证据路径和结论。自动化通过但硬件未执行的项目必须保持“待确认”。

预期提交：

```text
test: record LK-MD2202 ROCK 4B+ hardware validation
```

## 14. 预期提交序列

```text
docs: establish LK-MD2202 migration baseline
feat: add LK-MD2202 Modbus RTU codec
feat: model LK-MD2202 registers and motor settings
feat: add queued Modbus transport and safe motor discovery
feat: switch motor runtime to LK-MD2202 two-axis control
feat: add verified LK-MD2202 motor settings dialog
feat: integrate LK-MD2202 scan with spectrometer acquisition
docs: finalize PyQt6 motor diagnostics and operating guide
build: package LK-MD2202 support for ROCK 4B+
test: validate LK-MD2202 host integration
test: record LK-MD2202 ROCK 4B+ hardware validation
```

提交消息可以在实施时按实际范围微调，但不得把未经实机验证的内容描述为“硬件通过”。

## 15. 停止条件与问题处理

遇到以下情况暂停当前阶段，不扩大修改范围：

1. PDF 寄存器事实与实机响应不一致；
2. 设备名的寄存器字节顺序无法由说明书或只读实机样本确认；
3. 动作寄存器写入时机、32 位顺序或响应语义不确定；
4. 配置写入后地址/波特率丢失且旧值、出厂值均无法重连；
5. PyQt6 切换导致原光谱仪回归失败；
6. 扫描 owner、停止或保存行为需要修改光谱仪控制逻辑才能通过；
7. 用户未提交文件与目标文件发生路径冲突；
8. USB-RS485 适配器身份不明确，却需要安装宽泛 udev 权限；
9. 实机电流、温升、方向或限位与设计不符；
10. 自动化测试通过但真实位置、采集文件或诊断证据不一致。

排障顺序固定为：

```text
USB枚举/权限
-> 串口参数
-> Modbus原始帧与CRC
-> 寄存器语义
-> 控制器状态机
-> 坐标换算
-> 扫描计划
-> 光谱仪联动
-> GUI显示
-> 部署与启动
```

一次只改变一个层级，并保留最小复现、预期结果和实际证据。

## 16. 最终完成标准

只有以下全部满足才宣布完成：

- 原光谱仪完整回归通过；
- 活动代码只有 X/Y 和 320 pulse/mm；
- Modbus 编解码、事务重试边界和异常处理测试通过；
- 连接只读硬件配置，不自动覆盖；
- 设置写入、回读和通信参数恢复通过；
- 未回零运动/扫描策略符合确认；
- 蛇形扫描计数、步时、返回和光谱仪分轮采集通过；
- PyQt6/QtSerialPort 在 ROCK 4B+ 本地桌面通过；
- USB-RS485、零点开关、方向、1/15 mm 位移、电流和温升实机通过；
- 断线、停止、结果未知和机械卡滞恢复流程通过；
- 部署载荷、诊断、用户文档、备份标签和硬件验收记录完整；
- 用户未提交文件保持未修改、未暂存。
