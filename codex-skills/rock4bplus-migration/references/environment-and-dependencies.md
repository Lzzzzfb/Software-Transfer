# ROCK 4B+ 环境与依赖

## 目录

1. 基线探针
2. 依赖来源顺序
3. apt、pip 与 venv
4. glibc 与 wheel
5. 编译和交叉编译
6. Linux 用户路径
7. 字体、屏幕与图形库
8. 资源和稳定性
9. 验证清单

## 1. 基线探针

先在板端只读采集：

```bash
cat /etc/os-release
uname -m
python3 --version
ldd --version | head -n 1
dpkg --print-architecture
id
df -h
free -h
```

记录精确输出，不根据“ROCK 4B+”名称推断系统镜像、架构或 Python。

常见目标为 Debian 12 Bookworm、`aarch64`/`arm64`、Python 3.11，但用户可能
刷入其他镜像。环境不符时先决定重刷、适配还是改变目标，不要继续安装。

## 2. 依赖来源顺序

优先级：

1. Debian 12 官方 ARM64 包；
2. 架构无关的 Python 包；
3. 明确支持当前 Python、ARM64 和 glibc 的 wheel；
4. 板端原生构建；
5. 交叉编译。

每个依赖记录：

| 字段 | 内容 |
|---|---|
| 名称和用途 | 为什么运行时需要 |
| 原版本 | 源平台实际版本 |
| ARM64 来源 | apt / wheel / source |
| 目标版本 | 锁定或范围 |
| 系统库 | glibc、Qt、OpenGL 等 |
| 导入自检 | 最小命令 |
| 回退 | 删除或恢复方法 |

不要把开发和测试依赖放入板端运行清单。

## 3. apt、pip 与 venv

推荐边界：

- apt 管理 Python、Qt、系统 GUI、驱动绑定和需要系统 ABI 的库；
- venv 管理应用级纯 Python 包；
- 使用系统 PyQt6 时，可用 `python3 -m venv --system-site-packages`；
- 不在系统 Python 中直接 `pip install`；
- 不在 pip 中重复安装 apt 已提供的 Qt 绑定；
- 安装脚本最后执行真实 venv 的导入自检。

示例：

```bash
python3 -m venv --system-site-packages /opt/APP/.venv
/opt/APP/.venv/bin/python -c \
  "from PyQt6 import QtCore, QtWidgets; print(QtCore.QT_VERSION_STR)"
```

若 `--system-site-packages` 引入过多不可控包，改用纯 venv，并为所有编译型
依赖重新验证 ARM64 wheel 或构建方案。

## 4. glibc 与 wheel

wheel 可下载不等于可运行。核对：

- Python ABI，例如 `cp311`；
- 架构，例如 `aarch64`；
- manylinux 标签；
- wheel 要求的最低 glibc；
- 依赖的系统动态库。

本次案例中，目标 Debian 12 使用 glibc 2.36，而当时可用的 PySide6 ARM64
wheel 要求更高 glibc；最终采用 Debian 官方 PyQt6。通用经验是比较实际 ABI，
而不是只比较 Python 包名和版本。

出现 `GLIBC_x.y not found` 时，不要用软链接或替换系统 libc 绕过。选择兼容包、
板端构建或不同系统镜像。

## 5. 编译和交叉编译

采用交叉编译前回答：

- 官方 ARM64 包是否已满足需求？
- 依赖是否纯 Python？
- 板端原生构建时间是否可接受？
- 是否拥有与目标 rootfs 完全一致的 sysroot？
- 图形、GPU 和插件是否有运行时验证条件？
- 后续谁维护工具链？

厂商的 Qt 交叉编译教程适用于需要自定义 Qt 或 C++ ABI 的场景，不意味着
Python/Qt 应用必须交叉编译。优先减少自建二进制供应链。

## 6. Linux 用户路径

系统程序和用户数据分离：

```text
/opt/APP/                         程序和 venv
~/.config/VENDOR/APP/             配置
~/.local/state/VENDOR/APP/        日志和诊断
~/.cache/VENDOR/APP/              可删除缓存
~/APP-Data/                       用户输出
```

遵循：

- 日常运行不需要 `sudo`；
- 用户输出不写入 `/opt`；
- 配置使用临时文件和原子替换；
- 日志目录不可写时明确降级；
- 空间检查针对实际缓存和输出分区；
- 不让 Windows `APPDATA` 决定 Linux 路径。

## 7. 字体、屏幕与图形库

GUI 项目核对：

- Noto Sans CJK 等中文字体；
- 1920×1080 主目标；
- 1366×768 基本可操作性；
- X11/Wayland 会话；
- `xcb`、OpenGL/EGL 等平台库；
- 高 DPI 和窗口最小尺寸；
- 桌面快捷方式的绝对 `Exec` 路径。

不要把 SSH 无 `DISPLAY` 误诊为 Qt 包损坏。先区分图形会话与依赖问题。

## 8. 资源和稳定性

记录：

- CPU 核心数和单线程性能；
- 可用内存与 swap；
- 数据分区持续写入能力；
- 温度与降频；
- USB 拓扑和总线速率；
- 显示分辨率和刷新负载。

总 CPU 空闲不能排除单线程瓶颈。温度只在与频率、负载和时间线对齐后用于
判断，不因一次高值直接改算法。

## 9. 验证清单

- [ ] Debian、ARM64、Python、glibc 已记录
- [ ] 所有运行依赖有明确来源
- [ ] 编译型依赖已验证 ABI
- [ ] venv 导入自检通过
- [ ] 配置、状态、缓存和数据路径可写
- [ ] 普通用户可以运行
- [ ] 中文字体和目标分辨率已实机检查
- [ ] 磁盘、内存和温度有基线
- [ ] 安装可重复执行
- [ ] 有依赖失败和回退说明
