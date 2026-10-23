"""Experimental Windows floating start/stop controller for the MuMu helper."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "mumu_fishing_clicker.py"
LOG_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MuMuFishingAssistant" / "assistant.log"


class Controller:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.process: subprocess.Popen | None = None
        self.log = None
        root.title("MuMu 钓鱼助手（实验版）")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        self.status = tk.StringVar(value="已停止")
        tk.Label(root, textvariable=self.status, padx=20, pady=12).pack()
        self.button = tk.Button(root, text="启动钓鱼助手", width=24, command=self.toggle)
        self.button.pack(padx=16, pady=(0, 8))
        tk.Label(root, text="Windows 实验版：尚未验证", fg="#a33").pack(pady=(0, 12))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(500, self.poll)

    def toggle(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.status.set("正在停止…")
            return
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.log = LOG_PATH.open("a", encoding="utf-8", buffering=1)
            python = ROOT / ".venv" / "Scripts" / "python.exe"
            args = [str(python), str(SCRIPT), "--start", "--no-bobber-interval", "1",
                    "--no-bobber-jitter", "0.2", "--button-jitter", "20",
                    "--hold-min-ms", "50", "--hold-max-ms", "120",
                    "--bobber-cooldown", "0.1", "--bobber-loss-grace", "0.25",
                    "--input-latency", "0.045", "--max-size", "1920",
                    "--max-fps", "60", "--seconds", "3600"]
            self.process = subprocess.Popen(args, cwd=ROOT, stdout=self.log,
                                            stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
            self.status.set("运行中")
        except Exception as exc:
            messagebox.showerror("启动失败", f"{exc}\n\n日志：{LOG_PATH}")
            self.status.set("启动失败")

    def poll(self) -> None:
        if self.process and self.process.poll() is not None:
            self.process = None
            if self.log:
                self.log.close()
                self.log = None
            self.status.set("已停止")
        self.button.configure(text="停止钓鱼助手" if self.process else "启动钓鱼助手")
        self.root.after(500, self.poll)

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
        if self.log:
            self.log.close()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    Controller(window)
    window.mainloop()
