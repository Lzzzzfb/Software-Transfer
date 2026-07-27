# ROCK 4B+ 保存隔离与错帧保护实施计划

日期：2026-07-27  
依据：`docs/superpowers/specs/2026-07-27-rock4bplus-storage-isolation-design.md`  
状态：待执行

## 执行约束

1. 只修改 Linux 专用副本。
2. 不暂存或修改用户现有 Excel、产品 PDF 和实机验证数据。
3. 每阶段先建立针对性测试，再做最小实现。
4. 不把大型导出失败降级到 Qt 主线程执行。
5. 完成后重建并核对 `deploy/rock4bplus`。

## 阶段 1：处理上下文和导出数据模型

修改：

- `spectrometer/domain/models.py`
- `spectrometer/processing/processor.py`
- `spectrometer/acquisition/controller.py`
- `spectrometer/storage/session_manager.py`
- 相关模型、处理和控制器测试

实现：

- 增加可序列化、不可变的处理上下文快照；
- 在任务开始时冻结处理模式、背景、参考、强度校准和波长；
- 增加允许浮点及负数的处理后保存帧模型；
- 绘图与保存复用相同处理函数；
- 当前任务不受采集中途界面设置变更影响。

验证：

```powershell
python -m pytest -q tests/test_processor.py tests/test_acquisition_controller.py tests/test_storage_session_manager.py
```

## 阶段 2：原始帧质量分析

新增或修改：

- `spectrometer/acquisition/frame_quality.py`
- `spectrometer/acquisition/coordinator.py`
- `spectrometer/ui/main_window.py`
- `tests/test_frame_quality.py`
- `tests/test_acquisition_coordinator.py`

实现：

- 协议长度和像素数量仍在解析入口检查；
- 序号重复、回退和跳变形成结构化诊断；
- 基于原始 U16 的多特征错位检测；
- 明确错误帧丢弃，可疑帧保留并记录；
- B0001/B0002 的小型固定回归样本进入测试夹具，不复制完整用户数据文件。

验证：

```powershell
python -m pytest -q tests/test_frame_quality.py tests/test_acquisition_coordinator.py
```

## 阶段 3：独立保存进程

新增或修改：

- `spectrometer/storage/process_worker.py`
- `spectrometer/storage/coordinator.py`
- `spectrometer/storage/session_manager.py`
- `spectrometer/storage/csv_exporter.py`
- `spectrometer/storage/xlsx_exporter.py`
- `tests/test_storage_process.py`
- `tests/test_storage_coordinator.py`
- `tests/test_storage_session_manager.py`

实现：

- 主进程只写 `.part` 并提交有界批次任务；
- 单独工作进程读取原始帧、质量分析、光谱处理和文件导出；
- Linux 下降低工作进程优先级；
- 临时输出成功后原子替换；
- 停止设备后异步等待保存结果；
- 工作进程失败时保留 `.part`；
- CSV/Excel 只写处理后浮点结果及处理元数据。

验证：

```powershell
python -m pytest -q tests/test_storage_process.py tests/test_storage_coordinator.py tests/test_storage_session_manager.py tests/test_batch_exporters.py tests/test_spool.py
```

## 阶段 4：界面状态与退出

修改：

- `spectrometer/ui/main_window.py`
- `spectrometer/ui/diagnostics.py`
- 相关 UI 和会话测试

实现：

- 区分“采集中”“后台保存中”“保存完成”“保存失败”；
- 停止按钮不等待 CSV/Excel；
- 队列警告、受控停止、丢帧和可疑帧显示明确诊断；
- 退出超时保留 `.part` 并提示。

验证：

```powershell
python -m pytest -q tests/test_ui_smoke.py tests/test_storage_session_manager.py tests/test_acquisition_controller.py
```

## 阶段 5：完整回归和部署

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

- 用户 Excel 仍是未暂存修改；
- 产品 PDF 和验证数据未纳入提交；
- `deploy/rock4bplus` 只包含板端运行内容；
- 新源文件均进入部署副本；
- 原 Windows 项目未修改。

## 实机复验

1. 传输更新后的 `deploy/rock4bplus`。
2. 采集背景并选择背景扣除。
3. 开启 CSV+Excel 连续采集，观察曲线、主进程 CPU 和保存进程 CPU。
4. 停止后确认界面立即响应，并等待后台保存完成。
5. 核对 CSV 与 Excel 含负浮点值且与曲线处理口径一致。
6. 检查帧序号、错误帧诊断及 `.part` 清理。
7. 重复断线重连验收。
