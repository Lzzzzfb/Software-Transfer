# STM32 方波固件身份、状态与参数范围修改

本说明适用于工程：

```text
D:\codex\fangbo
```

只修改：

```text
D:\codex\fangbo\MDK-ARM\hardware\USB_Command.h
D:\codex\fangbo\MDK-ARM\hardware\USB_Command.c
```

不修改 PB10、TIM2、主循环、USB 描述符或波形产生代码。默认值继续为 10 Hz、5 μs。

## 1. 修改前备份

关闭 Keil 后，在 Windows PowerShell 执行：

```powershell
$backup = "D:\codex\fangbo-backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Path "$backup\MDK-ARM\hardware" -Force
Copy-Item -LiteralPath 'D:\codex\fangbo\MDK-ARM\hardware\USB_Command.h' -Destination "$backup\MDK-ARM\hardware\USB_Command.h"
Copy-Item -LiteralPath 'D:\codex\fangbo\MDK-ARM\hardware\USB_Command.c' -Destination "$backup\MDK-ARM\hardware\USB_Command.c"
$backup
```

保存最后输出的备份路径。

## 2. 修改 `USB_Command.h`

打开：

```text
D:\codex\fangbo\MDK-ARM\hardware\USB_Command.h
```

在默认参数定义附近增加：

```c
#define SQUARE_WAVE_DEVICE_NAME       "ZGCAI_SQUARE_WAVE"
#define SQUARE_WAVE_PROTOCOL_VERSION  1
#define MIN_PULSE_FREQ_HZ             1
#define MAX_PULSE_FREQ_HZ             10
#define MIN_PULSE_WIDTH_US            1
#define MAX_PULSE_WIDTH_US            9999
```

保留原有默认值：

```c
#define DEFAULT_PULSE_WIDTH     5
#define DEFAULT_PULSE_FREQ      10
```

把原来的状态枚举：

```c
typedef enum {
    CMD_STATUS_SUCCESS,
    CMD_STATUS_ERROR,
    CMD_STATUS_UNKNOWN,
    CMD_STATUS_INVALID_PARAM
} CommandStatus_t;
```

替换为：

```c
typedef enum {
    CMD_STATUS_SUCCESS,
    CMD_STATUS_ERROR,
    CMD_STATUS_UNKNOWN,
    CMD_STATUS_INVALID_PARAM,
    CMD_STATUS_RESPONSE_SENT
} CommandStatus_t;
```

`CMD_STATUS_RESPONSE_SENT` 表示命令处理函数已经发送了数据响应，外层不能再附加 `OK`。

## 3. 修改通用响应分派

打开：

```text
D:\codex\fangbo\MDK-ARM\hardware\USB_Command.c
```

在 `USB_Command_ProcessRxData()` 的 `switch (status)` 中，在现有分支后增加：

```c
case CMD_STATUS_RESPONSE_SENT:
    /* ID? 和 STATUS? 已发送数据响应，不再追加通用 OK。 */
    break;
```

修改后的完整分派应为：

```c
switch (status)
{
    case CMD_STATUS_SUCCESS:
        USB_Command_SendResponse("OK\r\n");
        break;
    case CMD_STATUS_ERROR:
        USB_Command_SendResponse("ERROR\r\n");
        break;
    case CMD_STATUS_INVALID_PARAM:
        USB_Command_SendResponse("INVALID PARAM\r\n");
        break;
    case CMD_STATUS_UNKNOWN:
        USB_Command_SendResponse("UNKNOWN COMMAND\r\n");
        break;
    case CMD_STATUS_RESPONSE_SENT:
        /* ID? 和 STATUS? 已发送数据响应，不再追加通用 OK。 */
        break;
}
```

## 4. 增加 `ID?` 和 `STATUS?`

在 `USB_Command_ParseAndExecute()` 中，放在 `START` 判断之前：

```c
if (strcmp(command, "ID?") == 0)
{
    USB_Command_SendResponse(
        "ID %s protocol=%d\r\n",
        SQUARE_WAVE_DEVICE_NAME,
        SQUARE_WAVE_PROTOCOL_VERSION
    );
    return CMD_STATUS_RESPONSE_SENT;
}
else if (strcmp(command, "STATUS?") == 0)
{
    USB_Command_SendResponse(
        "STATUS running=%u freq=%u width=%u\r\n",
        (unsigned int)g_PulseConfig.isRunning,
        (unsigned int)g_PulseConfig.pulseFreq,
        (unsigned int)g_PulseConfig.pulseWidth
    );
    return CMD_STATUS_RESPONSE_SENT;
}
else if (strcmp(command, "START") == 0)
```

`START` 后面的原有代码保持不变。注意这里使用 `else if` 接回原判断链，不能留下两个
互不关联的 `if`。

## 5. 修改参数范围

把脉宽分支中的判断：

```c
StringToInt(command + 12, &width) && width > 0 && width < 10000
```

替换为：

```c
StringToInt(command + 12, &width) &&
width >= MIN_PULSE_WIDTH_US && width <= MAX_PULSE_WIDTH_US
```

把频率分支中的判断：

```c
StringToInt(command + 11, &freq) && freq > 0 && freq < 10000
```

替换为：

```c
StringToInt(command + 11, &freq) &&
freq >= MIN_PULSE_FREQ_HZ && freq <= MAX_PULSE_FREQ_HZ
```

## 6. Keil5 编译

1. 打开 `D:\codex\fangbo\MDK-ARM\fangbo.uvprojx`；
2. 确认当前 Target 是原工程使用的 STM32F103C8；
3. 点击 `Project → Rebuild all target files`；
4. 确认结果为 `0 Error(s)`；
5. 记录警告数量，不要忽略新增的类型或格式化警告；
6. 在 `MDK-ARM\fangbo` 输出目录确认生成时间已经更新。

如果出现 `enumeration value not handled in switch`，说明某个 `switch (CommandStatus_t)`
还没有增加 `CMD_STATUS_RESPONSE_SENT`，逐一补齐后重新编译。

## 7. ST-LINK 烧录

1. 方波板断电；
2. 连接 ST-LINK 的 SWDIO、SWCLK、GND 和参考电压；
3. 给目标板正常供电；
4. 在 Keil 中点击 `Flash → Download`；
5. 确认出现 `Verify OK`；
6. 给目标板重新断电、上电；
7. 重新插拔 USB CDC。

烧录前后不要让 Windows 上位机或 ROCK 4B+ 软件占用该串口。

## 8. 最小串口验证

先只验证只读命令。部署新版 ROCK 4B+ 软件后执行：

```bash
cd /opt/zgcai-spectrometer
./.venv/bin/python -m spectrometer.square_wave.probe \
  --port /dev/ttyACM0 \
  --baud 9600 \
  --json
```

预期包含：

```json
{
  "ok": true,
  "identity": {
    "name": "ZGCAI_SQUARE_WAVE",
    "protocol_version": 1
  },
  "status": {
    "running": false,
    "frequency_hz": 10,
    "pulse_width_us": 5
  }
}
```

再通过正式界面验证边界：

- 频率 1、10 接受；0、11 拒绝；
- 脉宽 1、9999 接受；0、10000 拒绝；
- `START` 后 STATUS 显示 `running=1`；
- `STOP` 后 STATUS 显示 `running=0`；
- 示波器实际频率和脉宽与设置一致；
- STOP 后 PB10 保持低电平。

## 9. 回退

如果编译、烧录或波形验证失败：

1. 关闭 Keil；
2. 从第 1 节记录的备份目录恢复 `USB_Command.c/.h`；
3. 重新打开工程并 Rebuild；
4. 用 ST-LINK 重新 Download；
5. 重新上电并验证旧版 START/STOP。

不要用未确认来源的 HEX 覆盖备份，也不要修改 CubeMX `.ioc` 试图解决本次文本协议问题。
