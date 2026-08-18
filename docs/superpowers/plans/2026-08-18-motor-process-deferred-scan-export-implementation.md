# ROCK 4B+ 电机独立进程与扫描延期导出实施计划

- 日期：2026-08-18
- 依据：`docs/superpowers/specs/2026-08-18-motor-process-deferred-scan-export-design.md`
- 设计提交：`d9a9e9c`
- 状态：待实施
- 说明：当前会话没有 `writing-plans` 技能，本文件按项目既有格式提供等价的测试优先计划

## 1. 工作区保护与基线

当前已确认但不属于本轮的用户文件包括 Office、CSV、PDF、诊断包、`tmp/`和用户修改的
`docs/motor/rock4bplus-update-1df16e3.md`。不得清理、覆盖、移动或批量暂存这些文件；
每次暂存仅列出本轮明确文件。

实施前基线：

```text
tests/motor/test_scan_controller.py
tests/motor/test_motor_controller.py
tests/test_acquisition_controller.py
tests/test_storage_session_manager.py
tests/test_sealed_spool_export.py

87 passed in 2.74s
```

所有测试使用 PyQt6 离屏模式和系统临时目录。修改顺序固定为：失败测试、最小实现、
针对性测试、完整回归、部署镜像、板端实机。

## 2. 子进程内扫描路径执行器

先新增 `tests/motor/test_process_scan_runner.py`，使用假电机核心覆盖：

1. 路径开始后第一段立即发起；
2. 一段完成后下一段在执行器内部直接发起，不依赖 GUI 侧回调；
3. 最终扫描段关闭预测交接，返回段全部使用严格完成；
4. 步时为 0 不创建等待，正步时由执行器定时推进；
5. 进度事件保留轴、方向、脉冲、逻辑子步、轮次和单调时间；
6. 命令拒绝、运动失败、限位/故障和用户停止终止余下路径；
7. 路径完成只发出一次结果，过期运动信号不能推进新路径。

最小实现新增 `spectrometer/motor/process_scan_runner.py`。执行器只依赖现有
`MotorController`公开运动和完成信号，不复制 Modbus、坐标、限位或安全逻辑。

## 3. 电机工作进程和 GUI 代理

先新增 `tests/motor/test_motor_process_worker.py`和
`tests/motor/test_motor_process_proxy.py`，覆盖：

1. 命令序号、请求 ID、未知命令和异常结果；
2. 子进程状态快照正确镜像连接、配置、坐标、限位、校准、运动和安全锁；
3. 连接、断开、设置、手动运动、回零、脱离卡死、清故障和急停均由子进程执行；
4. 整条扫描/返回路径使用 `MotorScanRunner`，不逐段往返 GUI；
5. 关键事件有界且不可静默丢弃，队列失去监管时受控停止；
6. 子进程异常退出时代理发出断开和故障，扫描能停止光谱任务；
7. `shutdown()`先请求停止和关闭，再有界等待，最后才强制终止；
8. `spawn`环境和 PyQt6 导入烟雾测试通过。

实现：

- `spectrometer/motor/process_worker.py`：创建 `QCoreApplication`、进程内
  `MotorController`、不另开线程的 `MotorSerialTransport`和命令分派；
- `spectrometer/motor/process_proxy.py`：进程生命周期、IPC轮询、状态镜像和现有界面
  兼容信号；
- `spectrometer/motor/factory.py`：正式 Linux 使用进程代理，显式仿真和依赖注入继续
  使用直接控制器；进程失败不得静默降级；
- `spectrometer/motor/controller.py`：只添加路径执行和状态快照所需的最小接口，不改
  协议和安全条件；
- `spectrometer/ui/main_window.py`：通过工厂创建控制器并保持原有注入接口。

## 4. 扫描任务快速封存和延期导出

先扩展 `tests/test_acquisition_controller.py`和
`tests/test_storage_session_manager.py`：

1. `AcquisitionOwner.SCAN`停止后先封存、核对并冻结导出上下文；
2. 封存成功即释放设备和全局采集状态并发出 `scan_capture_sealed`；
3. 此时不得启动 CSV/Excel，下一轮采集可以被接受；
4. 已准备的上下文按任务 ID保存，重复加入、未知 ID和已导出 ID被拒绝；
5. `start_deferred_scan_exports()`按轮次顺序、并发度 1启动；
6. 每轮正式导出完成后仍使用 `task_finished`报告最终文件；
7. 封存失败、完整/持久化计数不一致和导出失败保留 `.zgs`；
8. 普通单机、普通总控、校准和手动保存的现有时序完全不变；
9. 关闭时未导出的上下文保留恢复文件且产生明确诊断。

最小实现：

- 在 `AcquisitionController`中仅为扫描所有者增加延期导出注册表、封存信号和顺序启动
  方法；
- 分开“释放硬件任务”和“发出最终 task_finished”，避免每轮正式导出阻塞下一轮；
- 复用 `StorageSessionManager.prepare_sealed_export()`和
  `start_prepared_export()`，不修改 `.zgs`格式、处理快照、原子发布和计数核对；
- 同一时刻只启动一个扫描导出，下一个由 `session_closed`推进。

## 5. 扫描总控改为轮次级编排

先扩展 `tests/motor/test_scan_controller.py`：

1. 支持路径执行器时，一轮扫描只提交一个冻结路径命令；
2. 进度和诊断段事件由子进程事件映射，旧假控制器仍走逐段兼容路径；
3. 双轴严格校验后停止光谱，收到封存信号立即返回；
4. 第一轮返回结束后可开始第二轮，而第一轮导出尚未启动；
5. 最后一轮返回后进入新增 `EXPORTING`，再触发顺序导出；
6. 所有导出成功后才完成清单和 `scan_finished`；
7. 任一导出失败时显示“运动已完成，数据导出失败”并保留恢复文件；
8. 纯电机扫描不进入封存或导出状态；
9. 用户停止、电机故障、采集封存失败和同步信号重入均无自动返回或重复完成。

实现修改 `spectrometer/motor/scan_controller.py`与
`spectrometer/motor/manifest.py`。正常路径：

```text
SCANNING
→ STOPPING_ACQUISITION
→ scan_capture_sealed
→ RETURNING
→ 下一轮或 EXPORTING
→ task_finished（逐轮）
→ COMPLETED / FAULTED
```

## 6. 界面、诊断和关闭流程

先扩展主界面、面板和诊断测试：

1. 新状态文字区分封存、返回和最终导出；
2. 导出期间扫描仍保持锁定，急停和退出仍可用；
3. 电机进程 PID、启动、停止、异常、IPC队列压力和路径时序进入现有飞行记录器；
4. 封存延期、导出启动/完成/失败与扫描—采集关联进入同一时间线；
5. 关闭窗口先停止扫描和电机进程，再封存光谱，最后有界等待存储；
6. 诊断 ZIP仍不默认包含完整光谱数据或另建扫描日志文件。

修改 `spectrometer/ui/main_window.py`、`spectrometer/ui/motor_panel.py`和必要的诊断
导出测试，不改变绘图、处理或普通光谱控制算法。

## 7. 分层验证

定向测试按阶段执行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_process_scan_runner.py
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_motor_process_worker.py tests/motor/test_motor_process_proxy.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_acquisition_controller.py tests/test_storage_session_manager.py
.\.venv\Scripts\python.exe -m pytest -q tests/motor/test_scan_controller.py
```

回归：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/motor
.\.venv\Scripts\python.exe -m pytest -q tests/test_acquisition_controller.py tests/test_storage_session_manager.py tests/test_sealed_spool_export.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_main_window_acquisition.py tests/test_main_window.py tests/test_diagnostic_bundle.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q main.py spectrometer tools
git diff --check
```

任何失败只能通过修复本轮实现解决，不调整限位、15 mm 行程、预测安全余量、协议严格
校验或数据完整性条件。

## 8. 部署镜像、更新和回退

自动回归通过后：

```powershell
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py --check
.\.venv\Scripts\python.exe -m pytest -q tests/test_rock4bplus_deploy.py
```

生成新的增量更新说明和精确文件清单。板端先退出 GUI并确认主进程、电机进程和光谱
采集进程全部停止，再创建时间戳备份、逐文件安装、SHA-256和 `compileall`校验。
回退以本轮完整兼容文件集合为单位，不单独回退半个 IPC或扫描状态机。

## 9. ROCK 4B+ 实机验收

按设计规格第 10 节执行，包括手动控制、机械回零、脱离卡死、限位、急停、五轮纯电机、
五轮联动、0.1 s步时、光谱完整性、导出延期、故障恢复和退出清理。

重点量化：

- 子进程内相邻段交接 P95 ≤ 20 ms、最大 ≤ 50 ms；
- 联动运动时长中位值相对纯电机同参数增加不超过 10%；
- 电机运动结束前没有正式 CSV/Excel导出事件；
- 每轮 `完整帧 == 持久化帧 == 导出帧`；
- 无遗留进程、无无界队列和无静默故障。

自动测试通过只代表可部署，只有实机矩阵通过后才能标记本任务完成。
