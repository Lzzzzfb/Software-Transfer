# ROCK 4B+ 光谱曲线精确绘制更新（88660c3）

本轮修复光谱曲线显示层可能丢失相邻窄峰、改变峰值比例或跨越 NaN/Inf 直接连线的问题。
采集、处理、存储、电机、扫描联动和界面布局均未改变。

主要变化：

- PyQtGraph 关闭自动降采样，保留可见范围裁剪，使用有限值断点；
- legacy 后端删除按绘图区宽度执行的全帧 `linspace` 抽样；
- legacy 每次从完整数组选择可见点和边界相邻点，并按 NaN/Inf 分段；
- 支持每台设备不同的实际像素数，不固定为 4096；
- 手动框选后保持缩放范围；双击恢复完整范围但不擅自开启持续自动缩放。

本轮只安装以下四个运行文件：

- `spectrometer/ui/plot_data.py`（新增）
- `spectrometer/ui/plot_widget.py`
- `spectrometer/ui/pyqtgraph_plot_widget.py`
- `spectrometer/ui/main_window.py`

## 1. Windows 上传

先在 ROCK 4B+ 桌面正常退出软件。然后在 Windows PowerShell 中进入工程根目录：

```powershell
ssh radxa@192.168.1.247 "mkdir -p /home/radxa/zgcai-update-88660c3"
scp -r ".\deploy\rock4bplus\app" `
  "radxa@192.168.1.247:/home/radxa/zgcai-update-88660c3/"
```

`scp` 必须在 Windows PowerShell 中执行，不能粘贴到 ROCK 4B+ 的 SSH Bash 中。

## 2. ROCK 4B+ 备份与安装

SSH 登录 ROCK 4B+，确认正式软件已退出：

```bash
pgrep -af '/opt/zgcai-spectrometer/main.py' || true
```

不应显示正在运行的正式软件进程。然后执行：

```bash
install_root=/opt/zgcai-spectrometer
update_root=/home/radxa/zgcai-update-88660c3/app
backup_dir="/home/radxa/zgcai-backups/pre-88660c3-$(date +%Y%m%d-%H%M%S)"

files='spectrometer/ui/plot_data.py
spectrometer/ui/plot_widget.py
spectrometer/ui/pyqtgraph_plot_widget.py
spectrometer/ui/main_window.py'

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

echo "UPDATE_OK 88660c3"
```

新增文件会记录 `.NOT_PRESENT`，因此可以准确回退；已有备份目录不会被覆盖。

## 3. 安装后静态检查

```bash
cd /opt/zgcai-spectrometer

./.venv/bin/python -m py_compile \
  spectrometer/ui/plot_data.py \
  spectrometer/ui/plot_widget.py \
  spectrometer/ui/pyqtgraph_plot_widget.py \
  spectrometer/ui/main_window.py

sha256sum \
  spectrometer/ui/plot_data.py \
  spectrometer/ui/plot_widget.py \
  spectrometer/ui/pyqtgraph_plot_widget.py \
  spectrometer/ui/main_window.py
```

当前更新文件的预期 SHA-256 为：

```text
81a906654cb1d75ba7a8ab226e4ee9df4f6229ccc46c02edbededea836672dd0  spectrometer/ui/plot_data.py
5738114b0b6e225acde2773a99e716fcf834ad4e8925eac44615074dd3abefa1  spectrometer/ui/plot_widget.py
f600e5b77c4f951f0e7cbd73d972ca4add529a8dbbd5fac59379f0cadc24d49c  spectrometer/ui/pyqtgraph_plot_widget.py
5c0da5e84e9177d7652b757298572019ee4f7c02c43caf3af1cf6c6598ed88d4  spectrometer/ui/main_window.py
```

## 4. 不连接硬件的精确绘图探针

下面的命令不会连接光谱仪或电机，也不会产生运动：

```bash
cd /opt/zgcai-spectrometer

QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c '
import numpy as np
from spectrometer.qt import QtWidgets
from spectrometer.ui.plot_data import visible_finite_segments
from spectrometer.ui.plot_widget import SpectrumPlotWidget
from spectrometer.ui.pyqtgraph_plot_widget import PyQtGraphSpectrumPlotWidget

app = QtWidgets.QApplication([])
x = np.arange(6144, dtype=np.float64)
y = np.zeros(x.size, dtype=np.float64)
y[[1945, 1948, 1951]] = [60000, 50000, 40000]
y[[618, 624, 633]] = [45000, 30000, 15000]

segments = visible_finite_segments(x, y, 1940, 1956)
visible = {
    int(x_value): float(y_value)
    for segment_x, segment_y in segments
    for x_value, y_value in zip(segment_x, segment_y)
    if x_value in (1945, 1948, 1951)
}
assert visible == {1945: 60000.0, 1948: 50000.0, 1951: 40000.0}

legacy = SpectrumPlotWidget()
legacy.update_device_curve(0, x, y, "legacy")
legacy.set_view_range(1940, 1956, -1000, 65000)
legacy.restore_initial_view()
assert legacy.device_curves[0].x.size == 6144
assert not legacy.auto_range_enabled

fast = PyQtGraphSpectrumPlotWidget()
fast.update_device_curve(0, x, y, "pyqtgraph")
item = fast.device_curves[0].item
assert item.opts["clipToView"] is True
assert item.opts["autoDownsample"] is False
assert item.opts["connect"] == "finite"
fast.set_view_range(1940, 1956, -1000, 65000)
fast.restore_initial_view()
assert not fast.auto_range_enabled

y_with_gap = y.copy()
y_with_gap[1948] = np.nan
fast.update_device_curve(0, x, y_with_gap, "finite-gap")
assert np.isnan(fast.device_curves[0].y[1948])
print("EXACT_PLOT_SMOKE_OK", legacy.device_curves[0].x.size, fast.device_curves[0].x.size)
'
```

预期最后输出：

```text
EXACT_PLOT_SMOKE_OK 6144 6144
```

## 5. 实机验证

启动软件后按以下顺序验证：

1. 先确认诊断页记录的实时绘图后端为 `pyqtgraph`；
2. 连接一台实际光谱仪连续采集，确认普通完整视图稳定；
3. 对包含相邻窄峰的区域框选放大，并与保存的原始 CSV/Excel 像素值对照；
4. 确认窄峰的数量、像素坐标、峰顶强度和高低顺序一致；
5. 保持放大状态继续采集，确认曲线更新但 X/Y 视图不跳回全局；
6. 双击恢复完整范围，确认后续新帧不会擅自持续改变范围；需要持续自动范围时，手动勾选
   “自动缩放”；
7. 如果存在不同像素数设备，分别连接并确认每台设备都显示完整数组；
8. 尽可能进行三台设备连续显示；设备不足时至少完成现有设备的长时间连续采集；
9. 最后复测电机手动运动、纯电机扫描和光谱联动扫描，确认本轮显示修改没有影响控制逻辑。

如果出现卡顿、曲线异常或其他回归，请立即导出诊断包，并记录当时连接设备数量、每台设备
实际像素数、显示后端、是否启用 airPLS 和是否处于放大状态。

## 6. 回退

如更新后出现关键异常，先关闭软件，再执行：

```bash
install_root=/opt/zgcai-spectrometer
backup_dir="$(cat /home/radxa/zgcai-last-backup.txt)"

case "$backup_dir" in
  /home/radxa/zgcai-backups/pre-88660c3-*) ;;
  *) echo "备份路径不符合本轮规则：$backup_dir" >&2; exit 1 ;;
esac

files='spectrometer/ui/plot_data.py
spectrometer/ui/plot_widget.py
spectrometer/ui/pyqtgraph_plot_widget.py
spectrometer/ui/main_window.py'

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

回退只恢复本轮四个文件，不删除采集数据、用户设置、虚拟环境、光谱仪原有控制逻辑、电机
代码、历史诊断目录或任何旧备份。

## 7. 开发侧验证记录

- 设计：`2996b26`
- 实施计划：`81682ca`
- 源码与测试：`17b5772`
- ROCK 部署副本：`88660c3`
- 自动测试：`516 passed`
- 部署副本校验：99 个运行文件一致
- 离屏多设备探针：2048、3648、6144 点三条曲线连续更新 100 帧，完成 100 次 paint 事件
- 已知警告：PyQtGraph 参数树依赖产生 5 条弃用警告，与本轮绘图逻辑无关
- 尚待完成：ROCK 4B+ 真实桌面、真实光谱仪和多设备实机验收
