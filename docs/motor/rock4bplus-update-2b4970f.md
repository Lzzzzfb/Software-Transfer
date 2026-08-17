# ROCK 4B+ 增量更新与回退（2b4970f）

本轮更新只替换以下4个运行文件：

- `spectrometer/motor/controller.py`
- `spectrometer/motor/scan_controller.py`
- `spectrometer/ui/main_window.py`
- `spectrometer/ui/motor_panel.py`

更新内容包括：运动结束焦点修复、脱离卡死停止状态复核、无光谱仪纯电机扫描和现有诊断日志记录。原光谱仪软件、虚拟环境、设置、数据和历史诊断目录均保留。

## 1. Windows 上传

先正常关闭 ROCK 4B+ 上的软件。在 Windows PowerShell 中进入工程根目录，然后执行：

```powershell
ssh radxa@192.168.1.247 "mkdir -p /home/radxa/zgcai-update-2b4970f"
scp -r ".\deploy\rock4bplus\app" "radxa@192.168.1.247:/home/radxa/zgcai-update-2b4970f/"
```

`scp`必须在 Windows PowerShell 中执行，不能粘贴到 ROCK 4B+ 的 SSH Bash 中。

## 2. ROCK 4B+ 备份与安装

SSH 登录 ROCK 4B+。先确认软件已经退出：

```bash
pgrep -af '/opt/zgcai-spectrometer/main.py' || true
```

如果仍显示进程，请先从桌面正常关闭软件。确认没有进程后执行：

```bash
install_root=/opt/zgcai-spectrometer
update_root=/home/radxa/zgcai-update-2b4970f/app
backup_dir="/home/radxa/zgcai-backups/pre-2b4970f-$(date +%Y%m%d-%H%M%S)"

files=(
  spectrometer/motor/controller.py
  spectrometer/motor/scan_controller.py
  spectrometer/ui/main_window.py
  spectrometer/ui/motor_panel.py
)

for relative_path in "${files[@]}"; do
  mkdir -p "$(dirname "$backup_dir/$relative_path")"
  cp -a "$install_root/$relative_path" "$backup_dir/$relative_path"
done

printf '%s\n' "$backup_dir" | tee /home/radxa/zgcai-last-backup.txt

for relative_path in "${files[@]}"; do
  sudo install -m 0644 "$update_root/$relative_path" "$install_root/$relative_path"
done
```

终端应打印一个类似下面的备份目录：

```text
/home/radxa/zgcai-backups/pre-2b4970f-20260817-120000
```

## 3. 安装后检查

执行 Python 编译和文件哈希核对：

```bash
cd /opt/zgcai-spectrometer

./.venv/bin/python -m py_compile \
  spectrometer/motor/controller.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/ui/main_window.py \
  spectrometer/ui/motor_panel.py

for relative_path in \
  spectrometer/motor/controller.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/ui/main_window.py \
  spectrometer/ui/motor_panel.py
do
  sha256sum \
    "/home/radxa/zgcai-update-2b4970f/app/$relative_path" \
    "/opt/zgcai-spectrometer/$relative_path"
done
```

每组两行哈希必须一致。再执行离屏界面检查：

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c '
from spectrometer.qt import QtWidgets
from spectrometer.ui.motor_panel import MotorPanel
app = QtWidgets.QApplication([])
panel = MotorPanel()
assert panel.scan_start_button.text() == "开始扫描"
print("UI_SMOKE_OK", panel.scan_start_button.text())
'
```

LK-MD2202 已供电且串口未被软件占用时，执行只读探针：

```bash
./.venv/bin/python -m spectrometer.motor.probe \
  --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --address 1 \
  --baud 9600 \
  --timeout 10 \
  --json
```

探针返回 `"ok": true` 后再启动桌面软件。

## 4. 本轮实机验证顺序

1. 分别执行一次安全的 X/Y 小距离定距运动，确认运动结束后“X行程”不再自动选中；
2. 在安全位置执行“脱离卡死”，确认结束后不提示必须重连，并可以直接机械回零；
3. 不连接光谱仪，先使用小行程、`n=1`、`m=1`、X/Y步数1、步时0验证纯电机扫描和返回；
4. 再验证分步、步时和多轮扫描；
5. 打开“诊断”页检查扫描参数、轮次和结果，导出诊断包并确认包含本次日志；
6. 连接光谱仪后执行一轮联动扫描，确认连续采集、保存、返回和绘图均正常。

纯电机扫描不生成独立 `scan-*.json`。首次运动必须从安全位置和小行程开始，并保留随时切断驱动板电源的条件。

## 5. 回退

如果更新后关键功能异常，关闭软件并执行：

```bash
install_root=/opt/zgcai-spectrometer
backup_dir="$(cat /home/radxa/zgcai-last-backup.txt)"

case "$backup_dir" in
  /home/radxa/zgcai-backups/pre-2b4970f-*) ;;
  *) echo "备份路径不符合本轮规则：$backup_dir" >&2; exit 1 ;;
esac

files=(
  spectrometer/motor/controller.py
  spectrometer/motor/scan_controller.py
  spectrometer/ui/main_window.py
  spectrometer/ui/motor_panel.py
)

for relative_path in "${files[@]}"; do
  sudo install -m 0644 "$backup_dir/$relative_path" "$install_root/$relative_path"
done

cd /opt/zgcai-spectrometer
./.venv/bin/python -m py_compile \
  spectrometer/motor/controller.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/ui/main_window.py \
  spectrometer/ui/motor_panel.py

echo "ROLLBACK_OK $backup_dir"
```

回退只恢复本轮4个文件，不删除数据、设置、虚拟环境或原光谱仪软件。
