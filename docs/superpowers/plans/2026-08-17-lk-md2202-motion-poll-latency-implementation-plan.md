# LK-MD2202 扫描轴间衔接优化实施计划

## 1. 基线与保护

- 设计规格：`docs/superpowers/specs/2026-08-17-lk-md2202-motion-poll-latency-design.md`
- 设计提交：`4ad1be8`
- 代码回退基线：`2b4970f`
- 当前分支：`codex/lk-md2202-integration`
- 完整自动化参考：435 passed
- 板端当前备份：`/home/radxa/zgcai-backups/pre-2b4970f-20260817-135327`

只修改本计划列出的电机控制器、测试、验证文档和由构建脚本生成的部署副本。不得暂存、覆盖或清理工作区中用户已有的 Office、CSV、PDF、诊断包和 `tmp/`资料。光谱仪采集、绘图和保存源码不在本次修改范围内。

## 2. 阶段一：锁定失败行为

先修改：

- `tests/motor/test_motor_controller.py`

新增失败测试：

1. 普通运动的 `motion_status` 请求使用 100 ms 超时、0 次事务内重试；
2. 动作写入应答和有效运行状态后，下一次轮询间隔为 20 ms；
3. 一次状态读取超时后动作保持活动，不发出失败完成信号，并重新调度查询；
4. 多次间歇状态读取失败后收到有效停止和目标位置，动作仍正常完成；
5. 状态读取失败到动作总截止时间后，仍进入现有位置急停流程；
6. 串口断开、位置不匹配、限位异常和停止无法确认的既有安全行为保持不变；
7. 动作完成和失败各输出一条汇总诊断，包含轴、动作类型、耗时、查询次数、失败次数、结果和原因；
8. 汇总诊断不为每一次成功轮询单独写日志。

定向运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_controller.py
```

新增测试必须先因当前 500 ms、两次自动重试及立即失败行为而失败，确认测试能够捕获本次实机问题。

## 3. 阶段二：最小控制器实现

修改：

- `spectrometer/motor/controller.py`

实现：

1. 新增 `MOTION_STATUS_TIMEOUT_MS=100`、`MOTION_STATUS_REPOLL_MS=20`和零事务内重试常量；
2. 为活动动作记录开始单调时间、状态查询次数和读取失败次数；
3. 每次发送 `motion_status` 时增加查询次数，并显式传入短超时和零重试；
4. 有效运行状态返回后使用 20 ms 调度下一次查询；
5. 仍连接且未到动作总截止时间时，将只读超时、Modbus异常和协议错误视为可恢复状态漏读，增加失败计数并重新调度；
6. 连接丢失不恢复，继续执行现有断线失败逻辑；
7. 到达总截止时间后继续调用现有位置急停和停止确认流程；
8. 普通运动、机械回零和脱离卡死的目标位置、限位转换和安全锁判据保持不变；
9. 动作结束时只输出一次汇总诊断，不逐次记录高频轮询。

定向验证通过后单独提交：

```text
fix: reduce LK-MD2202 motion status retry latency
```

## 4. 阶段三：扫描与安全回归

运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_controller.py tests/motor/test_scan_controller.py tests/motor/test_motor_transport.py
python -m pytest -q tests/motor
```

重点确认：

1. 扫描控制器仍只在上一动作成功发出 `motion_finished` 后开始下一动作；
2. X/Y 顺序、分步、步时、轮次和返回路径没有变化；
3. 单次状态漏读不终止扫描，也不启动下一轴；
4. 持续状态读取失败、位置不匹配、急停和断线仍终止扫描；
5. 脱离卡死最多运行 1 秒及其专用停止复核没有回归；
6. 纯电机扫描和光谱联动扫描的既有测试保持通过。

若定向测试暴露当前测试夹具无法验证请求级超时参数，只扩展测试夹具记录调用参数；不修改 `spectrometer/motor/transport.py` 的生产行为。

## 5. 阶段四：完整回归与文档

按实现结果更新：

- `docs/motor/lk-md2202-host-validation.md`
- `docs/motor/rock4bplus-acceptance-checklist.md`
- 新的 ROCK 4B+ 增量更新与回退说明

运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q
python -m compileall -q main.py spectrometer tools
git diff --check
```

完整回归不得通过修改、移动或清理用户资料来修复。与本任务无关的既有问题只记录证据，不扩大本次代码范围。

文档提交与功能提交分开，便于独立回退。

## 6. 阶段五：ROCK 4B+ 部署镜像

运行：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
python -m compileall -q deploy/rock4bplus/app
python -m pytest -q tests/test_rock4bplus_deploy.py
```

检查：

1. `deploy/rock4bplus/app/spectrometer/motor/controller.py` 与主源码一致；
2. 部署镜像不包含测试、文档、Office 文件、诊断包、用户数据或缓存；
3. 本次增量更新只安装实际变化的运行文件；
4. 部署镜像单独提交：

```text
build: refresh ROCK 4B+ motion polling fix
```

## 7. 板端更新与回退

板端目标仍为 `/opt/zgcai-spectrometer`。更新步骤必须：

1. 正常退出软件并确认进程已停止；
2. 从 Windows 上传到新的 `/home/radxa/zgcai-update-<commit>/app`目录；
3. 只备份和安装本次变化的运行文件；
4. 创建 `/home/radxa/zgcai-backups/pre-<commit>-<timestamp>`备份；
5. 将备份路径写入 `/home/radxa/zgcai-last-backup.txt`；
6. 安装后执行 `py_compile`和文件一致性校验；
7. 先执行 LK-MD2202 只读探针，再从本地图形桌面启动 GUI；
8. 更新失败时按记录的完整文件集合回退，不删除原光谱仪软件。

## 8. 实机验收顺序

1. 不连接光谱仪，使用 X=10 mm、Y=1 mm、行程数 10、扫描次数 1、步数 1、步时 0，连续运行三次；
2. 现场确认上一轴停止后才启动下一轴，不允许两轴重叠；
3. 导出诊断包，确认不再出现约 1 秒的重试阶梯；
4. 核对每个动作汇总中的耗时、查询次数和读取失败次数；
5. 将步时设为 1 秒，确认用户等待时间仍准确存在；
6. 连接光谱仪运行一轮联动扫描，确认采集、保存和返回无回归；
7. 验证急停、机械回零、脱离卡死和 15 mm 行程保护；
8. 持续通信故障必须停止扫描，不能继续发送下一轴命令。

只有自动测试、部署校验和上述 ROCK 4B+ 实机验收均通过，才将本问题标记完成。
