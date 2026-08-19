# 电机控制器USB CDC协议

> 历史协议：仅适用于 STM32/TMC2209 版本。当前 LK-MD2202 活动协议见
> `lk-md2202-modbus-protocol.md`。

## 1. 传输

- USB CDC；
- ASCII；
- 命令和响应以 `\n` 结束，接收兼容 `\r\n`；
- 单行最大长度为96字节；
- 一次只执行一个会改变运动状态的事务；
- 查询命令允许在运动期间执行；
- 上位机不得自动重发运动命令。

固件必须跨USB包缓存文本，只有收到换行后才解析完整命令。

## 2. 身份

命令：

```text
ID?
```

响应示例：

```text
OK ID=TMC2209 MOTOR_PROTOCOL=2 TRAVEL_MM=15.0 PULSES_PER_MM=640
```

自动识别必须同时验证：

- USB VID:PID优先为 `0483:5740`；
- `ID=TMC2209`；
- `MOTOR_PROTOCOL=2`。

USB序列号存在时用于稳定绑定同一块STM32；设备未提供序列号时，以系统位置作为
退化身份。VID:PID只改变探测优先级，不能代替协议握手。

## 3. 轴

协议轴标识：

| 轴 | 名称 | 旧命令编号 |
|---|---|---:|
| X | `X` | 1 |
| Y | `Y` | 2 |
| Z | `Z` | 3 |

Y1/Y2是下位机内部两个驱动器，不作为两个独立运动轴暴露。

## 4. 速度

兼容命令：

```text
SPEED1=10000
SPEED2=10000
SPEED3=10000
```

成功：

```text
OK SPEED AXIS=X HZ=10000
```

范围：

```text
100 <= HZ <= 14000
```

超界值必须拒绝，不能静默截断或发生整数环绕。

## 5. 相对移动

兼容命令：

```text
MOVE1=10.000:1
```

方向：

- `1`：固件轴配置中的正方向；
- `0`：固件轴配置中的负/零点方向。

成功接收：

```text
OK MOVE AXIS=X MM=10.000 DIR=1
```

ACK只表示命令被接受，不表示机械运动已完成。上位机通过 `STATUS` 查询
`MOVING=0` 和停止原因。

拒绝条件：

- 距离不为正；
- 换算后不足一个脉冲；
- 轴不存在；
- 速度无效；
- 轴正在运动；
- 故障未清除；
- 零点开关闭合且仍请求负方向；
- 坐标可信时目标小于0或大于15 mm。

## 6. 状态

命令：

```text
STATUS
```

响应为单行：

```text
OK STATUS X_POS=0.000 X_VALID=1 X_MOVING=0 X_LIMIT=0 X_STOP=DONE Y_POS=0.000 Y_VALID=1 Y_MOVING=0 Y_LIMIT=0 Y_STOP=DONE Z_POS=0.000 Z_VALID=1 Z_MOVING=0 Z_LIMIT=0 Z_STOP=DONE
```

`LIMIT=1` 表示物理开关闭合；协议层屏蔽GPIO低电平的内部实现。

独立查询：

```text
LIMIT?
POS?
FAULT?
```

示例：

```text
OK LIMIT X=0 Y=0 Z=0
OK POS X=0.000 XV=1 Y=0.000 YV=1 Z=0.000 ZV=1
OK FAULT LATCHED=0 CODE=NONE
```

## 7. 坐标设置

命令：

```text
POSSET=X:3.250
```

成功：

```text
OK POSSET AXIS=X MM=3.250
```

用途：

- 同一控制器正常关闭后的坐标恢复；
- 用户确认当前位置；
- 软件坐标清零。

只接受0～15 mm。运动期间、限位故障未处理或轴状态不确定时拒绝。

## 8. 机械回零

命令：

```text
HOME=X
HOME=Y
HOME=Z
HOME=ALL
```

接受：

```text
OK HOME AXIS=X STATE=STARTED
```

完成状态通过 `STATUS` 查询：

```text
X_MOVING=0 X_POS=0.000 X_VALID=1 X_STOP=HOMED
```

失败：

```text
ERR CODE=HOME_TIMEOUT AXIS=X
ERR CODE=HOME_SWITCH AXIS=X
```

机械回零是可选操作，扫描前不强制执行。

## 9. 停止

命令：

```text
STOP
```

行为：

- 立即停止三轴；
- 清除运动中标记；
- 不自动把坐标设0；
- 部分运动导致结果不确定时把相关坐标标记无效。

响应：

```text
OK STOP
```

旧 `RESET` 保留为兼容停止别名：

```text
OK STOP ALIAS=RESET
```

禁止把 `RESET` 描述为机械回零。

## 10. 故障

查询：

```text
FAULT?
```

清除：

```text
CLEARFAULT
```

清除故障只解除锁存，不恢复不可信坐标，也不绕过仍闭合的限位开关。

错误格式：

```text
ERR CODE=<稳定错误码> [AXIS=<X|Y|Z>] MSG=<短说明>
```

稳定错误码至少包括：

- `BAD_COMMAND`
- `BAD_AXIS`
- `BAD_VALUE`
- `SPEED_RANGE`
- `TRAVEL_RANGE`
- `BUSY`
- `LIMIT_ACTIVE`
- `FAULT_LATCHED`
- `POSITION_UNKNOWN`
- `HOME_TIMEOUT`
- `HOME_SWITCH`
- `USB_BUSY`

## 11. 驱动使能

兼容：

```text
PB14=0
PB14=1
```

响应：

```text
OK ENABLE VALUE=1
OK ENABLE VALUE=0
```

协议值应表达“电机是否使能”，不要把PB14电气反相暴露给新上位机。旧命令继续按
PB14原电平兼容，新上位机优先使用结构化状态。

## 12. 禁止的命令

所有 `SCAN_*` 命令返回：

```text
ERR CODE=HOST_SCAN_REQUIRED
```

扫描路径和光谱采集联动只由PyQt6上位机状态机执行。
