# LK-MD2202 迁移基线

- 记录日期：2026-08-10
- 工程：`D:\codex\光谱仪 - Linux转移版`
- 分支：`codex/lk-md2202-integration`
- 计划基线：`06c420d5c458e6c330f9abd34f8dca71dca4f9cb`
- 回退标签：`backup/pre-lk-md2202-20260810`
- 回退提交：`399d500894e6846858f68dfbb4113138fb4e51ab`
- 说明书：`LK-MD2202两路微型步进电机驱动器使用说明V2.0.pdf`
- 说明书 SHA-256：`3B2BA0E1811D9E7B5565E9989F904EAF3A2CA8C2CA3C1C45E749EC0201162973`
- Windows 测试解释器：Python 3.9.13；当前兼容绑定 PyQt5
- 产品目标：ROCK 4B+、Debian 12 Bookworm ARM64、PyQt6/QtSerialPort
- 现有基线：电机、固件契约和部署测试 `57 passed`

## 工作区保护

实施开始时存在用户修改的 Word、Excel 文件以及未跟踪 CSV、XLSX、PDF、Word、诊断包。这些文件不属于本次提交范围，不得暂存、移动、覆盖或清理。所有 Git 暂存必须列出明确路径。

## 硬件边界

- 活动链路：`ROCK 4B+ -> USB转RS-485 -> LK-MD2202`；
- X 对应 M1，Y 对应 M2；
- X/Y 零点开关分别闭合 M1-K/GND、M2-K/GND；
- 两轴行程 15 mm，终点 4800 pulse，换算 320 pulse/mm；
- USB-RS485 的 VID/PID、序列号、内核驱动和稳定设备路径必须由实机采集，当前不得猜测；
- 自动化测试不能证明电流、温升、回零方向、真实位移或机械卡滞。

## 待板端采集

- `/etc/os-release`、`uname -m`、Python、glibc；
- PyQt6、QtSerialPort 与 pyqtgraph 实际绑定；
- `lsusb`、`lsusb -t`、`udevadm info`、`dmesg`；
- 当前用户组和串口权限；
- USB-RS485 设备身份；
- LK-MD2202 只读身份、版本、配置和状态响应样本。

