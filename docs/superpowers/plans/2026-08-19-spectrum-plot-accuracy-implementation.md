# 光谱曲线精确绘制修复实施计划

- 日期：2026-08-19
- 依据：`docs/superpowers/specs/2026-08-19-spectrum-plot-accuracy-design.md`
- 设计提交：`2996b26`
- 状态：待实施
- 说明：当前会话没有 `writing-plans` 技能，本文件按项目既有格式提供等价的测试优先计划

## 1. 范围与工作区保护

本轮仅修复光谱绘图准确性和必要的缩放状态衔接。不得修改主窗口尺寸、工具栏、设备栏、
设备卡片、电机区、设置弹窗布局，也不得新增帧信息控件。

当前工作区存在不属于本轮的用户修改和未跟踪资料，包括 Excel、Word、CSV、PDF、`tmp/`、
诊断包及用户修改的 `docs/motor/rock4bplus-update-1df16e3.md`。实施时：

- 不清理、还原、移动或覆盖这些文件；
- 不使用批量暂存；
- 每次只按明确路径暂存本轮文件；
- 构建部署副本前先记录源码和部署副本差异；
- 板端安装前创建时间戳备份和文件哈希。

实施开始时重新运行基线：

```powershell
git status --short
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_plot_interaction.py `
  tests/test_pyqtgraph_plot_widget.py `
  tests/test_ui_smoke.py `
  tests/test_plot_backend.py
.\.venv\Scripts\python.exe -m pytest -q
```

最后一次已知基线为相关测试 23 项通过、完整测试 492 项通过；若重新运行结果不同，先区分
用户现有改动、本轮环境变化和真实基线失败，再开始修改。

## 2. 共享绘图数据规则

### 2.1 先写失败测试

新增 `tests/test_plot_data.py`，覆盖：

1. X/Y 转换为连续的一维 `float64` 数组；
2. 不同长度、不同维度或 X 含 NaN/Inf 时拒绝；
3. Y 含 NaN/Inf 时保留整帧；
4. 空数组、单点数组和任意有效长度均可安全处理；
5. 自动边界忽略非有限 Y，并在整条 Y 均无效时返回“无可用边界”；
6. 可见范围内所有原始点均被选择；
7. 可见范围左右各包含一个参与边界相交线段的相邻点；
8. Y 的非有限值把曲线拆成多个连续段；
9. X 单调、反向或非单调时均按原始采集顺序返回可见连续段；
10. 使用多种数组长度参数化，并测试同一进程中不同长度曲线。

### 2.2 最小实现

新增 `spectrometer/ui/plot_data.py`，只提供三个无 Qt 状态的纯数据函数：

- `validated_plot_arrays(x, y)`：统一输入校验和连续数组转换；
- `finite_curve_bounds(x, y)`：返回可参与自动范围计算的有限边界或 `None`；
- `visible_finite_segments(x, y, x_min, x_max)`：返回当前视图需要绘制的连续有限片段。

可见片段不依赖固定像素数，也不按屏幕宽度限制点数。对相邻 X 点构成的线段，只要该
线段的 X 区间与当前视图相交，就保留两个端点；因此既包含可见点，也包含正确裁剪边界所需
的相邻点。函数不平滑、不插值、不重排原始数组。

定向验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_plot_data.py
```

## 3. Legacy 后端精确绘制

### 3.1 先扩展测试

修改 `tests/test_plot_interaction.py`：

1. 删除“每水平像素最多一个点”的旧验收；
2. 验证设备曲线、参考曲线和基线曲线都使用统一输入规则；
3. 构造 1945、1948、1951 三个窄峰，验证完整数组没有抽样；
4. 构造 618、624、633 三个不等高峰，验证原始坐标、强度和顺序不变；
5. 验证局部视图选择包含全部峰及边界相邻点；
6. 验证 NaN/Inf 被拆成独立折线，不能跨断点连接；
7. 非法更新抛出明确错误且不替换上一帧曲线；
8. 空数组、单点和全无效 Y 不导致绘制或自动范围崩溃；
9. 手动缩放后更新不同长度最新帧，视图范围保持不变。

### 3.2 最小实现

修改 `spectrometer/ui/plot_widget.py`：

- 设备、参考和基线曲线统一调用 `validated_plot_arrays()`；
- 校验成功后再替换原曲线，保证非法新帧不会破坏上一帧显示；
- `_calculate_bounds()` 使用 `finite_curve_bounds()`，跳过没有有限 Y 的曲线；
- `_draw_curve()` 调用 `visible_finite_segments()`，逐段构造 `QPolygonF` 并绘制；
- 删除 `_sample_index_cache`、`_curve_point_limit()` 和 `linspace` 抽样；
- 全局视图绘制全部原始点，局部视图仅裁剪不可见部分；
- 保留现有坐标轴、图例、十字光标、固定 Y 轴和图片导出逻辑。

定向验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_plot_data.py `
  tests/test_plot_interaction.py `
  tests/test_ui_smoke.py
```

## 4. PyQtGraph 后端精确绘制

### 4.1 先扩展测试

修改 `tests/test_pyqtgraph_plot_widget.py`：

1. 将 `autoDownsample=True` 的旧期望改为关闭自动降采样；
2. 保持 `clipToView=True`；
3. 验证曲线使用 `connect="finite"`，且未跳过有限值检查；
4. 验证 Y 含 NaN/Inf 可以更新并保留断点；
5. 验证 X 含 NaN/Inf 和形状不匹配时保留上一帧；
6. 验证 1945、1948、1951 三峰和 618、624、633 不等高三峰；
7. 在完整视图和局部放大视图核对峰坐标、强度及顺序；
8. 使用多种实际数组长度，并同时保留不同长度的多设备曲线；
9. 验证空数组、单点和全无效 Y 的自动范围不会崩溃；
10. 验证更新现有曲线时复用同一个 `PlotDataItem`。

### 4.2 最小实现

修改 `spectrometer/ui/pyqtgraph_plot_widget.py`：

- 使用共享输入校验和有限边界函数；
- `PlotDataItem` 设置为 `clipToView=True`、`autoDownsample=False`、
  `connect="finite"`；
- 不调用 `setSkipFiniteCheck(True)`；若当前 PyQtGraph 版本提供该选项，则明确保持
  `False`；
- 新建和更新曲线时都保持相同连接规则；
- 自动范围忽略非有限 Y，对无有限数据的曲线安全降级；
- 继续复用曲线对象、关闭抗锯齿和 OpenGL。

定向验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_plot_data.py `
  tests/test_pyqtgraph_plot_widget.py `
  tests/test_plot_backend.py
```

## 5. 双击恢复与自动缩放状态

当前两个绘图控件的 `reset_initial_view()` 会同时恢复范围并开启持续自动缩放，主窗口也会
无条件勾选“自动缩放”。设计要求把“恢复当前完整数据范围”和“开启持续自动缩放”分开。

### 5.1 先写失败测试

修改 `tests/test_plot_interaction.py`、`tests/test_pyqtgraph_plot_widget.py` 和
`tests/test_ui_smoke.py`：

1. 手动框选后自动缩放关闭；
2. 双击恢复完整数据范围，但自动缩放仍关闭；
3. 主窗口复选框与绘图控件实际状态一致；
4. 用户主动勾选“自动缩放”后才恢复持续自动范围；
5. 固定 Y 轴、取消固定 Y 轴和显式 `auto_range()` 的原有行为不回归。

### 5.2 最小实现

- 在两个绘图控件中分离“恢复初始范围”和“启用持续自动范围”的内部路径；
- 鼠标双击调用只恢复范围的路径；
- `enable_auto_range(True)` 和程序显式自动范围继续启用持续自动范围；
- 修改 `spectrometer/ui/main_window.py` 的 `_plot_view_reset()`，根据绘图控件的真实
  `auto_range_enabled` 状态更新复选框，不再无条件勾选；
- 不修改主窗口布局或任何其他控件。

定向验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_plot_interaction.py `
  tests/test_pyqtgraph_plot_widget.py `
  tests/test_ui_smoke.py
```

## 6. 双后端一致性和完整回归

增加或整理参数化测试，使相同输入规则同时验证 legacy 与 PyQtGraph：

- 动态像素数；
- 相邻窄峰；
- 不等高峰；
- 非有限 Y 断点；
- 非法 X/形状；
- 手动视图连续更新；
- 参考曲线、基线曲线和设备曲线。

完成代码后运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_plot_data.py `
  tests/test_plot_interaction.py `
  tests/test_pyqtgraph_plot_widget.py `
  tests/test_plot_backend.py `
  tests/test_ui_smoke.py `
  tests/test_main_window_acquisition.py
.\.venv\Scripts\python.exe -m pytest -q tests/motor
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q main.py spectrometer tools
git diff --check
```

绘图修复导致的失败必须修复实现或更新与新设计直接冲突的旧期望；不得通过跳过测试、放宽
采集协议、改变存储计数或修改电机/扫描逻辑解决。

## 7. 部署副本同步

自动回归通过后再同步部署副本：

```powershell
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py --check
.\.venv\Scripts\python.exe -m pytest -q tests/test_rock4bplus_deploy.py
```

预期部署文件集合至少包含：

- `deploy/rock4bplus/app/spectrometer/ui/plot_data.py`；
- `deploy/rock4bplus/app/spectrometer/ui/plot_widget.py`；
- `deploy/rock4bplus/app/spectrometer/ui/pyqtgraph_plot_widget.py`；
- `deploy/rock4bplus/app/spectrometer/ui/main_window.py`。

构建脚本会同步整个运行源码树，因此提交前必须检查部署差异，确保没有把文档、测试、用户
数据、诊断包或缓存复制到部署目录。

## 8. ROCK 4B+ 更新与回退

生成仅包含上述运行文件的增量包和独立更新说明。板端操作顺序：

1. 退出正式 GUI，确认 `/opt/zgcai-spectrometer/main.py` 进程已停止；
2. 以最终更新提交短哈希和执行时间生成唯一备份目录，例如
   `/home/radxa/zgcai-backups/pre-2996b26-20260819-153000/`；正式命令使用实际更新提交
   短哈希，不固定复用示例目录；
3. 按相对目录备份本轮全部目标文件；
4. 使用 `install -m 0644` 逐文件安装；
5. 运行 `py_compile`/`compileall`、SHA-256 和源目标一致性校验；
6. 先执行离屏导入和合成数据探针，再启动正式 GUI；
7. 将备份路径写入 `/home/radxa/zgcai-last-backup.txt`。

回退必须以本轮完整文件集为单位，不能只回退一个后端或遗漏共享模块。回退后重新运行编译
检查和后端导入探针。

## 9. ROCK 4B+ 实机验收

### 9.1 合成准确性

- 在 legacy 和 PyQtGraph 后端分别注入三窄峰及不等高三峰；
- 在完整视图和框选放大后核对峰数、像素坐标、原始强度和相对顺序；
- 注入含 NaN 的 Y，确认断点两侧没有跨区直线；
- 使用多种长度，不以 4096 作为固定验收条件。

### 9.2 连续采集

- 使用实际光谱仪连续采集；
- 框选放大后保持视图，并继续更新最新帧；
- 双击恢复范围后确认不会擅自持续自动缩放；
- 用户主动开启自动缩放后确认范围正常更新；
- 保存的原始数据与绘图所用完整数组抽查一致。

### 9.3 性能和隔离

- 使用现有可用设备验证各自实际像素数；
- 尽可能使用三台设备，设备不足时用实际设备加等效模拟显示负载；
- 记录输入、处理、显示发布和实际 paint FPS，而不是只看 GUI 定时器；
- 记录 GUI CPU、内存、温度以及实时页/诊断页差异；
- 确认采集、持久化、电机运动、纯电机扫描和光谱联动未发生回归；
- 若显示性能不足，另行确认降低显示刷新频率，不恢复任何破坏性抽样。

自动测试通过只代表可以部署；真实桌面、真实光谱仪和连续运行验收完成后，才可宣告修复在
ROCK 4B+ 上通过。

## 10. 提交拆分

按以下顺序形成可审查、可回退的提交：

1. `test: specify exact spectrum plotting`：共享规则和两个后端的失败测试；
2. `fix: preserve exact visible spectrum data`：共享模块和双后端最小实现；
3. `fix: separate plot reset from auto range`：缩放状态衔接及测试；
4. `build: sync exact plotting to rock4bplus`：部署副本和部署验证；
5. `docs: package spectrum plotting update`：板端更新、回退和验收说明。

每次提交只暂存计划列出的文件。若实际实现证明两个步骤无法独立通过测试，可以合并相邻
提交，但不得把用户现有修改纳入其中。
