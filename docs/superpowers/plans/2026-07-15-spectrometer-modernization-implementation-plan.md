# ZGCAI 光谱仪上位机优化与完善实施计划

日期：2026-07-15
依据：`docs/superpowers/specs/2026-07-15-spectrometer-modernization-design.md`
状态：可执行

## 1. 执行原则

1. 先建立现有工程基线，再修改产品代码。
2. 每一阶段先写失败测试或可重复验证，再实现功能。
3. 每个阶段独立提交，提交前运行该阶段测试和全量回归测试。
4. 协议、采集、存储和处理逻辑必须可在无硬件环境下测试。
5. 实机只用于验证下位机行为和端到端性能，不用实机代替单元测试。
6. 不提交 `build/`、`dist/`、`data/`、`.venv/`、缓存或可视化伴侣文件。
7. 不覆盖用户已有采集数据和说明材料。

## 2. 阶段 0：版本基线和开发环境

### 任务 0.1：提交批准后的规范与实施计划

修改：

- `docs/superpowers/specs/2026-07-15-spectrometer-modernization-design.md`
- `docs/superpowers/plans/2026-07-15-spectrometer-modernization-implementation-plan.md`

验证：

```powershell
git diff --cached --check
git status --short
```

提交：

```text
docs: approve design and add implementation plan
```

### 任务 0.2：提交现有工程基线

纳入基线：

- `main.py`
- `spectrometer/**/*.py`
- `requirements.txt`
- `README.md`
- `spectrometer.spec`
- `ZGCAI光谱仪控制软件.spec`
- 协议、需求和界面参考文件

不纳入基线：

- `.claude/`
- Office 临时锁文件
- 构建、运行数据和缓存目录

验证：

```powershell
git status --short
python -m compileall -q main.py spectrometer
```

提交：

```text
chore: capture legacy spectrometer application baseline
```

### 任务 0.3：建立 Python 3.12 64 位环境

新增或修改：

- `pyproject.toml`
- `requirements.txt`
- `requirements-dev.txt`

运行依赖：

- PySide6 / Qt SerialPort
- pyqtgraph
- NumPy
- SciPy
- XlsxWriter

开发依赖：

- pytest
- pytest-qt
- coverage

验证：

```powershell
python -c "import struct; print(struct.calcsize('P') * 8)"
python -c "import PySide6, pyqtgraph, numpy, scipy, xlsxwriter"
python -m pytest --collect-only -q
```

## 3. 阶段 1：协议与字节流解析

### 任务 1.1：建立协议黄金测试

新增：

- `tests/communication/test_protocol_packets.py`
- `tests/communication/test_data_packets.py`
- `tests/fixtures/protocol_frames.py`

覆盖：

- 普通指令构建和累加和。
- 版本、状态和参数响应。
- `0x80` 的特殊长度规则。
- U32 小端包号和 U16 大端像素。
- 0、1、2048 和 4096 像素边界。
- 数据校验字节任意但必须被消费。
- 校准系数现有反序行为。
- 触发枚举 `0/1/2`。

验证：

```powershell
python -m pytest tests/communication/test_protocol_packets.py tests/communication/test_data_packets.py -q
```

### 任务 1.2：重构协议编码器

修改：

- `spectrometer/communication/protocol.py`

实现：

- 命令枚举和参数类型集中定义。
- 普通包构建、解析和状态码解释。
- 版本信息 40 字节数据解析。
- 参数范围验证和明确异常类型。
- 校准收发反序保持不变。

### 任务 1.3：新增增量字节流解码器

新增：

- `spectrometer/communication/frame_decoder.py`
- `tests/communication/test_frame_decoder.py`

实现：

- 半包和任意分块。
- 连续粘包。
- 帧头前噪声。
- 普通帧总长 `4 + nLength`。
- `0x80` 总长 `3 + nLength`。
- 最大长度保护和重同步。
- 错误事件不阻断后续有效帧。

验证：

```powershell
python -m pytest tests/communication -q
```

提交：

```text
fix: implement protocol-correct incremental frame decoding
```

## 4. 阶段 2：领域模型、设备和采集协调

### 任务 2.1：建立不可变领域对象

新增：

- `spectrometer/domain/__init__.py`
- `spectrometer/domain/models.py`
- `spectrometer/domain/enums.py`
- `tests/domain/test_models.py`

对象：

- `SpectrumFrame`
- `DeviceInfo`
- `DeviceConfig`
- `AcquisitionSession`
- `SpectrumReference`
- `DeviceState`

### 任务 2.2：迁移串口传输层到 PySide6

重写：

- `spectrometer/communication/serial_port.py`

新增测试替身：

- `spectrometer/communication/fake_transport.py`
- `tests/communication/test_serial_transport.py`

要求：

- 每设备独立线程。
- 线程只收发字节和运行解码器。
- 通过信号发送不可变帧和结构化错误。
- 不共享 `latest_pixels` 可变属性。
- 断线保留端口信息并触发退避重连。

### 任务 2.3：建立设备服务和状态机

新增或重写：

- `spectrometer/services/device_service.py`
- `spectrometer/device/device_manager.py`
- `tests/services/test_device_service.py`

覆盖：

- 发现、连接、初始化、验证和重新连接。
- 同一端口去重和失败后再次扫描。
- 查询全部支持的参数。
- 参数只有在 ACK 后才更新为已生效值。
- 按起始波长排序。

### 任务 2.4：建立无损采集协调器

重写：

- `spectrometer/acquisition/engine.py`

新增：

- `spectrometer/acquisition/coordinator.py`
- `spectrometer/acquisition/sequence_tracker.py`
- `tests/acquisition/test_coordinator.py`
- `tests/acquisition/test_sequence_tracker.py`

覆盖：

- 单次、连续、等待外触发、停止。
- 软件同步。
- 总内硬同步布防顺序。
- 总外硬同步只布防和接收。
- U32 高 24 位帧序号缺口、重复和回绕。
- 存储通道接收全部帧，显示通道只接收最新快照。

验证：

```powershell
python -m pytest tests/domain tests/services tests/acquisition tests/communication -q
```

提交：

```text
feat: add stateful multi-device acquisition core
```

## 5. 阶段 3：缓存、CSV、Excel 和恢复

### 任务 3.1：实现版本化临时缓存

新增：

- `spectrometer/storage/spool.py`
- `spectrometer/storage/recovery.py`
- `tests/storage/test_spool.py`
- `tests/storage/test_recovery.py`

实现：

- `.part` 头部版本和会话信息。
- 设备、包号、时间戳、像素长度和 U16 像素记录。
- 每条记录 CRC32。
- 截断尾记录恢复。
- 旧缓存扫描和恢复报告。

### 任务 3.2：实现批量 CSV

新增或重写：

- `spectrometer/storage/csv_exporter.py`
- `tests/storage/test_csv_exporter.py`

布局：

- 每台设备每批一个 CSV。
- `Pixel`、`Wavelength` 后每帧一列。
- 元数据和帧标题包含包号、时间和采集参数。

### 任务 3.3：实现多工作表 Excel

新增：

- `spectrometer/storage/xlsx_exporter.py`
- `tests/storage/test_xlsx_exporter.py`

布局：

- `采集概要`工作表。
- 每台设备独立工作表。
- 每帧一列、每像素一行。
- 不同波段、像素数和帧数互不复用坐标列。

### 任务 3.4：建立存储协调器

重写：

- `spectrometer/storage/exporter.py`

新增：

- `spectrometer/storage/coordinator.py`
- `tests/storage/test_storage_coordinator.py`

要求：

- 批次范围 1–1000，默认 500。
- 默认 `CSV + Excel`，可选仅 CSV 或仅 Excel。
- 队列按每活动设备 4 批容量；50% 警告、90% 受控停止。
- CSV 优先，Excel 失败可重试。
- 中文路径和文件占用错误可诊断。

验证：

```powershell
python -m pytest tests/storage -q
```

提交：

```text
feat: add recoverable batch storage and workbook export
```

## 6. 阶段 4：光谱处理和参考数据

### 任务 4.1：建立受限公式解析器

新增：

- `spectrometer/processing/formula.py`
- `tests/processing/test_formula.py`

支持：

- `I`、`Idark`、`Ib`、`I0`、`x`。
- `+ - * / **`、正负号和括号。
- `log10`、`log`、`loge`、`abs`、`sqrt`。

拒绝：

- 属性访问、下标、导入、未知名称和任意函数调用。
- 形状不匹配和非有限结果。

### 任务 4.2：重构处理管线

新增或重写：

- `spectrometer/processing/processor.py`
- `spectrometer/device/spectrometer.py`
- `spectrometer/extensions/airpls.py`
- `tests/processing/test_processor.py`
- `tests/processing/test_airpls.py`

覆盖：

- 原始数据、强度校准、扣背景和吸光度。
- 零分母和非有限值。
- airPLS 后台处理和最新任务替换。
- 原始与处理后数据分离。

### 任务 4.3：建立背景和参考数据仓库

新增：

- `spectrometer/processing/references.py`
- `tests/processing/test_references.py`

要求：

- 按生产序列号和像素配置匹配。
- 默认最新兼容记录。
- 支持历史选择和独立保存。
- 禁止跨设备静默套用。

验证：

```powershell
python -m pytest tests/processing -q
```

提交：

```text
feat: make spectrum processing safe and traceable
```

## 7. 阶段 5：PySide6 主界面

### 任务 5.1：应用入口和主题

修改：

- `main.py`
- `spectrometer/ui/theme.py`
- `spectrometer/ui/styles.qss`

实现：

- PySide6 应用入口。
- Windows 高 DPI。
- 现代蓝白工业主题。
- `--simulate` 无硬件演示入口。

### 任务 5.2：主窗口结构

重写或新增：

- `spectrometer/ui/main_window.py`
- `spectrometer/ui/ribbon.py`
- `spectrometer/ui/device_sidebar.py`
- `spectrometer/ui/status_panel.py`
- `tests/ui/test_main_window.py`

实现：

- 顶部主 Ribbon。
- 上下文工具带。
- 左侧总控与设备卡片。
- 中央实时、历史和诊断标签。
- 底部帧率、包缺口、存储队列和磁盘状态。

### 任务 5.3：绘图组件

重写：

- `spectrometer/ui/plot_widget.py`
- `tests/ui/test_plot_widget.py`

要求：

- 约 30 fps 最新帧刷新。
- 缩放状态保持。
- 实时、对比和基线曲线独立管理。
- 最多 64 条对比曲线。
- 图例显隐、十字光标和坐标提示。

### 任务 5.4：参数、设置和历史页面

重写或新增：

- `spectrometer/ui/device_parameters.py`
- `spectrometer/ui/settings_dialog.py`
- `spectrometer/ui/history_viewer.py`
- `spectrometer/ui/diagnostics.py`
- `tests/ui/test_dialogs.py`

实现：

- 显式单次/连续选择。
- 触发、同步、主机和延时配置。
- 背景/参考状态和历史选择。
- 至少 12 个历史标签、每标签最多 64 条曲线。
- 结构化错误和诊断日志。

验证：

```powershell
python -m pytest tests/ui -q
python main.py --simulate
```

提交：

```text
feat: rebuild the desktop UI with PySide6
```

## 8. 阶段 6：功能集成与设置持久化

新增或修改：

- `spectrometer/services/settings_service.py`
- `spectrometer/services/session_service.py`
- `spectrometer/ui/main_window.py`
- `tests/integration/test_simulated_session.py`

完成：

- 全部参数查询与设置。
- 强度校准参数读写。
- 外触发状态查询和手动 `0x54` 诊断。
- 自动/手动存储和文件命名选项。
- 背景、参考和处理结果导出。
- 关闭程序时安全停止、排空存储队列和保存设置。

验证：

```powershell
python -m pytest tests/integration -q
python -m pytest -q
```

提交：

```text
feat: complete acquisition workflows and persistence
```

## 9. 阶段 7：性能、实机和发布

### 任务 7.1：模拟压力测试

新增：

- `tests/performance/test_acquisition_throughput.py`
- `tools/run_simulation.py`

场景：

- 4 台设备。
- 每台 4096 像素、100 fps。
- 绘图 30 fps。
- 批次 500 帧。
- 持续运行并检查内存、队列和帧数。

### 任务 7.2：实机验收

需要用户连接设备时提出请求。按设计规范第 11.3 节执行，保存原始日志和帧数核对结果，不提交用户采集数据。

### 任务 7.3：Windows 64 位打包

新增或修改：

- `spectrometer.spec` 或 PySide6 部署配置
- `README.md`
- `docs/user-guide.md`

验证：

- 在干净目录启动程序。
- 自动发现和手动连接设备。
- Qt 平台插件和串口模块可用。
- CSV 和 Excel 可生成并打开。
- 关闭后无残留采集线程或未完成缓存。

最终命令：

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer
```

最终提交：

```text
release: complete spectrometer workstation modernization
```

## 10. 完成条件

只有同时满足以下条件才算完成：

1. 全量自动测试通过。
2. 模拟 4 设备压力测试无静默丢帧和无界内存增长。
3. 实机完成协议、采集、同步、处理和存储验收。
4. CSV、Excel 和恢复结果经过帧数及代表性像素核对。
5. Windows 64 位程序可以独立启动、连接设备并安全退出。
6. 用户文档说明连接、采集、同步、处理、存储、恢复和诊断操作。
