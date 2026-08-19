# PyQt6 QSerialPort 电机错误信号兼容性修复设计

日期：2026-08-10

## 背景与故障证据

ROCK 4B+ 使用 Debian 12 ARM64、Python 3.11 和 PyQt6。电机探测程序打开串口时，在执行
`QSerialPort.errorOccurred.connect()` 处抛出以下异常并终止：

```text
TypeError: decorated slot has no signature compatible with
errorOccurred(QSerialPort::SerialPortError)
```

`MotorSerialWorker._on_error` 当前声明为 `@Slot(object)`。PyQt6 会校验被装饰槽的 C++ 签名，
而 `object` 与 `QSerialPort.SerialPortError` 枚举信号不匹配。异常发生在协议通信开始前，
因此该现象不能用于判断 RS485 接线、串口权限、LK-MD2202 地址或波特率是否正确。

## 目标

- 使电机串口错误处理回调能在 PyQt6 下连接 `QSerialPort.errorOccurred`。
- 保持 PyQt5、PyQt6 及现有 Qt 兼容层可用。
- 不改变 Modbus RTU、LK-MD2202、电机扫描或光谱仪采集控制逻辑。
- 给 ROCK 4B+ 提供可备份、可回退的最小更新方式。

## 已确认方案

删除 `MotorSerialWorker._on_error` 上的 `@Slot(object)`，保留方法本身为普通 Python 回调。
Qt 信号可连接普通 Python 可调用对象，运行时会把 `SerialPortError` 枚举实例直接传入；
方法继续通过现有 `_serial_enum()` 兼容函数判断 `ResourceError`。

未采用以下方案：

- 精确声明 `@Slot(QSerialPort.SerialPortError)`：不同 Qt 绑定及版本的枚举类型暴露方式不同，
  会扩大跨绑定兼容风险。
- 使用 `lambda` 转发：可以绕过签名检查，但引入无必要的间接层，并使连接关系和生命周期
  更难检查。

## 修改范围

仅修改以下运行时代码：

- `spectrometer/motor/transport.py`
- 部署镜像中的对应文件 `deploy/rock4bplus/app/spectrometer/motor/transport.py`

新增或调整电机传输层测试，验证 `QSerialPort.errorOccurred` 可以直接连接
`MotorSerialWorker._on_error`。不修改 `spectrometer/communication/serial_port.py`，因为其
`_on_error` 已经是未加类型装饰器的普通方法。

## 数据流与错误处理

修复后数据流保持不变：

```text
QSerialPort.errorOccurred(SerialPortError)
  -> MotorSerialWorker._on_error(error)
  -> 仅在 ResourceError 时发出 connection_lost(reason)
  -> MotorSerialTransport 清空活动事务并通知界面连接断开
```

串口打开失败仍由 `open_port()` 的既有失败分支报告；普通串口枚举错误不会被误报为设备移除。

## 验证标准

1. 静态检查确认 `_on_error` 不再带 `@Slot(object)`。
2. Qt 回归测试构造 `MotorSerialWorker` 和 `QSerialPort`，直接连接 `errorOccurred`，不需要
   真实串口或电机。
3. 电机传输层定向测试通过。
4. 项目完整自动测试通过，或明确记录与本次改动无关的环境性跳过项。
5. 源码与 ROCK 4B+ 部署镜像对应文件一致。
6. ROCK 4B+ 上重新运行电机探测程序时，不再出现信号槽签名异常；随后才根据探测输出继续
   验证权限、RS485 接线和 Modbus 通信。

## 部署、备份与回退

本次板端更新优先采用单文件替换：先把
`/opt/zgcai-spectrometer/spectrometer/motor/transport.py` 复制为带时间戳的备份，再安装
修复后的同名文件并执行 Python 编译检查。若验证失败，将备份文件复制回原路径即可回退。

该更新不覆盖配置、采集数据、光谱仪模块或虚拟环境。完整部署包仍同步同一修复，供后续
全量安装使用。

## 非目标

- 不调整串口自动识别策略、端口权限或 udev 规则。
- 不改变 LK-MD2202 寄存器、地址、波特率或运动参数。
- 不修改扫描必须回零与否的既有策略。
- 不处理尚未观察到的物理通信或电机运动问题。
