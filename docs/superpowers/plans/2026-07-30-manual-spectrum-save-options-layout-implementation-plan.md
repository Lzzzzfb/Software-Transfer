# 选项布局与上一任务手动保存实施计划

## 目标

实现已确认的设计：

- 左侧栏只显示设备；
- 采集方式移动到“保存图片”右侧；
- 批次、格式和自动批量存储移动到“系统选项”；
- 增加“保存光谱”按钮；
- 未自动存储的最近一次正常普通采集可在停止后手动导出；
- 新普通采集覆盖未保存缓存；
- 正常退出清理未保存缓存；
- Y 轴范围控件收进独立弹窗；
- 自动存储格式、命名、处理快照、全帧持久化和异步导出保持不变。

设计规格：

`docs/superpowers/specs/2026-07-30-manual-spectrum-save-options-layout-design.md`

## 约束

- 只修改 `D:\codex\光谱仪 - Linux转移版`。
- 不修改原 Windows 项目。
- 不覆盖、删除或暂存当前用户已有的 Excel、Word、PDF、诊断包和采集文件。
- 先写失败测试，再写最小实现。
- 不在 GUI 线程读取整批 spool 或生成 CSV/Excel。
- 正常待保存缓存和异常恢复缓存必须分开。
- `deploy/rock4bplus` 只通过构建脚本同步运行文件。

## 阶段 1：迁移界面控件

### 1.1 写失败的界面测试

修改：

- `tests/test_ui_smoke.py`
- 新增 `tests/test_y_axis_dialog.py`

测试：

1. `DeviceSidebar` 标题为“设备”。
2. `DeviceSidebar` 不再具有：
   - `acquisition_mode`；
   - `batch_size`；
   - `storage_format`；
   - `auto_store`。
3. 主窗口具有 `acquisition_mode`，默认“连续采集”。
4. 工具栏按钮顺序中：
   - “恢复缓存”后是“保存光谱”；
   - “保存光谱”后是“保存图片”；
   - “保存图片”后是采集方式。
5. “保存光谱”默认禁用。
6. 工具栏只有“Y 轴设置…”按钮，不再直接显示固定、最小、最大和应用控件。
7. `SettingsDialog` 正确加载和返回：
   - 数据目录；
   - 曲线宽度；
   - 每批帧数；
   - 存储格式；
   - 自动存储。
8. 自动存储启动时仍强制未勾选。
9. Y 轴对话框：
   - 取消不返回修改；
   - 未固定时允许确认；
   - 固定时拒绝 `maximum <= minimum`；
   - 有效输入返回不可变设置值。

运行：

```powershell
python -m pytest -q tests/test_ui_smoke.py tests/test_y_axis_dialog.py
```

预期：新断言先失败。

### 1.2 实现侧栏与选项对话框

修改：

- `spectrometer/ui/device_sidebar.py`
- `spectrometer/ui/settings_dialog.py`
- `spectrometer/ui/input_controls.py`（仅在现有控件无法复用时）

实现：

- 删除侧栏存储表单；
- 标题改成“设备”；
- 在设置对话框使用 `DirectSpinBox` 和 `NoWheelComboBox`；
- 添加批次、格式和自动存储；
- `values()` 返回完整设置；
- 自动存储只作为当前运行期选择，不改变启动强制关闭规则。

### 1.3 实现 Y 轴对话框

新增：

- `spectrometer/ui/y_axis_dialog.py`

定义：

- 冻结值对象 `YAxisSettings`：
  - `fixed: bool`；
  - `minimum: float`；
  - `maximum: float`。
- `YAxisDialog`：
  - 使用禁止滚轮误操作的数值框；
  - 自己负责输入验证；
  - 不直接访问绘图控件。

修改：

- `spectrometer/ui/main_window.py`

实现：

- 工具栏只创建“Y 轴设置…”；
- 主窗口保存当前 Y 轴设置值；
- 确认后调用现有 `set_fixed_y_range()` 或 `disable_fixed_y()`；
- 保持 `_plot_user_zoomed()` 和 `_plot_view_reset()` 语义。

### 1.4 重排上下文工具栏

修改：

- `spectrometer/ui/main_window.py`

实现顺序：

```text
打开文件
恢复缓存
保存光谱
保存图片
采集方式
处理控件
X 轴
自动缩放
Y 轴设置
清除对比
```

把 `_selected_acquisition_mode()` 改为读取
`MainWindow.acquisition_mode`。

### 1.5 运行阶段测试

```powershell
python -m pytest -q tests/test_ui_smoke.py tests/test_y_axis_dialog.py tests/test_plot_interaction.py
```

验收：

- UI 断言通过；
- 现有绘图缩放与固定范围测试不回归。

## 阶段 2：冻结封存导出上下文

### 2.1 写失败的存储上下文测试

修改：

- `tests/test_storage_session_manager.py`
- `tests/test_sealed_spool_export.py`

测试：

1. 从请求、设备、封存结果和处理快照创建不可变导出上下文。
2. 上下文冻结：
   - 输出目录；
   - 波长；
   - 标签；
   - 设备元数据；
   - 处理快照；
   - 文件名；
   - 每设备预期帧数；
   - spool 路径。
3. 创建上下文后修改设备、设置或处理提供器，不改变导出结果。
4. `start_prepared_export()` 使用上下文中的目录，而不是管理器当前目录。
5. 自动保存现有 `start_sealed_export()` 继续工作，并委托给相同实现。
6. 清理源为真时成功删除，导出失败时保留。

运行：

```powershell
python -m pytest -q tests/test_storage_session_manager.py tests/test_sealed_spool_export.py
```

预期：上下文 API 测试先失败。

### 2.2 新增导出上下文模型

新增：

- `spectrometer/storage/export_context.py`

定义不可变值对象：

- `FrozenDeviceExport`：
  - 设备 ID；
  - 波长；
  - 显示标签；
  - 本地化前的设备元数据；
  - 文件名 stem；
  - 处理快照。
- `SealedExportContext`：
  - 请求；
  - 输出目录；
  - workbook stem；
  - 每设备冻结信息；
  - spool 路径；
  - 预期帧数。

构造时：

- 复制所有映射；
- 把数组转成不可变 tuple；
- 校验设备集合与请求完全一致；
- 校验路径和帧数存在；
- 不持有设备对象或 Qt 控件。

### 2.3 拆分准备与启动

修改：

- `spectrometer/storage/session_manager.py`
- `spectrometer/storage/sealed_export.py`

新增：

- `prepare_sealed_export(request, devices, sealed_results, *, output_directory=None, processing_snapshots=None)`
- `start_prepared_export(context, *, cleanup_sources=True)`

保留：

- `start_sealed_export(...)`

现有方法改为：

```text
prepare_sealed_export()
→ start_prepared_export()
```

`SealedSpoolExporter` 接收冻结上下文，或由管理器把上下文解包为现有构造参数。
优先保持导出器单一职责，不让导出器重新读取设备或 UI。

### 2.4 运行阶段测试

```powershell
python -m pytest -q tests/test_storage_session_manager.py tests/test_sealed_spool_export.py tests/test_export_policy.py tests/test_storage_naming.py
```

验收：

- 自动导出行为不变；
- 上下文冻结测试通过。

## 阶段 3：实现上一任务缓存状态机

### 3.1 写失败的控制器测试

修改：

- `tests/test_acquisition_controller.py`

新增测试：

1. 未自动存储、封存成功、帧数一致且大于 0：
   - 不删除 spool；
   - 创建一个待保存对象；
   - 发出可用信号；
   - 普通 `task_finished` 不把它报告为异常恢复文件。
2. 持久化 0 帧：
   - 清理正常空缓存；
   - 不创建待保存对象。
3. 自动存储：
   - 继续立即启动后台导出；
   - 不创建手动候选。
4. 封存失败或帧数不一致：
   - 只报告恢复文件；
   - 不创建手动候选。
5. 启动下一普通任务：
   - 清理旧候选；
   - 发出覆盖诊断；
   - 再开始配置。
6. 旧候选清理失败：
   - 拒绝新任务；
   - 保留旧候选；
   - 报告路径。
7. 背景和参考任务不清除候选。
8. 并行单机任务后完成者替代前一个候选。
9. 替代时旧候选清理失败：
   - 保留旧候选；
   - 新任务 spool 作为恢复文件报告；
   - 不静默删除。
10. `save_pending_capture()`：
    - 没有候选时拒绝；
    - 调用准备好的异步导出；
    - 导出期间 busy；
    - 重复调用被拒绝。
11. 导出成功：
    - 清空候选；
    - 发出完成信号。
12. 导出失败：
    - spool 仍存在时保留候选；
    - 允许重试。
13. `discard_pending_capture("shutdown")` 清理正常候选。

运行：

```powershell
python -m pytest -q tests/test_acquisition_controller.py
```

预期：新生命周期断言先失败。

### 3.2 新增待保存值对象

新增：

- `spectrometer/storage/manual_capture.py`

定义：

- `PendingManualCapture`：
  - `context: SealedExportContext`；
  - 创建时间；
  - 总帧数属性；
  - 文件路径属性。

只保存冻结上下文，不加载帧。

### 3.3 扩展控制器信号与属性

修改：

- `spectrometer/acquisition/controller.py`

新增信号：

- `manual_capture_changed(object)`：候选或 `None`；
- `manual_export_started(str)`；
- `manual_export_finished(str, object, bool)`。

新增属性/方法：

- `pending_manual_capture`；
- `manual_export_active`；
- `can_save_pending_capture`；
- `save_pending_capture()`；
- `discard_pending_capture(reason)`；
- `_replace_pending_capture(context)`；
- `_prepare_manual_capture(task)`。

`busy` 包含活动手动导出，但单纯存在待保存候选不算 busy。

### 3.4 调整任务完成分支

修改 `_finish_finalization()`：

```text
自动存储
→ 按现有逻辑启动导出

未自动存储 + 普通任务 + 正常封存 + 帧数一致且 > 0
→ 冻结导出上下文
→ 登记待保存
→ 释放任务

未自动存储 + 0 帧
→ 清理空缓存
→ 释放任务

失败或不一致
→ 保留恢复文件
→ 释放失败任务
```

背景/参考继续执行参考提交，并按原规则清理正常缓存。

### 3.5 新任务覆盖

在 `start_local()` 和 `start_global()` 完成设备、同步和参数预校验后，调用：

```text
discard_pending_capture("new_acquisition")
```

只有清理成功才创建新请求和进入 `_claim_task()`。

`capture_local_reference()` 与 `capture_global_reference()` 不调用覆盖。

### 3.6 手动导出完成路由

调整 `_on_storage_closed()`：

- 活动采集任务：保持现有处理；
- 手动导出任务：
  - 无错误：清空候选并报告输出；
  - 有错误且 spool 存在：保留候选；
  - 有错误且 spool 已不存在：清空候选并报告不可重试；
  - 不把手动导出结果误传给已释放的采集任务。

### 3.7 运行阶段测试

```powershell
python -m pytest -q tests/test_acquisition_controller.py tests/test_storage_session_manager.py tests/test_spool_lifecycle.py
```

验收：

- 状态机和失败路径全部通过；
- 旧的未自动存储立即删除测试更新为待保存语义；
- 异常恢复缓存测试继续通过。

## 阶段 4：主窗口接线与用户反馈

### 4.1 写失败的主窗口测试

修改：

- `tests/test_main_window_acquisition.py`
- `tests/test_ui_smoke.py`

测试：

1. `_storage_options()` 从 `settings` 获取批次、格式和自动存储。
2. `_selected_acquisition_mode()` 从工具栏下拉框获取。
3. 待保存信号到达后：
   - 所有控制任务空闲时按钮启用；
   - 日志报告设备数、帧数和覆盖规则。
4. 还有任一采集任务时按钮禁用；任务全部结束后启用。
5. 点击保存：
   - 调用 `control.save_pending_capture()`；
   - 文本变为“保存中…”；
   - 不打开文件对话框。
6. 保存成功：
   - 报告 CSV/Excel 文件；
   - 按钮恢复并禁用。
7. 保存失败：
   - 报告恢复路径；
   - 按钮恢复可用。
8. 新任务覆盖：
   - 按钮禁用；
   - 诊断页包含覆盖信息。
9. `open_settings()` 保存批次和格式，但自动存储只在当前运行中生效。
10. `closeEvent()`：
    - 清理待保存候选；
    - 存储清理失败不阻塞退出。

运行：

```powershell
python -m pytest -q tests/test_main_window_acquisition.py tests/test_ui_smoke.py
```

预期：新 UI/信号测试先失败。

### 4.2 接入工具栏与设置

修改：

- `spectrometer/ui/main_window.py`

实现：

- `self.save_spectrum_button`；
- `self.acquisition_mode`；
- `_save_pending_spectrum()`；
- `_manual_capture_changed()`；
- `_manual_export_started()`；
- `_manual_export_finished()`；
- `_refresh_save_spectrum_state()`。

调整：

- `_apply_settings()`；
- `_storage_options()`；
- `_selected_acquisition_mode()`；
- `open_settings()`；
- `_refresh_control_status()`；
- `closeEvent()`。

自动存储每次启动仍执行：

```python
self.settings["auto_store"] = False
```

但设置对话框在当前运行中可修改它。

### 4.3 处理冻结快照生命周期

当前 `_control_task_finished()` 会删除 `_processing_by_device`。调整为：

- 控制器在释放任务前通过存储管理器冻结导出上下文；
- 主窗口可继续删除活动显示快照；
- 手动保存完全依赖上下文，不再访问已删除快照。

为避免一个设备参与并行任务时误删另一个任务的快照，补充任务 ID 或引用计数
测试；只在没有其他活动任务使用该设备时丢弃显示处理任务。

### 4.4 日志语义

新增清晰日志：

- “上一任务已保留 N 帧，可点击‘保存光谱’导出”；
- “上一任务未保存数据已被新采集覆盖”；
- “正在后台保存上一任务光谱”；
- “光谱保存完成，生成 N 个文件”；
- “光谱保存失败，缓存已保留，可重试”；
- “正常退出，未保存的上一任务缓存已清除”。

异常恢复仍使用“恢复文件”，不得称为“待手动保存”。

### 4.5 运行阶段测试

```powershell
python -m pytest -q tests/test_main_window_acquisition.py tests/test_ui_smoke.py tests/test_simulated_session.py
```

验收：

- 按钮状态与控制状态一致；
- GUI 不同步导出；
- 设置来源完成迁移。

## 阶段 5：保存格式与完整性回归

### 5.1 添加端到端手动导出测试

新增：

- `tests/test_manual_spectrum_export.py`

使用小型真实 spool 和冻结上下文测试：

1. 单设备 CSV + Excel 只生成 CSV。
2. 多设备 CSV + Excel 只生成 Excel，且一个设备一个工作表。
3. 纯 CSV 每台设备一个文件。
4. 仅 Excel 生成 Excel。
5. `batch_size=2`、总帧 5 时生成 B0001、B0002、B0003。
6. 每个输出包含全部预期帧且无重复。
7. 采集后修改全局设置不影响结果。
8. 背景、参考、强度校准和 airPLS 使用冻结快照。
9. 重名时追加后缀，不覆盖旧文件。
10. 导出期间 Qt 事件循环继续响应。

### 5.2 跑存储与处理回归

```powershell
python -m pytest -q `
  tests/test_manual_spectrum_export.py `
  tests/test_export_policy.py `
  tests/test_export_localization.py `
  tests/test_history_export_compatibility.py `
  tests/test_intensity_calibration.py `
  tests/test_airpls.py `
  tests/test_processor.py `
  tests/test_storage_naming.py
```

## 阶段 6：文档与 ROCK 4B+ 部署同步

### 6.1 更新用户文档

修改：

- `README.md`
- `docs/user-guide.md`

说明：

- 批量存储设置的新位置；
- 采集方式的新位置；
- “保存光谱”只保存最近正常完成的一次普通采集；
- 新采集无提示覆盖未保存缓存；
- 正常退出清理；
- 自动保存与手动保存区别；
- 异常恢复缓存不会被覆盖；
- Y 轴设置入口。

### 6.2 更新硬件验证脚本

修改：

- `tools/hardware_ui_control_validation.py`

检查：

- 设置控件位置；
- 默认值；
- “保存光谱”初始状态；
- 工具栏顺序；
- Y 轴设置按钮。

### 6.3 同步部署

运行：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
```

确认：

- 只同步 `main.py` 和 `spectrometer`；
- 新增运行模块进入部署副本；
- 文档、测试、诊断包和技能未进入部署目录。

## 阶段 7：完整验证

### 7.1 本机验证

按顺序运行：

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer tools
python tools/build_rock4bplus_deploy.py --check
git diff --check
```

若 Windows 本机仍未安装 PyQtGraph，允许对应绘图后端测试保持已知跳过，但不能
新增失败。

### 7.2 变更范围复核

```powershell
git status --short
git diff --name-only
git diff --stat
```

确认：

- 原 Windows 项目未修改；
- 受保护 Excel、Word、PDF、诊断包和采集文件保持原状态；
- 只暂存本功能相关源码、测试、文档和部署副本。

### 7.3 ROCK 4B+ 实机验收

从 Windows PowerShell 上传部署包或增量文件，在 ROCK 4B+ 本地图形桌面启动。

依次验证：

1. 启动默认连续采集、500 帧、CSV + Excel、自动存储关闭。
2. 工具栏和选项布局。
3. 未自动存储，采集 10 秒并停止：
   - 完整帧等于持久化帧；
   - “保存光谱”启用；
   - GUI 不在停止时导出。
4. 点击“保存光谱”：
   - 后台保存；
   - GUI 可操作；
   - 输出格式、命名、中文标签和总帧数正确；
   - 成功后按钮禁用、spool 清理。
5. 再采一批但不保存，开始第三批：
   - 第二批缓存被清理；
   - 有覆盖日志；
   - 第三批正常采集。
6. 开启自动存储：
   - 原自动导出流程正常；
   - 不启用手动保存按钮。
7. 单设备与多设备策略。
8. 扣背景、强度校准和 airPLS 后手动保存，显示与文件一致。
9. 保存失败模拟：
   - 缓存保留；
   - 可重试；
   - 新采集在清理失败时被拒绝。
10. 正常关闭清理未保存缓存。
11. 拔插设备并重新握手。
12. 导出诊断包，核对：
    - 完整/持久化帧率；
    - 显示 FPS；
    - 拒帧、重同步和缺帧；
    - 手动保存、覆盖和清理事件。

## 提交建议

按可独立回滚的阶段提交：

1. `feat: move storage settings and add y-axis dialog`
2. `refactor: freeze sealed export context`
3. `feat: retain last capture for manual export`
4. `feat: wire manual spectrum save controls`
5. `test: cover manual spectrum export lifecycle`
6. `docs: document manual save and sync ROCK deploy`

实施时不得使用宽范围暂存；每个提交显式列出文件。
