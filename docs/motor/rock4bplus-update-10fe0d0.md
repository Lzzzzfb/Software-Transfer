# ROCK 4B+ 电机进程隔离与扫描延期导出更新（10fe0d0）

本轮将 LK-MD2202 电机串口、Modbus 通信、运动状态轮询和整段扫描路径放入独立
进程。光谱仪采集仍使用原有采集进程；每轮采集停止后先封存 `.zgs` 恢复文件，
立即释放光谱仪并让电机返回、开始下一轮，全部电机运动完成后再逐轮生成正式
CSV/Excel。这样光谱仪首轮初始化、绘图和文件生成不会再阻塞电机换轴衔接。

未连接光谱仪时仍可执行纯电机扫描。原光谱仪协议和采集控制逻辑、LK-MD2202
参数、机械回零、限位、脱离卡死、15 mm 行程、用户设置、虚拟环境、历史数据和
诊断目录均保留。

本轮只安装以下九个运行文件：

- `spectrometer/acquisition/controller.py`
- `spectrometer/motor/factory.py`（新增）
- `spectrometer/motor/manifest.py`
- `spectrometer/motor/process_proxy.py`（新增）
- `spectrometer/motor/process_scan_runner.py`（新增）
- `spectrometer/motor/process_worker.py`（新增）
- `spectrometer/motor/scan_controller.py`
- `spectrometer/ui/main_window.py`
- `spectrometer/ui/motor_panel.py`

## 1. Windows 上传

先从桌面正常关闭 ROCK 4B+ 上的软件。在 Windows PowerShell 中进入工程根目录：

```powershell
ssh radxa@192.168.1.247 "mkdir -p /home/radxa/zgcai-update-10fe0d0"
scp -r ".\deploy\rock4bplus\app" `
  "radxa@192.168.1.247:/home/radxa/zgcai-update-10fe0d0/"
```

`scp` 必须在 Windows PowerShell 中执行，不能粘贴到 ROCK 4B+ 的 SSH Bash 中。

## 2. ROCK 4B+ 备份与安装

SSH 登录 ROCK 4B+，确认软件已经退出：

```bash
pgrep -af '/opt/zgcai-spectrometer/main.py' || true
pgrep -af 'zgcai-motor' || true
```

两条命令都不应显示正在运行的软件进程。然后执行：

```bash
install_root=/opt/zgcai-spectrometer
update_root=/home/radxa/zgcai-update-10fe0d0/app
backup_dir="/home/radxa/zgcai-backups/pre-10fe0d0-$(date +%Y%m%d-%H%M%S)"

files='spectrometer/acquisition/controller.py
spectrometer/motor/factory.py
spectrometer/motor/manifest.py
spectrometer/motor/process_proxy.py
spectrometer/motor/process_scan_runner.py
spectrometer/motor/process_worker.py
spectrometer/motor/scan_controller.py
spectrometer/ui/main_window.py
spectrometer/ui/motor_panel.py'

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
  sudo install -D -m 0644 \
    "$update_root/$relative_path" \
    "$install_root/$relative_path"
done <<EOF
$files
EOF

echo "UPDATE_OK 10fe0d0"
```

新增文件会记录 `.NOT_PRESENT` 标记，因此本轮可以准确回退；旧备份不会被覆盖。

## 3. 安装后静态检查

```bash
cd /opt/zgcai-spectrometer

./.venv/bin/python -m py_compile \
  spectrometer/acquisition/controller.py \
  spectrometer/motor/factory.py \
  spectrometer/motor/manifest.py \
  spectrometer/motor/process_proxy.py \
  spectrometer/motor/process_scan_runner.py \
  spectrometer/motor/process_worker.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/ui/main_window.py \
  spectrometer/ui/motor_panel.py

sha256sum \
  spectrometer/acquisition/controller.py \
  spectrometer/motor/factory.py \
  spectrometer/motor/manifest.py \
  spectrometer/motor/process_proxy.py \
  spectrometer/motor/process_scan_runner.py \
  spectrometer/motor/process_worker.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/ui/main_window.py \
  spectrometer/ui/motor_panel.py
```

预期 SHA-256 依次为：

```text
175ed80742dba54f677b109b496105c75f7df702e06564c88ddc701743e7b6b3  spectrometer/acquisition/controller.py
15447e14b28a120a3169cf559e703d20c966eaaf1a7555c51691ed5b98d329b8  spectrometer/motor/factory.py
c0dc2c5e8bdac72326f119bbd7600e0be0d157eee92842010fc4d15bdcb2a9cd  spectrometer/motor/manifest.py
9451f78f037433ec5acd019e24a39fd10729d57635495e45b4f1dc77807c0fb0  spectrometer/motor/process_proxy.py
f636d829abbad6a73bc730e47a39641ea293a91c453219fd8f2ffd5abca1e627  spectrometer/motor/process_scan_runner.py
322db7c30530db247eb1c6dcab0f68dc64c5fbbb80d15230cd7c3a7854d25d35  spectrometer/motor/process_worker.py
359300e80ea20ec5b00e4e23184ad701a1e9aa3c13c66bc9030025025e2b877c  spectrometer/motor/scan_controller.py
9a6a860e72c638009c8eab2b5ae7fd4186b0c4c859f3a949f445bae43ec52e4f  spectrometer/ui/main_window.py
024105886170b2e3113b163bea471beea570792e9adaa000212f3963dbbeaf42  spectrometer/ui/motor_panel.py
```

执行不连接电机、不会产生运动的离屏检查：

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c '
from spectrometer.qt import QtWidgets
from spectrometer.motor.factory import create_motor_controller
from spectrometer.motor.process_proxy import MotorProcessProxy
from spectrometer.ui.motor_panel import MotorPanel

app = QtWidgets.QApplication([])
panel = MotorPanel()
controller = create_motor_controller(
    settings_path="/tmp/zgcai-motor-update-smoke.json",
    platform="linux",
    simulation=False,
)
assert isinstance(controller, MotorProcessProxy)
assert panel.scan_progress.text() == "等待开始"
controller.shutdown()
print("PROCESS_UI_SMOKE_OK")
'
```

LK-MD2202 供电且软件未启动时，再执行原有只读探针；必须返回 `"ok": true`：

```bash
./.venv/bin/python -m spectrometer.motor.probe \
  --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --address 1 \
  --baud 9600 \
  --timeout 10 \
  --json
```

## 4. 实机验证顺序

首次验证使用小矩阵、安全位置，并保留立即切断驱动板电源的条件：

1. 启动软件并打开诊断页，确认日志出现“电机控制进程已启动：PID …”；
2. 不连接光谱仪，以 X/Y 步数 1、步时 0 连续运行五次小矩阵纯电机扫描，确认
   X/Y 换轴稳定、返回起点正常、没有新增随机长停顿；
3. 设置 X 步数 10、步时 0，确认一次 X 行程连续运行，不因逻辑子步产生停顿；
4. 设置 X 步数 10、步时 0.1 s，确认仍按 10 个子步运行，每步等待约 0.1 s；
5. 连接光谱仪运行五轮连续联动扫描：运动期间只采集和封存；每轮封存后电机应
   立即返回并进入下一轮；全部运动结束后界面才显示“正在导出光谱”；
6. 导出完成后检查每轮正式 CSV/Excel 均存在，完整帧等于持久化帧，缺帧、错误
   帧和重新同步均为 0；
7. 验证扫描中停止、返回中停止和最终导出中停止。已经封存的数据必须继续导出，
   不得因停止操作被删除；
8. 最后复测急停、X/Y 零点限位、机械回零、脱离卡死和普通定距运动。

如果仍观察到停顿，请导出新的诊断包。新日志包含子进程 PID、路径段计划时刻、
完成时刻、封存时刻和延期导出结果，可区分电机执行、光谱采集和文件生成耗时。

## 5. 回退

如更新后关键功能异常，先正常关闭软件，再执行：

```bash
install_root=/opt/zgcai-spectrometer
backup_dir="$(cat /home/radxa/zgcai-last-backup.txt)"

case "$backup_dir" in
  /home/radxa/zgcai-backups/pre-10fe0d0-*) ;;
  *) echo "备份路径不符合本轮规则：$backup_dir" >&2; exit 1 ;;
esac

files='spectrometer/acquisition/controller.py
spectrometer/motor/factory.py
spectrometer/motor/manifest.py
spectrometer/motor/process_proxy.py
spectrometer/motor/process_scan_runner.py
spectrometer/motor/process_worker.py
spectrometer/motor/scan_controller.py
spectrometer/ui/main_window.py
spectrometer/ui/motor_panel.py'

while IFS= read -r relative_path; do
  if [ -e "$backup_dir/$relative_path" ]; then
    sudo install -D -m 0644 \
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
./.venv/bin/python -m compileall -q spectrometer
echo "ROLLBACK_OK $backup_dir"
```

回退只恢复本轮九个文件，不删除采集数据、用户设置、虚拟环境、原光谱仪软件、
历史诊断目录或任何旧备份。
