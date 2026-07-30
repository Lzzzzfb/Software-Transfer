# 部署与远程运维

## 目录

- [划分主机职责](#划分主机职责)
- [设计板端目录](#设计板端目录)
- [生成部署载荷](#生成部署载荷)
- [安装系统依赖](#安装系统依赖)
- [安装应用与虚拟环境](#安装应用与虚拟环境)
- [安装 udev 与桌面入口](#安装-udev-与桌面入口)
- [区分 GUI 启动与 SSH 运维](#区分-gui-启动与-ssh-运维)
- [使用 SCP 传输文件](#使用-scp-传输文件)
- [执行增量更新](#执行增量更新)
- [回滚与版本核对](#回滚与版本核对)
- [交付检查清单](#交付检查清单)

## 划分主机职责

始终先说明命令在哪一端执行：

| 场景 | 执行位置 | 典型工具 |
|---|---|---|
| 构建部署载荷 | Windows 开发机、项目根目录 | Git、Python、PowerShell |
| 上传文件 | Windows 开发机 | `scp` |
| 安装依赖和文件 | ROCK 4B+ 的 SSH Bash | `apt`、`install` |
| 启动 GUI | ROCK 4B+ 的本地图形桌面 | 桌面快捷方式、终端 |
| 看日志和资源 | Windows 经 SSH 连接板端 | `tail`、`top`、`dmesg` |
| 下载诊断包 | Windows 开发机 | `scp` |

不要在板端 Bash 中执行带 `.\deploy\...` 的 Windows 路径。不要把脚本文件当目录执行
`cd install.sh`；进入脚本所在目录后运行 `sudo ./install.sh`，或直接传入脚本的完整路径。

## 设计板端目录

推荐把程序与用户数据分开：

```text
/opt/<application>/
├── app/
├── .venv/
├── main.py
└── run.sh

$HOME/.config/<vendor>/<application>/
$HOME/.local/state/<vendor>/<application>/
$HOME/<application>-Data/
```

- `/opt` 保存只由安装流程更新的程序和虚拟环境。
- `$HOME/.config` 保存用户配置。
- `$HOME/.local/state` 保存日志、运行状态与诊断元数据。
- 用户选择的数据目录保存正式采集结果。
- 不要让普通运行过程修改源码目录。

## 生成部署载荷

把部署目录视为可复现产物，而不是手工拼装目录。只包括板端运行所需内容：

```text
deploy/rock4bplus/
├── app/
├── requirements*.txt
├── install.sh
├── run.sh
├── *.rules
└── *.desktop
```

不要放入设计文档、测试数据、诊断包、Git 元数据或个人技能。

提供一个构建脚本，从受控源文件生成部署载荷，并支持只检查不写入的模式：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
```

检查至少覆盖：

- 源文件与部署副本哈希一致；
- 必需文件存在；
- 没有多余运行文件；
- 启动入口和包导入路径一致；
- 依赖清单与实际导入一致。

## 安装系统依赖

优先使用 Debian 12 ARM64 原生包解决 Qt、串口和系统库依赖：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pyqt6 \
  python3-pyqt6.qtserialport python3-numpy python3-scipy \
  python3-openpyxl python3-xlsxwriter python3-pyqtgraph
```

实际包名以目标镜像仓库为准。安装后用导入测试验证，不以 `apt` 返回成功作为唯一证据。

## 安装应用与虚拟环境

安装脚本应：

1. 检查 ARM64、Debian 版本和 Python 版本；
2. 创建固定应用目录；
3. 用明确文件清单安装源码；
4. 创建 venv；
5. 在需要复用 apt Python 包时使用 `--system-site-packages`；
6. 安装仅由 pip 提供且 ARM64 可用的依赖；
7. 运行导入与语法检查；
8. 输出实际启动入口和版本。

示例：

```bash
sudo install -d -m 0755 /opt/example-app
sudo install -m 0644 /home/radxa/update/main.py /opt/example-app/main.py
python3 -m venv --system-site-packages /opt/example-app/.venv
/opt/example-app/.venv/bin/python -m compileall -q /opt/example-app
```

不要在未知目标上递归覆盖整个 `/opt`。先解析并显示精确目标，再执行安装。

## 安装 udev 与桌面入口

udev 规则只授予应用需要的访问能力。优先让设备属于 `dialout`，并让用户加入该组：

```bash
sudo usermod -aG dialout radxa
sudo install -m 0644 99-example.rules /etc/udev/rules.d/99-example.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

重新登录后组权限才可能生效。不要默认设置 `MODE="0666"`，也不要为多台相同设备创建冲突的固定公共链接。

桌面快捷方式必须：

- 使用绝对 `Exec` 路径；
- 不依赖 SSH 的环境变量；
- 写日志到用户可写目录；
- 能在本地图形会话获得 `DISPLAY`、Xauthority 和桌面会话变量。

## 区分 GUI 启动与 SSH 运维

SSH 终端通常没有图形显示：

```bash
echo "DISPLAY=${DISPLAY:-<empty>}"
echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-<empty>}"
```

若显示为空且会话类型是 `tty`，直接启动 Qt GUI 可能出现：

```text
qt.qpa.xcb: could not connect to display
Aborted
```

此时：

- 从板端桌面图标或本地图形终端启动 GUI；
- 用 SSH 查看进程、日志、USB、温度和磁盘；
- 仅在明确配置 X11 转发且性能可接受时从 SSH 显示 GUI；
- 离屏测试使用 `QT_QPA_PLATFORM=offscreen`，不要把离屏结果当作真实显示性能。

## 使用 SCP 传输文件

上传命令在 Windows PowerShell 执行：

```powershell
Set-Location -LiteralPath 'D:\path\to\project'
scp '.\deploy\rock4bplus\app\main.py' 'radxa@192.168.1.245:/home/radxa/update/main.py'
```

下载诊断包也在 Windows PowerShell 执行：

```powershell
scp 'radxa@192.168.1.245:/home/radxa/Desktop/诊断压缩包/example.zip' 'C:\Users\user\Desktop\example.zip'
```

在 Windows OpenSSH 中，目标写成完整文件路径通常比以反斜杠结尾的目录更稳妥。文件名含中文、空格或特殊字符时，分别引用远端源和本地目标。

## 执行增量更新

增量更新按以下顺序进行：

1. 停止采集并退出应用；
2. 从 Windows 上传到板端用户临时目录；
3. 检查上传文件哈希或内容标记；
4. 用 `sudo install -m 0644` 安装到精确位置；
5. 运行 `compileall` 和导入测试；
6. 从桌面重新启动；
7. 验证版本、功能和诊断指标。

不要直接让 `scp` 以 root 身份覆盖运行目录。临时目录加 `install` 能保留清晰的权限边界和操作记录。

## 回滚与版本核对

发布前保留可识别的上一个版本。回滚单位应是完整、相互兼容的文件集合，不能只回滚半个协议链路。

启动后至少核对：

```bash
pid="$(pgrep -n -f '/opt/example-app/main.py')"
ps -p "$pid" -o lstart,cmd
grep -R "EXPECTED_VERSION_MARKER" /opt/example-app
```

推荐在诊断包中记录：

- Git 提交或构建版本；
- 部署文件哈希；
- Python、Qt、绑定和关键依赖版本；
- 操作系统与内核；
- 启动时间和命令。

## 交付检查清单

- [ ] Windows 与 Linux 源码维护边界明确。
- [ ] 部署载荷可由脚本重新生成并通过 `--check`。
- [ ] 安装脚本只写入列明的板端目录。
- [ ] 系统依赖和 Python 依赖均有导入验证。
- [ ] udev 权限遵守最小权限原则。
- [ ] 桌面入口可在本地图形会话启动。
- [ ] SSH 只承担运维或已明确配置图形转发。
- [ ] 上传、安装、下载命令标注了执行主机。
- [ ] 增量更新有版本核对和回滚方案。
- [ ] 诊断包能证明板端实际运行的是目标版本。
