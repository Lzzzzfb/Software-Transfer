# ROCK 4B+ 强度校准、airPLS 与导出策略实施计划

对应设计：
`docs/superpowers/specs/2026-07-29-processing-calibration-export-policy-design.md`

## 实施约束

- 只修改 `D:\codex\光谱仪 - Linux转移版`。
- 不修改原 Windows 项目 `D:\codex\光谱仪`。
- 不暂存、覆盖、删除或提交用户现有 Excel、DOCX、PDF、诊断 ZIP 和
  `data/` 中的参考 CSV/Excel。
- 文件名继续使用当前 Linux 短命名规则。
- `.part/.zgs` 继续保存未经处理的完整原始帧。
- 不修改 MCU 协议、串口拆包、采集进程全量落盘和缓存生命周期。
- 先写失败测试，再修改实现；每个阶段完成后运行对应定向测试。

## 阶段 1：强度校准领域模型、导入器与仓库

新增 `spectrometer/processing/intensity_calibration.py`：

- 定义不可变 `IntensityCalibration`，保存序列号、有效像素数、系数、
  导入时间、源文件名、源文件 SHA-256、记录 ID 和格式版本。
- 实现 CSV/TXT 字节解析；按 `UTF-8-SIG`、`GB18030` 顺序解码。
- 支持单列系数和 `Pixel,Coefficient` 两列格式。
- 支持标题、空行、`#` 注释、逗号、制表符和空白分隔。
- 严格验证像素序号、数量、有限正数和有效像素数。
- 定义适合界面显示的、不会泄露整组系数的错误消息。

新增 `spectrometer/processing/calibration_repository.py`：

- 使用 `config_directory()/calibrations/intensity`。
- 按生产序列号和有效像素数查找。
- JSON 临时文件写入后原子替换。
- 加载时重新验证内容、记录 ID 和系数数量。
- 支持保存、加载和清除。
- 无生产序列号时由调用方只保留内存对象，不写磁盘。

扩展 `SpectrometerDevice`：

- 保存当前 `intensity_calibration` 记录，而不仅是裸数组；
- 保留兼容的 `intensity_calib` 数组读取接口，减少不相关改动；
- 保存 airPLS 设备覆盖的内存状态。

新增：

- `tests/test_intensity_calibration.py`
- `tests/test_calibration_repository.py`

覆盖设计中全部合法和非法输入、原子替换、隔离、清除和旧记录回滚。

## 阶段 2：统一处理快照和正确的强度校准顺序

修改 `spectrometer/processing/processor.py`：

- 扩展 `ProcessingSnapshot`，加入校准记录 ID、airPLS 设置和处理管线版本。
- `ProcessingSnapshot.config` 返回完整 `ProcessingConfig`。
- 当前帧、背景和参考在模式处理前分别乘同一组强度校准系数。
- 长度不一致直接失败，不截断、不补齐。
- airPLS 移到所有处理模式之后，包括吸光度。
- 缓存按“像素数 + 差分阶数”构造的稀疏差分惩罚矩阵。
- 处理结果继续拒绝 NaN 和无穷大。

修改 `spectrometer/extensions/airpls.py`：

- 把参数验证集中为可测试入口。
- 允许调用方传入或复用缓存的差分惩罚矩阵，避免每帧重建。
- 不改变默认算法参数和返回定义。

扩展 `tests/test_processor.py`，覆盖：

- 校准同时作用于当前帧、背景和参考；
- 扣背景和吸光度结果；
- airPLS 作用于原始、扣背景、吸光度和自定义公式；
- 快照序列化后仍携带完整配置；
- 长度和非有限值失败；
- 差分矩阵缓存复用。

扩展 `tests/test_storage_process.py`，验证后台导出只写最终处理结果和完整
处理元数据。

## 阶段 3：全局设置、设备覆盖与自动加载

修改 `spectrometer/services/settings_service.py`：

- 增加全局 `airpls_enabled`、`airpls_lam`、`airpls_order`、
  `airpls_max_iter` 默认值；
- 参数范围固定为：`1e-6 <= lambda <= 1e15`、`1 <= order <= 4`、
  `1 <= max_iter <= 100`；
- 设置文件中的非法值回退到对应默认值，不把非法值静默夹取为边界值；
- 保持旧设置文件兼容。

新增 `spectrometer/processing/profile_repository.py`：

- 保存按生产序列号索引的设备 airPLS 覆盖；
- 数据包含跟随全局、启用状态和三个参数；
- 原子写入；
- 无序列号设备只在内存中使用。

新增 `spectrometer/processing/profiles.py`：

- 定义全局 profile、设备 override 和最终生效 profile；
- 提供唯一的 `resolve_effective_profile()`；
- 集中参数验证，避免主窗口和对话框重复判断。

修改 `spectrometer/ui/main_window.py` 的设备握手完成路径：

- 在生产序列号和有效像素已知后加载强度校准；
- 加载设备 airPLS 覆盖；
- 加载失败只记录诊断，不使设备掉线；
- `_processing_snapshots()` 使用唯一 profile 解析入口和校准记录。

新增：

- `tests/test_processing_profiles.py`
- 针对自动加载的主窗口或设备管理定向测试。

## 阶段 4：airPLS 和强度校准界面

新增 `spectrometer/ui/airpls_dialog.py`：

- 全局 airPLS 参数对话框；
- 使用现有直接输入控件；
- 恢复默认值；
- 提交前严格验证。

修改 `spectrometer/ui/main_window.py`：

- 顶部处理栏增加 airPLS 开关和参数按钮；
- 设置变更写入用户设置；
- 当前采集快照保持冻结，变更只影响下一任务；
- 诊断记录参数变化和失败原因。

修改 `spectrometer/ui/device_parameters.py`：

- 增加“跟随全局”和设备覆盖控件；
- 显示最终生效来源；
- 增加强度校准状态、导入和清除按钮；
- 采集忙时拒绝修改；
- 导入失败不关闭对话框、不替换旧记录；
- 成功后刷新设备状态并写诊断。

必要时在 `DeviceParametersDialog` 构造参数中注入校准仓库、profile 仓库和
空闲状态回调，避免对话框直接依赖主窗口私有字段。

扩展：

- `tests/test_ui_smoke.py`
- `tests/test_main_window_acquisition.py`
- 新增 `tests/test_device_parameters_calibration.py`

覆盖全局设置、跟随/覆盖、导入、清除、忙状态拒绝和设置持久化。

## 阶段 5：airPLS 后台显示处理

新增 `spectrometer/processing/display_service.py`：

- 使用最多两个工作线程的有界后台执行器；
- 每台设备保留一个运行任务和一个最新待处理任务；
- 新任务覆盖尚未执行的旧待处理任务；
- 结果携带设备 ID、采集任务 ID 和快照代次；
- 过期结果不回传绘图；
- 关闭时有界等待；
- 错误信号限频交给主窗口记录。

修改 `spectrometer/ui/main_window.py`：

- airPLS 关闭时保留当前轻量同步处理路径；
- airPLS 开启时提交后台显示任务；
- 结果返回 Qt 主线程后更新曲线；
- 任务停止、设备断开、参数代次变化时作废旧结果；
- 不把显示任务计入采集缺帧或存储帧统计。

新增 `tests/test_display_processing_service.py`，覆盖：

- 最新帧覆盖；
- 不形成无界任务；
- 设备之间相互隔离；
- 快照代次失效；
- 处理异常；
- 安全关闭。

扩展 `tests/test_main_window_acquisition.py`，验证迟到处理结果不会覆盖当前
曲线或已停止任务。

## 阶段 6：统一 CSV/Excel 导出计划

新增 `spectrometer/storage/export_policy.py`：

- 定义不可变导出计划；
- 实现 `resolve_export_plan(storage_format, device_ids)`；
- 去重后设备数为 1：
  - CSV → CSV；
  - Excel → Excel；
  - CSV + Excel → 仅 CSV；
- 去重后设备数大于 1：
  - CSV → 每设备 CSV；
  - Excel → 一个 Excel；
  - CSV + Excel → 仅一个 Excel。

修改：

- `spectrometer/storage/coordinator.py`
- `spectrometer/storage/sealed_export.py`

两条路径必须只根据导出计划创建目标文件，不再各自判断
`StorageFormat.CSV_EXCEL`。

修改 `spectrometer/storage/process_worker.py`：

- 把强度校准记录、airPLS 参数和处理管线版本写入 CSV 元数据；
- 把会话级和设备级处理元数据传给 Excel 导出器；
- 所有目标文件先写临时文件，整批成功后再发布。

修改 `spectrometer/storage/xlsx_exporter.py`：

- 保持“采集概要 + 每台设备一个工作表”；
- 在概要设备表增加处理模式、强度校准和 airPLS 参数；
- 不改变设备工作表的 `Pixel/Wavelength/Frame` 布局。

文件命名模块 `spectrometer/storage/naming.py` 不修改。

新增 `tests/test_export_policy.py`，并扩展：

- `tests/test_storage_coordinator.py`
- `tests/test_sealed_spool_export.py`
- `tests/test_batch_exporters.py`
- `tests/test_storage_process.py`

覆盖 3 种用户选项 × 单/多设备的六种组合、尾批、元数据、失败清理和
当前短文件名。

## 阶段 7：文档与部署同步

更新：

- `README.md`
- `docs/user-guide.md`

说明：

- 强度校准文件格式、自动加载和清除；
- airPLS 全局/设备覆盖和处理顺序；
- airPLS 可能降低校正后显示 FPS，但不影响全量采集；
- 新的六格导出规则；
- 文件名保持当前 Linux 规则；
- `.part/.zgs` 仍保存完整原始帧。

运行 `tools/build_rock4bplus_deploy.py` 同步所有新增和修改的运行模块到
`deploy/rock4bplus/app`。部署目录不得包含测试、设计文档、参考文件、
用户数据或诊断包。

## 阶段 8：自动验证

依次运行：

1. 新增强度校准、profile、processor、显示服务和导出策略测试。
2. 存储协调器、封存导出、后台处理和主窗口定向测试。
3. `python -m pytest -q`。
4. 在可用 PyQtGraph 依赖下运行完整测试。
5. `python -m compileall -q main.py spectrometer tools`。
6. `python tools/build_rock4bplus_deploy.py --check`。
7. `git diff --check`。
8. Git 状态和原 Windows 项目状态只读检查。

验收标准：

- 普通未启用 airPLS 的绘图和采集性能无回退；
- 全量原始帧持久化逻辑不变；
- 绘图与导出使用相同快照和处理顺序；
- 强度校准错误不覆盖旧记录；
- airPLS 后台显示没有无界队列或迟到结果；
- 六种导出组合产生准确的文件数量和结构；
- 用户现有文件和参考样例无变化；
- `deploy/rock4bplus` 与产品源码一致。

## 阶段 9：提交与 ROCK 4B+ 交付

实现提交只包含：

- 产品源码；
- 测试；
- 用户指南和 README；
- `deploy/rock4bplus` 对应运行文件。

不提交用户样例或诊断数据。

板端更新后按设计文档第 11 节逐项验收。至少先完成单台 SN003：

1. 3648 点全 1 系数导入、重启自动加载；
2. airPLS 开启后的界面响应和绘图/导出一致性；
3. `仅 CSV`、`仅 Excel`、`CSV + Excel` 三种单机输出；
4. 完整帧数与持久化帧数一致；
5. 导出新的诊断包。

多设备文件规则和设备覆盖在第二台实机可用后继续验收。
