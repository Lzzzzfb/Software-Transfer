# ROCK 4B+ 迁移命令目录

先确认提示符。`PS C:\...>` 是 Windows PowerShell；`user@host:~$` 是
ROCK 4B+ Bash。不要跨环境复制带本地路径的命令。

## Windows 电脑：只读项目检查

权限：普通用户。状态修改：否。

```powershell
git status --short
git diff --name-only
git log -10 --oneline
rg --files
python -m pytest -q
python -m compileall -q main.py
```

判读：先记录未提交文件；测试失败时先区分原有失败与迁移回归。

## Windows 电脑：上传部署包

权限：普通用户。状态修改：在板端创建上传目录。

```powershell
scp -r ".\deploy\rock4bplus" "user@BOARD_IP:/home/user/rock4bplus-upload"
```

判读：本地源路径必须存在。该命令不能在板端 SSH Shell 中执行。

常见错误：

- 在 Bash 中使用 `.\deploy\...`；
- 把反斜杠当作 Bash 续行；
- 目标路径引号与结尾反斜杠组合错误；
- 在错误的 Windows 当前目录执行。

## Windows 电脑：下载诊断包

权限：普通用户。状态修改：在电脑创建文件。

```powershell
scp "user@BOARD_IP:/home/user/Desktop/diagnostics/bundle.zip" .
```

含空格或中文时分别完整引用远端源和本地目标，不把两段引号连在一起。

## ROCK 4B+：系统环境

权限：普通用户。状态修改：否。

```bash
cat /etc/os-release
uname -m
python3 --version
ldd --version
id
df -h
echo "DISPLAY=${DISPLAY:-<empty>}"
echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-<empty>}"
```

判读：

- `uname -m` 应与目标架构一致；
- SSH 中 `DISPLAY=<empty>` 通常不能显示桌面 GUI；
- 磁盘不足会同时影响安装、缓存和导出。

## ROCK 4B+：USB、串口和内核

权限：普通用户；`dmesg` 在部分系统需要 `sudo`。状态修改：否。

```bash
lsusb
lsusb -t
ls -l /dev/ttyACM*
udevadm info --query=property --name=/dev/ttyACM0
sudo dmesg --since "10 minutes ago"
```

判读：确认驱动、USB 速率、设备节点、VID:PID、权限和重置/断线错误。
不要把 `/dev/ttyACM0` 当作永久物理身份。

## ROCK 4B+：进程、线程、温度

权限：普通用户。状态修改：否。

```bash
pid="$(pgrep -n -f '/opt/APP/main.py')"
top -H -b -n 5 -d 1 -p "$pid"
ps -p "$pid" -o pid,stat,pcpu,pmem,rss,vsz,etime,cmd
cat /sys/class/thermal/thermal_zone0/temp
```

判读：

- 总 CPU 仍空闲但主线程接近 100%，说明单线程/GIL/绘图瓶颈；
- 页面隐藏后 CPU 明显下降，说明可见绘制路径是主要成本；
- 温度值通常为毫摄氏度，需要除以 1000。

## ROCK 4B+：安装

权限：需要 `sudo`。状态修改：安装包、udev、用户组和桌面入口。

```bash
cd /home/user/rock4bplus-upload
chmod +x install.sh run.sh
sudo ./install.sh
```

执行前核对目录。不要运行 `cd /home/user/.../install.sh`，因为脚本是文件而非
目录。安装完成后按脚本提示重新登录或重插设备。

## ROCK 4B+：启动与日志

权限：普通用户。状态修改：创建用户日志/配置。

```bash
/opt/APP/run.sh
tail -n 150 "$HOME/.local/state/VENDOR/APP/startup.log"
```

桌面 GUI 优先从板端桌面快捷方式启动；SSH 用于观察日志和性能。

## Qt 离屏基准

权限：普通用户。状态修改：可能生成临时图片或结果。

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPATH=/opt/APP \
/opt/APP/.venv/bin/python \
/home/user/benchmark_plot_render.py
```

判读：离屏快不代表 X11 桌面绘制一定快；必须结合真实页面 CPU 和实际 paint
事件。

## 操作前安全检查

任何带 `sudo`、安装、覆盖、删除、移动或 udev 修改的命令，先记录：

- 精确目标绝对路径；
- 是否属于本次迁移范围；
- 是否可恢复；
- 当前用户和工作目录；
- 预期修改内容；
- 回退步骤。
