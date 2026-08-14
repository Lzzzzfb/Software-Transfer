# PyQt6 6.11 兼容修复实施计划

日期：2026-08-14

设计依据：`docs/superpowers/specs/2026-08-14-pyqt6-611-compatibility-design.md`

目标分支：`codex/lk-md2202-integration`

实施前提交：`e903493 docs: design PyQt6 6.11 compatibility fix`

## 1. 范围与保护

1. 只修改 Qt 兼容层、绘图事件坐标入口、对应测试和构建脚本生成的部署副本。
2. 不修改光谱仪采集、处理、保存、电机协议、扫描或安全控制逻辑。
3. 不暂存或清理用户现有 Office 文件、CSV/XLSX、PDF、`tmp/`和诊断包。
4. 源码修复与部署副本分别提交，便于独立回退。

## 2. 失败测试

修改 `tests/test_qt_compat.py`，验证 PyQt6 环境提供以下平铺别名：

- `QEvent.KeyPress`
- `Qt.NoModifier`
- `Qt.PreciseTimer`
- `QDialog.Accepted`
- `QDialog.Rejected`

现有 `tests/test_plot_interaction.py` 继续作为 `QPoint` 到 `QPointF` 兼容的失败用例。先运行相关测试，确认修复前失败。

## 3. 最小实现

1. 在 `spectrometer/qt.py` 的既有映射表中添加缺失别名，只在属性不存在时设置。
2. 在 `spectrometer/ui/plot_widget.py::_event_position()` 中统一返回 `QPointF`。
3. 不添加版本字符串判断，不在业务层散布 PyQt6 分支。

## 4. 分层验证

依次运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/test_qt_compat.py tests/test_input_controls.py tests/test_plot_interaction.py tests/test_storage_session_manager.py tests/test_y_axis_dialog.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q main.py spectrometer tools
git diff --check
```

源码阶段允许部署一致性测试单独失败，原因必须仅为部署副本尚未同步；其余测试必须通过。

预期源码提交：

```text
fix: support PyQt6 6.11 enum and point types
```

## 5. 部署副本

使用构建脚本生成，不手工复制：

```powershell
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py
.\.venv\Scripts\python.exe -m compileall -q deploy/rock4bplus/app
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py
.\.venv\Scripts\python.exe tools/build_rock4bplus_deploy.py --check
.\.venv\Scripts\python.exe -m pytest -q tests/test_rock4bplus_deploy.py
.\.venv\Scripts\python.exe -m pytest -q
```

部署目录语法编译会生成 `__pycache__`；编译后再次运行构建脚本，以恢复不含缓存的最小部署载荷，再执行纯净性测试。

新增兼容层测试后目标为 427 项全部通过，部署目录不包含测试、文档、Office 文件、用户数据、诊断包或缓存。

预期部署提交：

```text
build: refresh ROCK 4B+ PyQt6 compatibility deploy
```

## 6. 发布与实机边界

完整回归通过后才推送 `codex/lk-md2202-integration`。自动测试不替代 ROCK 4B+ 真实桌面、USB-RS485、电机与光谱仪联动验收；板端更新继续使用上传临时目录、时间戳备份、精确安装、语法校验和可回退流程。
