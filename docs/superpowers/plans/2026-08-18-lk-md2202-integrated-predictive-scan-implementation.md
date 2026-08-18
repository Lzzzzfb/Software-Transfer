# LK-MD2202 与光谱仪融合的预测扫描实施计划

- 日期：2026-08-18
- 依据：`docs/superpowers/specs/2026-08-18-lk-md2202-integrated-predictive-scan-design.md`
- 设计提交：`77f193a`
- 状态：待实施
- 当前分支：`codex/lk-md2202-integration`
- 当前源码自动化基线：PyQt6 6.11环境`440 passed`
- 当前板端代码基线：`c216da2`的`controller.py`，SHA-256为
  `3617b6c6432e7c8e678befce100aeafbf9ddcef78082c6713c72bcc6e545615f`
- 说明：当前会话未提供`writing-plans`技能，本文件按项目既有格式提供等价的测试优先计划

## 1. 工作区保护与范围

实施前记录：

```powershell
git status --short
git diff --name-only
git log -10 --oneline
```

只允许修改或新增：

- `spectrometer/motor/scan_timing.py`（新增）
- `spectrometer/motor/lk_md2202.py`
- `spectrometer/motor/controller.py`
- `spectrometer/motor/scan_controller.py`
- 对应`tests/motor/`测试
- 本轮验证与验收文档
- 构建工具生成的`deploy/rock4bplus/app`对应运行文件

共享Qt模型和普通光谱仪采集、存储、处理、绘图实现不在修改范围内；新增扫描内部结果
通过现有`object`载荷信号或控制器私有状态传递。

不得暂存、覆盖、移动或清理用户已有的Office、CSV、PDF、诊断包和`tmp/`资料；所有
`git add`使用明确文件路径。旧项目`D:\codex\电机控制软件`只读参考，不修改、不复制
其界面、线程、执行器或串口框架。

## 2. 阶段一：纯计算末端估算器

涉及文件：

- 新增`spectrometer/motor/scan_timing.py`
- 新增`tests/motor/test_scan_timing.py`

先写失败测试：

1. 正向和反向目标的剩余脉冲计算；
2. 160 pulse近终点、320 pulse终点超时范围；
3. 两个可信样本计算实测速度，并限制不高于配置速度；
4. 只有一个可信运动样本时使用配置速度；
5. 没有可信运动样本时禁止预测交接；
6. 80 ms安全余量和200 ms最短段期限；
7. 可信样本推算的截止时间已到时可处理跨过末端窗口；
8. 反向位置变化、非递增时间、零/负速度和越过目标拒绝；
9. 截止时间不早于当前计算时刻。

最小实现：

- 不可变的交接配置；
- 带单调时间的位置样本；
- 只保存最近两个样本和累计样本数；
- 明确的`has_trusted_motion_sample`、`is_near_target`、
  `timeout_is_end_phase`和`handoff_deadline`结果。

运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_scan_timing.py
```

验收：测试使用虚拟时间，不真实等待；模块不导入Qt、串口或光谱仪代码。

## 3. 阶段二：扫描绝对位置协议

涉及文件：

- 修改`spectrometer/motor/lk_md2202.py`
- 修改`tests/motor/test_lk_md2202.py`

先写失败测试：

1. X/M1绝对目标写入轴基址`+0x12`；
2. Y/M2绝对目标写入轴基址`+0x12`；
3. 32位无符号高低寄存器和边界值正确；
4. 负绝对目标和超过32位范围被拒绝；
5. 现有相对运动、速度、停止和回零请求字节不变。

实现`absolute_move_request(address, axis, target_pulses)`，只复用现有Modbus构造和
32位编码，不在协议层增加重试或状态判断。

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_lk_md2202.py tests/motor/test_modbus_rtu.py
```

验收：协议层只负责确定性帧构造，不能知道扫描、光谱仪或动作生命周期。

## 4. 阶段三：扫描轮次准备与坐标快照

涉及文件：

- 修改`spectrometer/motor/controller.py`
- 修改`spectrometer/motor/scan_controller.py`
- 修改`tests/motor/test_motor_controller.py`
- 修改`tests/motor/test_scan_controller.py`

先扩展测试夹具，使其记录请求帧、标签、超时、动作属性和信号顺序。新增失败测试：

1. 扫描启动先读取X/Y最新动态状态，再冻结原始起点、速度和方向；
2. 电机轮次准备成功后才允许启动光谱仪；
3. 无光谱仪时准备成功后直接进入扫描；
4. 准备失败不启动光谱仪、不发送运动；
5. 已校准目标仍受0～4800 pulse保护；
6. 未校准风险确认后使用驱动器`raw_start`，不会把逻辑0写成驱动器绝对0；
7. 方向反转正确映射累计逻辑位移；
8. 分步绝对目标按累计比例取整，最后一步严格等于整段目标；
9. 每轮返回完成后，下一轮重新取得并冻结起点快照。

实现边界：

- 为扫描轮次增加异步准备接口和完成/失败信号；
- `ScanController`在预检与启动采集之间等待准备完成；
- 准备结果内部保存，不暴露可由界面修改的预测参数；
- 扫描开始后继续通过现有`set_scan_active(True)`锁定手动操作和参数写入；
- 结束、停止和任何失败路径均释放扫描会话状态。

运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_motor_controller.py tests/motor/test_scan_controller.py
```

验收：未开始真实运动前已经得到可重复的绝对目标基准，光谱仪不会在电机预检失败时启动。

## 5. 阶段四：扫描指令接收确认

涉及文件：

- 修改`spectrometer/motor/controller.py`
- 修改`tests/motor/test_motor_controller.py`

建立脚本化响应，先写失败测试：

1. 第一次绝对写入收到合法回显，只发送一次；
2. 扫描写入使用60 ms动作响应期限、传输层0次自动重试；
3. 写回显超时后先读取目标轴，不能立即重发；
4. 目标轴按正确方向运动、位置朝目标变化或已经到位时不重发；
5. 目标轴停止在计划起点时，重发同一绝对目标；
6. 多次未执行后在1秒内收到回显，进入正常跟踪；
7. 目标轴状态本身超时时继续确认，但不能盲目重发；
8. 反向运动、越过目标、限位、异常中间停止和未知运行状态立即失败；
9. 明确Modbus异常、CRC/功能码/回显错误不作为忙碌重试；
10. 1秒内无法确认接收时进入现有停止与安全路径；
11. 用户停止可中断发送和确认循环，并对两轴发送紧急停止；
12. 普通手动相对运动继续使用现有严格语义，不受扫描短超时影响。

实现扫描专用接收阶段，记录第一次尝试、实际确认执行的发送时刻、尝试次数和第一次
可信目标轴状态。只有状态明确证明该绝对指令未执行时才允许重发。

验收：任何结果未知的相对运动都不会被重发；同一绝对目标也不会在目标轴可能已经
运动时盲目重发。

## 6. 阶段五：预测跟踪与可交接结果

涉及文件：

- 修改`spectrometer/motor/controller.py`
- 修改`tests/motor/test_motor_controller.py`
- 使用`spectrometer/motor/scan_timing.py`

先写失败测试：

1. 收到运动中且位置朝目标变化时向估算器增加可信样本；
2. 普通成功状态查询后约40 ms再查询当前轴；
3. 扫描状态读取使用150 ms、0次事务内重试；
4. 进入160 pulse范围后不再等待停止状态，按预测截止时间发出可交接结果；
5. 进入320 pulse范围后查询超时走相同预测路径；
6. 可信样本推算期限已到时处理跨过末端窗口；
7. 没有可信样本时保持严格轮询，不纯定时完成；
8. 驱动器提前返回停止且到位时立即严格完成，不增加80 ms；
9. 停止但位置不符、限位或反向运动仍失败；
10. 预测等待可被用户停止立即中断；
11. 每段只发出一次终态信号，不重复推进扫描；
12. 手动运动、回零、脱离卡死和返回段不产生预测结果。

预测段完成原因必须区分`handoff_ready`、`completed_strict`和
`fallback_strict_no_sample`，供扫描总控和诊断使用。预测可交接后仍保留本轮计划目标，
但不继续对旧轴发高频查询；下一段由接收确认状态机判断驱动器何时真正接受。

验收：脚本化“已到机械终点但停止状态暂不响应”场景不会等待状态恢复；没有可信运动
证据的场景不会为了流畅性跳过严格确认。

## 7. 阶段六：光谱仪总控融合

涉及文件：

- 修改`spectrometer/motor/scan_controller.py`
- 修改`tests/motor/test_scan_controller.py`

先写失败测试并断言完整信号顺序：

1. 有光谱仪每轮只启动一次连续采集；
2. `task_started`之前不发送第一段电机运动；
3. 普通预测段完成后执行现有步时，再发送下一段；
4. 步时为0时立即推进，但仍经过下一段接收确认；
5. 预测、换轴、子步和步时期间不停止、重置或重新启动光谱仪；
6. 扫描区域最后一段要求严格停止，不使用`handoff_ready`结束本轮；
7. 最后一段后严格读取X/Y，均在目标±1 pulse才调用`stop_global()`；
8. 双轴校验期间光谱仪保持采集；
9. 保存完成前不返回；
10. 返回使用绝对轮次起点并严格确认，返回完成前不开始下一轮；
11. 无光谱仪模式执行同一路径，只跳过采集、保存和清单；
12. 电机故障停止光谱仪，光谱仪故障停止电机；
13. 用户停止取消预测/步时/确认并同时停止电机与光谱仪；
14. 返回失败不开始下一轮，不自动回零。

保留现有光谱仪`AcquisitionController`接口和存储生命周期，不修改采集、保存或绘图
源码。必要的新扫描状态只属于`ScanController`，不能泄漏为普通采集状态。

运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_scan_controller.py tests/motor/test_motor_controller.py
```

验收：运动连续性优化只发生在扫描内部，光谱仪每轮采集范围完整覆盖第一段开始至轮末
严格位置校验成功。

## 8. 阶段七：诊断、界面与故障回归

涉及文件：

- 修改`spectrometer/motor/controller.py`
- 修改`spectrometer/motor/scan_controller.py`
- 更新`docs/motor/lk-md2202-host-validation.md`
- 更新`docs/motor/rock4bplus-acceptance-checklist.md`

测试要求：

1. 每段只有一条结构化摘要，不逐条记录位置样本；
2. 摘要包含扫描ID、轮次、段号、轴、逻辑/驱动目标、速度、样本数、有效速度、
   接收尝试、预测原因、查询失败、完成方式和总耗时；
3. 严格完成、预测交接和无样本降级可区分；
4. 诊断随现有包导出，不创建独立扫描日志；
5. 正常扫描不弹出预测提示，不增加设置项；
6. 接收超时、反向、越界、最终位置不符显示明确轴和目标；
7. 扫描结束后主界面按钮、焦点、自动发现和状态栏行为无回归；
8. 普通光谱仪单次/连续采集在电机安全锁期间仍保持原有可用性。

验收文档新增10次连续扫描、无重叠观察、最终位置、联动数据完整性和诊断字段检查。

## 9. 阶段八：自动回归与提交边界

按测试优先顺序提交：

1. 纯估算器与协议请求；
2. 扫描接收确认与预测状态机；
3. 光谱仪总控融合；
4. 验证文档；
5. 部署镜像与更新指南。

每次提交只暂存计划列出的文件。运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_scan_timing.py
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_lk_md2202.py tests/motor/test_motor_controller.py tests/motor/test_scan_controller.py tests/motor/test_motor_transport.py
.\.venv\Scripts\python.exe -m pytest -q tests/motor
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q main.py spectrometer tools
git diff --check
```

完整回归不得通过修改或清理用户资料解决。与本任务无关的既有问题只记录，不扩大范围。

## 10. 阶段九：ROCK 4B+ 部署镜像

运行：

```powershell
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py --check
.\.venv\Scripts\python.exe -m compileall -q deploy/rock4bplus/app
.\.venv\Scripts\python.exe -m pytest -q tests/test_rock4bplus_deploy.py
```

编译检查会生成`__pycache__`，完成后必须再次运行构建工具生成干净部署镜像，并再次
执行`--check`和部署结构测试。

检查：

1. 主源码和部署镜像对应文件逐字节一致；
2. 镜像不包含测试、文档、开发环境、Office文件、诊断包、数据或缓存；
3. 增量更新只安装实际变化的运行文件；
4. 为每个文件提供SHA-256、`py_compile`、备份和逐文件回退命令。

## 11. 阶段十：板端更新与实机验收

板端目标仍为`/opt/zgcai-spectrometer`。实施完成后生成新的
`docs/motor/rock4bplus-update-<commit>.md`，步骤必须包括：

1. 从桌面正常关闭软件并确认进程停止；
2. Windows PowerShell上传到新的`/home/radxa/zgcai-update-<commit>`目录；
3. 创建`/home/radxa/zgcai-backups/pre-<commit>-<timestamp>`，不覆盖现有备份；
4. 只备份和替换本轮变化文件；
5. 安装后执行SHA-256、`py_compile`、预测常量自检和LK-MD2202只读探针；
6. 先断开光谱仪，安全小行程运行一次；
7. 相同参数连续运行至少10次，检查无重复距离、反向、越界和明显两轴重叠；
8. 验证分步、非零步时、用户停止、限位、断线和返回；
9. 连接光谱仪执行单轮、多轮和中途停止，核对采集、保存、返回及诊断包；
10. 异常时按本轮完整文件集合回退，不删除数据、设置、虚拟环境或原光谱仪软件。

验收指标：

- 读取失败不再直接增加完整状态超时阶梯；
- 预测交接的软件调度延迟稳定，下一段接收尝试有明确记录；
- 不观察到明显两轴重叠或同一段重复启动；
- 每轮扫描最终位置和返回起点均严格正确；
- 光谱仪从第一段运动前开始，轮末双轴校验后才停止，导出数据完整；
- 急停和持续通信故障始终优先于连续性。

只有自动回归、部署一致性和上述实机验收全部通过，才将扫描不流畅问题标记完成。
