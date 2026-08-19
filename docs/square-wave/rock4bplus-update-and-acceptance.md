# ROCK 4B+ 方波控制集成：更新、回退与验收

本说明对应方波发生器集成版本。更新覆盖主机上位机源码和 udev 规则，不会自动修改或
烧录 STM32。先按 `stm32-id-status-modification.md` 修改、编译和烧录固件，至少确认
`ID?` 与 `STATUS?` 响应正确，再进行有输出的实机测试。

## 1. Windows 生成并上传部署目录

关闭 ROCK 4B+ 上的软件。在 Windows PowerShell 中执行：

```powershell
cd "D:\codex\光谱仪 - Linux转移版"

.\.venv\Scripts\python.exe tools\build_rock4bplus_deploy.py
.\.venv\Scripts\python.exe tools\build_rock4bplus_deploy.py --check

$updateId = "square-wave-$(Get-Date -Format yyyyMMdd-HHmmss)"
$remoteParent = "/home/radxa/zgcai-upload-$updateId"
$remoteRoot = "$remoteParent/rock4bplus"
ssh radxa@192.168.1.247 "mkdir -p '$remoteParent'"
scp -r ".\deploy\rock4bplus" "radxa@192.168.1.247:$remoteParent/"
ssh radxa@192.168.1.247 "test -f '$remoteRoot/install.sh' && echo UPLOAD_OK '$remoteRoot'"

Write-Host "板端更新目录：$remoteRoot"
```

`scp` 在 Windows PowerShell 中执行，不要粘贴到 ROCK 4B+ 的 Bash 终端。这里上传整个
`rock4bplus` 目录，不使用 `*` 通配符；安装前必须看到 `UPLOAD_OK`，否则不要继续。

## 2. ROCK 4B+ 备份并安装

SSH 登录板卡，先确认软件已经退出：

```bash
pgrep -af '/opt/zgcai-spectrometer/main.py' || true
```

如果仍有软件进程，先从界面退出。不要在方波正在输出或电机正在运动时强制结束进程。
把上一节显示的实际更新目录填入 `update_root`：

```bash
update_root=/home/radxa/zgcai-upload-square-wave-YYYYMMDD-HHMMSS/rock4bplus
backup_dir="/home/radxa/zgcai-backups/pre-square-wave-$(date +%Y%m%d-%H%M%S)"

test -f "$update_root/install.sh" || {
  echo "未找到 $update_root/install.sh，请停止安装并检查 Windows 上传结果"
  exit 1
}

mkdir -p "$backup_dir"
sudo cp -a /opt/zgcai-spectrometer "$backup_dir/application"
printf '%s\n' "$backup_dir" | tee /home/radxa/zgcai-last-backup.txt

sudo bash "$update_root/install.sh"
```

安装脚本保留用户配置、历史数据和诊断目录；会同步主程序、创建/更新虚拟环境，并安装
光谱仪、CH341 电机串口和 STM32 CDC 方波串口的 `dialout`/`0660` udev 规则。安装后
注销并重新登录，再重新插拔三个 USB 设备。

## 3. 无运动、无输出检查

```bash
cd /opt/zgcai-spectrometer

./.venv/bin/python -m py_compile \
  spectrometer/square_wave/models.py \
  spectrometer/square_wave/protocol.py \
  spectrometer/square_wave/transport.py \
  spectrometer/square_wave/controller.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/ui/main_window.py

QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c '
from spectrometer.qt import QT_API
from spectrometer.square_wave.models import SquareWaveParameters
from spectrometer.square_wave.protocol import id_command, status_command
assert QT_API == "PyQt6", QT_API
assert SquareWaveParameters() == SquareWaveParameters(10, 5)
assert id_command() == b"ID?\r\n"
assert status_command() == b"STATUS?\r\n"
print("SQUARE_WAVE_IMPORT_OK", QT_API)
'

ls -l /dev/serial/by-id/ 2>/dev/null || true
id
```

只读探针不会启停输出或写参数：

```bash
./.venv/bin/python -m spectrometer.square_wave.probe \
  --port /dev/serial/by-id/实际方波串口 \
  --timeout 8 \
  --json
```

预期 `ok` 为 `true`，身份为 `ZGCAI_SQUARE_WAVE`、协议版本为 `1`，并返回当前
`running`、`frequency_hz` 和 `pulse_width_us`。若探针失败，不要开始联动扫描。

## 4. 界面与手动输出验收

1. 启动软件，确认原光谱仪和电机功能仍存在；
2. 确认“扫描联动方波”默认未勾选；关闭并重新打开软件后仍应未勾选；
3. 方波设置中选择自动识别，确认只连接身份完全匹配的 STM32；
4. 设置 10 Hz、5 μs，点击“应用”，必须写入并由 `STATUS?` 精确回读后才显示成功；
5. 点击“启动”，用示波器确认输出；点击“停止”，确认输出停止；
6. 分别验证频率边界 1 Hz、10 Hz 和脉宽边界 1 μs、9999 μs；越界值应无法输入；
7. 输出中拔出 USB 后，界面必须显示“输出状态未知”。注意拔 USB 不保证 STM32 停止
   输出；无法重连停止时必须切断 STM32 输出板电源；
8. 重连时软件应先读取状态；若板卡仍在输出，应发送 STOP 并回读确认后才允许扫描。

## 5. 扫描联动验收

先使用安全电机行程和低风险负载，保持可立即断电。分别执行纯电机扫描和光谱联动扫描：

1. 勾选“扫描联动方波”，每轮顺序应为：方波确认开启 → 光谱仪开始采集（若已连接）
   → 电机扫描 → 光谱仪停止并封存 → 方波确认关闭 → 电机返回起点；
2. 未连接光谱仪时仍应执行：方波开启 → 电机扫描 → 方波关闭 → 电机返回；
3. 多轮扫描每轮只启动、停止方波各一次，返回和轮间阶段不得持续输出；
4. 方波未连接、正在手动输出或状态未知时，联动扫描应在电机运动前被拒绝；
5. 方波启动失败时不得启动光谱采集和电机路径；
6. 扫描中方波断线时应停止电机和光谱采集、不自动回程，并提示输出状态未知；
7. 用户停止时立即停止电机；有光谱数据时先封存，再关闭方波；不得自动回程；
8. 导出诊断包，时间线中应包含方波参数、启停结果、错误和逐轮扫描事件；有光谱清单时，
   `square_wave` 及每轮 `square_wave_output_started/stopped` 字段应完整。

## 6. 回退

先退出软件并确保电机停止、方波输出关闭。读取最近备份：

```bash
backup_dir="$(cat /home/radxa/zgcai-last-backup.txt)"
test -d "$backup_dir/application" || { echo "备份不存在"; exit 1; }

failed_dir="/home/radxa/zgcai-backups/failed-square-wave-$(date +%Y%m%d-%H%M%S)"
sudo mv /opt/zgcai-spectrometer "$failed_dir"
sudo cp -a "$backup_dir/application" /opt/zgcai-spectrometer
sudo sync
```

该回退保留失败版本目录，便于再次分析；不会删除用户配置、采集数据或诊断包。主机软件
回退不等于 STM32 固件回退。旧软件不会控制方波板，回退后应单独停止/断电；如需回退
固件，使用修改前保存的 Keil 工程和 ST-LINK 重新烧录。
