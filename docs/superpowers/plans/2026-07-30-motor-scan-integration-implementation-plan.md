# ROCK 4B+ 光谱仪软件电机控制与扫描联动实施计划

日期：2026-07-30

设计依据：`docs/superpowers/specs/2026-07-30-motor-scan-integration-design.md`

## 1. 实施原则

1. 唯一主工程为 `D:\codex\光谱仪 - Linux转移版`。
2. `D:\codex\Linux转移电机控制\SM2009 - 完整版` 只作为只读来源。
3. 不暂存、覆盖或删除用户现有 Excel、Word、PDF、诊断包和采集文件。
4. 每个阶段先增加失败测试，再做最小实现，再运行相关测试和全量回归。
5. 光谱仪现有控制、完整帧持久化和保存逻辑不得建立旁路。
6. `deploy/rock4bplus/app` 只由构建脚本同步，不手工维护第二套 Python 源码。
7. 下位机优先只修改复制后工程的 `main.c` 和 `MDK-ARM/hardware`。
8. 本机没有 ARM GCC、Keil、STM32CubeIDE 或 CubeProgrammer；固件编译、烧录、
   引脚电平、方向和机械行程必须在实机阶段完成，不以源码检查冒充编译通过。
9. 每阶段使用独立 Git 提交，保持可以回退到：
   - 纯光谱仪基线；
   - 原始电机固件基线；
   - 固件优化完成；
   - 上位机基础控制完成；
   - 扫描联动完成。

## 2. 阶段 0：保护工作区和固件基线

### 2.1 更新忽略规则

修改：

- `.gitignore`

新增忽略：

- `firmware/TMC2209/MDK-ARM/Objects/`
- `firmware/TMC2209/MDK-ARM/Listings/`
- 固件 `.axf`、`.map`、`.lst`、`.d`、`.o`、`.crf` 等中间产物；
- 根目录运行期 CSV/XLSX 和诊断导出不扩大到误伤正式测试夹具。

验证：

```powershell
git check-ignore -v firmware/TMC2209/MDK-ARM/Objects/example.o
git status --short
```

### 2.2 完整复制 TMC2209

来源：

```text
D:\codex\Linux转移电机控制\SM2009 - 完整版\TMC2209
```

目标：

```text
D:\codex\光谱仪 - Linux转移版\firmware\TMC2209
```

复制前后：

1. 验证目标不存在；
2. 完整复制目录；
3. 比较所有非生成文件的相对路径、大小和 SHA-256；
4. 确认来源目录没有改变；
5. 只暂存 `.gitignore` 和 `firmware/TMC2209`；
6. 提交原始固件基线，不在同一提交中修改 C 代码。

预期提交：

```text
chore: import motor firmware baseline
```

### 2.3 建立电机知识库

新增：

- `docs/motor/motor-control-knowledge-base.md`
- `docs/motor/motor-protocol.md`
- `docs/motor/rock4bplus-acceptance-checklist.md`

内容包括：

- 电机规格、丝杆、640 pulse/mm 和 15 mm 行程；
- STM32、TMC2209 地址、STEP/DIR、使能和 UART；
- PB3/PB4/PB5 零点输入和 PB6 低电平参考；
- 旧协议、问题、修复后的协议；
- 扫描路径公式；
- 机械方向和工具链待实机项；
- 烧录、回退和验收步骤。

预期提交：

```text
docs: add motor control knowledge base
```

## 3. 阶段 1：下位机限位、运动和协议

### 3.1 先写固件契约测试

新增：

- `tests/test_motor_firmware_contract.py`

测试读取复制后的固件源并验证：

1. 可编译工程关键文件完整存在；
2. 脉冲常量为 640 pulse/mm；
3. 最大行程为 9600 pulse/15 mm，不再是 160000；
4. 速度范围为 100～14000 pulse/s；
5. PB3/PB4/PB5 是上拉、下降沿零点输入；
6. PB6 是固定低电平输出；
7. PA3 DIAG 不再作为活动限位/堵转来源；
8. 存在 `ID?`、`LIMIT?`、`POS?`、`POSSET`、`HOME`、`STOP`、
   `FAULT?`、`CLEARFAULT`；
9. `RESET` 不再宣称机械回零；
10. 响应缓冲区操作保证终止和长度安全；
11. `main.c` 主循环持续调用运动/限位/回零更新；
12. `SCAN_*` 仍不在固件执行。

首次运行应失败：

```powershell
python -m pytest -q tests/test_motor_firmware_contract.py
```

### 3.2 修改 PUL

修改：

- `firmware/TMC2209/MDK-ARM/hardware/PUL.h`
- `firmware/TMC2209/MDK-ARM/hardware/PUL.c`

实现：

- 轴配置和方向常量；
- 640 pulse/mm、15 mm、9600 pulse；
- 100～14000 pulse/s；
- 累计目标坐标检查；
- 坐标可信状态；
- 明确的完成、停止、限位、超时和故障原因；
- `Motor_Stop` 同步清理运动状态；
- PB3～PB5 输入、PB6 低输出初始化；
- 原始限位边沿立即停止；
- 主循环消抖和故障锁存；
- 限位闭合时允许离开、禁止继续朝零点；
- 单轴和全部回零的非阻塞状态机；
- 快速接近、退出0.5 mm、慢速二次接近；
- 回零距离和时间上限；
- 限位状态和回零状态查询。

所有中断处理只执行有界操作，不调用 USB 发送或阻塞延时。

### 3.3 修改 main 和 USB 命令

修改：

- `firmware/TMC2209/Core/Src/main.c`
- `firmware/TMC2209/MDK-ARM/hardware/USB_Command.h`
- `firmware/TMC2209/MDK-ARM/hardware/USB_Command.c`

实现：

- 主循环调用运动和回零更新；
- `ID?` 返回短身份、协议版本、轴和能力；
- 结构化限位、位置、故障和运动状态；
- `POSSET` 由上位机恢复/确认坐标；
- `HOME=X|Y|Z|ALL`；
- `STOP`；
- `CLEARFAULT`；
- 保留旧移动和速度命令；
- 修复 SPEED ACK、速度溢出、状态缓冲区、CDC忙重试/队列；
- 将 CDC 输入处理改为跨包文本行缓冲；
- 旧 `RESET` 仅作为兼容停止别名并返回明确说明；
- 拒绝所有 `SCAN_*` 固件扫描命令。

### 3.4 固件验证

运行：

```powershell
python -m pytest -q tests/test_motor_firmware_contract.py
git diff --check -- firmware tests/test_motor_firmware_contract.py
```

人工源码审查：

- ISR 中无 `HAL_Delay`、`snprintf`、CDC发送或无限循环；
- 任一错误路径会停止 PWM；
- PB6 没有被写高；
- PB3/PB4/PB5 没有被配置为输出；
- SWD 引脚不变。

若之后获得工具链，执行 Keil/CubeIDE 全量重新构建并保存构建日志、HEX 和 SHA-256。

预期提交：

```text
feat: add motor limit homing and protocol safety
```

## 4. 阶段 2：上位机领域模型和协议

### 4.1 先写模型和路径失败测试

新增：

- `tests/motor/test_motor_models.py`
- `tests/motor/test_motor_protocol.py`
- `tests/motor/test_scan_plan.py`

测试：

1. 轴、速度、距离、坐标、可信状态和限位模型；
2. 100～14000 速度边界；
3. 0～15 mm 目标边界；
4. 640 pulse/mm 舍入；
5. 协议命令编码和结构化响应解析；
6. 分包、粘包、CR/LF 和未知字段；
7. `x=10,y=1,n=10,m=2` 每轮为11个X行程和10个Y推进；
8. 奇偶 `n` 的最终X位置；
9. X/Y子步数量、距离、方向和等待次数；
10. 任意起点的绝对路径校验；
11. 复位事件不属于采集路径；
12. 非法参数和小于一个脉冲的距离被拒绝。

### 4.2 实现纯模型和协议

新增：

- `spectrometer/motor/__init__.py`
- `spectrometer/motor/models.py`
- `spectrometer/motor/protocol.py`
- `spectrometer/motor/scan.py`（先实现纯路径规划部分）

要求：

- 不导入 Qt 控件；
- 不进行文件、串口或设备 I/O；
- 使用冻结 dataclass/Enum；
- 所有方向极性集中配置；
- 路径规划输出确定的不可变事件序列。

运行：

```powershell
python -m pytest -q tests/motor/test_motor_models.py tests/motor/test_motor_protocol.py tests/motor/test_scan_plan.py
```

预期提交：

```text
feat: add motor domain protocol and scan planner
```

## 5. 阶段 3：发现、串口、坐标和基础控制

### 5.1 写失败测试

新增：

- `tests/motor/test_motor_discovery.py`
- `tests/motor/test_motor_transport.py`
- `tests/motor/test_motor_state_store.py`
- `tests/motor/test_motor_controller.py`

测试：

1. VID:PID `0483:5740` 候选优先；
2. 只有 `ID?` 握手成功才识别为电机控制器；
3. 排除光谱仪占用端口；
4. MCU USB 序列号稳定绑定；
5. 文本行缓冲和严格单命令事务；
6. 运动命令超时不自动重发；
7. 断开和热拔插停止队列；
8. 正常坐标原子保存和同设备恢复；
9. 运动中断电、超时和设备不一致时坐标失效；
10. 正反移动、清零、软件回零和可选机械回零；
11. 限位触发、位置异常和故障清除；
12. 关闭时有限等待并安全释放线程。

### 5.2 实现模块

新增：

- `spectrometer/motor/discovery.py`
- `spectrometer/motor/transport.py`
- `spectrometer/motor/controller.py`
- `spectrometer/motor/state_store.py`

实现：

- `QSerialPortInfo` 枚举和握手；
- `QSerialPort` 工作对象和 `QThread` 生命周期；
- 串行命令队列、ACK、完成、超时和无重发运动事务；
- 状态文件使用临时文件加原子替换；
- 运动前写“进行中”，完成后写新坐标；
- 连接/断开、速度、相对移动、停止、清零、位置确认、软件回零、机械回零；
- 诊断信号和用户可读错误；
- 上位机退出时停止电机并关闭端口。

不得修改现有光谱仪 `communication/serial_port.py` 的协议行为。

运行：

```powershell
python -m pytest -q tests/motor/test_motor_discovery.py tests/motor/test_motor_transport.py tests/motor/test_motor_state_store.py tests/motor/test_motor_controller.py
```

预期提交：

```text
feat: add motor serial discovery and controller
```

## 6. 阶段 4：扫描与光谱采集总控

### 6.1 写失败测试

新增：

- `tests/motor/test_scan_controller.py`

修改：

- `tests/test_acquisition_control_models.py`
- `tests/test_acquisition_controller.py`

测试：

1. 新增 `AcquisitionOwner.SCAN` 不改变 GLOBAL/LOCAL/CALIBRATION；
2. 扫描入口复用现有总控设备选择、同步方式、自动保存、格式和批次；
3. 每轮第一段X之前只启动一次连续采集；
4. 等待 `task_started` 后才启动电机；
5. 子步和步时期间不停止采集；
6. 最后X完成后停止并等待 `task_finished`；
7. 采集完成后才复位电机；
8. 复位期间不采集；
9. 复位成功后才开始下一轮；
10. 完成 `m` 轮后进入 COMPLETED；
11. 用户停止、限位、断开、电机超时或采集失败时双向停止；
12. 中止不自动复位、不开始下一轮；
13. 当前轮次写入完成/未完成清单；
14. 普通采集、手动保存、背景和参考测试保持不变。

### 6.2 扩展采集控制器

修改：

- `spectrometer/domain/enums.py`
- `spectrometer/domain/models.py`（仅在需要扫描上下文时）
- `spectrometer/acquisition/controller.py`

实现：

- `AcquisitionOwner.SCAN`；
- 增量式 `start_scan_global()`，内部复用现有配置、启动、停止、尾帧和封存；
- 不改变 `start_global()` 默认语义；
- 扫描任务不覆盖成普通“待手动保存”候选；
- 通过已有 `task_started`/`task_finished` 暴露真实生命周期；
- 扫描输出上下文和轮次元数据采用附加字段，不改变旧文件内容。

### 6.3 实现扫描状态机

扩展：

- `spectrometer/motor/scan.py`

实现事件驱动状态：

```text
IDLE -> PRECHECK -> STARTING_ACQUISITION
-> SCANNING_X/SCANNING_Y/DWELLING
-> STOPPING_ACQUISITION -> RETURNING
-> 下一轮或 COMPLETED
```

停止路径：

```text
任意活动状态 -> STOPPING -> FAULTED/IDLE
```

使用 Qt 计时器完成步时，不使用阻塞 `sleep`。状态机冻结启动时参数，运行中修改
界面值不影响当前任务。

### 6.4 扫描清单

新增：

- `spectrometer/motor/manifest.py`（若 `scan.py` 内职责过重）

每轮原子写入：

- 扫描 ID、轮次、参数和起点；
- 电机/光谱仪身份；
- 开始/结束/停止原因；
- 实际输出文件；
- 完成或未完成。

不得复制或二次处理光谱数据，不迁移旧上位机数据汇总。

运行：

```powershell
python -m pytest -q tests/motor/test_scan_controller.py tests/test_acquisition_controller.py tests/test_acquisition_control_models.py
```

预期提交：

```text
feat: coordinate motor scan with spectrum acquisition
```

## 7. 阶段 5：PyQt6 电机面板

### 7.1 写失败界面测试

新增：

- `tests/motor/test_motor_panel.py`

修改：

- `tests/test_ui_smoke.py`
- `tests/test_main_window_acquisition.py`

测试：

1. 实时光谱页是垂直分割，绘图区在上、电机面板在下；
2. 历史和诊断页位置不变；
3. 软件标题不变；
4. 面板具有发现、连接、断开、速度、移动、坐标、限位、回零和扫描控件；
5. 默认参数 `x_steps=1`、`y_steps=1`、`dwell=0`；
6. 扫描前显示完整路径预检结果；
7. 扫描时参数和普通采集入口按状态禁用；
8. 三个零点开关使用低电平触发语义展示；
9. 回零按钮存在但扫描不要求先回零；
10. 不存在激光器、数据处理、Tkinter 或 Matplotlib 控件。

### 7.2 实现面板

新增：

- `spectrometer/ui/motor_panel.py`

修改：

- `spectrometer/ui/main_window.py`
- `spectrometer/ui/styles.qss`

实现：

- 保持现有顶栏、侧栏、历史、诊断和状态栏；
- 实时光谱页内部使用垂直 `QSplitter`；
- 电机面板默认占用较小下方区域并允许调整；
- 控件采用现有输入组件、字体、颜色和错误展示方式；
- `MainWindow` 只负责组装控制器、状态机和信号；
- 关闭窗口时先停止扫描和电机，再走现有光谱仪关闭流程。

运行：

```powershell
python -m pytest -q tests/motor/test_motor_panel.py tests/test_ui_smoke.py tests/test_main_window_acquisition.py tests/test_plot_interaction.py
```

预期提交：

```text
feat: embed motor controls in spectrum workspace
```

## 8. 阶段 6：ROCK 4B+ 部署与诊断

### 8.1 写失败部署测试

修改：

- `tests/test_rock4bplus_deploy.py`

新增断言：

- 部署包含 `spectrometer/motor`；
- udev 同时包含光谱仪 `1a86:fe0c` 和电机 `0483:5740`；
- 两种设备均为 `dialout`、`0660`、`uaccess`；
- 不使用 `0666`；
- 不部署固件、知识库、测试或用户数据；
- 安装和运行脚本不增加 Tkinter/Matplotlib/PySerial 依赖。

### 8.2 实现部署

修改：

- `deploy/rock4bplus/99-zgcai-spectrometer.rules`
- `tools/build_rock4bplus_deploy.py`（仅在同步逻辑需要时）
- `deploy/rock4bplus/install.sh`
- `deploy/rock4bplus/run.sh`
- 诊断面板/记录器的电机状态接入点。

运行同步：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
python -m pytest -q tests/test_rock4bplus_deploy.py
```

预期提交：

```text
chore: deploy motor integration to rock4bplus
```

## 9. 阶段 7：完整验证

### 9.1 自动验证

```powershell
python -m compileall -q main.py spectrometer tests tools
python -m pytest -q
python tools/build_rock4bplus_deploy.py --check
git diff --check
```

基线为 `281 passed, 1 skipped`。最终必须在原测试全部通过的基础上增加电机测试，
不能通过删除、跳过或放宽原断言获得绿色结果。

### 9.2 静态范围检查

```powershell
rg -n "tkinter|matplotlib|laser|激光|data_processing" spectrometer deploy/rock4bplus/app/spectrometer
rg -n "800|160000" spectrometer/motor firmware/TMC2209/MDK-ARM/hardware
```

所有命中必须是明确允许的说明或测试反例，目标运行代码不得依赖这些旧内容。

### 9.3 工作区和来源保护

确认：

- 原 `SM2009 - 完整版` 哈希未改变；
- 用户未提交文件仍未暂存；
- 每个提交只包含计划内文件；
- `main` 和回退标签仍指向纯光谱仪提交。

## 10. 阶段 8：固件烧录和 ROCK 4B+ 实机门禁

### 10.1 固件

在具备 Keil/CubeIDE 的环境：

1. 全量重新构建；
2. 保存无错误/警告构建日志；
3. 记录 HEX 文件 SHA-256；
4. 保留原始 HEX；
5. 烧录后先断开机械负载验证 PB6=0V、PB3～PB5上拉；
6. 手动短接各开关验证状态，不先运行电机；
7. 逐轴低速确认回零方向；
8. 验证开关闭合立即停止和允许离开；
9. 验证0.5 mm退出和二次接近重复性。

### 10.2 ROCK 4B+

1. 安装部署包和 udev 规则；
2. 确认电机控制器自动识别；
3. 验证连接、断开和热拔插；
4. 逐轴验证1/5/15 mm与速度；
5. 验证坐标保存、软件清零、软件回零和机械回零；
6. 执行 `x=10,y=1,n=10,m=2`；
7. 验证 X/Y 步数和步时；
8. 验证每轮采集一次、复位不采集；
9. 扫描中触发限位、断开USB和停止；
10. 核对每轮数据、恢复 spool 和扫描清单；
11. 长时间扫描观察 CPU、内存、帧完整性和界面响应。

任何实机门禁失败都不得标记迁移完成。修复后从相关阶段重新运行自动回归。

## 11. 最终提交和推送

每个阶段完成后：

```powershell
git status --short
git diff --check
git diff --cached --name-only
git commit
git push origin codex/motor-scan-integration
```

最终交付包括：

- GitHub 集成分支；
- 纯光谱仪回退标签；
- 原始与优化固件提交；
- 知识库、设计和实施计划；
- 自动测试结果；
- 固件编译/HEX/烧录说明；
- ROCK 4B+ 安装、回退和实机验收清单；
- 尚未完成的实机项和风险边界。
