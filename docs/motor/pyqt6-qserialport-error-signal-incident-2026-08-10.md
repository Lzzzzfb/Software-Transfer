# PyQt6 电机串口错误信号兼容问题复盘

## 基本信息

- 项目：光谱仪 Linux 转移版 / LK-MD2202 电机控制
- 故障基线：`8834093`，设计提交：`95d85b7`
- ROCK 4B+：Debian 12 Bookworm ARM64、Python 3.11.2、PyQt6
- 发生日期：2026-08-10
- 影响范围：电机探测程序和电机串口连接入口；光谱仪原有采集逻辑未执行到变更点
- 是否可复现：是；Windows PyQt5 与 ROCK 4B+ PyQt6 均可复现

## 现象

电机探测程序使用稳定设备路径或 `/dev/ttyUSB0` 时均在打开串口阶段终止：

```text
TypeError: decorated slot has no signature compatible with
errorOccurred(QSerialPort::SerialPortError)
```

异常发生在第一条 Modbus RTU 请求发出之前。

## 分层判断

| 假设 | 最小判断探针 | 结果 | 结论 |
|---|---|---|---|
| 设备路径错误 | 分别使用 `/dev/serial/by-id/...` 和 `/dev/ttyUSB0` | 同一签名异常 | 不是路径选择导致 |
| 串口权限错误 | 检查用户属于 `dialout`，设备属于 `dialout` | 权限条件满足 | 不是当前故障点 |
| PyQt 信号槽类型不兼容 | 无硬件构造 `QSerialPort` 并连接回调 | PyQt5 同样抛出异常 | 已确认 |
| RS485 或 Modbus 通信错误 | 检查异常发生位置 | 尚未发送请求 | 当前证据不足，待修复后验证 |

## 根因

- 故障层：Qt 平台兼容层 / 串口事件连接。
- 直接原因：`MotorSerialWorker._on_error` 使用 `@Slot(object)`，其声明签名与
  `QSerialPort.errorOccurred(QSerialPort::SerialPortError)` 不兼容。
- 结构性原因：原传输层测试使用假串口工作对象，没有连接真实 QtSerialPort 信号。
- 原测试未发现原因：测试覆盖了队列、超时和协议处理，但未覆盖 Qt 枚举信号的绑定校验。

## 修复

- 删除 `_on_error` 上的 `@Slot(object)`，保留普通 Python 回调。
- 增加真实 `QSerialPort.errorOccurred` 连接回归测试，不依赖物理串口。
- 通过构建脚本同步 ROCK 4B+ 部署镜像。
- 回退单位为源文件 `spectrometer/motor/transport.py`；板端替换前先保留带时间戳的备份。

## 验证

- 修复前定向测试：`1 failed, 4 passed`，失败与板端异常一致。
- 修复后定向测试：`5 passed`。
- 部署镜像与源码校验：93 个运行文件一致。
- 完整自动测试：`370 passed, 1 skipped`。
- 跳过项：Windows 环境未安装可选 PyQtGraph；与本次串口修复无关。
- ROCK 4B+ 实机：待安装修复文件后重新运行探测程序，不能提前标记通过。

## 适用边界

- 已验证：回调可在当前 Windows PyQt5 环境连接真实 QtSerialPort 枚举信号；其他自动测试无回归。
- 待实机确认：ROCK 4B+ PyQt6 信号连接、串口打开、LK-MD2202 应答、X/Y 运动与扫描联动。
- 无法由本次主机测试保证：RS485 极性、接地、终端匹配、驱动器地址、机械方向和行程安全。

## 知识提升

- 本结论先保留在本项目案例中。
- 若后续在多个项目和 Qt 绑定中重复验证，再考虑提升到迁移技能参考资料。
