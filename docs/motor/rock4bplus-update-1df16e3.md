# ROCK 4B+ 预测扫描衔接增量更新与回退（1df16e3）

本轮只替换四个电机扫描运行文件：

- `spectrometer/motor/controller.py`
- `spectrometer/motor/scan_controller.py`
- `spectrometer/motor/scan_timing.py`
- `spectrometer/motor/lk_md2202.py`

中间扫描段使用绝对目标、写入接收确认和基于可信位置样本的预测交接；最后一个
扫描段、两轴终点校验及返回本轮起点仍采用严格停止确认。连接光谱仪时，每轮连续
采集覆盖全部扫描段、预测交接、子步和步时，终点校验通过后才停止并保存；未连接
光谱仪时走相同电机路径，只跳过采集和保存。

原光谱仪控制逻辑、虚拟环境、电机参数、用户设置、采集数据和历史诊断目录均保留。

## 1. Windows 上传

先从桌面正常关闭ROCK 4B+上的软件。在Windows PowerShell中进入工程根目录：

```powershell
ssh radxa@192.168.1.247 "mkdir -p /home/radxa/zgcai-update-1df16e3/spectrometer/motor"

$files = @(
  "controller.py",
  "scan_controller.py",
  "scan_timing.py",
  "lk_md2202.py"
)
foreach ($file in $files) {
  scp ".\deploy\rock4bplus\app\spectrometer\motor\$file" `
    "radxa@192.168.1.247:/home/radxa/zgcai-update-1df16e3/spectrometer/motor/$file"
}
```

`scp`必须在Windows PowerShell中执行，不能粘贴到ROCK 4B+的SSH Bash中。

## 2. ROCK 4B+ 备份与安装

SSH登录ROCK 4B+，先确认软件已经退出：

```bash
pgrep -af '/opt/zgcai-spectrometer/main.py' || true
```

确认没有软件进程后执行：

```bash
install_root=/opt/zgcai-spectrometer
update_root=/home/radxa/zgcai-update-1df16e3
backup_dir="/home/radxa/zgcai-backups/pre-1df16e3-$(date +%Y%m%d-%H%M%S)"

files='spectrometer/motor/controller.py
spectrometer/motor/scan_controller.py
spectrometer/motor/scan_timing.py
spectrometer/motor/lk_md2202.py'

while IFS= read -r relative_path; do
  mkdir -p "$backup_dir/$(dirname "$relative_path")"
  if [ -e "$install_root/$relative_path" ]; then
    cp -a "$install_root/$relative_path" "$backup_dir/$relative_path"
  else
    : > "$backup_dir/$relative_path.NOT_PRESENT"
  fi
done <<EOF
$files
EOF

printf '%s\n' "$backup_dir" | tee /home/radxa/zgcai-last-backup.txt

while IFS= read -r relative_path; do
  sudo install -m 0644 \
    "$update_root/$relative_path" \
    "$install_root/$relative_path"
done <<EOF
$files
EOF
```

新备份不会覆盖已有的`pre-2b4970f-*`、`pre-c216da2-*`等备份。

## 3. 安装后静态检查

```bash
cd /opt/zgcai-spectrometer

./.venv/bin/python -m py_compile \
  spectrometer/motor/controller.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/motor/scan_timing.py \
  spectrometer/motor/lk_md2202.py

sha256sum \
  spectrometer/motor/controller.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/motor/scan_timing.py \
  spectrometer/motor/lk_md2202.py
```

预期SHA-256依次为：

```text
f234c00d9786bfe136d207f1515557685dc7e9cff40acdeaf3d12dd7913811a2  spectrometer/motor/controller.py
6fb13ffa83a62a9d9e412e74cfefab959c4cc8e91547f9b405a2578300b896bd  spectrometer/motor/scan_controller.py
2f4f765c6b3f5de277e76dbd16156d9f4c5d90338deec5ab8b20eb4be7fb9635  spectrometer/motor/scan_timing.py
c50f5accad8e3e6712b37fdafe225ca38014b72ba97ab0d58e61463878b85eb0  spectrometer/motor/lk_md2202.py
```

再执行不运动的导入检查：

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c '
from spectrometer.motor.controller import (
    SCAN_ACK_TIMEOUT_MS,
    SCAN_STATUS_REPOLL_MS,
    SCAN_STATUS_TIMEOUT_MS,
)
from spectrometer.motor.scan_timing import HandoffTimingConfig
from spectrometer.motor.scan_controller import ScanController

config = HandoffTimingConfig()
assert (SCAN_ACK_TIMEOUT_MS, SCAN_STATUS_TIMEOUT_MS, SCAN_STATUS_REPOLL_MS) == (60, 150, 40)
assert (config.near_target_pulses, config.timeout_target_pulses) == (160, 320)
assert (config.guard_seconds, config.minimum_segment_seconds) == (0.080, 0.200)
print("PREDICTIVE_SCAN_IMPORT_OK")
'
```

LK-MD2202供电且桌面软件未占用串口时，执行原只读探针：

```bash
./.venv/bin/python -m spectrometer.motor.probe \
  --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --address 1 \
  --baud 9600 \
  --timeout 10 \
  --json
```

探针返回`"ok": true`后再启动桌面软件。

## 4. 实机验证顺序

从安全位置、小矩阵和可立即切断驱动板电源的条件开始：

1. 断开光谱仪，使用`X=1 mm、Y=0.5 mm、n=1、m=1、X/Y步数=1、步时=0`运行纯电机扫描；
2. 确认最后一段完成后严格校验两轴，再按绝对目标返回本轮原始起点；
3. 使用相同参数连续运行至少10次，确认无重复距离、反向、越界和明显长时间两轴重叠；
4. 再测试分步和非零步时，确认每个用户步时只执行一次；
5. 连接光谱仪运行一轮联动扫描，确认采集覆盖整段扫描，并在终点校验后停止、保存、返回；
6. 导出诊断包，检查段操作编号、绝对起点/目标、预测交接、最终校验、返回和读取失败记录。

预测交接允许上一段接近终点时提前交给下一段，不能再用旧版“必须读到上一轴停止
才发下一轴”的标准判断。安全标准是：没有可信运动样本时绝不预测；最后一段和返回
严格确认；任何反向、越界、限位、异常中停或最终位置不符都会停止两轴与采集。

## 5. 回退

如果更新后关键功能异常，关闭软件并执行：

```bash
install_root=/opt/zgcai-spectrometer
backup_dir="$(cat /home/radxa/zgcai-last-backup.txt)"

case "$backup_dir" in
  /home/radxa/zgcai-backups/pre-1df16e3-*) ;;
  *) echo "备份路径不符合本轮规则：$backup_dir" >&2; exit 1 ;;
esac

files='spectrometer/motor/controller.py
spectrometer/motor/scan_controller.py
spectrometer/motor/scan_timing.py
spectrometer/motor/lk_md2202.py'

while IFS= read -r relative_path; do
  if [ -e "$backup_dir/$relative_path" ]; then
    sudo install -m 0644 \
      "$backup_dir/$relative_path" \
      "$install_root/$relative_path"
  elif [ -e "$backup_dir/$relative_path.NOT_PRESENT" ]; then
    sudo rm -f -- "$install_root/$relative_path"
  else
    echo "缺少备份记录：$relative_path" >&2
    exit 1
  fi
done <<EOF
$files
EOF

cd /opt/zgcai-spectrometer
./.venv/bin/python -m py_compile \
  spectrometer/motor/controller.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/motor/lk_md2202.py
echo "ROLLBACK_OK $backup_dir"
```

回退只恢复本轮四个文件，不删除采集数据、设置、虚拟环境、原光谱仪软件或旧备份。
