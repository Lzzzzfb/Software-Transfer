# LK-MD2202 实机反馈修复实施计划

## 1. 基线与保护

- 设计规格：`docs/superpowers/specs/2026-08-17-lk-md2202-real-machine-feedback-fixes-design.md`
- 设计提交：`fbec21a`
- 代码回退基线：`f8b6cc3`
- 分支：`codex/lk-md2202-integration`
- 完整测试参考：427 passed

只修改本计划列出的源码、测试、文档和由构建脚本生成的部署副本。不得暂存或改动工作区中用户已有的 Office、CSV、PDF、诊断包和 `tmp/`资料。

## 2. 阶段一：锁定失败行为

先修改测试：

- `tests/motor/test_motor_panel.py`
- `tests/motor/test_motor_controller.py`
- `tests/motor/test_scan_controller.py`

必要时补充主窗口测试文件中的无设备入口用例。

新增失败测试：

1. 运动按钮持有焦点时进入运动状态，“X 行程”不得获得焦点或自动全选；
2. 脱离卡死停止后第一次状态仍为运行时，不得立即进入安全锁；
3. 补发位置急停后确认停止，应结束动作、保持未校准并允许机械回零；
4. 状态读取失败后允许有界兜底确认；持续运行或持续无法读取才锁定；
5. 空 `device_ids`可以启动纯电机扫描；
6. 纯电机扫描不调用采集开始、停止或重置，不生成扫描清单；
7. 纯电机扫描保持扫描、返回、多轮、停止和故障语义；
8. 纯电机扫描事件进入现有诊断记录；
9. 有光谱仪时原联动扫描测试保持不变。

定向运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_motor_panel.py tests/motor/test_motor_controller.py tests/motor/test_scan_controller.py
```

预期先因新规则未实现而失败。

## 3. 阶段二：焦点和停止确认

修改：

- `spectrometer/ui/motor_panel.py`
- `spectrometer/motor/controller.py`

实现：

1. `MotorPanel.set_motion_active()`只在空闲到运动的转换中清除即将禁用的手动操作按钮焦点；
2. 不永久取消按钮键盘焦点，不影响扫描输入框正常编辑；
3. 脱离卡死写速度 0 后短暂等待再读取状态；
4. 第一次仍运行或读取失败时补发一次位置急停，并进行有限次数状态复核；
5. 确认停止后使用原始业务原因结束，保持轴未校准；
6. 只有复核截止后仍无法确认停止才进入现有安全锁；
7. 主动速度运行时间仍不超过1秒/4800 pulse理论上限。

定向验证后提交：

```text
fix: stabilize motor focus and stall stop confirmation
```

## 4. 阶段三：纯电机扫描与日志

修改：

- `spectrometer/motor/scan_controller.py`
- `spectrometer/ui/main_window.py`
- `spectrometer/ui/motor_panel.py`
- 必要时扩展扫描事件模型，但不创建第二套日志系统

实现：

1. 扫描配置允许 `device_ids=()`，并以布尔属性区分是否启用光谱采集；
2. 纯电机模式直接开始扫描运动，每轮结束后直接返回起点；
3. 纯电机停止或故障不等待光谱仪任务；
4. 纯电机模式不启动 `ScanManifestWriter`，完成信号的清单路径为 `None`；
5. 扫描控制器发出结构化事件，主窗口写入现有 `DiagnosticRecorder` 时间线并显示可读日志；
6. 无光谱仪时主窗口不拒绝扫描，也不调用采集重置；
7. 有光谱仪时原先启动采集、扫描、停止保存、返回的顺序不变；
8. 更新扫描分组标题、按钮和纯电机状态提示。

定向验证后提交：

```text
feat: allow logged motor-only scans
```

## 5. 阶段四：文档与完整回归

按实现结果更新：

- `docs/motor/lk-md2202-host-validation.md`
- `docs/motor/rock4bplus-acceptance-checklist.md`
- 必要的用户操作说明

运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor
python -m pytest -q
python -m compileall -q main.py spectrometer tools
git diff --check
```

完整测试不得通过修改或清理用户资料来修复。若出现与本任务无关的既有失败，记录证据并单独判断。

## 6. 阶段五：ROCK 4B+ 部署镜像

运行：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
python -m compileall -q deploy/rock4bplus/app
python -m pytest -q tests/test_rock4bplus_deploy.py
```

检查部署镜像只来自主源码，不包含测试、Office文件、用户数据、诊断包或临时目录。部署镜像单独提交：

```text
build: refresh ROCK 4B+ real-machine feedback fixes
```

## 7. 板端更新与回退

在 ROCK 4B+ 上更新前：

1. 停止当前软件；
2. 将更新上传到 `/home/radxa/`的新目录；
3. 对 `/opt/zgcai-spectrometer` 中所有待替换文件创建时间戳备份；
4. 把备份路径写入 `/home/radxa/zgcai-last-backup.txt`；
5. 安装明确文件并执行板端 `py_compile`；
6. 先运行只读 LK-MD2202 探针，再启动 GUI；
7. 按设计规格中的实机顺序验证焦点、脱离卡死、纯电机扫描和联动扫描。

任何关键验证失败时，使用记录的备份恢复对应文件，不覆盖或删除原光谱仪软件。
