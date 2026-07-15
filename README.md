# ZGCAI 光谱仪采集与分析工作站

面向 Windows 64 位的多设备光谱仪上位机。产品界面使用 PySide6/Qt Widgets，支持串口采集、同步布防、实时处理、批量 CSV/Excel、断点缓存恢复、历史查看和诊断。

## 已实现能力

- 协议普通帧与 `0x80` 数据帧分别按正确长度拆包。
- `nPacketNumb` 自动兼容实机 U16LE 与函数表 U32LE，像素按 U16 大端解析。
- 最多按当前目标同时使用约 4 台设备；显示约 30 fps，存储通道接收每一帧。
- 实机 16 位与新格式 24 位帧序号缺口、重复、乱序和回绕诊断。
- 单次、连续、软件同步、内部硬同步和外部硬同步布防。
- 原始、强度校准、扣背景、吸光度、airPLS 和受限自定义公式。
- 默认每 500 帧一批，可设置 1–1000；默认同时输出 CSV 和 Excel。
- CSV 每台设备一个文件；Excel 一个设备一个工作表。
- 所有批量文件均为每帧一列、每像素一行。
- `.part` 临时缓存包含版本、元数据和逐帧 CRC32，可恢复截断尾记录。
- 现代化双层工具带、设备侧栏、实时/历史/诊断页和状态栏。
- `--simulate` 可在无光谱仪时模拟 4 台 4096 像素设备。

## 环境

- Windows 10/11 64 位
- Python 3.12 64 位
- 依赖见 `requirements.txt`

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py --simulate
```

连接实机运行：

```powershell
.venv\Scripts\python main.py
```

只连接指定串口（推荐用于多个同型 USB 串口并存的电脑）：

```powershell
.venv\Scripts\python main.py --ports COM14 COM17 COM18
```

## 自动测试

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer
```

## 64 位打包

在安装 PySide6 和 PyInstaller 的 Python 3.12 64 位环境中：

```powershell
python -m PyInstaller --clean --noconfirm spectrometer.spec
```

输出位于 `dist/ZGCAI_Spectrometer_Workstation/`。当前开发机未安装 PySide6，因此打包执行留到发布环境验证；配置已迁移为 PySide6，并显式包含 Qt SerialPort。

## 协议要点

普通帧：

```text
0x24 | nLength(U16LE) | Cmd | Params | Checksum
物理总长度 = 4 + nLength，nLength 不含校验码
```

数据帧（函数表/新版格式）：

```text
0x24 | nLength(U16LE) | 0x80 | nPacketNumb(U32LE) | Data(U16BE[]) | Checksum
nLength = 6 + 2*N，物理总长度 = 3 + nLength
```

数据帧的校验字节会被消费，但按现有下位机约定不验证其数值。波长校准系数收发顺序继续沿用原工程正确的反序逻辑：下位机为 `C4,C3,C2,C1`，上位机模型为 `C1,C2,C3,C4`。

实机固件 2.2/2.3 还存在兼容格式：`U16LE` 包号、校验码不计入长度，
`nLength=3+2*N`、物理总长度为 `4+nLength`。解析器按 `nLength`
奇偶性自动兼容两种格式。

详细操作见 [用户指南](docs/user-guide.md)，实机结果见 [2026-07-15 三机联调报告](docs/hardware-validation-report-2026-07-15.md)，余下现场项见 [实机联调清单](docs/hardware-validation-pending.md)。
