# 采集控制、界面交互与存储优化实施计划

日期：2026-07-22  
依据：`docs/superpowers/specs/2026-07-22-acquisition-ui-controls-design.md`  
状态：待执行

## 1. 执行原则

1. 每个阶段先补充失败测试或可重复诊断，再修改产品代码。
2. 状态控制、设备命令、存储会话和界面呈现保持单向依赖，避免把新状态继续堆入 `MainWindow`。
3. 协议格式、校准系数顺序、像素裁剪、下位机同步脉冲和现有导出布局不改变。
4. 每一阶段完成后运行阶段测试和全量测试，形成独立 Git 提交。
5. 自动测试覆盖状态和错误分支，实机只验证真实串口时序、响应性和端到端结果。
6. 不提交 `data/`、`.venv/`、缓存、构建目录或用户修改的函数表 Excel。
7. 任一阶段出现协议或实机回归时停止扩展，先回到最近一次通过测试的阶段提交定位。

## 2. 基线和完成指标

执行前基线：

```powershell
python -m pytest -q --basetemp=data\validation\pytest_plan_baseline
.\.venv\Scripts\python.exe -m compileall -q main.py spectrometer tools
git status --short
```

已知需要保留的非本轮改动：

```text
CCD_CMOS模块采集卡(下位机)函数表20251027.xlsx
```

量化完成指标：

- 全量自动测试通过；
- PyQt5 兼容层和 PySide6 产品环境重点测试均通过；
- 慢速导出测试中 GUI 心跳最长间隔不超过测试设定阈值；
- 三台实机连续开始/停止 10 轮无界面长时间无响应；
- 总控和单机状态冲突全部被拒绝且不发送错误命令；
- 文件名、工作表和帧/像素布局符合规格；
- 固定 Y 轴和鼠标视图交互通过自动及人工检查。

## 3. 阶段 1：状态领域对象和设备命令结果

### 任务 1.1：建立状态模型测试

新增：

- `tests/test_acquisition_control_models.py`

修改：

- `spectrometer/domain/enums.py`
- `spectrometer/domain/models.py`

实现：

- `ControlState`：`IDLE/CONFIGURING/STARTING/ACQUIRING/STOPPING/FINALIZING/ERROR`；
- `AcquisitionOwner`：`GLOBAL/LOCAL/CALIBRATION`；
- 不可变的采集请求快照，包含任务代号、设备集合、采集方式、同步模式、自动存储和批量设置；
- 状态转换合法性和用户可见文本映射；
- 文件命名所需的本地开始时间保留在请求快照中。

测试：

- 枚举和值稳定；
- 请求必须包含设备；
- 批次范围、重复设备和非法组合被拒绝；
- 快照不随界面后续修改而变化。

### 任务 1.2：公开命令完成事件

修改：

- `spectrometer/device/device_manager.py`
- `tests/test_device_manager_ack.py`
- `tests/test_device_manager_sync.py`

实现：

- 新增统一命令结果信号：设备 ID、命令、成功/失败、响应参数；
- 设置触发、开始、停止和初始化 ACK 均发出一次结果；
- 无状态、NAK 和协议异常发出失败结果；
- 保留现有模型更新和固件兼容 ACK 行为；
- 不在设备管理器中新增 GUI 或存储状态。

测试：

- 成功、NAK、缺状态和兼容初始化响应；
- 开始/停止 ACK 对 `device.acquiring` 的更新；
- 每个响应只发出一次命令结果；
- 原同步测试继续通过。

验证：

```powershell
python -m pytest tests\test_acquisition_control_models.py tests\test_device_manager_ack.py tests\test_device_manager_sync.py -q
```

阶段提交：

```text
refactor: expose acquisition command state
```

## 4. 阶段 2：集中采集控制器

### 任务 2.1：建立控制器骨架和状态守卫

新增：

- `spectrometer/acquisition/controller.py`
- `tests/test_acquisition_controller.py`

控制器依赖：

- `DeviceManager`：只负责逐设备命令和设备信息；
- 存储会话接口：开始、提交、异步关闭；
- 参考提交回调：只在校准事务完成后调用；
- Qt 定时器：ACK、单帧和停止静默超时。

信号：

- 全局状态变化；
- 单设备状态变化；
- 操作被拒绝及用户提示；
- 总控/单机开始、停止和收尾完成；
- 背景/参考成功或失败；
- 诊断事件。

状态守卫：

- 总控启动要求所有已连接设备完全空闲；
- 卡片启动要求目标设备空闲且无总控任务；
- 多个 `LOCAL` 任务可并行；
- 总控活动时卡片请求拒绝；
- 任一活动或收尾任务存在时，所有背景/参考请求拒绝；
- 参数和移除接口提供 `can_modify_device()`、`can_remove_device()`。

### 任务 2.2：实现单机启动和停止

流程：

1. 生成 `LOCAL` 任务代号和请求快照；
2. 发送软件触发模式并进入 `CONFIGURING`；
3. 收到触发 ACK 后发送单次/连续开始并进入 `STARTING`；
4. 收到开始 ACK 后进入 `ACQUIRING`；
5. 停止时发送停止并进入 `STOPPING`；
6. ACK 后等待帧静默，移交存储收尾；
7. 存储完成后进入 `IDLE`。

测试：

- 非独立同步选择不影响单机始终配置软件触发；
- ACK 顺序正确；
- 重复开始/停止不重复发送；
- 配置、开始、停止超时；
- 过期 ACK 和过期定时器不影响新任务；
- 两台单机并行，停止一台不影响另一台；
- 单次任务首帧后自动停止。

### 任务 2.3：实现总控同步和回滚

流程：

- 独立/软件同步：按设备集合发送开始；
- 内部硬同步：配置从机外触发、主机软件主机，全部 ACK 后从机优先、主机最后启动；
- 外部硬同步：全部配置外触发并启动，进入等待外部触发状态；
- 任一配置或启动失败，停止已经启动的设备；
- 总控停止作用于请求快照中的全部设备；
- 新识别设备不加入当前任务。

测试：

- 总控前存在任意本地任务时拒绝并列出设备；
- 未勾选参与总控的活动设备仍会阻止总控；
- 各同步模式命令和顺序；
- 部分配置、部分启动和设备掉线回滚；
- 外部触发等待状态可由停止结束；
- 总控活动时卡片操作被拒绝且没有设备命令。

### 任务 2.4：背景和参考事务

修改：

- `spectrometer/processing/references.py`
- `tests/test_references.py`
- `tests/test_acquisition_controller.py`

实现：

- 全局空闲守卫；
- 顶部校准任务复用总控单次配置并暂存每台设备首帧；
- 卡片校准任务使用单机软件触发；
- 全局任务所有设备成功后才调用批量提交；
- `ReferenceRepository.save_batch()` 先写全部临时文件，再完成替换；失败时清理临时文件，活动背景/参考不改变；
- 单机失败只影响目标设备。

验证：

```powershell
python -m pytest tests\test_acquisition_controller.py tests\test_references.py -q
```

阶段提交：

```text
feat: add guarded global and per-device acquisition control
```

## 5. 阶段 3：并行存储会话和异步关闭

### 任务 3.1：建立存储会话管理器

新增：

- `spectrometer/storage/session_manager.py`
- `tests/test_storage_session_manager.py`

修改：

- `spectrometer/storage/coordinator.py`
- `spectrometer/domain/models.py`

实现：

- `StorageSessionManager` 维护会话表和 `device_id -> session_key` 路由；
- 一个总控会话可以映射多台设备；
- 每个本地任务创建独立单设备会话；
- 同一设备不能加入两个存储会话；
- 关闭时先解除帧路由，再在后台执行现有阻塞式 `coordinator.close()`；
- 关闭完成通过 Qt 信号返回导出文件、错误和任务代号；
- 关闭任务异常也必须释放路由和状态；
- `shutdown()` 支持窗口关闭时等待有界时间。

测试：

- 两个本地会话并行提交和交错关闭；
- 总控多设备帧进入同一协调器；
- 非成员设备帧被忽略并记录诊断，不抛到 GUI 帧循环；
- 关闭后尾帧不能进入已关闭或新会话；
- 慢速 `close()` 不阻塞主线程 Qt 心跳；
- 错误保留 `.part` 并完成状态释放。

### 任务 3.2：实现规范文件命名

新增或修改：

- `spectrometer/storage/naming.py`
- `spectrometer/storage/coordinator.py`
- `tests/test_storage_naming.py`
- `tests/test_batch_exporters.py`

实现：

- 本地开始时间、设备标识、采集/同步方式和 `B0001` 批次号；
- 总控 Excel、逐设备 CSV 和单机文件分别生成已确认格式；
- 非法字符替换、空标识回退、大小写无关重名检测和 `_01` 后缀；
- UUID 仅保留在 `.part` 和工作簿概要元数据；
- 重复设备序列号的 Excel 工作表追加端口或编号。

测试示例：

```text
20260722_153045_多设备_软件同步_B0001.xlsx
20260722_153045_SN003_COM14_软件同步_B0001.csv
20260722_153045_SN003_COM14_单机连续_B0001.xlsx
```

### 任务 3.3：接入控制器停止流程

实现：

- 停止 ACK 后启动帧静默定时器；
- 新尾帧重置静默定时器，但受最大等待上限约束；
- 安静后控制器调用异步关闭并进入 `FINALIZING`；
- 未启用自动存储时跳过后台导出，直接收尾；
- 存储背压回调只发起控制器停止请求，不递归调用阻塞关闭；
- 单设备关闭只更新对应卡片，总控关闭等待总控会话完成。

验证：

```powershell
python -m pytest tests\test_storage_coordinator.py tests\test_storage_session_manager.py tests\test_storage_naming.py tests\test_batch_exporters.py -q
```

阶段提交：

```text
feat: finalize acquisition storage without blocking UI
```

## 6. 阶段 4：输入控件与界面清理

### 任务 4.1：无滚轮直接输入组件

新增：

- `spectrometer/ui/input_controls.py`
- `tests/test_input_controls.py`

组件：

- `DirectSpinBox`；
- `DirectDoubleSpinBox`；
- `NoWheelComboBox`。

行为：

- 数值框 `NoButtons`；
- 忽略滚轮、上/下、PageUp/PageDown；
- 滚轮事件继续传给父级滚动区域，而不修改控件值；
- 文本输入、粘贴、删除、Enter 和 Tab 正常；
- 下拉框只屏蔽滚轮；
- 保留范围、精度和验证状态。

替换：

- `spectrometer/ui/device_sidebar.py`；
- `spectrometer/ui/device_parameters.py`；
- `spectrometer/ui/settings_dialog.py`；
- `spectrometer/ui/main_window.py` 中新增 Y 轴输入；
- 其他搜索到的数值框和下拉框。

### 任务 4.2：删除手动连接和顶部新建页面

修改：

- `spectrometer/ui/device_sidebar.py`
- `spectrometer/ui/ribbon.py`
- `spectrometer/ui/main_window.py`
- `tests/test_ui_smoke.py`

实现：

- 删除串口、波特率、刷新、手动连接控件及信号；
- 自动扫描不再向侧栏写入端口下拉列表；
- 保留顶部“查找设备”；
- 删除 Ribbon “新建页面”按钮和信号；
- 保留 `HistoryViewer` 内部按钮；
- 将“启用”重命名为“参与总控”；
- 每次创建主窗口都把自动存储设为未勾选，不从持久设置恢复该值；
- 设置保存时不再持久化自动存储为启动默认值。

### 任务 4.3：按钮状态和样式

修改：

- `spectrometer/ui/ribbon.py`
- `spectrometer/ui/device_sidebar.py`
- `spectrometer/ui/styles.qss`
- `tests/test_ui_smoke.py`

实现：

- 顶部单一开始/停止按钮；
- 卡片开始/停止、背景、参考；
- 卡片信号包含设备 ID；
- 控制器状态驱动文字、图标、颜色、工具提示；
- 总控占用时卡片显示占用状态，点击仍进入守卫并提示；
- 启动、停止、保存和错误状态可见。

验证：

```powershell
python -m pytest tests\test_input_controls.py tests\test_ui_smoke.py tests\test_simulated_session.py -q
```

阶段提交：

```text
feat: streamline acquisition controls and parameter entry
```

## 7. 阶段 5：主窗口控制器接入

### 任务 5.1：从主窗口移除生命周期逻辑

修改：

- `spectrometer/ui/main_window.py`
- `tests/test_simulated_session.py`
- `tests/test_ui_smoke.py`

实现：

- 创建 `StorageSessionManager` 和 `AcquisitionController`；
- 顶部与卡片信号只调用控制器；
- `_frame_arrived` 将帧分别交给采集诊断、控制器和存储路由；
- 删除主窗口中的单一 `self.storage`、同步启动和阻塞 `_close_storage()`；
- 控制器信号更新状态栏、Ribbon 和卡片；
- 参数对话框打开/应用前检查目标设备空闲；
- 设备移除通过控制器守卫和受控停止；
- 窗口关闭等待控制器异步清理，并防止重复关闭事件。

### 任务 5.2：模拟器适配

修改：

- `spectrometer/ui/main_window.py`
- `tests/test_simulated_session.py`

实现：

- 模拟帧只为控制器标记为采集中的设备生成；
- 模拟设备开始/停止 ACK 与真实设备走同一状态路径；
- 单次、并行单机、总控和背景/参考均可模拟测试；
- 自动存储开关默认关闭。

验证：

```powershell
python -m pytest tests\test_acquisition_controller.py tests\test_simulated_session.py tests\test_ui_smoke.py -q
```

阶段提交：

```text
refactor: route UI acquisition through controller
```

## 8. 阶段 6：绘图视图交互

### 任务 6.1：拆分范围模型和数值格式

修改：

- `spectrometer/ui/plot_widget.py`

新增：

- `tests/test_plot_widget.py`

实现：

- 视图保存“自动数据范围”“当前显示范围”“固定 Y 初始范围”；
- 提供纯函数完成屏幕矩形到数据范围换算；
- `format_axis_value()` 按像素、原始强度和小数值输出定点十进制；
- 去除尾零和负零，禁止 `e/E`；
- 使用 `QFontMetrics` 计算边距和可容纳刻度数。

### 任务 6.2：框选、双击和滚轮

实现：

- 左键按下记录起点；移动时绘制半透明框；释放时应用 X/Y 范围；
- 框选限制在绘图区，过小范围忽略；
- 删除左键平移逻辑；
- 双击按固定 Y 状态恢复初始视图；
- 保留绘图区滚轮中心缩放；
- 人工缩放发出状态信号并暂停自动跟随。

### 任务 6.3：固定 Y 控件

修改：

- `spectrometer/ui/main_window.py`
- `spectrometer/ui/styles.qss`
- `tests/test_plot_widget.py`
- `tests/test_ui_smoke.py`

实现：

- 固定 Y、最小值、最大值和应用按钮；
- 应用时验证有限数值及 `min < max`；
- 固定后新帧不改变当前 Y；
- 框选和滚轮仍可改变 Y；
- 双击恢复用户初始 Y；
- 取消固定恢复实时自动 Y。

测试：

- 大整数、小数和负数均无科学计数法；
- 框选数据换算；
- 固定 Y 后更新曲线不改变范围；
- 固定 Y 下人工缩放生效且保持；
- 双击固定/非固定两种恢复规则；
- 长坐标不越界或重叠到不可读。

验证：

```powershell
python -m pytest tests\test_plot_widget.py tests\test_ui_smoke.py -q
```

阶段提交：

```text
feat: add stable fixed-axis spectrum navigation
```

## 9. 阶段 7：全量自动和故障注入验证

### 任务 7.1：双 Qt 环境回归

运行：

```powershell
python -m pytest -q --basetemp=data\validation\pytest_acquisition_ui
python -m compileall -q main.py spectrometer tools
```

PySide6 `.venv` 未安装 pytest 时，至少直接运行重点测试函数和 UI 模拟工具；若开发依赖已经安装，则执行同一全量测试。

覆盖：

- 现有协议、校准、处理、恢复和导出测试无回归；
- 关闭慢导出期间 GUI 心跳；
- 状态超时、设备掉线、存储异常和窗口关闭；
- 自动存储每次启动未勾选；
- 所有输入和下拉控件无滚轮修改。

### 任务 7.2：模拟端到端

扩展或新增：

- `tools/run_ui_control_validation.py`

场景：

1. 四设备总控连续开始/停止 10 次；
2. 四张卡片交错启动、停止；
3. 单机运行时尝试总控；
4. 总控运行时尝试卡片、背景、参考；
5. 总控和卡片单次；
6. 自动存储关闭和开启；
7. 慢导出时持续采样 GUI 心跳；
8. 保存后核对文件名、工作表、帧列和像素行。

阶段提交：

```text
test: cover acquisition UI and asynchronous storage workflow
```

## 10. 阶段 8：三台实机验证

### 任务 8.1：低风险连接与参数检查

- 启动源代码，不指定端口；
- 确认 COM10 被协议握手排除；
- 确认 COM14/COM17/COM18 的序列号、像素和校准系数未变化；
- 确认自动存储未勾选；
- 检查所有数值框和下拉框滚轮不会改值。

### 任务 8.2：总控采集

- 独立模式总控连续开始/停止 10 轮；
- 记录每轮停止按钮响应、设备 ACK、尾帧静默和收尾时长；
- 自动存储开启时生成完整批和尾批；
- 总控单次每台只完成一次任务；
- 软件同步及可安全执行的硬同步配置/ACK；
- 总控运行时卡片、背景和参考请求被拒绝。

### 任务 8.3：单机并行

- 在非独立同步选择下分别启动卡片，确认先切换软件触发；
- COM14/COM17/COM18 交错启动和停止；
- 停止一台并导出时其他两台继续采集；
- 单机活动时总控开始被拒绝；
- 自动存储文件只含对应设备帧。

### 任务 8.4：背景、参考和绘图

- 全局空闲时执行顶部背景和参考，三台全部成功才提交；
- 执行每张卡片的背景和参考；
- 任一设备活动时所有背景/参考请求被拒绝；
- 固定 Y 轴连续采集无跳动；
- 框选可同时缩放 X/Y，双击恢复设置范围；
- 像素、波长和强度刻度均完整显示。

### 任务 8.5：存储核对

- 文件名符合时间、设备、模式和批次规则；
- 三台总控 Excel 按设备分工作表；
- 每帧一列、每像素一行；
- CSV/Excel 代表帧一致；
- 无尾帧串入下一会话；
- 正常结束不残留 `.part`，错误路径保留恢复缓存。

实机结果保存到忽略的 `data/validation/`，并更新正式联调报告。

阶段提交：

```text
docs: record acquisition control hardware validation
```

## 11. 阶段 9：文档和最终交付

修改：

- `README.md`
- `docs/user-guide.md`
- `docs/hardware-validation-pending.md`
- 新的实机验证报告

内容：

- 自动发现和无手动连接界面；
- 顶部总控与卡片单机规则；
- 背景/参考全局空闲要求；
- 自动存储默认关闭；
- 文件命名示例；
- 框选、双击和固定 Y 操作；
- 异步停止和恢复缓存说明；
- 已验证与仍需现场条件的项目。

最终验证：

```powershell
git diff --check
python -m pytest -q --basetemp=data\validation\pytest_final
.\.venv\Scripts\python.exe -m compileall -q main.py spectrometer tools
git status --short
```

最终提交只包含本轮源代码、测试和文档。用户函数表 Excel 保持未暂存状态。

## 12. 需求覆盖矩阵

| 需求 | 主要阶段 |
| --- | --- |
| 数值框仅直接输入，所有输入控件屏蔽滚轮 | 阶段 4 |
| 删除手动连接控件 | 阶段 4 |
| 自动存储每次启动不勾选 | 阶段 4、5 |
| 删除顶部新建页面 | 阶段 4 |
| 左键框选、双击恢复 | 阶段 6 |
| 停止采集卡顿 | 阶段 3、5 |
| 规范文件命名 | 阶段 3 |
| 顶部开始/停止切换 | 阶段 2、4、5 |
| 总控与卡片单机采集 | 阶段 2、4、5 |
| 坐标完整显示 | 阶段 6 |
| 固定 Y 轴且允许人工缩放 | 阶段 6 |
| 顶部/单机背景和参考 | 阶段 2、4、5 |
| 同步和并发防错 | 阶段 2、3、7、8 |

全部需求均有实现任务、自动测试和实机验收路径，不存在待定项。
