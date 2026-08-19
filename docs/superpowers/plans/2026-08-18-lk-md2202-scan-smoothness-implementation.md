# LK-MD2202 扫描流畅度优化实施计划

- 日期：2026-08-18
- 依据：`docs/superpowers/specs/2026-08-18-lk-md2202-scan-smoothness-design.md`
- 设计提交：`2e9e6cc`
- 状态：代码与部署镜像已完成，待ROCK 4B+实机验收
- 说明：当前会话没有 `writing-plans` 技能，本文件按项目既有格式提供等价的测试优先计划

## 1. 工作区保护与基线

只修改或生成以下范围：

- `spectrometer/motor/scan.py`
- `spectrometer/motor/transport.py`
- `spectrometer/motor/scan_controller.py`
- `spectrometer/diagnostics/bundle_exporter.py`
- 对应 `tests/motor/` 与 `tests/test_diagnostic_bundle.py`
- 本轮计划、更新说明和构建工具生成的部署镜像对应文件

不得暂存、覆盖、移动或清理现有 Office、CSV、PDF、诊断包、`tmp/`和用户修改的
`docs/motor/rock4bplus-update-1df16e3.md`。所有暂存使用明确路径。

实施前运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_scan_plan.py
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_motor_transport.py
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_scan_controller.py tests/test_diagnostic_bundle.py
```

## 2. 零步时子步合并

先在 `tests/motor/test_scan_plan.py`增加失败测试：

1. `X/Y步数 > 1, 步时=0`时，每条完整轴行程只生成一个执行命令；
2. 用户参数中的步数不变，合并命令记录原逻辑子步数量；
3. 合并前后的整数总脉冲、方向、蛇形终点和返回完全一致；
4. 不跨轴、方向或完整行程边界合并；
5. `步时=0.1`及其他正值时保留全部子步和每步等待；
6. 非整除脉冲在非零步时下继续精确分配余数；
7. 最后一段步时仍为0，最终严格校验语义不变。

最小实现：

- 为 `ScanMove`增加只读的逻辑子步计数，默认1；
- `_substeps()`在步时严格为0时生成一个总脉冲命令并记录原步数；
- 正步时继续调用 `_split_pulses()`生成独立子步；
- 计划事件增加逻辑与物理命令数量，现有参数和清单不改名。

定向运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_scan_plan.py tests/motor/test_scan_controller.py
```

## 3. 完整 Modbus 事务线程隔离

先扩展 `tests/motor/test_motor_transport.py`，覆盖：

1. 打开、请求队列、拆分响应、解析、验证和超时位于同一事务对象；
2. `own_thread=True`时事务对象及其 `QTimer`属于电机线程；
3. 主线程暂时不处理事件时，事务线程仍可接收响应并取消超时；
4. 读请求按配置重试，动作请求不由传输层自动重发；
5. 断开、资源错误和协议错误失败当前及排队事务；
6. 紧急停止清除普通事务并保持停止请求顺序；
7. `own_thread=False`的假串口测试保持同步、可确定；
8. `shutdown()`有界停止线程，不产生跨线程定时器警告。

实现边界：

- 新内部事务工作对象独占串口、请求队列、活动事务、响应缓冲和超时计时器；
- `MotorSerialTransport`保留公开门面及现有信号名称；
- 门面只用 queued signal向事务线程提交操作并转发结果；
- 不改 Modbus帧、CRC、响应严格验证、标签和控制器错误语义；
- 不增加固定等待，不延长扫描安全参数。

定向运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_motor_transport.py tests/motor/test_motor_controller.py
```

## 4. 多轮联动诊断关联

先在 `tests/motor/test_scan_controller.py`与 `tests/test_diagnostic_bundle.py`增加失败测试：

1. 每轮光谱仪真实启动后产生 `motor_scan_acquisition_link`事件；
2. 事件包含扫描ID、轮次和采集ID；
3. 导出最新一轮采集时自动找到同一扫描的全部采集ID；
4. 时间线、性能和帧摘要保留全部关联轮次及无采集ID事件；
5. 普通非扫描采集仍只导出目标采集ID；
6. ZIP清单记录扫描ID和有序去重后的关联采集ID；
7. 默认无完整光谱数据、脱敏、校验和临时文件语义不变。

实现：

- `ScanController._on_task_started()`发出关联事件；
- 导出器先分析完整时间线再建立过滤集合；
- 同一过滤集合应用于 timeline、performance和frame summaries；
- 不改变诊断记录器的全局活动采集语义。

定向运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_scan_controller.py tests/test_diagnostic_bundle.py tests/test_diagnostic_core.py
```

## 5. 自动回归与静态验证

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/motor
.\.venv\Scripts\python.exe -m pytest -q tests/test_diagnostic_bundle.py tests/test_diagnostic_core.py tests/test_diagnostic_acquisition.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q main.py spectrometer tools
git diff --check
```

失败只能通过修复本轮代码解决，不调整预测常量、不放宽协议验证、不清理用户资料。

## 6. 部署镜像和板端更新

运行：

```powershell
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py --check
.\.venv\Scripts\python.exe -m pytest -q tests/test_rock4bplus_deploy.py
```

生成新的 `docs/motor/rock4bplus-update-<commit>.md`，仅列出本轮变化运行文件。板端更新必须：

1. 先关闭软件并确认进程停止；
2. 在 `/home/radxa/zgcai-backups/`创建新的时间戳备份；
3. 逐文件备份、安装、SHA-256和 `py_compile`校验；
4. 提供逐文件回退，不删除数据、设置、虚拟环境或原光谱仪软件。

## 7. 实机验收

1. 五轮纯电机：`X步数=10, 步时=0`，观察完整 X行程连续运动；
2. 五轮纯电机：`X步数=10, 步时=0.1`，确认10个独立子步和100 ms等待；
3. 五轮联动：10 ms积分时间压力测试，首轮与后续流畅度差异应显著缩小；
4. 核对写入应答超时、同目标重发和状态读取失败统计；
5. 验证最终严格位置、返回起点、急停、限位、用户停止和断线；
6. 导出诊断包，确认全部五轮采集和电机段均存在；
7. 核对完整帧等于持久化帧，缺帧、错误帧和重新同步为0。

自动回归只能证明软件行为；只有上述 ROCK 4B+实机验收完成后，才能将流畅度优化标记完成。
