# ROCK 4B+ 软件迁移知识库与 Codex 技能实施计划

日期：2026-07-30
依据：`docs/superpowers/specs/2026-07-30-rock4bplus-migration-knowledge-skill-design.md`

## 1. 实施原则

1. 只修改 `D:\codex\光谱仪 - Linux转移版` 及用户已确认的个人技能目录。
2. 不修改原 Windows 项目 `D:\codex\光谱仪`。
3. 不暂存、覆盖或删除当前 Excel、Word、PDF、诊断包和采集数据。
4. 当前项目中的 `codex-skills/rock4bplus-migration` 是技能权威源。
5. 个人技能目录只安装通过校验的权威源副本。
6. 案例事实、经验建议和待实机确认项必须明确区分。
7. 通用技能不得把 CH569W 专用协议当作其他项目的默认前提。
8. `deploy/rock4bplus` 不包含知识库或 Codex 技能。

## 2. 阶段 0：保护基线

### 任务 0.1：记录 Git 和受保护文件状态

执行：

```powershell
git status --short
git log -1 --oneline
```

确认以下内容保持未暂存：

- `CCD_CMOS模块采集卡(下位机)函数表20251027.xlsx`
- `上位机软件光谱仪控制功能.docx`
- `radxa_rock4bp_product_brief.pdf`
- `诊断包/`
- 工作区根目录现有 CSV/XLSX 数据

### 任务 0.2：检查个人技能目标

只读检查：

```powershell
Test-Path "C:\Users\74997\.codex\skills\rock4bplus-migration"
```

若已有同名目录，先比较来源和内容，不得直接覆盖未知用户资产。

## 3. 阶段 1：初始化技能骨架

### 任务 1.1：使用官方技能初始化器

使用 skill-creator 自带的 `init_skill.py`，在项目内创建权威源：

```powershell
python C:\Users\74997\.codex\skills\.system\skill-creator\scripts\init_skill.py `
  rock4bplus-migration `
  --path codex-skills `
  --resources references `
  --interface display_name="ROCK 4B+ 软件迁移" `
  --interface short_description="将 Python/Qt 等软件可靠迁移到 ROCK 4B+" `
  --interface default_prompt="Use $rock4bplus-migration to assess and plan this software migration to Radxa ROCK 4B+."
```

不创建示例占位文件，不添加无用途的 README、安装指南或变更日志。

### 任务 1.2：检查生成结构

预期至少包含：

```text
codex-skills/rock4bplus-migration/
├── SKILL.md
├── agents/openai.yaml
└── references/
```

检查 `agents/openai.yaml`：

- 字符串全部加引号；
- `display_name`、`short_description` 和 `default_prompt` 与技能一致；
- `default_prompt` 明确包含 `$rock4bplus-migration`；
- 不声明不存在的 MCP 依赖。

## 4. 阶段 2：编写项目案例手册

新增：

```text
docs/rock4bplus-migration-retrospective.md
```

### 任务 2.1：建立时间线和结论摘要

基于迁移设计、后续设计文档、Git 提交和实机诊断结论，整理：

- 初始范围和验收目标；
- 环境与依赖选择；
- 首次板端安装和启动；
- 串口协议与控制故障；
- GUI、绘图和存储性能问题；
- 全量采集和诊断体系；
- 文件生命周期与产品语义；
- 最终能力和保证边界。

### 任务 2.2：逐项记录问题

每个问题严格包含：

```text
现象 / 证据 / 排除项 / 根因 / 修复 / 验证 / 通用经验 / 适用边界
```

至少覆盖：

- 脏工作区和双项目隔离；
- PySide6/glibc 与 PyQt6；
- SSH 无 `DISPLAY`；
- VID:PID 非唯一身份；
- udev 和 `dialout`；
- 协议长度、字节序、CheckSum 与单帧验证；
- 拆包重新同步和 ACK 超时；
- 4 FPS、90 FPS 与缺帧误报；
- QPainter 与 PyQtGraph；
- 自动保存卡顿和导出进程隔离；
- 全量 spool；
- `.zgs` 意外保留；
- 背景扣除与可疑帧判断；
- 飞行记录器和诊断 ZIP；
- SCP、SSH 和安装目录操作错误；
- 部署副本漂移；
- CSV/Excel 内容验证。

### 任务 2.3：增加可复制工具表

按电脑端和板端分别列出：

- 环境探针；
- USB/串口探针；
- 性能探针；
- Qt 探针；
- 协议探针；
- 部署与下载命令；
- 自动测试和数据核对。

命令必须说明是否只读、是否需要 `sudo` 和如何解释输出。

## 5. 阶段 3：编写技能主流程

修改：

```text
codex-skills/rock4bplus-migration/SKILL.md
```

### 任务 3.1：前置元数据

YAML 只包含：

- `name`
- `description`

`description` 同时说明：

- 技能做什么；
- 目标设备是 Radxa ROCK 4B+；
- 何时使用；
- 以 Python/Qt 为主，也适用于其他软件的通用迁移盘点、部署和诊断。

### 任务 3.2：核心阶段门禁

正文使用命令式写法并保持精简，包含：

1. 保护工作区；
2. 盘点项目；
3. 核对板端环境；
4. 形成并确认迁移设计；
5. 分层实施；
6. 分层验证；
7. 证据驱动诊断；
8. 交付和复盘。

### 任务 3.3：参考路由

主文件直接链接所有参考和模板，并明确何时读取。引用最多一层，不允许参考
文件再要求读取更深层文件。

### 任务 3.4：安全边界

明确禁止：

- 未确认边界就修改源项目；
- 覆盖用户修改；
- 把显示 FPS 当采集 FPS；
- 放宽校验掩盖协议错误；
- 丢弃完整帧换取 GUI 性能；
- 未验证就采用交叉编译、OpenGL 或重写；
- 混用 Windows PowerShell 和板端 Bash 命令；
- 自动执行有状态板端操作而不说明影响。

## 6. 阶段 4：编写按需参考

所有参考文件超过 100 行时在顶部提供目录。

### 任务 4.1：环境与依赖

新增：

```text
references/environment-and-dependencies.md
```

内容：

- ROCK 4B+ 系统盘点；
- Debian 12 ARM64 依赖决策；
- apt、pip 和 venv；
- glibc 与 wheel；
- 编译/交叉编译选择；
- XDG 路径、字体、分辨率和磁盘；
- 环境检查命令。

### 任务 4.2：Python/Qt

新增：

```text
references/python-qt-migration.md
```

内容：

- PySide6/PyQt6 兼容层；
- Qt 枚举、Signal/Slot、QtSerialPort；
- 图形会话与 `DISPLAY`；
- offscreen 测试；
- QPainter/PyQtGraph；
- GUI 线程边界；
- 页面可见性和刷新策略。

### 任务 4.3：USB、串口与协议

新增：

```text
references/usb-serial-and-protocol.md
```

内容：

- 驱动、枚举、权限和 udev；
- 候选设备与物理身份；
- 协议事实验证；
- 长度、字节序、校验和包号；
- 分片、重新同步、ACK 和尾帧；
- 可观测计数与错误恢复。

### 任务 4.4：性能和完整性

新增：

```text
references/performance-and-data-integrity.md
```

内容：

- 分层速率模型；
- 采集、持久化、处理、显示和导出隔离；
- 有界队列；
- spool 和原子发布；
- CPU、GIL、绘图和温度；
- 异常帧策略；
- 保证边界。

### 任务 4.5：部署和远程操作

新增：

```text
references/deployment-and-remote-operations.md
```

内容：

- 最小部署包；
- `/opt` 与用户目录；
- 安装、启动和桌面快捷方式；
- SSH/SCP 执行位置；
- udev 和用户组；
- 升级、回退和一致性；
- 常见命令错误。

### 任务 4.6：诊断和验收

新增：

```text
references/diagnostics-and-acceptance.md
```

内容：

- 假设矩阵；
- 最小判别探针；
- 飞行记录器；
- 诊断包；
- 自动测试、模拟和实机验收；
- 功能、性能、完整性、恢复和部署五维矩阵；
- 复盘和知识提升规则。

## 7. 阶段 5：编写模板

新增：

```text
codex-skills/rock4bplus-migration/templates/
```

### 任务 5.1：迁移基线清单

新增 `migration-baseline-checklist.md`，提供可复制的：

- 项目边界；
- Git 状态；
- 依赖；
- 平台 API；
- 硬件；
- 数据；
- 部署；
- 验收范围。

### 任务 5.2：实机验收矩阵

新增 `hardware-validation-matrix.md`，包含：

- 环境；
- 功能；
- 性能；
- 完整性；
- 恢复；
- 部署；
- 证据路径；
- 通过/失败/待确认状态。

### 任务 5.3：事故复盘

新增 `incident-retrospective.md`，强制记录：

- 现象；
- 时间线；
- 假设；
- 证据；
- 根因；
- 修复；
- 回归；
- 适用边界；
- 是否提升到通用技能。

### 任务 5.4：命令目录

新增 `command-catalog.md`，按以下结构提供模板：

- 执行位置；
- 权限；
- 是否修改状态；
- 命令；
- 预期输出；
- 判读；
- 常见错误。

## 8. 阶段 6：技能验证

### 任务 6.1：基本规范校验

执行：

```powershell
python C:\Users\74997\.codex\skills\.system\skill-creator\scripts\quick_validate.py `
  codex-skills\rock4bplus-migration
```

修复所有：

- YAML 错误；
- 命名错误；
- 缺失字段；
- 占位符；
- 无效链接；
- `openai.yaml` 不一致。

### 任务 6.2：内容静态检查

检查：

```powershell
rg -n "TODO|TBD|PLACEHOLDER" codex-skills docs/rock4bplus-migration-retrospective.md
git diff --check
```

人工核对：

- 所有参考从 `SKILL.md` 一层可达；
- 没有 README 等多余技能文件；
- 没有密码、密钥或完整诊断数据；
- 没有把 CH569W 规则泛化；
- 电脑端和板端命令分区明确。

### 任务 6.3：场景前向验证

不创建新的用户任务或远程修改板端。使用三种静态场景检查技能输出路径：

1. Python/PySide6 GUI 迁移到 ROCK 4B+；
2. 非 Qt Python 服务迁移到 ROCK 4B+；
3. USB 串口实时采集软件出现“GUI 低 FPS，但设备输入正常”。

确认技能分别选择正确参考，而不是加载全部内容或套用光谱仪协议。

## 9. 阶段 7：安装个人技能

### 任务 7.1：安装前检查

再次检查目标：

```text
C:\Users\74997\.codex\skills\rock4bplus-migration
```

目标不存在时创建。若已存在，仅在确认它就是本项目先前安装的同名技能后更新；
否则停止并报告冲突。

### 任务 7.2：复制已验证技能

将整个权威源复制到个人技能目录，排除临时文件和缓存。

### 任务 7.3：安装后核对

- 对权威源和安装副本逐文件计算 SHA-256；
- 确认文件集合与哈希完全一致；
- 对安装副本再次运行 `quick_validate.py`；
- 确认 `SKILL.md` 和 `agents/openai.yaml` 可读取；
- 向用户说明新任务中可使用 `$rock4bplus-migration`。

## 10. 阶段 8：项目回归与提交

执行：

```powershell
python -m pytest -q
python -m compileall -q main.py spectrometer tools
python tools/build_rock4bplus_deploy.py --check
git diff --check
git status --short
```

确认：

- 知识库和技能没有进入 `deploy/rock4bplus`；
- 运行源码没有改变；
- 原 Windows 项目没有改变；
- 受保护文件继续保持原未暂存状态。

明确暂存：

```text
docs/rock4bplus-migration-retrospective.md
codex-skills/rock4bplus-migration/**
docs/superpowers/plans/2026-07-30-rock4bplus-migration-knowledge-skill-implementation-plan.md
```

建议提交：

```text
docs: add reusable ROCK 4B+ migration playbook
```

## 11. 完成交付

向用户报告：

- 项目案例手册路径；
- 项目内技能源路径；
- 个人技能安装路径；
- 调用方式；
- 技能适用范围与保证边界；
- 技能更新方法；
- 校验和回归结果；
- Git 提交号；
- 受保护用户文件仍未暂存。
