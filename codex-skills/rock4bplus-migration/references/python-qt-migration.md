# Python 与 Qt 迁移

## 目录

1. 绑定选择
2. 兼容层
3. 图形会话
4. Qt 平台插件
5. 线程与进程
6. 实时绘图
7. 页面可见性
8. 离屏测试
9. 退出和资源释放
10. 验证清单

## 1. 绑定选择

先根据目标系统可安装性选择 Qt 绑定，再适配应用：

- Debian 官方 PyQt6 通常比不兼容 glibc 的 PySide6 wheel 更可靠；
- 不同时在同一进程导入多个 Qt 绑定；
- QtSerialPort 应与主 Qt 绑定来自同一体系；
- 系统 Qt 包由 apt 管理，应用 venv 可通过 `--system-site-packages` 使用；
- Windows 原项目与 Linux 专用版可分开维护，避免为“单源码跨平台”扩大范围。

## 2. 兼容层

集中封装：

- `Signal`、`Slot`、`Property`；
- `exec()`/`exec_()`；
- PyQt6 scoped enum 与 Qt5/PySide 风格扁平枚举；
- `QSerialPort` 错误枚举；
- `QImage.Format`、`QPainter.RenderHint` 等枚举；
- 文件对话框和信号参数差异。

不要在业务文件中散布绑定判断。兼容层应有导入和最小控件测试。

对 PyQt6 scoped enum 使用明确映射或兼容函数，不用版本字符串猜测：

```python
def enum_member(owner, group, name):
    if hasattr(owner, name):
        return getattr(owner, name)
    return getattr(getattr(owner, group), name)
```

## 3. 图形会话

SSH 登录通常得到：

```text
DISPLAY=<empty>
XDG_SESSION_TYPE=tty
```

此时运行 GUI 会出现 `could not connect to display` 或 `xcb` 初始化失败。正确
做法：

- 在 ROCK 4B+ 本地桌面、应用菜单或桌面快捷方式启动 GUI；
- 用 SSH 查看日志、CPU、温度和串口状态；
- 只有明确拥有当前桌面会话授权时才设置 `DISPLAY`/Xauthority；
- 不把硬编码 `DISPLAY=:0` 写入通用启动脚本。

## 4. Qt 平台插件

遇到 `xcb` 错误时按顺序检查：

1. 是否存在图形会话；
2. `DISPLAY` 是否属于当前用户；
3. Qt 绑定来自哪个解释器；
4. 平台插件是否存在；
5. `ldd` 是否有缺失动态库；
6. X11/Wayland 会话类型；
7. 是否混装 pip Qt 和 apt Qt。

重新安装 Qt 只能解决确实缺包的问题，不能解决无图形会话。

## 5. 线程与进程

GUI 线程只负责：

- 事件处理；
- 控件状态；
- 有界的轻量数据更新；
- 最终绘制调用。

不要在 GUI 线程执行：

- 持续串口解析；
- 每个输入单元的重计算；
- 大型 CSV/XLSX；
- 压缩、哈希和长时间 `fsync`；
- 无界日志追加；
- 阻塞式停止等待。

QThread 能隔离事件循环，但 Python 计算仍可能争用 GIL。持续高吞吐接收、解析、
持久化或复杂处理需要评估独立进程。

跨进程只传必要数据。不要把每个高维数组无界地塞进 `multiprocessing.Queue`。

## 6. 实时绘图

排查顺序：

1. 测量实际完成的 paint，不测定时器触发；
2. 比较实时页与隐藏页 CPU；
3. 比较离屏渲染与桌面绘制；
4. 检查每帧是否重建曲线、坐标轴和图例；
5. 检查 Python 对象和数组复制；
6. 检查自动范围和十字光标更新频率；
7. 检查抗锯齿、线宽和降采样。

高效模式：

- 复用一个曲线对象并调用 `setData()`；
- 使用连续 NumPy 数组；
- 不显示每点 symbol；
- 关闭不必要抗锯齿；
- `clipToView`；
- 使用保留峰值的降采样；
- 十字光标限频；
- 只在范围真正变化时更新自动范围；
- 页面不可见时停止显示处理，但保持采集。

不要默认启用 OpenGL。先在 ROCK 4B+ 实机验证驱动、稳定性、黑屏和导出行为。

## 7. 页面可见性

实时数据页隐藏时：

- 不做显示处理和 `setData()`；
- 只保留最新待显示单元；
- 采集、持久化和诊断继续；
- 切回后立即显示最新数据；
- 不在主线程积压历史显示事件。

这通常能快速判断 GUI 瓶颈：如果切到诊断页后 CPU 和卡顿消失，数据主通道
未必有问题。

## 8. 离屏测试

使用：

```bash
QT_QPA_PLATFORM=offscreen /opt/APP/.venv/bin/python benchmark.py
```

适合：

- 验证控件能构造；
- 比较算法和数据转换；
- 测量无桌面绘制成本；
- CI 截图。

局限：

- 不代表 X11 合成和实际显示成本；
- 不覆盖 GPU/驱动；
- 不证明输入事件和桌面插件正常；
- 不证明真实刷新率。

## 9. 退出和资源释放

关闭流程应：

1. 停止接受新 UI 操作；
2. 请求设备/工作进程停止；
3. 继续消费尾部控制和数据；
4. 有界等待；
5. 保留未完成恢复文件；
6. 断开信号并释放串口/进程；
7. 最后关闭窗口。

超时不能让界面永久卡住，也不能把失败伪装成成功。

## 10. 验证清单

- [ ] 目标解释器只加载一种 Qt 绑定
- [ ] Signal/Slot/enum 兼容测试通过
- [ ] QtSerialPort 来自同一绑定
- [ ] 桌面、菜单和启动脚本均验证
- [ ] SSH 无 `DISPLAY` 的错误语义明确
- [ ] GUI 线程没有高吞吐持久化和大型导出
- [ ] 实际 paint FPS 有独立计数
- [ ] 隐藏页面不会浪费显示处理
- [ ] OpenGL 未经实机验证不会默认开启
- [ ] 停止和退出有超时、错误和恢复语义
