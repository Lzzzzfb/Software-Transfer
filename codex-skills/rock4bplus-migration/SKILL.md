---
name: rock4bplus-migration
description: "将现有软件可靠迁移到 Radxa ROCK 4B+（Debian 12 Bookworm ARM64），覆盖工作区保护、依赖与 Qt/Python 适配、USB/串口、性能与数据完整性、部署、SSH/SCP、诊断和实机验收。用于评估、设计、实施、排障或复盘 ROCK 4B+ 软件迁移；以 Python/Qt 为重点，也适用于其他软件的通用迁移盘点。"
---

# ROCK 4B+ 软件迁移

把迁移视为“平台适配 + 运行部署 + 实机验证”，不要把它简化成复制源码或安装
依赖。先保护用户资产并建立证据，再设计和实施。

## 核心保证

执行迁移时始终遵守：

- 先确认源项目、迁移副本、允许写入范围和未提交文件。
- 优先在专用副本实施；未经明确授权不修改原项目。
- 先核对 Debian 12 ARM64 官方包，再决定 pip、源码编译或交叉编译。
- 把硬件候选身份、协议身份和物理设备身份分开。
- 把输入、解析、持久化、处理、显示和导出速率分开测量。
- 不用丢弃完整数据换取界面流畅，不用放宽协议校验掩盖错误。
- 自动测试不能代替 ROCK 4B+ 实机验收。
- 明确区分 Windows 电脑命令与 ROCK 4B+ 板端命令。
- 对安装包、udev、用户组和系统目录修改说明影响并取得授权。
- 对无法由上位机保证的硬件或传输边界明确说明。

## 工作流

### 1. 保护工作区

先执行只读检查：

```text
git status --short
git diff --name-only
git log -10 --oneline
rg --files
```

记录：

- 原项目与迁移副本；
- 禁止修改目录；
- 已跟踪但未提交的文件；
- 未跟踪资料和数据；
- 当前测试基线；
- 用户授权的远程和系统操作。

发现脏工作区时不要清理、还原或批量暂存。使用明确路径编辑和暂存。

复制 [迁移基线清单](templates/migration-baseline-checklist.md) 作为工作记录。

### 2. 盘点软件边界

识别：

- 入口和生命周期；
- Python/C/C++/Node 等运行时；
- Qt、图形、音视频或浏览器依赖；
- Windows 专用 API、路径、注册表、COM 和打包逻辑；
- USB、串口、GPIO、I2C、SPI、摄像头或其他硬件；
- 线程、进程、队列和数据路径；
- 配置、日志、缓存和用户数据；
- 构建、安装、启动、升级和回退；
- 可以自动验证和必须实机验证的部分。

只读取与本次迁移有关的内容，不做无关重构。

### 3. 核对 ROCK 4B+ 环境

在板端先采集系统证据：

```bash
cat /etc/os-release
uname -m
python3 --version
ldd --version
id
df -h
```

有桌面 GUI 时再检查 `DISPLAY`、`XDG_SESSION_TYPE`、屏幕尺寸和 Qt 平台插件。
涉及 Python、Debian 包、glibc、venv、字体或分辨率时，读取
[环境与依赖](references/environment-and-dependencies.md)。

涉及 Qt 绑定、图形会话、事件循环或绘图时，读取
[Python 与 Qt](references/python-qt-migration.md)。

### 4. 形成迁移设计

在修改代码前提交可审阅设计，至少明确：

- 保留、适配、替换和删除的组件；
- ARM64 依赖来源；
- 平台兼容层；
- 硬件发现和身份；
- 数据与线程/进程边界；
- Linux 用户路径；
- 部署目录和安装方式；
- 失败、恢复和回退；
- 自动测试与实机验收矩阵。

给出 2–3 个合理方案及取舍，获得确认后再实施。

### 5. 分层实施

遵循：

```text
失败测试
→ 最小实现
→ 针对性测试
→ 完整回归
→ 部署副本同步
→ 板端验证
```

把平台差异集中在适配层。高吞吐硬件软件应让接收/持久化主通道独立于 GUI、
绘图和大型导出。

涉及 USB、串口或二进制协议时，读取
[USB、串口与协议](references/usb-serial-and-protocol.md)。

涉及卡顿、低 FPS、丢帧、保存慢或数据完整性时，读取
[性能与数据完整性](references/performance-and-data-integrity.md)。

### 6. 分层验证

按顺序验证：

```text
静态检查
→ 自动测试
→ 无硬件模拟
→ 板端依赖自检
→ 单功能硬件探针
→ 正式 GUI 实机
→ 长时间运行
→ 保存和停止
→ 断线重连
→ 故障恢复
```

使用 [实机验收矩阵](templates/hardware-validation-matrix.md) 记录环境、命令、
预期、实际结果、证据路径和结论。

### 7. 证据驱动排障

先定位故障层：

```text
环境/依赖
→ 图形会话
→ 驱动/权限
→ 原始输入
→ 协议拆包
→ 控制响应
→ 持久化
→ 处理
→ GUI 显示
→ 导出
→ 部署操作
```

一次只改变一个层级。为每个假设定义一个最小判别探针、预期的正反结果和
下一步。不要同时改协议、绘图和存储。

需要工具选择、飞行记录器或验收方法时，读取
[诊断与验收](references/diagnostics-and-acceptance.md)。

### 8. 部署和远程操作

建立只含运行所需内容的部署目录；排除测试、文档、开发环境、构建缓存和用户
数据。使用构建工具生成并校验部署副本，避免手工复制漂移。

涉及 `/opt`、apt、venv、udev、桌面快捷方式、SSH、SCP、升级或回退时，
读取 [部署与远程操作](references/deployment-and-remote-operations.md)。

从 [命令目录](templates/command-catalog.md) 复制命令时，先确认命令属于
Windows 电脑还是 ROCK 4B+。

### 9. 交付和复盘

交付：

- 迁移设计与实施计划；
- 最小部署包；
- 安装、启动、升级和回退命令；
- 自动测试结果；
- 实机验收报告；
- 已知限制和保证边界；
- 用户文件保护结果。

遇到新问题时使用 [事故复盘模板](templates/incident-retrospective.md)。先写入
具体项目；只有跨项目有效且有证据支持的结论，才提升到本技能。

## 决策规则

### 是否交叉编译

按以下顺序选择：

1. Debian 12 ARM64 官方包；
2. 架构无关 Python 包；
3. 有兼容 wheel 的 venv 包；
4. 板端原生构建；
5. 最后才考虑交叉编译。

不要因为厂商提供交叉编译教程就默认采用。

### GUI 低 FPS

先比较：

- 输入帧率；
- 完整解析帧率；
- 持久化帧率；
- 显示发布帧率；
- 实际完成绘制帧率；
- 页面可见与隐藏时 CPU；
- 主线程与工作线程 CPU；
- 离屏基准与真实桌面绘制。

只有最后两层低时，才把问题归到绘图或显示路径。

### 缺帧或异常帧

以最靠近输入且观察全部数据的层为权威。显示抽帧、队列覆盖和 GUI 节流不能
生成真实缺帧告警。只按结构、精确长度、协议状态和可验证校验拒绝数据；单一
强度阈值只能标记可疑，不能直接删除。

### 保存卡顿

检查 GUI 是否同步执行：

- 每帧处理；
- 大型矩阵复制；
- CSV/XLSX 生成；
- 压缩、哈希或 `fsync`；
- 无界日志和信号。

优先使用顺序追加缓存、独立处理/导出进程、原子发布和有界队列。

## 参考路由

- 系统、glibc、apt、venv、XDG、字体与屏幕：
  [environment-and-dependencies.md](references/environment-and-dependencies.md)
- PySide6/PyQt6、QtSerialPort、`DISPLAY`、绘图和线程：
  [python-qt-migration.md](references/python-qt-migration.md)
- USB、udev、设备身份、二进制协议、ACK 和重新同步：
  [usb-serial-and-protocol.md](references/usb-serial-and-protocol.md)
- 速率分层、进程隔离、spool、导出和完整性：
  [performance-and-data-integrity.md](references/performance-and-data-integrity.md)
- 部署目录、安装、桌面入口、SSH/SCP 和回退：
  [deployment-and-remote-operations.md](references/deployment-and-remote-operations.md)
- 假设矩阵、诊断记录、实机验收和复盘：
  [diagnostics-and-acceptance.md](references/diagnostics-and-acceptance.md)

## 模板

- [迁移基线清单](templates/migration-baseline-checklist.md)
- [实机验收矩阵](templates/hardware-validation-matrix.md)
- [事故复盘模板](templates/incident-retrospective.md)
- [命令目录](templates/command-catalog.md)
