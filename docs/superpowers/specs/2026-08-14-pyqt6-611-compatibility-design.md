# PyQt6 6.11 兼容修复设计

日期：2026-08-14

## 背景

完整回归在 Python 3.13、PyQt6 6.11 环境下得到 419 项通过、7 项失败。其中 1 项是部署副本尚未同步，另外 6 项来自 PyQt6 6.11 的 scoped enum 和 `QPoint`/`QPointF` 类型差异。项目依赖声明为 `PyQt6>=6.7,<7`，因此不能通过固定旧版本规避问题。

## 目标与边界

- 保持 `PyQt6>=6.7,<7` 的既有依赖范围。
- 让现有 Qt5/PySide 风格调用在 PyQt6 6.11 下继续工作。
- 不修改光谱仪采集、处理、保存、电机协议、扫描或安全控制逻辑。
- 不通过修改测试期望掩盖实际兼容问题。

## 方案比较

1. **完善集中兼容层（采用）**：在 `spectrometer/qt.py` 只补充缺失的旧式枚举别名，并在绘图事件入口统一坐标类型。改动集中，兼容当前与后续 PyQt6 6.x。
2. **限制 PyQt6 版本**：短期改动少，但违背现有版本范围，升级后问题会重现。
3. **仅修改测试**：不能保证真实界面路径可用，不采用。

## 设计

### Qt 枚举兼容

沿用 `_install_flat_enum_aliases()` 的现有模式，只在别名不存在时添加以下映射：

- `QtCore.QEvent.KeyPress` → `QtCore.QEvent.Type.KeyPress`
- `QtCore.Qt.NoModifier` → `QtCore.Qt.KeyboardModifier.NoModifier`
- `QtCore.Qt.PreciseTimer` → `QtCore.Qt.TimerType.PreciseTimer`
- `QtWidgets.QDialog.Accepted` → `QtWidgets.QDialog.DialogCode.Accepted`
- `QtWidgets.QDialog.Rejected` → `QtWidgets.QDialog.DialogCode.Rejected`

旧版绑定或已提供平铺别名的版本保持原值，不覆盖、不改变行为。

### 绘图事件坐标

`spectrometer/ui/plot_widget.py` 的 `_event_position()` 继续兼容 `event.position()` 和 `event.pos()` 两种接口，但统一返回 `QtCore.QPointF`。这样 `QRectF.contains()` 在 PyQt6 6.11 中不会收到拒绝隐式转换的 `QPoint`，同时不改变缩放、双击复位或鼠标事件语义。

### 部署副本

代码测试通过后，使用 `tools/build_rock4bplus_deploy.py` 从受控源码重新生成 `deploy/rock4bplus/app`，不手工复制文件。随后运行 `--check` 和部署结构测试，确认源码与板端载荷一致。

## 测试与验收

1. 扩充 `tests/test_qt_compat.py`，直接验证新增别名存在且指向对应 scoped enum。
2. 运行本次失败的输入控件、绘图交互、存储会话和 Y 轴对话框测试。
3. 运行全部自动测试；目标为 426 项全部通过。
4. 生成部署副本并再次运行部署校验及完整回归。
5. 自动测试只证明软件兼容性；ROCK 4B+ 的真实桌面、USB-RS485、电机和光谱仪联动仍按实机验收清单执行。

## 回退

兼容修复单独提交。出现问题时可整体回退该提交；部署更新仍保留板端更新前备份，不对用户配置和采集数据执行覆盖。
