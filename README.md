# ZGCAI 光谱仪采集与分析工作站

面向 Radxa ROCK 4B+、Debian 12 ARM64 的 Linux 专用光谱仪上位机。产品界面使用 PyQt6/Qt Widgets，支持 CH569W 串口采集、同步布防、实时处理、批量 CSV/Excel、断点缓存恢复、历史查看和诊断。本副本与 Windows 版本分开维护。

## 已实现能力

- 协议普通帧与 `0x80` 数据帧分别按正确长度拆包。
- 自动扫描严格筛选 `1a86:fe0c`，再发送版本查询进行协议握手；只有返回合法设备信息的串口才会显示并参与采集。
- `nPacketNumb` 自动兼容实机 U16LE 与函数表 U32LE，像素按 U16 大端解析。
- 最多按当前目标同时使用约 4 台设备；实时显示目标 25 FPS，在约 90 FPS
  单机输入下通常实际显示约 22～23 FPS，存储通道仍接收每一帧。
- 顶部一个“开始/停止”按钮负责总控；每张设备卡可独立开始/停止并采集自己的背景或参考，多台单机任务可并行。
- 总控、单机、背景/参考共用 ACK、超时、掉线回滚、停止尾帧和异步保存状态机，冲突操作会被拒绝。
- 实机 16 位与新格式 24 位帧序号缺口、重复、乱序和回绕诊断。
- 单次、连续、软件同步、内部硬同步和外部硬同步布防。
- 原始、逐像素强度校准、扣背景、吸光度、airPLS 和受限自定义公式；
  绘图与导出共用采集开始时冻结的处理快照。
- 强度校准可从 CSV/TXT 导入，并按生产序列号和有效像素数自动恢复；
  当前帧、背景和参考在模式计算前应用同一组校准系数。
- airPLS 支持全局参数和每台设备覆盖，在所选处理模式之后执行；
  校正后的显示使用有界后台任务，不阻塞采集和全量存储。
- 默认每 500 帧一批，可设置 1–1000；自动存储每次启动默认不勾选。
- “CSV + Excel”会按任务设备数自动选择：单设备只生成 CSV，多设备只生成
  一个 Excel；仅 CSV 时每台设备单独生成 CSV，仅 Excel 时生成一个工作簿。
- Excel 一个设备一个工作表，并保留“采集概要”工作表。
- 所有批量文件均为每帧一列、每像素一行。
- 文件名包含本地日期、设备序列号和批次号，重名自动追加顺序后缀，不会覆盖已有数据。
- `.part` 临时缓存包含版本、元数据和逐帧 CRC32，可恢复截断尾记录。
- 停止命令、尾帧静默和 Excel/CSV 收尾不会阻塞 GUI；导出失败会立即释放状态并保留 `.part`。
- 光谱图支持左键框选 X/Y 放大、滚轮缩放、左键双击复位、固定 Y 初始范围和完整十进制刻度。
- 所有参数数值框仅允许直接输入，数值框与下拉框均屏蔽滚轮误操作。
- 现代化双层工具带、设备侧栏、实时/历史/诊断页和状态栏。
- `--simulate` 可在无光谱仪时模拟 4 台 4096 像素设备。

## ROCK 4B+ 环境

- Debian 12 Bookworm ARM64
- Python 3.11
- Debian 官方 PyQt6、QtSerialPort 和 PyQtGraph
- 源码 + venv 运行

发送 `deploy/rock4bplus` 到板子后安装：

```bash
cd rock4bplus
sudo ./install.sh
```

注销并重新登录、重新插拔光谱仪后，从应用菜单或桌面快捷方式手动启动。也可运行：

```bash
/opt/zgcai-spectrometer/run.sh
```

Linux 默认使用 PyQtGraph 实时绘图后端。实机 A/B 排查时可以显式选择：

```bash
ZGCAI_PLOT_BACKEND=pyqtgraph /opt/zgcai-spectrometer/run.sh
ZGCAI_PLOT_BACKEND=legacy /opt/zgcai-spectrometer/run.sh
```

未显式选择且 PyQtGraph 不可用时，程序会降级到旧绘图后端并在诊断页说明
原因；显式强制 PyQtGraph 时缺少依赖会直接报告启动错误。

排查串口问题时也可临时限制扫描范围；正常使用无需指定端口：

```bash
/opt/zgcai-spectrometer/run.sh --ports ttyACM0
```

配置位于 `~/.config/ZGCAI/Spectrometer/`，默认数据目录为
`~/ZGCAI-Spectrometer-Data/`。第一阶段只提供手动启动，不配置开机自启。

## 自动测试

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer tools

# 四设备模拟总控/单机/存储端到端验证
python tools\run_ui_control_validation.py
```

## 板端部署内容

`deploy/rock4bplus` 只包含运行源码、ARM64 依赖清单、安装脚本、启动脚本、udev 规则和桌面快捷方式。运行源码副本由
`tools/build_rock4bplus_deploy.py` 生成并校验。

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

详细操作见 [用户指南](docs/user-guide.md)，本轮实机结果见 [2026-07-23 采集控制与界面联调报告](docs/hardware-validation-report-2026-07-23.md)，余下现场项见 [实机联调清单](docs/hardware-validation-pending.md)。
