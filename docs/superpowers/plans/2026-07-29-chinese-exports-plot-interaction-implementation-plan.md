# CSV/Excel 中文化与绘图交互限制实施计划

对应设计：
`docs/superpowers/specs/2026-07-29-chinese-exports-plot-interaction-design.md`

## 约束

- 只修改 Linux 专用副本。
- 不修改内部处理快照、`.part/.zgs` 元数据结构和文件命名。
- 不覆盖或提交用户已有 Excel、Word、PDF、诊断包及导出的 CSV/Excel。
- 产品源码与 `deploy/rock4bplus/app` 保持一致。

## 阶段 1：导出本地化

新增 `spectrometer/storage/localization.py`：

- 集中维护已知元数据标签、处理模式、同步模式和布尔值的中文转换。
- 未知标签和值原样返回。
- 转换函数保持纯函数，便于单元测试。

修改 CSV/Excel 导出器：

- CSV 固定标题、固定元数据和数据列头改为中文。
- Excel 采集概要中的会话元数据标签和值通过本地化函数输出。
- Excel 设备概要中的处理模式使用中文。
- Excel 设备页列头改为“像素序号”“波长 (nm)”和中文帧标题。

先新增/更新测试，再修改实现：

- `tests/test_export_localization.py`
- `tests/test_batch_exporters.py`
- `tests/test_storage_process.py`
- 必要的历史查看/恢复测试断言。

## 阶段 2：绘图交互限制

修改 legacy `SpectrumPlotWidget`：

- `wheelEvent` 接受并忽略事件，不改变视图。
- 非左键按下、拖动和释放不进入选择状态。
- 保留左键框选与左键双击复位。
- 更新工具提示。

修改 PyQtGraph `_SpectrumViewBox`：

- `wheelEvent` 接受并忽略，不调用父类。
- `mouseDragEvent` 只把左键拖动交给父类。
- 右键和其他按键拖动直接接受并忽略。
- 菜单继续禁用，工具提示移除滚轮说明。

更新：

- `tests/test_plot_interaction.py`
- `tests/test_pyqtgraph_plot_widget.py`
- `tests/test_ui_smoke.py`

## 阶段 3：文档、部署与验证

- 更新 README 和用户指南中的绘图操作与中文导出说明。
- 运行 `tools/build_rock4bplus_deploy.py` 同步运行源码。
- 运行定向测试。
- 运行 `python -m pytest -q`。
- 运行 `python -m compileall -q main.py spectrometer tools`。
- 运行 `python tools/build_rock4bplus_deploy.py --check`。
- 运行 `git diff --check`。
- 只暂存本次源码、测试、文档和部署副本并提交。
