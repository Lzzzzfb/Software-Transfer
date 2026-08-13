# LK-MD2202 脱离卡死与机械回零有效性校验实施计划

日期：2026-08-13

设计依据：`docs/superpowers/specs/2026-08-13-lk-md2202-stall-release-home-validation-design.md`

目标分支：`codex/lk-md2202-integration`

实施前提交：`ca70b34 docs: design LK-MD2202 stall recovery`

## 1. 范围与保护

1. 唯一修改工程为 `D:\codex\光谱仪 - Linux转移版`。
2. 不修改现有光谱仪设备协议、采集、绘图、保存和数据处理逻辑。
3. 不修改或清理用户已有的两个 Office 文件、CSV/XLSX 数据、PDF、`tmp/`、诊断包和项目总结文档。
4. 每次只对本计划明确列出的路径执行 `git add`，每一阶段形成可独立回退的提交。
5. 不手工修改 `deploy/rock4bplus` 中的运行副本；源代码通过后统一由构建脚本生成部署镜像。
6. Windows 自动化测试不能替代 ROCK 4B+、USB-RS485、真实限位和电机运动验收。
7. 自动发现和命令行探针继续保持只读；只有用户在正式 GUI 中主动点击运动、设速、回零、急停或脱离卡死时才写驱动板。
8. 持续运动命令、相对运动和回零命令不得因超时自动重发；写入超时按“结果未知”处理。

实施开始前记录：

```powershell
git status --short --branch
git diff --name-only
git diff --cached --name-only
git log -6 --oneline --decorate
```

现有预期自动化基线为完整测试 `385 passed, 1 skipped`，电机测试 `93 passed`。正式实施时先重新运行电机基线；若基线变化，先记录差异，不通过清理用户文件解决。

## 2. 阶段 1：速度模式协议请求

### 2.1 先写失败测试

修改：

- `tests/motor/test_lk_md2202.py`

新增测试：

1. `velocity_mode_request(1, X, 5000)` 使用一次 Modbus `0x10` 写入 `0x001C～0x001D`；
2. `velocity_mode_request(1, Y, -5000)` 使用一次 `0x10` 写入 `0x003C～0x003D`；
3. 正数、负数、int32边界和 0 均使用现有统一的 `int32_to_registers()` 编码；
4. 非 X/Y 轴和 int32 溢出明确拒绝；
5. 现有 `position_speed_request()` 仍写 `0x001E/0x001F` 或 `0x003E/0x003F`，避免把两种速度语义混淆。

先运行：

```powershell
python -m pytest -q tests/motor/test_lk_md2202.py
```

预期：因 `velocity_mode_request()` 尚不存在而失败。

### 2.2 最小实现

修改：

- `spectrometer/motor/lk_md2202.py`

新增 `velocity_mode_request(address, axis, signed_speed_pps)`：

- X/M1 使用 `base + 0x0C = 0x001C`；
- Y/M2 使用 `base + 0x0C = 0x003C`；
- 使用一次 `build_write_multiple()` 写两个寄存器；
- 使用 `int32_to_registers()`，允许 0 作为速度模式停止命令；
- 不在协议层施加 UI 的 `100～14000 pps`范围，协议层只保证 int32 和轴合法性。

### 2.3 验证与提交

```powershell
python -m pytest -q tests/motor/test_lk_md2202.py tests/motor/test_modbus_rtu.py
git diff --check -- spectrometer/motor/lk_md2202.py tests/motor/test_lk_md2202.py
```

预期提交：

```text
feat: add LK-MD2202 velocity mode command
```

## 3. 阶段 2：纯状态规则与控制器失败测试

这一阶段先用不接实机的测试固定时间公式、限位转换和停止未知语义，再改控制器。

### 3.1 扩展领域状态

先修改测试，再最小修改：

- `tests/motor/test_motor_models.py`
- `spectrometer/motor/models.py`

要求：

1. 未知限位使用 `None`，不再把“未读到”伪装成“限位断开”；
2. 已读取状态仍使用 `True/False`；
3. `MotorStatus` 能表达是否存在“停止状态未知”的运动锁定及原因；
4. 增加纯函数或等价的可独立测试逻辑：

```text
stall_release_timeout(5000)  = 0.96 s
stall_release_timeout(10000) = 0.48 s
stall_release_timeout(100)   = 1.00 s
home_timeout(10000)          = 2.00 s
home_timeout(5000)           = 3.38 s
home_timeout(100)            = 5.00 s
```

脱离卡死时间按 `min(1.0, 4800 / abs(speed))`；回零时间按 `clamp(4800 / abs(speed) * 3 + 0.5, 2.0, 5.0)`。

### 3.2 控制器失败测试

修改：

- `tests/motor/test_motor_controller.py`

扩展 `FakeTransport` 以记录速度模式、急停、失败和重连状态。新增测试组：

#### A. 设速双重语义

1. `set_speed(X, -5000)` 向位置速度寄存器写 `5000`；
2. 成功后控制器正常位置速度保存为 `5000`；
3. `0`、绝对值小于100或大于14000被拒绝；
4. 写入应答超时仍沿用现有回读确认，不自动重发。

#### B. 脱离卡死启动和停止

1. `release_stall(X, -5000)` 写入 X 速度模式 `-5000`，不经过上位机方向反转；
2. 调用时不要求此前点击“设速”，直接使用当前输入值；
3. 设备未连接、已有运动、扫描中、速度非法或停止未知锁定时拒绝；
4. 动作开始立即把该轴标记为未校准；
5. 初始限位闭合，看到闭合→断开后立即写速度 0；
6. 初始限位断开，看到断开→闭合后立即写速度 0并返回方向风险原因；
7. 限位不变化时按1秒/4800 pulse理论时间上限停止；
8. 写速度 0 后必须读取运行状态，确认停止后才结束动作；
9. 脱离卡死成功或失败后均保持未校准。

#### C. 机械回零有效性

1. 回零开始时限位闭合，拒绝发送回零命令并提示先“脱离卡死”；
2. 回零开始时限位未知，拒绝发送命令并先要求有效状态；
3. 开始时限位断开才允许发送回零；
4. 运动轮询期间记录本次断开→闭合，即使中间状态仍显示运行中也不能丢失转换；
5. 最终累计位置0、限位闭合、停止，但没有本次转换证据时不校准；
6. 有转换证据但累计位置非0、最终限位未闭合或仍在运行时不校准；
7. 五项条件全部满足时才把软件零点和机械坐标设为0并标记已校准；
8. 双轴回零按 X→Y执行，每一轴开始前分别做限位入口检查；
9. 回零使用2～5秒动态超时，不再使用固定60秒；
10. 超时后发送位置急停并确认停止。

#### D. 停止未知和重连

1. 速度0写入超时后补发位置急停，但不宣称已停止；
2. 急停后仍不能回读停止时设置运动锁定、两轴未校准和明确原因；
3. 锁定时拒绝手动运动、回零、脱离卡死、参数写入和扫描租约；
4. 重连初始化后两轴均停止才清除锁定；
5. 重连后任一轴仍在运行，发送速度0和位置急停，确认停止后解锁；
6. 重连仍无法确认停止时保持锁定；
7. 串口断开时不尝试自动重发原动作。
8. 应用退出时若脱离卡死仍在运行，先尽力发送速度0和两轴位置急停，再关闭串口；测试不得把未收到状态确认描述为可靠停止。
9. “清除故障”不能直接清除停止未知锁定；只有重新读取并确认两轴停止的恢复流程可以解锁。

先运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_models.py tests/motor/test_motor_controller.py
```

预期：新状态、速度模式、回零转换和锁定接口尚不存在而失败。

## 4. 阶段 3：控制器状态机最小实现

修改：

- `spectrometer/motor/controller.py`
- `spectrometer/motor/models.py`

### 4.1 活动动作模型

扩展现有 `_ActiveMotion` 或拆出等价的内部不可变状态，至少记录：

- `kind`：`move`、`home`、`stall_release`、`stopping`；
- 轴和操作ID；
- 截止时间；
- 初始限位状态；
- 本次动作是否观察到需要的限位转换；
- 脱离卡死的带符号速度；
- 停止原因；
- 双轴回零的剩余轴。

所有动作仍由一个控制器状态机串行管理，不在 UI 中复制动作状态。

### 4.2 设速与会话符号

- `set_speed()` 验证 `100 <= abs(value) <= 14000`；
- 向位置速度寄存器写 `abs(value)`；
- `_speeds[axis]` 只保存正的位置速度；
- 新增结果信号，向 UI 提供轴、用户带符号输入、绝对位置速度和结果消息；
- 驱动板重连后以配置回读的正数重置 UI，会话负号不持久化。

### 4.3 脱离卡死

新增 `release_stall(axis, signed_speed_pps)`：

1. 校验连接、互斥、锁定和速度范围；
2. 读取当前轴已缓存状态，记录初始限位；
3. 标记该轴未校准；
4. 计算 `min(1.0, 4800 / abs(speed))`截止时间；
5. 写入带符号速度模式请求，动作写不重试；
6. 每50 ms沿用现有运动轮询请求读取状态；
7. 按设计中的限位转换或截止时间进入停止序列；
8. 写速度0，回读停止；必要时补发位置急停；
9. 确认停止后结束，未确认则锁定。

不能用累计位置增量判断脱离成功，也不能在结束后自动发机械回零。

### 4.4 机械回零

- 回零入口先检查缓存状态有效且限位断开；
- 动态计算2～5秒截止时间；
- 每次运动状态响应先记录限位转换，再判断是否仍在运行；
- 最终只在“本次断开→闭合 + 停止 + 累计位置0 + 限位闭合”同时满足时校准；
- 双轴回零切换到下一轴前重新执行入口检查和动态截止时间计算；
- 失败不自动启动脱离卡死，也不自动重新回零。

### 4.5 停止未知锁定

- 控制器维护单一的安全锁定及原因，并反映到 `MotorStatus`；
- `stop()` 对速度模式先发送速度0，同时保留两轴位置急停兜底；
- 通信失败时锁定，提示切断驱动板电源；
- 初始化完成后先检查两轴运行状态，再决定解锁或进入停止恢复；
- `shutdown()`在关闭transport前对活动脱离卡死执行速度0和位置急停的尽力停止；通信已经丢失时只能记录停止未知，不能提供虚假保证；
- `clear_faults()`不得用一个界面点击绕过停止确认；若用于刷新状态，也必须走与重连相同的两轴停止验证；
- 普通光谱仪采集不依赖该锁定。

### 4.6 空闲状态轮询

增加独立 `QTimer`：

- 连接并完成初始化后启动，间隔500 ms；
- 断开和关闭时停止；
- transport忙或运动状态机正在轮询时跳过当前周期，不并发发送；
- 每轮按 X→Y读取状态；
- 读取失败显示状态未知，但不因单次空闲读失败自动发运动或停止命令；
- 运动状态未知锁定仍按专用恢复流程处理，不能被普通空闲读悄悄清除。

### 4.7 定向验证与提交

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_models.py tests/motor/test_motor_controller.py
python -m pytest -q tests/motor/test_lk_md2202.py tests/motor/test_motor_transport.py
git diff --check -- spectrometer/motor tests/motor
```

预期提交：

```text
feat: add controlled stall release and validated homing
```

## 5. 阶段 4：自动发现和连接生命周期

### 5.1 先写失败测试

修改：

- `tests/motor/test_motor_discovery.py`
- `tests/motor/test_motor_controller.py`

覆盖：

1. `ttyFIQ0`、`ttyFIQ1`以及 system location 末段为 `ttyFIQ*`的记录不进入自动候选；
2. 有USB VID/PID或明确 `ttyUSB*`、`ttyACM*`路径的端口可进入自动候选；
3. Windows USB COM候选继续可用；
4. 手动指定端口不经过自动候选过滤；
5. 自动候选仍必须通过LK-MD2202只读身份响应，USB元数据不能直接确认身份；
6. 无候选、全部身份失败、连接成功和主动断开均发出明确的连接生命周期结果。

### 5.2 实现

修改：

- `spectrometer/motor/discovery.py`
- `spectrometer/motor/controller.py`

规则：

- 自动候选首先排除 `ttyFIQ*`；
- 自动候选限于具有USB元数据或平台明确USB串口命名的记录；
- 保留已有“排除光谱仪占用端口”和首选稳定路径排序；
- 手动连接不受过滤；
- 无候选时也发出失败连接状态，让 UI 能结束“正在识别”；
- 全部探测失败时只读结束，不写设备。

### 5.3 验证与提交

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_discovery.py tests/motor/test_motor_controller.py
git diff --check -- spectrometer/motor/discovery.py spectrometer/motor/controller.py tests/motor
```

预期提交：

```text
fix: filter onboard UART from motor discovery
```

## 6. 阶段 5：PyQt6 界面、状态栏和扫描锁定

### 6.1 先写失败测试

修改：

- `tests/motor/test_motor_panel.py`
- `tests/motor/test_scan_controller.py`
- `tests/test_main_window_acquisition.py`（只增加电机连接状态和未校准扫描相关用例）

覆盖：

1. X/Y各有一个“脱离卡死”按钮并发出轴和当前带符号速度；
2. 速度框范围为 `-14000～14000`，输入0时请求由控制器拒绝；
3. 点击设速仍发出原始带符号值，由控制器写绝对值；
4. 设速成功后输入框保留当前会话负号并显示双重速度提示；
5. 断开、重连或配置初始化后输入框显示驱动板正数位置速度；
6. 限位 `True/False/None`分别显示“驱动器上报：限位闭合”“驱动器上报：限位断开”“限位状态未知”；
7. 动作进行时按钮互斥，急停保持可用；
8. 停止未知锁定时禁用手动运动、回零、脱离卡死和扫描；
9. 锁定解除但坐标未校准时，扫描仍可在用户确认后启动；
10. 识别成功后状态栏为`电机已连接：...`；
11. 识别失败后为`未找到LK-MD2202电机驱动板`；
12. 主动断开后为`电机已断开`，不再保留“正在识别电机串口……”；
13. 独立光谱仪采集控件不因电机停止未知锁定而禁用。

### 6.2 面板实现

修改：

- `spectrometer/ui/motor_panel.py`

具体改动：

- 新增 `stall_release_requested(axis, signed_speed)` 信号；
- 在每轴“机械回零”旁增加“脱离卡死”；
- 速度框改为有符号范围；
- 增加设置轴速度的方法，区分“连接回读时强制正值”和“当前会话保留符号”；
- 限位按三态显示；
- 根据控制器状态统一刷新按钮，不让单个按钮自行维护冲突状态；
- 不改变扫描参数布局和原光谱仪绘图区结构。

### 6.3 主窗口接线与状态栏

修改：

- `spectrometer/ui/main_window.py`

具体改动：

- 把“脱离卡死”信号连接到控制器；
- 处理设速完成提示并保留会话符号；
- 连接成功、失败和断开时明确更新状态栏；
- 保持已有未校准扫描确认，但使用规格中的提示语义；
- 扫描开始前增加控制器安全锁检查；
- 电机锁定不改变现有独立光谱仪采集逻辑。

### 6.4 扫描控制器

如现有入口不能覆盖安全锁，最小修改：

- `spectrometer/motor/scan_controller.py`

在 `start()`预检中拒绝“停止状态未知”，但继续支持 `allow_uncalibrated=True`。不得把未校准和停止未知合并成同一个条件。

### 6.5 验证与提交

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_panel.py tests/motor/test_scan_controller.py tests/test_main_window_acquisition.py
python -m pytest -q tests/motor
git diff --check -- spectrometer/ui spectrometer/motor tests
```

预期提交：

```text
feat: expose safe motor recovery controls
```

## 7. 阶段 6：知识库、协议和验收文档

修改：

- `docs/motor/lk-md2202-modbus-protocol.md`
- `docs/motor/motor-control-knowledge-base.md`
- `docs/motor/rock4bplus-acceptance-checklist.md`
- `docs/motor/lk-md2202-host-validation.md`

记录：

1. 速度控制和位置速度寄存器的区别；
2. “脱离卡死”的单击操作、带符号速度、1秒/4800 pulse理论限制和停止确认；
3. 机械回零必须有本次限位断开→闭合转换；
4. 累计脉冲不能证明步进电机真实移动；
5. 未校准扫描只警告，停止未知才锁定；
6. `ttyFIQ*`排除和状态栏预期；
7. ROCK 4B+ 实机用例和记录字段；
8. 通信中断无法确认停止时必须切断驱动板电源。

验证：

```powershell
rg -n "脱离卡死|0x001C|0x003C|断开.*闭合|ttyFIQ" docs/motor
git diff --check -- docs/motor
```

预期提交：

```text
docs: document LK-MD2202 stall recovery
```

## 8. 阶段 7：完整回归和部署镜像

依次运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_lk_md2202.py
python -m pytest -q tests/motor/test_motor_controller.py
python -m pytest -q tests/motor/test_motor_discovery.py tests/motor/test_motor_panel.py tests/motor/test_scan_controller.py
python -m pytest -q tests/motor
python -m pytest -q
python -m compileall -q main.py spectrometer tools
git diff --check
```

然后生成部署镜像：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
python -m compileall -q deploy/rock4bplus/app
python -m pytest -q tests/test_rock4bplus_deploy.py
```

检查：

- 部署镜像中的源文件与主源码一致；
- 未包含测试、规格、Office文件、用户数据、诊断包或 `tmp/`；
- Git差异只包含本任务文件和构建脚本生成的部署副本；
- 光谱仪完整回归无变化。

部署镜像单独提交：

```text
build: refresh ROCK 4B+ motor recovery deploy
```

## 9. 阶段 8：Git发布与回退点

提交前逐次检查：

```powershell
git status --short
git diff --name-only
git diff --cached --name-only
git log -10 --oneline --decorate
```

只暂存本计划列出的文件。完整测试通过后推送当前分支 `codex/lk-md2202-integration`。不自动合并主分支，不删除历史提交和备份标签。

回退方式：

- 单阶段问题：反向提交对应阶段提交；
- 整体功能回退：回到实施前提交 `ca70b34`或上一可运行代码 `bbd85f5`；
- ROCK 4B+：使用部署前生成的时间戳备份恢复被替换文件。

## 10. 阶段 9：ROCK 4B+ 更新

在板端执行前：

1. 关闭正在运行的光谱仪软件，确认没有进程占用 `/dev/ttyUSB0`；
2. 把更新文件先上传到 `/home/radxa/`下的新目录，不直接覆盖 `/opt`；
3. 对 `/opt/zgcai-spectrometer` 中每个目标文件创建带提交号和时间戳的备份；
4. 将备份目录写入 `/home/radxa/zgcai-last-backup.txt`；
5. 使用 `sudo install -m 0644`逐个安装明确文件；
6. 使用板端 venv执行 `py_compile`；
7. 先运行只读探针，确认身份、配置和状态；
8. 再启动 GUI，不立即运动。

只读探针：

```bash
cd /opt/zgcai-spectrometer
./.venv/bin/python -m spectrometer.motor.probe \
  --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --address 1 \
  --baud 9600 \
  --timeout 10 \
  --json
```

若只读探针失败，不进入运动测试，先按备份路径恢复。

## 11. 阶段 10：实机验收顺序

每一步保存速度、方向、限位、运行状态、累计脉冲和实际机械现象；前一步不通过，不扩大到下一步。

1. 验证自动候选不包含 `ttyFIQ0`，连接成功后状态栏结束识别提示；
2. 保证机构处于中间安全位置或卸除负载；
3. 先用用户确认的平稳速度10000 pps验证 X 正、负“脱离卡死”方向和速度0停止；
4. 使用5000 pps复核约0.96秒理论上限，不把低速视为必然更安全；
5. 对Y轴重复方向和停止测试；
6. 限位初始闭合时执行脱离，验证释放后提前停止；
7. 限位初始断开时选择朝零点方向，验证闭合后立即停止并提示方向风险；
8. 验证急停、1秒绝对超时和4800 pulse理论时间上限；
9. 限位保持断开执行机械回零，验证2～5秒内超时急停且不校准；
10. 执行一次真实断开→闭合回零，确认停止、累计位置0、限位闭合和已校准；
11. 上电时保持限位闭合，验证机械回零被拒绝，不再直接显示有效校准；
12. 在安全条件下断开RS-485，验证停止未知锁定、切断电源提示和重连停止确认；
13. 验证负号在当前会话保留，断开重连后恢复驱动板正速度；
14. 未校准启动扫描，验证警告后可以继续；
15. 停止未知锁定时验证扫描被拒绝，但独立光谱仪采集仍可使用；
16. 完成正常扫描与光谱仪联动采集，确认原有保存和绘图无回归。

不得仅根据累计脉冲变化宣告普通运动真实成功，必须同时记录肉眼观察或独立机械证据。

## 12. 停止实施条件

出现以下任一情况时停止扩大改动并保留证据：

- 实测速度模式寄存器或正负方向与说明书/厂商工具不一致；
- 写入速度0后驱动板仍持续运行；
- 位置急停被误认为速度模式的可靠停止而掩盖失败；
- 限位转换在运动状态查询中无法稳定观察；
- 回零超时后无法确认停止；
- 完整回归出现光谱仪采集、保存或扫描路径变化；
- 部署镜像不能由主源码一致生成；
- 板端目标文件无法备份或恢复路径不明确；
- 工作区出现与用户 Office、数据或诊断文件重叠的修改。
