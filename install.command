#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "找不到 Python 3。请安装 Python 3.10+： https://www.python.org/downloads/macos/"
  exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "需要 Python 3.10 或更新版本。当前版本：$("$PYTHON" --version)"
  exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "找不到 swiftc。请先安装 Xcode Command Line Tools：xcode-select --install"
  exit 1
fi

"$PYTHON" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python3" -m pip install --upgrade pip
"$ROOT/.venv/bin/python3" -m pip install -r "$ROOT/requirements.txt"

APP="$ROOT/dist/MuMu钓鱼助手.app"
mkdir -p "$APP/Contents/MacOS"
swiftc -O -framework AppKit "$ROOT/FishingAssistantController.swift" \
  -o "$APP/Contents/MacOS/FishingAssistantController"
chmod +x "$APP/Contents/MacOS/FishingAssistantController"
"$ROOT/.venv/bin/python3" - "$APP/Contents/Info.plist" "$ROOT" <<'PY'
import plistlib
import sys

path, project_root = sys.argv[1:]
info = {
    "CFBundleExecutable": "FishingAssistantController",
    "CFBundleIdentifier": "local.mumu.fishing-assistant",
    "CFBundleName": "MuMu钓鱼助手",
    "CFBundlePackageType": "APPL",
    "LSUIElement": True,
    "NSHighResolutionCapable": True,
    "FishingAssistantProjectRoot": project_root,
}
with open(path, "wb") as output:
    plistlib.dump(info, output)
PY

DESKTOP="$HOME/Desktop"
mkdir -p "$DESKTOP"
ln -sfn "$APP" "$DESKTOP/MuMu钓鱼助手.app"
echo "安装完成。双击桌面的 MuMu钓鱼助手.app 打开启停悬浮窗。"
