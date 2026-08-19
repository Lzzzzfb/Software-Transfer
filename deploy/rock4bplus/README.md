# ROCK 4B+ 部署与回退

本目录是运行副本，不包含测试、用户数据、说明书 PDF 或历史 STM32 固件。

## 安装

将整个 `rock4bplus` 目录复制到 ROCK 4B+ 后执行：

```bash
cd rock4bplus
sudo bash ./install.sh
```

安装脚本要求 Debian 12 ARM64 和 Python 3.11，安装 PyQt6、QtSerialPort、
PyQtGraph，将当前用户加入 `dialout`，并执行依赖自检。注销重新登录后运行：

```bash
/opt/zgcai-spectrometer/run.sh
```

在连接电机并允许运动前先执行只读探针：

```bash
cd /opt/zgcai-spectrometer
./.venv/bin/python -m spectrometer.motor.probe --json
```

将修改后的 STM32 固件烧录并给板卡上电后，先执行方波发生器只读探针。该命令只发送
`ID?` 和 `STATUS?`，不会修改频率/脉宽，也不会启停输出：

```bash
cd /opt/zgcai-spectrometer
./.venv/bin/python -m spectrometer.square_wave.probe --json
```

方波发生器使用 STM32 USB CDC `0483:5740`、9600 baud、8N1。USB VID/PID 仅用于
候选排序，软件仍要求 `ID ZGCAI_SQUARE_WAVE protocol=1` 才会连接。频率范围为
1～10 Hz，脉宽范围为 1～9999 μs；每次启动软件时“扫描联动方波”默认关闭。

如扫描中拔出方波 USB，软件会停止电机和光谱采集，但 STM32 可能仍在输出。此时不要
依赖上位机状态，应重新连接执行停止确认；无法重连时切断 STM32 输出板电源。

## 回退

迁移前 Git 标签为 `backup/pre-lk-md2202-20260810`。如需恢复旧软件：

1. 在开发机从该标签重新生成独立部署目录；
2. 保存板端 `~/.config/ZGCAI/Spectrometer/` 和数据目录；
3. 将旧部署目录传到板端并重新执行其 `install.sh`；
4. 不要把历史 STM32/TMC2209 HEX 烧入 LK-MD2202；
5. 回退后用光谱仪模拟/实机采集确认原功能。

回退主机软件不会自动回退 STM32 固件。旧主机软件不识别方波发生器，回退后应单独
停止或断电方波板；需要回退固件时使用烧录前保存的 Keil 工程备份。

当前 LK-MD2202 版本不保存软件零点或机械校准状态。回退软件前后均应把机构视为
位置未知，先人工检查安全空间。
