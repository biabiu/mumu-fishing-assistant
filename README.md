# MuMu Fishing Assistant

一个面向 macOS + MuMu 安卓模拟器的本地钓鱼小游戏识别助手。它通过 scrcpy 视频流逐帧识别进度条、黄色区间和红色浮标，并在浮标接近或进入目标区时发送触摸操作。识别在本机完成，不需要大模型、云服务或录屏软件。

> 当前项目针对这款钓鱼界面做了颜色和区域标定。它不是通用的安卓 UI 自动化框架；换游戏、换界面或改分辨率时需要重新校准。

## 功能

- MuMu Player Pro 自动读取运行实例的 ADB 端口；旧版 MuMu 兼容常见端口；也可以手动指定 ADB。
- scrcpy H.264 视频流，最高 60 FPS。
- NumPy 颜色阈值识别深蓝色轨道、黄色目标区和红色浮标。
- 按最近浮标轨迹预测进入黄色区的时间；短暂漏检时保留轨迹。
- 浮标在目标区或预计即将入区时，点击冷却默认为 0.1 秒；无浮标时每次按 0.8–1.2 秒随机间隔兜底。
- macOS 置顶悬浮窗，可一键启停并查看运行状态。
- 点击坐标在按钮中心附近随机偏移（默认每轴 ±20 px），按住时长随机 50–120 ms；触摸通过后台线程发送，视频识别不会等待按住结束。

## 环境要求

- macOS（Apple Silicon 或 Intel）
- MuMu Player Pro 或旧版 MuMu，且安卓实例已启动
- Python 3.10 或更新版本
- Xcode Command Line Tools（用于本机编译悬浮窗）
- 网络连接（首次安装 Python 依赖时使用）

安装 Command Line Tools：

```bash
xcode-select --install
```

Python 可从 [python.org](https://www.python.org/downloads/macos/) 安装。安装后确认 `python3 --version` 显示 3.10 或更新版本。

## 安装和启动

1. 克隆项目并进入目录：

   ```bash
   git clone https://github.com/biabiu/mumu-fishing-assistant.git
   cd mumu-fishing-assistant
   ```

2. 运行安装器：

   ```bash
   ./install.command
   ```

   安装器会创建本地 `.venv`、安装 Python 依赖，并编译 `dist/MuMu钓鱼助手.app`。它也会在桌面创建一个指向该应用的快捷方式。

3. 启动 MuMu 安卓实例，并打开游戏的钓鱼界面。

4. 双击桌面上的 **MuMu钓鱼助手.app**，在置顶窗口点“启动钓鱼助手”。再次点击即可停止。关闭悬浮窗只会隐藏窗口；从桌面重新打开应用即可恢复显示。

## 命令行运行

安装后也可以从终端运行，便于查看诊断日志：

```bash
.venv/bin/python3 mumu_fishing_clicker.py --start
```

停止当前任务可按 `Ctrl-C`。也可查看参数：

```bash
.venv/bin/python3 mumu_fishing_clicker.py --help
```

悬浮窗启动时的日志位于：

```text
~/Library/Logs/MumuFishingAssistant.log
```

## MuMu 与 ADB 连接

脚本默认按以下顺序查找 ADB：

1. `--adb /path/to/adb`
2. `MUMU_ADB` 环境变量
3. MuMu Player Pro 自带 ADB
4. 旧版 MuMu 自带 ADB
5. `PATH` 中的 `adb`

MuMu Player Pro 的实例端口会动态变化。脚本通过 `mumutool info all` 获取正在运行实例的端口并连接，不需要手工填写端口。旧版 MuMu 会尝试常见的 `127.0.0.1:16384` 和 `127.0.0.1:5555`。若使用其他模拟器或单独安装的 Android Platform Tools，可设置：

```bash
export MUMU_ADB="/absolute/path/to/adb"
```

如有多个安卓设备，可先列出设备，再显式选一个：

```bash
adb devices -l
.venv/bin/python3 mumu_fishing_clicker.py --serial 127.0.0.1:PORT
```

## 校准按钮与识别区域

当前参考画布为 **1080×1920**，启动钓鱼按钮默认中心为 `(540, 1580)`。坐标是安卓画布坐标，不是 Mac 桌面坐标；移动或缩放 MuMu 窗口不会改变坐标。视频流分辨率会自动映射到参考画布。

如果按钮位置不同，可传入新的中心坐标：

```bash
.venv/bin/python3 mumu_fishing_clicker.py --button 540 1580
```

首次运行建议先不带 `--start`，观察视频流、日志和按钮映射；确认位置正确后再使用 `--start`。进度条 ROI 和颜色阈值位于 `mumu_fishing_clicker.py` 的 `detect_gauge()` 中。游戏界面变更时，应基于本机截图调整这些阈值。

## 当前默认行为

| 项目 | 默认值 |
|---|---:|
| 视频帧率上限 | 60 FPS |
| 浮标合格点击的最小间隔 | 0.1 秒 |
| 无浮标兜底点击间隔 | 每次随机 0.8–1.2 秒 |
| 浮标漏检宽限 | 0.25 秒 |
| 预测输入延迟 | 0.045 秒 |
| 按住时长 | 随机 50–120 ms |
| 按钮位置偏移 | 参考画布每轴 ±20 px |

目标位置、方向和预计入区时间每个视频帧都会重新计算。触摸在后台串行发送，点击冷却只限制触摸发送，不会暂停视觉识别，也不会堆积尚未发送的触摸任务。

> 位置随机范围以安卓参考画布像素计。不同模拟器分辨率会按比例缩放；如按钮较小，可用 `--button-jitter` 调低范围。触摸时长是输入事件的按住时间，不代表模拟器或游戏识别的真实响应延时。

## 故障排查

### 找不到 ADB 或设备

- 确认 MuMu 安卓实例已经启动并完成开机。
- 运行 `adb devices -l`；设备状态应为 `device`。
- MuMu Pro 可以运行 `mumutool info all`，确认实例状态为 `running`。
- 用 `--adb` 或 `MUMU_ADB` 指向当前版本自带的 ADB。

### 视频流启动失败

- 确认设备已启动且 ADB 可用；再试一次启动。
- 更新 MuMu 后，确认其内置 ADB 与实例版本匹配。
- 检查 `~/Library/Logs/MumuFishingAssistant.log` 或终端错误信息。

### 画面能打开，但没有黄色区间或浮标日志

- 确认游戏当前处于钓鱼进度条界面，而不是大厅或结果页面。
- 检查模拟器方向和游戏布局是否仍对应 1080×1920 参考画布。
- 游戏更新、皮肤或特效改变后，重新校准 `detect_gauge()` 的颜色阈值和 ROI。

### macOS 阻止打开本机编译的应用

先从项目目录运行 `./install.command`。如 Finder 首次打开时提示来源限制，可在系统设置的“隐私与安全性”中允许本机编译的应用，或通过终端运行安装器和生成的 app。

## 技术文档

- [技术方案与识别细节](TECHNICAL_DESIGN.zh-CN.md)
- [第三方组件与许可](THIRD_PARTY_NOTICES.md)

## Windows 实验版本

GitHub 仓库的 [`windows/`](windows/README.zh-CN.md) 目录包含 Windows 启动器和试验性控制器。它是在 macOS 环境开发，尚未在 Windows + MuMu 上验证，**不保证可用性**；遇到安装、ADB 或视频流问题时，可把错误日志和环境信息交给 AI 辅助 debug。请勿附带账号、个人信息或游戏外的敏感画面。

## 模拟器安装包

安装包和教程放在夸克网盘，不直接提交到 Git：

- [下载链接](https://pan.quark.cn/s/b3fb3d886e39)
- 提取码：`SL57`
- 使用方法：**仅需安装国际版可直接使用。**

## 许可

本项目采用 MIT License。第三方库按各自许可单独提供；项目不打包 MuMu、ADB 或 scrcpy 的二进制文件。
