# Radxa ROCK 4B+ Linux 迁移实施计划

日期：2026-07-27  
依据：`docs/superpowers/specs/2026-07-27-rock4bplus-linux-migration-design.md`  
状态：待执行

## 1. 执行原则

1. 只修改 `D:\codex\光谱仪 - Linux转移版`。
2. 不读取实施所不需要的原 Windows 项目文件，不修改 `D:\codex\光谱仪`。
3. 始终保留且不暂存 `CCD_CMOS模块采集卡(下位机)函数表20251027.xlsx`。
4. 不暂存工作区中未跟踪的 `radxa_rock4bp_product_brief.pdf`。
5. 每个代码阶段先添加失败测试，再实现最小改动。
6. 保留协议、采集、绘图和存储核心，不进行无关重构。
7. 每阶段运行针对性测试；进入部署阶段前运行全量测试。
8. `deploy/rock4bplus` 只包含板端运行内容。
9. 自动测试不能替代 ROCK 4B+ 单机实机验收。
10. 任一阶段发现协议、停止或数据完整性回归时，先修复回归再继续。

## 2. 完成指标

- Python 元数据支持 3.11；
- Linux 产品运行时使用 PyQt6；
- 开发环境现有 PySide6 测试链路保持可用；
- 仅自动连接 USB VID:PID `1a86:fe0c`；
- 协议握手仍是确认光谱仪的必要条件；
- 同型号设备使用生产序列号关联身份；
- tty 重新编号后可以重新发现；
- 实时曲线、停止、CSV、Excel 和 `.part` 行为无回归；
- Linux 配置、数据和日志路径可由普通用户写入；
- `deploy/rock4bplus` 内容完整且无开发文件；
- ROCK 4B+ 上完成第一阶段全部验收；
- Excel 和产品简介 PDF 保持未暂存状态；
- 原 Windows 项目保持不变。

## 3. 阶段 0：建立迁移基线

### 任务 0.1：记录工作区保护状态

检查：

```powershell
git status --short --branch
git diff --name-only
```

预期：

- 函数表 Excel 仍是唯一已跟踪的用户修改；
- 产品简介 PDF 仍为未跟踪文件；
- 本轮只新增实施计划。

### 任务 0.2：运行开发环境基线

执行：

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer tools
```

若默认 Python 缺少项目依赖，使用现有可用 venv 执行相同命令。记录解释器、Qt 绑定、测试数量和失败项，不因迁移任务修改现有 venv。

提交：

```text
不提交；仅记录基线
```

## 4. 阶段 1：Qt 绑定和 Python 3.11

### 任务 1.1：为 PyQt6 兼容层添加测试

修改：

- `tests/test_qt_transport.py`
- 新增 `tests/test_qt_compat.py`

覆盖：

- `Signal`、`Slot`、`Property` 映射；
- `QByteArray` 排队调用；
- Qt 6 `exec()`；
- PyQt6 枚举作用域；
- QtSerialPort 模块可用性；
- 当前开发环境使用 PySide6 时原测试不回归。

### 任务 1.2：实现 Linux 产品绑定顺序

修改：

- `spectrometer/qt.py`
- `main.py`

实现：

- 产品代码支持 PySide6 和 PyQt6；
- Linux 环境优先使用 PyQt6；
- Windows 开发环境保留 PySide6 优先；
- 删除 PyQt5 作为 Linux 发布依赖；
- 64 位检查改为平台无关提示；
- `--ports` 示例和 metavar 改为 tty 语义；
- 字体使用 Noto Sans CJK 或系统回退，不硬编码 Microsoft YaHei UI。

### 任务 1.3：调整项目元数据

修改：

- `pyproject.toml`
- `requirements.txt`
- `README.md`

实现：

- `requires-python` 包含 Python 3.11；
- 根依赖继续服务开发环境，板端依赖另行维护；
- README 明确该副本为 Linux 专用版本；
- 移除 Windows 打包和 COM 口作为主要运行说明；
- 文档明确 Debian 12 板端采用 PyQt6 的原因。

验证：

```powershell
python -m pytest -q tests/test_qt_transport.py tests/test_ui_smoke.py
python -m compileall -q main.py spectrometer
```

建议提交：

```text
feat(linux): add PyQt6 and Python 3.11 runtime compatibility
```

## 5. 阶段 2：精确设备发现

### 任务 2.1：建立 USB 身份筛选测试

新增：

- `tests/test_linux_device_discovery.py`

覆盖：

- `1a86:fe0c` 被接受；
- `1a86` 的其他 PID 被拒绝；
- 其他已知 USB 串口 VID 被拒绝；
- 描述含 `CH340` 但 VID:PID 不匹配时被拒绝；
- VID/PID 缺失时不作为正式自动连接候选；
- `/dev/ttyACM0` 和 `/dev/ttyACM1` 都能作为候选位置；
- `system_location` 和 `port_name` 被规范化但不混为设备身份。

### 任务 2.2：实现 CH569W 候选模型

修改：

- `spectrometer/communication/serial_port.py`
- `spectrometer/ui/main_window.py`

实现：

- 定义目标 VID 和 PID 常量；
- `DeviceFinder` 返回稳定的候选结构；
- 自动扫描严格匹配 `1a86:fe0c`；
- 日志包含 port name、system location、VID、PID、USB 序列号和描述；
- `--ports` 仅作为诊断 allowlist，不绕过 VID:PID 和握手。

验证：

```powershell
python -m pytest -q tests/test_linux_device_discovery.py tests/test_device_handshake.py
```

建议提交：

```text
feat(device): identify CH569W by exact USB identity
```

## 6. 阶段 3：生产序列号身份和 tty 重新绑定

### 任务 3.1：建立设备身份测试

新增或修改：

- `tests/test_device_identity.py`
- `tests/test_device_handshake.py`

覆盖：

- 握手前只按当前端口管理候选；
- 握手后以非空生产序列号建立身份索引；
- 相同端口重复扫描不创建 worker；
- 相同生产序列号不创建第二个已识别设备；
- 设备从 ttyACM0 变为 ttyACM1 后更新当前连接位置；
- 空生产序列号保留设备并标记身份不确定；
- 重复生产序列号保留诊断信息且不静默合并；
- 过期 probe timer 不删除已恢复设备。

### 任务 3.2：分离物理身份与连接位置

修改：

- `spectrometer/device/spectrometer.py`
- `spectrometer/device/device_manager.py`
- `spectrometer/communication/serial_port.py`

实现：

- 设备模型区分生产序列号和当前 tty 位置；
- 设备管理器维护生产序列号索引；
- 候选连接成功并读取序列号后完成身份关联；
- 重绑定时清理旧 worker/thread 后再创建新 worker；
- 扫描和延迟重连使用 generation/token 防止重复任务；
- 现有设备 ID 和曲线颜色尽量保持稳定。

### 任务 3.3：补充诊断展示

修改：

- `spectrometer/ui/diagnostics.py`
- `spectrometer/ui/device_sidebar.py`
- `spectrometer/ui/main_window.py`

实现：

- 显示当前 tty、VID:PID、生产序列号和重连次数；
- 空或重复生产序列号显示明确警告；
- 不把 tty 路径当作永久设备名称。

验证：

```powershell
python -m pytest -q tests/test_device_identity.py tests/test_device_handshake.py tests/test_device_manager_ack.py
```

建议提交：

```text
feat(device): reconnect CH569W devices by protocol serial
```

## 7. 阶段 4：断线状态和存储收尾

### 任务 4.1：建立断线会话测试

新增或修改：

- `tests/test_device_reconnect.py`
- `tests/test_main_window_acquisition.py`
- `tests/test_storage_session_manager.py`
- `tests/test_spool.py`

覆盖：

- 空设备启动后热插入；
- 采集中资源错误释放采集状态；
- 断线只结束当前会话一次；
- 已完成帧进入尾批；
- 不完整帧不写入；
- `.part` 在导出失败时保留；
- 重连后保持就绪但不自动开始；
- 重连后的帧进入新会话；
- 多个延迟重连回调不能重复打开同一端口。

### 任务 4.2：统一断线收尾

修改：

- `spectrometer/device/device_manager.py`
- `spectrometer/acquisition/controller.py`
- `spectrometer/acquisition/coordinator.py`
- `spectrometer/storage/session_manager.py`
- `spectrometer/ui/main_window.py`

实现：

- 串口资源错误和扫描消失进入同一断线入口；
- 清除 probing、initializing 和 acquiring 状态；
- 采集控制器收到设备离线事件；
- 存储协调器异步完成当前设备尾批；
- GUI 立即恢复可操作；
- 重连后不复用旧会话。

验证：

```powershell
python -m pytest -q tests/test_device_reconnect.py tests/test_main_window_acquisition.py tests/test_storage_session_manager.py tests/test_spool.py
```

建议提交：

```text
fix(reconnect): finalize acquisition safely on USB disconnect
```

## 8. 阶段 5：Linux 用户路径和界面适配

### 任务 5.1：建立 XDG 路径测试

修改：

- `tests/test_settings_service.py`
- 新增 `tests/test_linux_paths.py`

覆盖：

- 配置默认位于 `XDG_CONFIG_HOME`；
- 未设置 XDG 时回退到 `~/.config`；
- 数据默认位于 `~/ZGCAI-Spectrometer-Data`；
- 路径可创建且不指向 `/opt`；
- 用户自定义绝对路径继续有效；
- Windows `APPDATA` 不再决定 Linux 路径。

### 任务 5.2：实现 Linux 默认路径和日志

修改：

- `spectrometer/services/settings_service.py`
- `spectrometer/ui/main_window.py`
- 新增 `spectrometer/services/platform_paths.py`

实现：

- 集中定义配置、数据和日志路径；
- 设置文件继续原子写入；
- 首次启动创建默认数据目录；
- 启动和依赖错误可写入用户日志；
- `shutil.disk_usage` 使用实际输出目录。

### 任务 5.3：适配字体和低分辨率

修改：

- `main.py`
- `spectrometer/ui/styles.qss`
- 受影响的 UI 布局文件
- `tests/test_ui_smoke.py`

实现：

- 使用 Noto Sans CJK 和系统字体回退；
- 1920×1080 保持正常布局；
- 1366×768 下核心按钮、设备卡、曲线和保存设置可访问；
- 不为更低分辨率承诺完整布局。

验证：

```powershell
python -m pytest -q tests/test_settings_service.py tests/test_linux_paths.py tests/test_ui_smoke.py
```

建议提交：

```text
feat(linux): use XDG paths and Debian desktop defaults
```

## 9. 阶段 6：板端部署目录

### 任务 6.1：建立部署清单和一致性校验

新增：

- `tools/build_rock4bplus_deploy.py`
- `tests/test_rock4bplus_deploy.py`
- `deploy/rock4bplus/app/main.py`
- `deploy/rock4bplus/app/spectrometer/`

实现：

- 构建工具只复制运行所需源码；
- 测试校验根源码与部署副本内容一致；
- 排除测试、工具、文档、数据、缓存、spec、Excel 和 PDF；
- 检查部署目录不存在 `.pyc`、`__pycache__`、venv、build 或 dist；
- 构建过程不得删除或改写工作区根目录文件。

### 任务 6.2：编写板端依赖清单

新增：

- `deploy/rock4bplus/requirements-rock4bplus.txt`

内容：

- NumPy；
- SciPy；
- XlsxWriter；
- openpyxl。

要求：

- 使用已在 Debian 12 ARM64 Python 3.11 实机安装验证的版本范围；
- 不列入 PyQt6；
- 不列入开发和测试依赖。

### 任务 6.3：编写 udev 规则

新增：

- `deploy/rock4bplus/99-zgcai-spectrometer.rules`

实现：

- 只匹配 tty 子系统下 `1a86:fe0c`；
- 设置 `dialout` 组访问权限；
- 不创建公共唯一软链接；
- 不使用 `MODE="0666"`。

### 任务 6.4：编写安装脚本

新增：

- `deploy/rock4bplus/install.sh`

实现：

- `set -euo pipefail`；
- 校验 Debian 12、aarch64、Python 3.11；
- apt 安装 venv、PyQt6、QtSerialPort、Qt/XCB/OpenGL 和字体；
- 安装到 `/opt/zgcai-spectrometer`；
- 创建 `--system-site-packages` venv；
- pip 安装板端 requirements；
- 安装 udev 规则并 reload/trigger；
- 将调用用户加入 `dialout`；
- 安装 XDG 菜单和桌面快捷方式；
- 最终运行导入自检；
- 任一步失败返回非零；
- 重复执行保持幂等；
- 不配置开机自启。

### 任务 6.5：编写启动脚本和快捷方式

新增：

- `deploy/rock4bplus/run.sh`
- `deploy/rock4bplus/zgcai-spectrometer.desktop`

实现：

- 使用 `/opt/zgcai-spectrometer/.venv/bin/python`；
- 工作目录固定为安装目录；
- 创建用户配置、日志和数据目录；
- stderr 写入用户日志；
- `.desktop` 使用绝对 Exec 路径；
- 只手动启动；
- 普通用户运行时不调用 sudo。

验证：

```powershell
python -m pytest -q tests/test_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
```

建议提交：

```text
feat(deploy): add ROCK 4B+ source and venv deployment
```

## 10. 阶段 7：全量回归和静态检查

执行：

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer tools
python tools/build_rock4bplus_deploy.py --check
git diff --check
git status --short
```

人工核对：

- 没有 Windows 项目路径写入产品代码；
- 没有将 PySide6 加入板端 requirements；
- 没有公共 `/dev/zgcai-spectrometer` 软链接；
- 没有将 tty 路径作为永久身份；
- 没有自动恢复采集；
- 没有开机自启；
- deploy 目录内容符合规格；
- Excel 和 PDF 未暂存。

建议提交：

```text
test(linux): complete ROCK 4B+ migration regression coverage
```

## 11. 阶段 8：ROCK 4B+ 安装验证

### 任务 8.1：发送部署目录

只发送：

```text
deploy/rock4bplus
```

不得发送工作区 venv、测试、文档、Excel、PDF、build、dist 或 data。

### 任务 8.2：执行安装

在板端记录：

```bash
cat /etc/os-release
uname -m
python3 --version
ldd --version
```

运行：

```bash
cd rock4bplus
sudo ./install.sh
```

安装完成后注销并重新登录，再重新插拔光谱仪。

检查：

```bash
id
lsusb
ls -l /dev/ttyACM*
/opt/zgcai-spectrometer/.venv/bin/python -c "from PyQt6 import QtCore, QtWidgets, QtSerialPort; print(QtCore.QT_VERSION_STR)"
```

### 任务 8.3：桌面和分辨率验证

- 从应用菜单启动；
- 从桌面快捷方式启动；
- 命令行运行 `run.sh`；
- 检查 1920×1080；
- 如能设置 1366×768，检查核心操作可访问；
- 确认普通用户无需 sudo。

## 12. 阶段 9：单台 CH569W 实机验收

### 任务 9.1：识别、握手和参数

- 无设备启动保持等待状态；
- 插入设备后自动发现；
- 核对 `1a86:fe0c`；
- 记录 `/dev/ttyACM*`；
- 完成版本握手；
- 读取固件、生产序列号、像素数、起始像素、有效像素、校准系数、积分时间、触发模式、平均次数、增益和延时；
- 确认初始化完成前无法采集。

### 任务 9.2：实时采集和停止

- 开始连续采集；
- 检查实时曲线；
- 记录显示 FPS 和诊断计数；
- 连续执行多轮开始/停止；
- 检查停止 ACK、尾帧和 GUI 响应；
- 确认停止后状态回到空闲。

### 任务 9.3：CSV 和 Excel

- 启用 CSV+Excel；
- 生成完整批；
- 生成不足一批的尾批；
- 重新打开全部文件；
- 核对 Pixel、Wavelength、帧列、像素行数和帧数；
- 确认没有覆盖已有文件；
- 正常结束后无残留 `.part`。

### 任务 9.4：断线重连

- 采集中拔出 USB；
- 确认 GUI 不冻结；
- 确认当前会话安全结束；
- 检查尾批或 `.part`；
- 重新插入设备；
- 若 tty 号变化，记录旧、新路径；
- 确认通过生产序列号恢复同一设备身份；
- 确认没有自动恢复采集；
- 手动开始新采集并生成独立文件。

### 任务 9.5：形成验收报告

新增：

- `docs/hardware-validation-report-2026-07-27-rock4bplus.md`

报告包含：

- 系统和依赖版本；
- USB、udev 和权限；
- 握手和参数；
- 采集与停止；
- CSV/Excel 结构；
- 断线和重连耗时；
- tty 重新编号结果；
- 屏幕分辨率；
- 未通过项及复测结果。

建议提交：

```text
docs: record ROCK 4B+ single-device validation
```

## 13. 最终交付检查

```powershell
git log --oneline --decorate -10
git status --short
git diff --name-only HEAD^
```

最终状态必须满足：

- 只有用户 Excel 和产品简介 PDF 保持在预期的未提交状态；
- 所有 Linux 迁移代码已提交；
- `deploy/rock4bplus` 可直接发送到板子；
- 实机验收报告已提交；
- 原 Windows 项目未被修改；
- 第一阶段七项验收全部通过。
