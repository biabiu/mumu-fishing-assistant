# Windows 实验版

> **实验性移植，不能保证可用。** 项目主要在 macOS 开发；Windows + MuMu 的安装、ADB 发现、scrcpy 视频流和输入行为尚未验证。请先在非关键游戏场景试用。遇到问题可把本页要求的日志和环境信息交给 AI 辅助 debug。

## 安装

1. 安装 Windows 版 MuMu，并启动 Android 实例。
2. 安装 Python 3.10+（建议从 [python.org](https://www.python.org/downloads/windows/) 安装）。
3. 在仓库根目录打开 PowerShell，运行：

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\windows\install_windows.ps1
   ```

4. 在同一个 PowerShell 窗口运行悬浮启停控制器：

   ```powershell
   & .\.venv\Scripts\python.exe .\windows\controller_windows.py
   ```

## 配置 ADB

脚本按 `MUMU_ADB` 环境变量或 `PATH` 查找 `adb.exe`。MuMu 安装未将 ADB 放进 PATH 时，先在 PowerShell 临时设置实际路径：

```powershell
$env:MUMU_ADB = "C:\实际安装目录\adb.exe"
```

MuMu Pro 的实例端口会变化时，可设置 `MUMU_TOOL` 指向实际的 `mumutool.exe`：

```powershell
$env:MUMU_TOOL = "C:\实际安装目录\mumutool.exe"
```

再运行控制器。也可以先用下列命令检查 ADB：

```powershell
& $env:MUMU_ADB devices -l
```

如果当前 MuMu Windows 版没有 `mumutool.exe`，或者其输出格式不同，设备发现可能需要适配。可先尝试在 MuMu 的开发者/ADB 设置中启用本地 ADB，再通过 `adb connect 127.0.0.1:<端口>` 连接，并将实际端口传入助手：

```powershell
& .\.venv\Scripts\python.exe .\mumu_fishing_clicker.py --serial 127.0.0.1:端口
```

## 提交给 AI debug 的信息

请附上 Windows 版本、MuMu 版本、Python 版本、ADB 路径（遮掉个人目录也可以）、`adb devices -l` 输出，以及日志文件：

```text
%LOCALAPPDATA%\MuMuFishingAssistant\assistant.log
```

可使用这样的提示：

> 我在 Windows 上运行这个 macOS 开发的 MuMu 钓鱼助手实验版，出现以下问题。请先根据日志定位兼容性问题，再给出最小修复，并明确哪些修改尚未在 Windows 验证。我的 Windows、MuMu、Python 版本和完整报错如下：……

提交前请检查日志，删除账号、个人路径、设备序列号以及任何不相关的游戏/桌面信息。不要提供包含个人资料的屏幕录制。

## 限制

- 该控制器和 MuMu Windows 兼容性未实机验证，不保证可用。
- 游戏画面布局、DPI/分辨率或颜色变化可能造成识别失效；按钮位置与算法参数也可能需要重新校准。
- 本实验实现没有 Windows 打包程序；需要本机 Python、依赖和 PowerShell。
