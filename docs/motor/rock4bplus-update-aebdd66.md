# ROCK 4B+ 扫描流畅度优化增量更新与回退（aebdd66）

本轮只替换四个运行文件：

- `spectrometer/diagnostics/bundle_exporter.py`
- `spectrometer/motor/scan.py`
- `spectrometer/motor/scan_controller.py`
- `spectrometer/motor/transport.py`

主要变化：步时为0时，同一轴的一次完整行程只发送一条物理运动命令，避免把
`X/Y步数`拆成多个没有等待意义的短命令；步时大于0时仍严格保留用户设置的子步
数量和每步等待。串口、Modbus队列、响应拼帧、超时和重试统一移到电机后台线程，
降低光谱仪首轮高负载对电机通信的影响。诊断包可以自动关联同一次扫描的全部采集
轮次。

原光谱仪控制逻辑、预测交接安全参数、最终严格位置校验、返回起点、急停、限位、
电机设置、虚拟环境、用户设置、采集数据和历史诊断目录均保留。

## 1. Windows上传

先从桌面正常关闭ROCK 4B+上的软件。在Windows PowerShell中进入工程根目录：

```powershell
ssh radxa@192.168.1.247 "mkdir -p /home/radxa/zgcai-update-aebdd66/spectrometer/diagnostics /home/radxa/zgcai-update-aebdd66/spectrometer/motor"

scp ".\deploy\rock4bplus\app\spectrometer\diagnostics\bundle_exporter.py" `
  "radxa@192.168.1.247:/home/radxa/zgcai-update-aebdd66/spectrometer/diagnostics/bundle_exporter.py"

$files = @("scan.py", "scan_controller.py", "transport.py")
foreach ($file in $files) {
  scp ".\deploy\rock4bplus\app\spectrometer\motor\$file" `
    "radxa@192.168.1.247:/home/radxa/zgcai-update-aebdd66/spectrometer/motor/$file"
}
```

`scp`必须在Windows PowerShell中执行，不能粘贴到ROCK 4B+的SSH Bash中。

## 2. ROCK 4B+备份与安装

SSH登录ROCK 4B+，确认软件已经退出：

```bash
pgrep -af '/opt/zgcai-spectrometer/main.py' || true
```

确认没有软件进程后执行：

```bash
install_root=/opt/zgcai-spectrometer
update_root=/home/radxa/zgcai-update-aebdd66
backup_dir="/home/radxa/zgcai-backups/pre-aebdd66-$(date +%Y%m%d-%H%M%S)"

files='spectrometer/diagnostics/bundle_exporter.py
spectrometer/motor/scan.py
spectrometer/motor/scan_controller.py
spectrometer/motor/transport.py'

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

本轮备份目录独立生成，不会覆盖之前的`pre-1df16e3-*`等备份。

## 3. 安装后检查

```bash
cd /opt/zgcai-spectrometer

./.venv/bin/python -m py_compile \
  spectrometer/diagnostics/bundle_exporter.py \
  spectrometer/motor/scan.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/motor/transport.py

sha256sum \
  spectrometer/diagnostics/bundle_exporter.py \
  spectrometer/motor/scan.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/motor/transport.py
```

预期SHA-256依次为：

```text
b7fe9e8ab6756328122472a1e0edba3ee52204382ab961f148250503aca686a9  spectrometer/diagnostics/bundle_exporter.py
c0bda62b3d987334ab4a735e695398fdd8719d6de9c50219c43cac2d35bdbc23  spectrometer/motor/scan.py
fd5f3ab32361a34dd8c958b20fd44b02440efb19ac74afd7ac3325eea2c5f6ed  spectrometer/motor/scan_controller.py
11fb07cf18e3f215afd94ff19e9281cbcaa618b0b864223e124195d0b95b8d08  spectrometer/motor/transport.py
```

再执行不连接电机、不会运动的规划检查：

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c '
from spectrometer.motor.models import Axis, Position
from spectrometer.motor.scan import ScanParameters, build_scan_plan

zero = build_scan_plan(
    ScanParameters(10, 1, 2, 1, x_steps=10, y_steps=2, dwell_seconds=0),
    start=Position(0, 0),
)
timed = build_scan_plan(
    ScanParameters(10, 1, 2, 1, x_steps=10, y_steps=2, dwell_seconds=0.1),
    start=Position(0, 0),
)
assert len(zero.rounds[0].scan_moves) == 5
assert len(timed.rounds[0].scan_moves) == 34
assert [m.logical_substeps for m in zero.rounds[0].scan_moves if m.axis is Axis.X] == [10, 10, 10]
print("SCAN_SMOOTHNESS_IMPORT_OK")
'
```

## 4. 实机验证顺序

从安全位置、小矩阵和可立即切断驱动板电源的条件开始：

1. 断开光谱仪，以`X=1 mm、Y=0.5 mm、n=1、m=1、X步数=10、Y步数=2、步时=0`运行纯电机扫描；确认每个完整X/Y行程连续运动；
2. 参数不变，只将步时改为`0.1 s`；确认X仍分10步、Y仍分2步，每步间隔约100 ms；
3. 连续运行五轮纯电机扫描，确认无随机长停顿、越界、反向或无法返回；
4. 连接光谱仪，以10 ms积分时间连续运行五轮联动扫描，重点观察第一轮流畅度；
5. 验证最终严格位置、返回本轮起点、急停、限位、用户停止和断线行为；
6. 导出诊断包，确认`bundle-manifest.json`中存在同一扫描的`scan_id`和全部`related_acquisition_ids`；
7. 核对完整帧等于持久化帧，缺帧、错误帧和重新同步为0。

步时为0时，`X/Y步数`仍保存在扫描参数和诊断事件中，但不再制造没有等待意义的
短运动；步时大于0时，子步和等待语义不变。

## 5. 回退

如果更新后关键功能异常，关闭软件并执行：

```bash
install_root=/opt/zgcai-spectrometer
backup_dir="$(cat /home/radxa/zgcai-last-backup.txt)"

case "$backup_dir" in
  /home/radxa/zgcai-backups/pre-aebdd66-*) ;;
  *) echo "备份路径不符合本轮规则：$backup_dir" >&2; exit 1 ;;
esac

files='spectrometer/diagnostics/bundle_exporter.py
spectrometer/motor/scan.py
spectrometer/motor/scan_controller.py
spectrometer/motor/transport.py'

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
  spectrometer/diagnostics/bundle_exporter.py \
  spectrometer/motor/scan.py \
  spectrometer/motor/scan_controller.py \
  spectrometer/motor/transport.py
echo "ROLLBACK_OK $backup_dir"
```

回退只恢复本轮四个文件，不删除采集数据、设置、虚拟环境、原光谱仪软件或旧备份。
