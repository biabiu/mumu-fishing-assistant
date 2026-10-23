#!/usr/bin/env python3
"""Low-latency MuMu fishing helper using scrcpy's H.264 frame stream."""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

# An optional local dependency folder is useful for development; normal installs
# use the project virtual environment and requirements.txt.
DEPS = Path(__file__).resolve().parents[1] / "work" / "scrcpy" / "pydeps"
sys.path.insert(0, str(DEPS))

import numpy as np
from py_scrcpy_sdk import ScrcpyClient, ScrcpyConfig

ADB_DEFAULT = None
MUMU_PRO_ADB = "/Applications/MuMuPlayer Pro.app/Contents/MacOS/MuMu Android Device.app/Contents/MacOS/tools/adb"
MUMU_LEGACY_ADB = "/Applications/MuMuPlayer.app/Contents/MacOS/MuMuEmulator.app/Contents/MacOS/tools/adb"
REFERENCE_W, REFERENCE_H = 1080, 1920


@dataclass
class Gauge:
    left: int
    right: int
    y: int
    marker_x: float | None


def resolve_adb(requested: str | None) -> str:
    """Find ADB from an explicit path, environment, MuMu installs, or PATH."""
    candidates = [requested, os.environ.get("MUMU_ADB"), MUMU_PRO_ADB, MUMU_LEGACY_ADB,
                  shutil.which("adb")]
    for candidate in candidates:
        if candidate:
            candidate_path = Path(candidate).expanduser()
            if candidate_path.is_file():
                return str(candidate_path)
    raise RuntimeError(
        "找不到 adb。请安装 MuMu，或把 adb 加入 PATH，或设置 MUMU_ADB=/path/to/adb；"
        "也可以使用 --adb 指定路径。"
    )


def get_device(adb: str, requested: str | None) -> str:
    from py_scrcpy_sdk import list_devices

    # MuMu Player Pro assigns an ADB port per running device. Query mumutool
    # instead of assuming the legacy 16384/5555 ports, then connect the SDK's
    # ADB client to each active instance before listing devices.
    dynamic_serials: list[str] = []
    adb_path = Path(adb)
    configured_tool = os.environ.get("MUMU_TOOL")
    mumutool_candidates = ([Path(configured_tool).expanduser()] if configured_tool else []) + [
                           *(parent / name for parent in adb_path.parents
                             for name in ("mumutool", "mumutool.exe")),
                           Path("/Applications/MuMuPlayer Pro.app/Contents/MacOS/mumutool")]
    mumutool = next((candidate for candidate in mumutool_candidates if candidate.is_file()), None)
    if mumutool is not None and mumutool.is_file():
        try:
            info = subprocess.run([str(mumutool), "info", "all"], check=True,
                                  capture_output=True, text=True, timeout=5)
            data = json.loads(info.stdout).get("return", {})
            records = data.get("results", []) if isinstance(data, dict) else []
            for device in records:
                if device.get("state") != "running" or not device.get("adb_port"):
                    continue
                serial = f"127.0.0.1:{int(device['adb_port'])}"
                subprocess.run([adb, "connect", serial], capture_output=True, text=True, timeout=5)
                dynamic_serials.append(serial)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            # Keep legacy ADB discovery as a fallback if mumutool is unavailable.
            pass

    if not dynamic_serials:
        # Older MuMu releases commonly use one of these localhost ports.
        for serial in ("127.0.0.1:16384", "127.0.0.1:5555"):
            try:
                subprocess.run([adb, "connect", serial], capture_output=True, text=True, timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass

    devices = list_devices(adb_path=adb)
    online = [d.serial for d in devices if d.state == "device"]
    if requested:
        if requested not in online:
            raise RuntimeError(f"设备 {requested} 不在线；可用设备：{', '.join(online) or '无'}")
        return requested
    for preferred in (*dynamic_serials, "127.0.0.1:16384", "127.0.0.1:5555"):
        if preferred in online:
            return preferred
    if online:
        return online[0]
    raise RuntimeError("ADB 没发现在线设备。请确认 MuMu 已启动，并检查 adb connect 127.0.0.1:16384")


def detect_gauge(bgr: np.ndarray, yellow_history: deque[np.ndarray] | None = None) -> Gauge | None:
    """Detect the yellow window and red bobber in the lower game HUD."""
    h, w, _ = bgr.shape
    sx, sy = w / REFERENCE_W, h / REFERENCE_H
    x0, x1 = int(235 * sx), int(850 * sx)
    y0, y1 = int(1285 * sy), int(1405 * sy)
    band = bgr[y0:y1, x0:x1]
    blue, green, red = band[..., 0], band[..., 1], band[..., 2]
    # First require the actual dark-blue horizontal gauge rail. This rejects
    # red/yellow artwork from the lobby button, fish, bait and score UI.
    rail_mask = ((blue > red * 1.15) & (blue > green * 0.85) &
                 (red < 140) & (green < 180) & (blue < 220))
    rail_col_count = rail_mask.sum(axis=0)
    rail_cols = np.flatnonzero(rail_col_count >= max(2, int(5 * sy)))
    if len(rail_cols) == 0:
        return None
    rail_groups = [part for part in np.split(rail_cols, np.where(np.diff(rail_cols) > 3)[0] + 1)
                   if len(part) >= max(2, int(w * 0.02))]
    if not rail_groups:
        return None
    rail_merged: list[np.ndarray] = []
    for part in rail_groups:
        if rail_merged and part[0] - rail_merged[-1][-1] <= max(8, int(w * 0.08)):
            rail_merged[-1] = np.concatenate((rail_merged[-1], part))
        else:
            rail_merged.append(part)
    rail = max(rail_merged, key=len)
    if len(rail) < int(w * 0.20):
        return None
    rail_left, rail_right = x0 + int(rail[0]), x0 + int(rail[-1])
    rail_width = rail_right - rail_left
    inner_left = rail_left + int(rail_width * 0.05)
    inner_right = rail_right - int(rail_width * 0.04)
    # The game's yellow is pale (R≈250, G≈235, B≈95); use blue as the
    # discriminating channel instead of requiring R to greatly exceed G.
    yellow = (red > 175) & (green > 115) & (blue < 135) & (red > blue * 1.35) & (green > blue * 1.15)
    playable_x = np.arange(x0, x1)
    yellow &= (playable_x[None, :] >= inner_left) & (playable_x[None, :] <= inner_right)
    col_scores = yellow.sum(axis=0)
    # Short windows may expose only thin slivers around the bobber. A modest
    # per-column row count keeps those slivers while rail validation filters
    # unrelated yellow pixels elsewhere on the screen.
    min_rows = max(3, int((y1 - y0) * 0.08))
    current_yellow = col_scores >= min_rows
    if yellow_history is not None:
        yellow_history.append(current_yellow)
        # The bobber can cover a short highlight completely in one frame.
        # Accumulate stable yellow columns over a brief rolling window so its
        # movement reveals the target across adjacent frames.
        votes = np.sum(np.stack(yellow_history, axis=0), axis=0)
        required_votes = min(2, len(yellow_history))
        cols_local = np.flatnonzero(votes >= required_votes)
    else:
        cols_local = np.flatnonzero(current_yellow)
    if len(cols_local) == 0:
        return None
    xs = cols_local + x0
    groups = list(np.split(xs, np.where(np.diff(xs) > 2)[0] + 1))
    # The red bobber covers the yellow stripe and splits its mask into two
    # islands. Merge nearby islands before choosing the widest yellow interval.
    merged: list[np.ndarray] = []
    for part in groups:
        if merged and part[0] - merged[-1][-1] <= max(8, int(w * 0.075)):
            merged[-1] = np.concatenate((merged[-1], part))
        else:
            merged.append(part)
    zone = max(merged, key=len)
    left, right = int(zone[0]), int(zone[-1])
    # The highlighted interval can shrink to about the bobber's own width.
    # Keep a short contiguous segment, even when the bobber splits it into
    # two small visible pieces; discard only spans narrower than ~3.5% of rail.
    if right - left < max(8, int(rail_width * 0.02)):
        return None

    # The bar can shift slightly vertically between game states and recordings.
    # Anchor the bobber search to the strongest horizontal rail row, not a
    # hard-coded midpoint of the broad ROI.
    y = y0 + int(np.argmax(rail_mask.sum(axis=1)))
    half_strip = max(18, int(40 * sy))
    strip = bgr[max(0, y - half_strip):min(h, y + half_strip + 1), x0:x1]
    bb, gg, rr = strip[..., 0], strip[..., 1], strip[..., 2]
    red_mask = (rr > 150) & (rr > gg * 1.32) & (rr > bb * 1.25) & (gg < 155)
    counts = red_mask.sum(axis=0)
    cols = np.flatnonzero(counts >= max(3, int(5 * sy)))
    marker_x = None
    if len(cols):
        candidates = [part for part in np.split(cols, np.where(np.diff(cols) > max(3, int(5 * sx)))[0] + 1)
                      if len(part) >= max(2, int(3 * sx))]
        candidates = [part for part in candidates
                      if inner_left <= part.mean() + x0 <= inner_right]
        if candidates:
            chosen = max(candidates, key=lambda part: int(counts[part].sum()))
            marker_x = float(np.average(chosen + x0, weights=counts[chosen]))
    return Gauge(left=left, right=right, y=y, marker_x=marker_x)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=ADB_DEFAULT, help="ADB executable path (auto-detected by default)")
    parser.add_argument("--serial", default=None, help="ADB serial (auto-select MuMu by default)")
    parser.add_argument("--start", action="store_true", help="tap the main fishing button before watching")
    parser.add_argument("--no-bobber-interval", type=float, default=1.0,
                        help="center of randomized no-bobber tap interval")
    parser.add_argument("--no-bobber-jitter", type=float, default=0.2,
                        help="symmetric random variation around no-bobber interval (default gives 0.8–1.2s)")
    parser.add_argument("--seconds", type=float, default=3600, help="maximum monitoring time")
    parser.add_argument("--button", nargs=2, type=int, default=(540, 1580), metavar=("X", "Y"),
                        help="button center in 1080x1920 Android coordinates")
    parser.add_argument("--margin", type=int, default=2, help="shrink detected zone by this many stream pixels")
    parser.add_argument("--bobber-cooldown", type=float, default=0.3,
                        help="minimum seconds between eligible taps while the bobber is detected")
    parser.add_argument("--interval-jitter", type=float, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--button-jitter", type=int, default=20,
                        help="random +/- tap offset in reference-screen pixels")
    parser.add_argument("--hold-min-ms", type=float, default=50.0, help="minimum randomized touch duration")
    parser.add_argument("--hold-max-ms", type=float, default=120.0, help="maximum randomized touch duration")
    parser.add_argument("--bobber-loss-grace", type=float, default=0.25,
                        help="seconds to tolerate brief bobber detection gaps before no-bobber tapping")
    parser.add_argument("--input-latency", type=float, default=0.045,
                        help="estimated frame processing + input-to-game delay, seconds")
    parser.add_argument("--max-size", type=int, default=1920, help="scrcpy longest side; 1920 keeps native resolution")
    parser.add_argument("--max-fps", type=int, default=60, help="scrcpy video frame rate")
    parser.add_argument("--debug-every", type=float, default=0.5, help="seconds between diagnostic log lines")
    args = parser.parse_args()
    if args.interval_jitter is not None:
        args.no_bobber_jitter = args.interval_jitter
    if (args.no_bobber_interval <= 0 or args.no_bobber_jitter < 0 or
            args.button_jitter < 0 or args.hold_min_ms <= 0 or
            args.hold_max_ms < args.hold_min_ms or args.bobber_cooldown <= 0):
        parser.error("interval/jitter values must be valid, bobber cooldown positive, and hold duration must satisfy 0 < min <= max")

    args.adb = resolve_adb(args.adb)
    serial = get_device(args.adb, args.serial)
    print(f"设备：{serial}；启动低延迟 H.264 流（{args.max_fps} fps 上限）", flush=True)
    config = ScrcpyConfig(serial=serial, adb_path=args.adb, max_size=args.max_size,
                          max_fps=args.max_fps, video_bit_rate=12_000_000,
                          frame_timeout=5.0, connect_timeout=8.0)

    started = time.monotonic()
    last_click = float("-inf")
    next_click_allowed = float("-inf")
    periodic_interval = lambda: random.uniform(
        max(0.1, args.no_bobber_interval - args.no_bobber_jitter),
        args.no_bobber_interval + args.no_bobber_jitter)
    next_periodic_click = started + periodic_interval()
    history: deque[tuple[float, float]] = deque(maxlen=7)
    last_marker_seen: float | None = None
    last_log = 0.0
    frames = 0
    frame_times: deque[float] = deque(maxlen=120)
    yellow_history: deque[np.ndarray] = deque(maxlen=max(12, round(args.max_fps * 0.50)))

    try:
        with ScrcpyClient(config) as client, ThreadPoolExecutor(max_workers=1, thread_name_prefix="touch") as touch_worker:
            first = client.wait_until_ready(timeout=10, copy=False)
            h, w = first.shape[:2]
            tap_x = round(args.button[0] * w / REFERENCE_W)
            tap_y = round(args.button[1] * h / REFERENCE_H)
            print(f"视频流：{w}×{h}；按钮映射：{args.button[0]},{args.button[1]} → {tap_x},{tap_y}", flush=True)

            tap_future: Future | None = None

            def tap_button(cooldown: float) -> float:
                nonlocal last_click, next_click_allowed, tap_future
                # Keep the tap close to the button center while avoiding a
                # perfectly repeated coordinate and hold duration.
                jitter_x = round(random.uniform(-args.button_jitter, args.button_jitter) * w / REFERENCE_W)
                jitter_y = round(random.uniform(-args.button_jitter, args.button_jitter) * h / REFERENCE_H)
                x = min(w - 1, max(0, tap_x + jitter_x))
                y = min(h - 1, max(0, tap_y + jitter_y))
                hold_ms = random.uniform(args.hold_min_ms, args.hold_max_ms)
                issued = time.monotonic()
                tap_future = touch_worker.submit(client.tap, x, y, hold_seconds=hold_ms / 1000)
                last_click = issued
                next_click_allowed = issued + cooldown
                print(f"点击参数：{x},{y}；按住 {hold_ms:.1f}ms；下次最早 {cooldown:.2f}s 后", flush=True)
                return issued

            def touch_ready() -> bool:
                nonlocal tap_future
                if tap_future is None:
                    return True
                if not tap_future.done():
                    return False
                try:
                    tap_future.result()
                except Exception as exc:
                    print(f"触摸发送失败：{exc}", file=sys.stderr, flush=True)
                tap_future = None
                return True

            if args.start:
                print("启动钓鱼：点击", flush=True)
                interval = periodic_interval()
                last_click = tap_button(interval)
                next_periodic_click = last_click + interval

            deadline = time.monotonic() + args.seconds
            for frame in client.frames(copy=False, timeout=5):
                now = time.monotonic()
                if now >= deadline:
                    break
                ready_for_touch = touch_ready()
                frames += 1
                frame_times.append(now)
                gauge = detect_gauge(frame, yellow_history)
                if gauge is not None and gauge.marker_x is not None:
                    last_marker_seen = now
                    low, high = gauge.left + args.margin, gauge.right - args.margin
                    x = gauge.marker_x
                    inside = low <= x <= high
                    history.append((now, x))
                    velocity = 0.0
                    if len(history) >= 3:
                        t0 = history[0][0]
                        ts = np.fromiter((t - t0 for t, _ in history), dtype=np.float64)
                        xs = np.fromiter((p for _, p in history), dtype=np.float64)
                        # Linear fit over the recent frames smooths pixel jitter while
                        # responding quickly when the float reverses direction.
                        velocity = float(np.polyfit(ts, xs, 1)[0])

                    entering_eta = None
                    if velocity > 0.0 and x < low:
                        entering_eta = (low - x) / velocity
                    elif velocity < 0.0 and x > high:
                        entering_eta = (high - x) / velocity
                    # Inject early enough for the touch to reach the game at the
                    # boundary; otherwise click on the first frame already inside.
                    predicted_entry = entering_eta is not None and entering_eta <= args.input_latency + 1 / args.max_fps
                    # Re-evaluate eligibility on every video frame. The cooldown
                    # gates touch dispatch only; it never pauses tracking or ETA updates.
                    should_click = inside or predicted_entry
                    if should_click and now >= next_click_allowed and ready_for_touch:
                        eta = 0.0 if inside else max(0.0, float(entering_eta or 0.0))
                        print(f"命中窗口：浮标 x={x:.1f}，v={velocity:+.0f}px/s，"
                              f"黄色=[{low},{high}]，预计 {eta:.3f}s 后进入；点击", flush=True)
                        tap_button(args.bobber_cooldown)
                    if now - last_log >= args.debug_every:
                        fps = (len(frame_times) - 1) / max(1e-6, frame_times[-1] - frame_times[0]) if len(frame_times) > 1 else 0
                        print(f"识别：x={x:.1f}，方向={velocity:+.0f}px/s，黄色=[{gauge.left},{gauge.right}]，"
                              f"fps={fps:.1f}", flush=True)
                        last_log = now
                else:
                    # A few frames can lose the red bobber when it overlaps the
                    # yellow stripe or due to compression. Preserve the recent
                    # trajectory during this short gap and never interpret it as
                    # the no-bobber phase (which would send a periodic tap).
                    if (last_marker_seen is not None and
                            now - last_marker_seen <= args.bobber_loss_grace):
                        if now - last_log >= args.debug_every:
                            gap_ms = 1000 * (now - last_marker_seen)
                            print(f"浮标短暂漏检 {gap_ms:.0f}ms：保留轨迹，暂不点击", flush=True)
                            last_log = now
                        continue

                    if last_marker_seen is not None:
                        # Start the one-second fallback interval from the last
                        # confirmed bobber frame, not from the first missed frame.
                        next_periodic_click = max(
                            next_periodic_click,
                            last_marker_seen + periodic_interval(),
                        )
                        last_marker_seen = None
                        history.clear()
                        if gauge is None:
                            yellow_history.clear()

                    if args.start and now >= next_periodic_click and now >= next_click_allowed and ready_for_touch:
                        print(f"未检测到浮标：按设定间隔拉钩点击，基准坐标 {tap_x},{tap_y}", flush=True)
                        interval = periodic_interval()
                        issued = tap_button(interval)
                        next_periodic_click = issued + interval
                    if now - last_log >= 1.0:
                        print("等待黄色区间/浮标…", flush=True)
                        last_log = now
    except KeyboardInterrupt:
        print("已停止。", flush=True)
        return 0
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr, flush=True)
        return 2

    elapsed = max(1e-6, time.monotonic() - started)
    print(f"结束：{frames} 帧 / {elapsed:.1f}s，平均 {frames / elapsed:.1f} fps。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
