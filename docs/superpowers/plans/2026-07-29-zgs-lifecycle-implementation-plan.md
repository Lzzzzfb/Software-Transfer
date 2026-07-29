# `.zgs` 临时缓存生命周期实施计划

对应设计：
`docs/superpowers/specs/2026-07-29-zgs-lifecycle-design.md`

## 实施约束

- 只修改 `D:\codex\光谱仪 - Linux转移版`。
- 不修改原 Windows 项目。
- 不暂存、覆盖或删除用户现有的 Excel、DOCX、PDF 和诊断 ZIP。
- 不改变串口协议、全量帧解析、显示节流、处理算法和 CSV/Excel 内容。
- 先写失败测试，再修改实现。

## 阶段 1：建立统一缓存清理边界

新增 `spectrometer/storage/spool_lifecycle.py`：

- 定义不可变的清理结果，分别记录已删除文件、仍保留文件和警告。
- 提供按路径去重的 `cleanup_spool_files()`。
- 缺失文件视为已经清理。
- `unlink` 失败只记录警告，不覆盖、不截断、不重复创建文件。

在 `tests/test_spool_lifecycle.py` 覆盖成功、文件已不存在、单文件删除失败和
多文件部分失败。

## 阶段 2：未启用自动存储时清理

修改 `spectrometer/acquisition/controller.py`：

- 所有设备封存完成后，先维持现有 `sealed`、完整帧数和持久化帧数验证。
- 背景或参考任务先完成参考提交。
- `auto_store=false` 且任务没有错误时调用统一清理函数。
- 清理全部成功时以空文件列表结束任务。
- 任务失败时不调用清理，完成文件列表报告现存 `.part/.zgs`。
- 清理部分失败时任务保持采集成功，但通过诊断警告和完成文件列表报告
  剩余恢复文件。

在 `tests/test_acquisition_controller.py` 增加具备真实持久化会话行为的假设备
管理器，覆盖正常删除、计数不一致保留、任务错误保留和参考提交失败保留。

## 阶段 3：自动导出成功后清理

修改 `spectrometer/storage/sealed_export.py`：

- 增加 `cleanup_sources` 参数。
- 只有全部批次导出成功且导出帧数等于期望值时才执行清理。
- 采集任务此前已经失败时使用 `cleanup_sources=false`，仍可导出合法帧，但
  保留 `.zgs`。
- 暴露仍存在的恢复文件和非致命清理警告。

修改 `spectrometer/storage/session_manager.py`：

- 启动封存导出时传递 `cleanup_sources`。
- 后台收尾结果同时携带正常输出文件、现存恢复文件、致命错误和非致命
  警告。
- 非致命警告通过现有 `warning_event` 返回控制器。
- `session_closed` 的公开参数数量保持不变，减少不相关改动。

扩展 `tests/test_sealed_spool_export.py` 和
`tests/test_storage_session_manager.py`，覆盖成功删除、帧数不一致保留、
显式禁止清理、删除失败警告和恢复文件回传。

## 阶段 4：用户可见日志

修改 `spectrometer/ui/main_window.py`：

- 按扩展名区分 CSV/XLSX 用户输出与 `.part/.zgs` 恢复文件。
- 只有 CSV/XLSX 计入“已生成文件”。
- 恢复文件使用 WARN/ERROR 单独报告路径。
- 未勾选且正常清理后只报告采集任务停止。
- 飞行记录器继续记录完成文件列表和最终全量帧统计。

扩展 `tests/test_main_window_acquisition.py`，验证正常输出和恢复文件日志不会
混淆。

更新 `docs/user-guide.md`，明确 `.part/.zgs` 是临时安全缓存，正常完成后
删除，异常时保留用于恢复。

## 阶段 5：部署同步

运行项目现有部署构建工具，把变更后的运行文件和新增模块同步到
`deploy/rock4bplus/app`。不得把测试、文档、诊断包或用户文件复制到部署
目录。

## 阶段 6：验证

依次运行：

1. 缓存生命周期、控制器、封存导出、存储管理器和主窗口定向测试。
2. 默认绘图后端完整测试。
3. 强制 PyQtGraph 后端完整测试。
4. `tests/test_rock4bplus_deploy.py` 部署结构与源码一致性测试。
5. `git diff --check` 和 Git 状态检查。

自动验证标准：

- 正常未勾选采集不留下 `.part/.zgs`。
- 正常自动导出只留下选定 CSV/XLSX。
- 异常、计数不一致和导出失败保留恢复文件。
- 删除失败不破坏正确输出并产生明确警告。
- 原有完整帧持久化、协议、处理、绘图和恢复测试全部通过。

## 阶段 7：ROCK 4B+ 实机复验

部署后执行三组测试：

1. 未勾选自动存储，连续采集后停止，确认数据目录没有新增数据文件。
2. 勾选 CSV + Excel，确认只留下 CSV/XLSX 且帧数完整。
3. 在可控条件下制造导出目录不可写，确认 `.zgs` 保留并显示恢复路径。

每组导出诊断包，比较完整帧、持久化帧、缺帧、重同步、GUI FPS、CPU 和
温度。本改动不得降低已经达到的约 22 FPS 绘图和约 90 FPS 全量采集表现。
