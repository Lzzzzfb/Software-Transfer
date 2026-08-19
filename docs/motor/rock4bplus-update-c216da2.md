# ROCK 4B+ 扫描衔接优化增量更新与回退（c216da2）

本轮只替换 `spectrometer/motor/controller.py`。它将运动完成状态查询改为100 ms
独立超时、查询内部不重试、失败后20 ms重新轮询，以消除偶发丢帧造成的约1秒
换轴停顿。前一轴仍必须同时满足“停止”和“到达目标位置”才会启动下一轴，
不使用时间估算，也不允许两轴重叠运动。

原光谱仪控制逻辑、电机参数、虚拟环境、设置、数据和历史诊断目录均保留。

## 1. Windows 上传

先正常关闭ROCK 4B+上的软件。在Windows PowerShell中进入工程根目录并执行：

```powershell
ssh radxa@192.168.1.247 "mkdir -p /home/radxa/zgcai-update-c216da2/spectrometer/motor"
scp ".\deploy\rock4bplus\app\spectrometer\motor\controller.py" `
  "radxa@192.168.1.247:/home/radxa/zgcai-update-c216da2/spectrometer/motor/controller.py"
```

`scp`必须在Windows PowerShell中执行，不能粘贴到ROCK 4B+的SSH Bash中。

## 2. ROCK 4B+ 备份与安装

SSH登录ROCK 4B+，确认软件已经退出：

```bash
pgrep -af '/opt/zgcai-spectrometer/main.py' || true
```

如果仍显示进程，请从桌面正常关闭软件。确认没有进程后执行：

```bash
install_root=/opt/zgcai-spectrometer
update_file=/home/radxa/zgcai-update-c216da2/spectrometer/motor/controller.py
backup_dir="/home/radxa/zgcai-backups/pre-c216da2-$(date +%Y%m%d-%H%M%S)"
relative_path=spectrometer/motor/controller.py

mkdir -p "$backup_dir/spectrometer/motor"
cp -a "$install_root/$relative_path" "$backup_dir/$relative_path"
printf '%s\n' "$backup_dir" | tee /home/radxa/zgcai-last-backup.txt

sudo install -m 0644 "$update_file" "$install_root/$relative_path"
```

这一步不会覆盖原来的 `pre-2b4970f-*` 备份。

## 3. 安装后静态检查

```bash
cd /opt/zgcai-spectrometer

./.venv/bin/python -m py_compile spectrometer/motor/controller.py

sha256sum \
  /home/radxa/zgcai-update-c216da2/spectrometer/motor/controller.py \
  /opt/zgcai-spectrometer/spectrometer/motor/controller.py
```

两行SHA-256必须完全相同，预期值为：

```text
3617b6c6432e7c8e678befce100aeafbf9ddcef78082c6713c72bcc6e545615f
```

LK-MD2202供电且串口未被软件占用时，再执行原只读探针：

```bash
./.venv/bin/python -m spectrometer.motor.probe \
  --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --address 1 \
  --baud 9600 \
  --timeout 10 \
  --json
```

探针返回 `"ok": true` 后再启动桌面软件。

## 4. 实机验证

从安全位置和小行程开始，并保留随时切断驱动板电源的条件：

1. 断开光谱仪，设置X/Y步数1、步时0，先运行一次小矩阵纯电机扫描；
2. 使用相同参数连续运行至少3次，观察X/Y换轴衔接是否稳定；
3. 确认前一轴完全停止后下一轴才启动，任何时候都没有两轴重叠运动；
4. 再测试分步和非零步时，确认每处只保留用户设置的步时；
5. 连接光谱仪运行一轮联动扫描，确认采集、保存、返回和绘图均正常；
6. 导出诊断包，检查每段运动摘要中的耗时、状态查询次数和读取失败次数。

本轮优化只减少查询丢帧带来的额外等待，不改变电机实际运行时间。若仍有停顿，
请保留本次诊断包；新的运动摘要可直接区分电机运动耗时和串口状态读取失败。

## 5. 回退

如果更新后关键功能异常，关闭软件并执行：

```bash
install_root=/opt/zgcai-spectrometer
backup_dir="$(cat /home/radxa/zgcai-last-backup.txt)"
relative_path=spectrometer/motor/controller.py

case "$backup_dir" in
  /home/radxa/zgcai-backups/pre-c216da2-*) ;;
  *) echo "备份路径不符合本轮规则：$backup_dir" >&2; exit 1 ;;
esac

sudo install -m 0644 "$backup_dir/$relative_path" "$install_root/$relative_path"

cd /opt/zgcai-spectrometer
./.venv/bin/python -m py_compile spectrometer/motor/controller.py
echo "ROLLBACK_OK $backup_dir"
```

回退只恢复本轮一个文件，不删除数据、设置、虚拟环境、原光谱仪软件或任何旧备份。
