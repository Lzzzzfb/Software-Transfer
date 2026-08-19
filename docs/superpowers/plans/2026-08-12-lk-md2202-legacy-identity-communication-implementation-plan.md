# LK-MD2202 旧固件身份与通信配置兼容实施计划

日期：2026-08-12

设计依据：`docs/superpowers/specs/2026-08-11-lk-md2202-legacy-identity-communication-design.md`

目标分支：`codex/lk-md2202-integration`

实施前提交：`d20045c docs: design legacy LK-MD2202 compatibility`

## 1. 范围与保护

- 仅修改 `D:\codex\光谱仪 - Linux转移版`。
- 不修改光谱仪采集、保存、扫描计划、运动命令、限位或界面逻辑。
- 不写驱动板寄存器；本次板端首轮验收仅执行 Modbus `0x03` 只读请求。
- 不暂存、移动、覆盖或清理现有 Office 文件、CSV/XLSX 数据、PDF、`tmp/` 和诊断包。
- 所有 Git 暂存使用明确文件路径。
- 源码验证完成后才运行部署构建脚本；不手工编辑部署镜像副本。

## 2. 任务 1：先补协议失败测试

修改 `tests/motor/test_lk_md2202.py`：

1. 保留标准设备名通过测试。
2. 加入实测的 16 寄存器旧固件身份签名并断言通过统一身份入口。
3. 断言旧固件真实设备名保持原始解码结果，不伪造 `LK-MD2202`。
4. 改变旧签名任一关键寄存器后断言拒绝。
5. 断言空名、全 `FFFF` 和响应过短拒绝。
6. 断言 `(1, 3, 0, 0, 0)` 解码成地址 1、9600、8N1 无校验。
7. 参数化覆盖未知码和非 8N1 原始码的拒绝行为。

先运行：

```powershell
python -m pytest -q tests/motor/test_lk_md2202.py
```

预期：因统一身份入口尚不存在、通信原始码尚未映射而失败。

## 3. 任务 2：补工作流失败测试

修改：

- `tests/motor/test_lk_md2202_probe.py`
- `tests/motor/test_motor_controller.py`

覆盖：

1. 只读探针收到精确旧固件签名后继续读取 X/Y 配置、通信配置和 X/Y 状态并成功结束。
2. 探针输出的身份名称不被伪造。
3. 控制器收到精确签名后进入现有完整初始化链，只有所有只读步骤成功后才连接。
4. 近似签名不进入初始化链。
5. 修改通信参数后的重连使用同一身份入口。
6. 现有测试中的通信响应改用说明书和实机一致的原始码 `0,0,0`。

运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_lk_md2202_probe.py tests/motor/test_motor_controller.py
```

## 4. 任务 3：最小协议实现

修改 `spectrometer/motor/lk_md2202.py`：

1. 定义只读的精确旧固件身份签名常量。
2. 保持 `decode_identity()` 只负责忠实解码。
3. 新增统一受支持身份解码入口：标准名称或精确旧签名通过，其余明确抛错。
4. 不向 `DeviceIdentity` 增加原始寄存器，也不改写 `name`。
5. 增加通信原始枚举码映射。
6. 把实测 `(0,0,0)` 映射为 `(8,1,0)`。
7. 对手册已定义但应用不支持的非 8N1 组合及未知码明确报错。

只做满足失败测试的最小实现，不调整其他寄存器语义。

## 5. 任务 4：统一调用入口

修改：

- `spectrometer/motor/controller.py`
- `spectrometer/motor/probe.py`

将以下路径改用同一受支持身份入口：

- 自动串口探测；
- 命令行只读探针；
- 地址或波特率修改后的身份重连。

现有 X 配置 → Y 配置 → 通信配置 → X 状态 → Y 状态初始化顺序保持不变。

## 6. 任务 5：分层验证

依次运行：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q tests/motor/test_lk_md2202.py
python -m pytest -q tests/motor/test_lk_md2202_probe.py tests/motor/test_motor_controller.py
python -m pytest -q tests/motor
python -m pytest -q
python -m compileall -q main.py spectrometer tools
git diff --check
```

定向测试先证明协议与工作流；完整测试证明光谱仪和扫描未回归。

## 7. 任务 6：生成并校验部署镜像

在源码全部通过后运行：

```powershell
python tools/build_rock4bplus_deploy.py
python tools/build_rock4bplus_deploy.py --check
python -m compileall -q deploy/rock4bplus/app
python -m pytest -q tests/test_rock4bplus_deploy.py
```

检查源文件与 `deploy/rock4bplus/app/` 对应文件一致，且没有把测试、设计、用户数据或诊断文件复制进部署载荷。

## 8. 任务 7：提交与板端只读验收

提交前检查：

```powershell
git status --short
git diff --name-only
git diff --cached --name-only
```

只暂存计划、协议实现、控制器、探针、测试和构建脚本生成的部署副本。创建独立提交并推送当前分支。

板端更新按“停止应用 → 上传到用户目录 → 备份精确目标文件 → `sudo install` → 编译检查 → 正式只读探测”的顺序进行。正式探测不注入诊断函数：

```bash
cd /opt/zgcai-spectrometer
./.venv/bin/python -m spectrometer.motor.probe \
  --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --address 1 \
  --baud 9600 \
  --json
```

只有输出 `"ok": true` 后，才进入 GUI 自动连接验证；本次修复不直接开始运动。

## 9. 停止条件

出现以下任一情况时停止扩大改动并保留证据：

- 实测身份寄存器与已记录签名不同；
- 通信寄存器不再是手册定义的枚举范围；
- 完整回归显示光谱仪或扫描逻辑发生变化；
- 部署镜像无法由构建脚本一致生成；
- 板端正式只读探测出现写请求、运动或无法回退的文件覆盖。
