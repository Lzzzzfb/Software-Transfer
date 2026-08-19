# 方波发生器 USB CDC 协议

## 1. 设备身份

| 项目 | 值 | 说明 |
|---|---|---|
| USB 类型 | CDC ACM | ROCK 4B+ 通常枚举为 `/dev/ttyACM*` |
| VID:PID | `0483:5740` | 只用于筛选候选，不能单独证明设备身份 |
| USB 产品字符串 | `STM32 Virtual ComPort` | 通用字符串，不作为协议身份 |
| USB 序列号 | STM32 MCU UID 派生 | 用于识别物理板卡和稳定保存端口偏好 |
| 串口格式 | 9600、8 数据位、1 停止位、无校验、无流控 | USB CDC 仍显式设置 |
| 命令编码 | ASCII | 一次只发送一条命令 |
| 行结束符 | `\r\n` | 接收端以 CR 或 LF 结束一条命令 |

自动发现只能向候选设备发送只读 `ID?`。在身份确认前禁止发送参数、`START` 或
`STOP`。

## 2. 参数

| 参数 | 合法范围 | 默认值 | 单位 |
|---|---:|---:|---|
| 频率 | 1–10 | 10 | Hz |
| 脉宽 | 1–9999 | 5 | μs |

上位机和 STM32 固件必须执行相同边界校验。最高频率 10 Hz 的周期为 100000 μs，
因此 9999 μs 的最大脉宽仍小于一个周期。

## 3. 命令与响应

### 3.1 只读身份

```text
ID?\r\n
```

成功响应：

```text
ID ZGCAI_SQUARE_WAVE protocol=1\r\n
```

只有设备名严格等于 `ZGCAI_SQUARE_WAVE` 且协议版本等于 `1` 时，上位机才确认该
串口是方波发生器。

### 3.2 只读状态

```text
STATUS?\r\n
```

成功响应示例：

```text
STATUS running=0 freq=10 width=5\r\n
```

字段顺序固定。`running` 只能为 `0` 或 `1`，`freq` 和 `width` 必须在协议范围内。

### 3.3 设置频率

```text
pulse_freq=10\r\n
```

合法值返回：

```text
OK\r\n
```

越界或格式错误返回：

```text
INVALID PARAM\r\n
```

### 3.4 设置脉宽

```text
pulse_width=5\r\n
```

响应规则与频率相同。

### 3.5 启动输出

```text
START\r\n
```

命令被接受时返回 `OK`。上位机随后必须发送 `STATUS?` 并确认 `running=1`，不能仅凭
串口写入成功或 `OK` 更新界面状态。

### 3.6 停止输出

```text
STOP\r\n
```

命令被接受时返回 `OK`。上位机随后必须发送 `STATUS?` 并确认 `running=0`。

## 4. 通用错误

```text
ERROR\r\n
INVALID PARAM\r\n
UNKNOWN COMMAND\r\n
```

- `ERROR`：命令执行失败；
- `INVALID PARAM`：参数格式或范围错误；
- `UNKNOWN COMMAND`：命令名不受支持。

`ID?` 和 `STATUS?` 返回自己的数据行，不额外附加 `OK`。

## 5. 事务和重试

- 同一时刻只允许一个等待响应的事务；
- `ID?`、`STATUS?` 是只读命令，超时可以有限重试；
- `START` 和参数写入有副作用，超时不能自动重发；
- `STOP` 在故障收尾时允许尽力重试，但只有 `STATUS running=0` 才算确认停止；
- 串口断开时，上位机必须把输出状态标记为未知，不能假定 STM32 已停止。

## 6. 明确不支持

以下旧测试脚本格式不被当前 STM32 固件支持，上位机不得生成：

```text
START:<frequency>,<width>
```

频率和脉宽必须使用两条独立参数命令设置，再发送纯 `START`。
