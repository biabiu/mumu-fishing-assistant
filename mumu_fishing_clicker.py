#!/usr/bin/env python3
"""Low-latency MuMu fishing helper using scrcpy's H.264 frame stream."""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import json
import math
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
BAIT_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "bait_icon_template.npy"
BAIT_CONFIRM_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "bait_confirm_template.npy"
_BAIT_TEMPLATE_BGR: np.ndarray | None = None
_BAIT_CONFIRM_TEMPLATE_BGR: np.ndarray | None = None


@dataclass
class Gauge:
    left: int
    right: int
    y: int
    marker_x: float | None


def detect_bait_icon(
    bgr: np.ndarray,
    reference_size: tuple[int, int] = (REFERENCE_W, REFERENCE_H),
) -> tuple[int, int, float] | None:
    """Find warm bag-shaped components, then verify them against the icon template."""
    global _BAIT_TEMPLATE_BGR
    if _BAIT_TEMPLATE_BGR is None:
        if not BAIT_TEMPLATE_PATH.is_file():
            return None
        # The stored template is RGB; scrcpy frames are BGR.
        _BAIT_TEMPLATE_BGR = np.load(BAIT_TEMPLATE_PATH, allow_pickle=False)[:, :, ::-1].copy()

    h, w = bgr.shape[:2]
    reference_w, reference_h = reference_size
    base_scale = min(w / reference_w, h / reference_h)
    scan_stride = 4
    image = bgr[::scan_stride, ::scan_stride]
    blue, green, red = image[..., 0], image[..., 1], image[..., 2]
    warm = ((red > 145) & (green > 25) & (green < 205) &
            (red > green * 1.10) & (red > blue * 1.20))
    # A broad red wash or diagnostic test pattern is not a UI icon; avoid
    # spending component-labeling time on a mostly uniform warm-color frame.
    if np.count_nonzero(warm) > warm.size * 0.12:
        return None
    cyan = ((blue > 115) & (green > 75) & (red < 165) &
            (blue > red * 1.25) & (green > red * 1.12))
    warm_ys, warm_xs = np.where(warm)
    if not len(warm_xs):
        return None

    template = _BAIT_TEMPLATE_BGR
    template_red = template[..., 2]
    template_green = template[..., 1]
    template_blue = template[..., 0]
    template_warm = ((template_red > 145) & (template_green > 25) &
                     (template_green < 205) & (template_red > template_green * 1.10) &
                     (template_red > template_blue * 1.20))
    template_y, template_x = np.where(template_warm)
    tx0, tx1 = int(template_x.min()), int(template_x.max())
    ty0, ty1 = int(template_y.min()), int(template_y.max())

    # Connected components are built on a 4x reduced color mask, then the
    # icon template is checked only around plausible bag-shaped candidates.
    visited = np.zeros(warm.shape, dtype=np.uint8)
    image_h, image_w = warm.shape
    scale_from_w = scan_stride / max(1, tx1 - tx0 + 1)
    scale_from_h = scan_stride / max(1, ty1 - ty0 + 1)
    sample_y = np.rint(np.linspace(3, template.shape[0] - 4, 8)).astype(int)
    sample_x = np.rint(np.linspace(3, template.shape[1] - 4, 8)).astype(int)
    sample_colors = template[np.ix_(sample_y, sample_x)].astype(np.int16)
    best_error, best_center = float("inf"), None
    for seed_y, seed_x in zip(warm_ys.tolist(), warm_xs.tolist()):
        if visited[seed_y, seed_x]:
            continue
        visited[seed_y, seed_x] = 1
        stack = [(seed_y, seed_x)]
        min_x = max_x = seed_x
        min_y = max_y = seed_y
        area = 0
        while stack:
            cy, cx = stack.pop()
            area += 1
            min_x, max_x = min(min_x, cx), max(max_x, cx)
            min_y, max_y = min(min_y, cy), max(max_y, cy)
            for ny in range(max(0, cy - 1), min(image_h, cy + 2)):
                for nx in range(max(0, cx - 1), min(image_w, cx + 2)):
                    if warm[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = 1
                        stack.append((ny, nx))

        box_w, box_h = max_x - min_x + 1, max_y - min_y + 1
        if not (10 <= box_w <= 50 and 10 <= box_h <= 50 and 0.70 <= box_w / box_h <= 1.35):
            continue
        if not 35 <= area <= 900:
            continue

        # A cyan/yellow emblem sits in the lower-middle of this bait bag.
        bx0, bx1 = max(0, min_x + int(box_w * 0.28)), min(image_w, max_x - int(box_w * 0.08) + 1)
        by0, by1 = max(0, min_y + int(box_h * 0.42)), min(image_h, max_y + 1)
        if bx1 <= bx0 or by1 <= by0 or int(cyan[by0:by1, bx0:bx1].sum()) < 5:
            continue

        center_x = (min_x + max_x) * scan_stride / 2
        center_y = (min_y + max_y) * scan_stride / 2
        estimated_scale = ((box_w * scale_from_w) + (box_h * scale_from_h)) / 2
        estimated_scale = max(base_scale * 0.55, min(base_scale * 1.65, estimated_scale))
        for relative_scale in (0.88, 1.0, 1.12):
            scale = estimated_scale * relative_scale
            px = np.rint(center_x + (sample_x - template.shape[1] / 2) * scale).astype(int)
            py = np.rint(center_y + (sample_y - template.shape[0] / 2) * scale).astype(int)
            if px.min() < 0 or py.min() < 0 or px.max() >= w or py.max() >= h:
                continue
            observed = bgr[np.ix_(py, px)].astype(np.int16)
            error = float(np.abs(observed - sample_colors).mean())
            if error < best_error:
                best_error = error
                # The warm-mask bbox center tracks the clickable bag body.
                best_center = (round(center_x), round(center_y))

    # Mean absolute BGR distance across a 8x8 signature tolerates compression.
    if best_center is None or best_error > 50:
        return None
    return best_center[0], best_center[1], best_error


def detect_bait_confirm_button(
    bgr: np.ndarray,
) -> tuple[int, int, float] | None:
    """Locate the green confirmation button in the bait dialog."""
    global _BAIT_CONFIRM_TEMPLATE_BGR
    if _BAIT_CONFIRM_TEMPLATE_BGR is None:
        if not BAIT_CONFIRM_TEMPLATE_PATH.is_file():
            return None
        _BAIT_CONFIRM_TEMPLATE_BGR = np.load(
            BAIT_CONFIRM_TEMPLATE_PATH, allow_pickle=False)[:, :, ::-1].copy()

    stride = 4
    image = bgr[::stride, ::stride]
    blue, green, red = image[..., 0], image[..., 1], image[..., 2]
    green_mask = ((green > 115) & (green > red * 1.14) &
                  (green > blue * 1.10) & (blue < 185))
    ys, xs = np.where(green_mask)
    if not len(xs) or len(xs) > green_mask.size * 0.25:
        return None

    template = _BAIT_CONFIRM_TEMPLATE_BGR
    tb, tg, tr = template[..., 0], template[..., 1], template[..., 2]
    template_mask = ((tg > 115) & (tg > tr * 1.14) &
                     (tg > tb * 1.10) & (tb < 185))
    ty, tx = np.where(template_mask)
    tx0, tx1, ty0, ty1 = int(tx.min()), int(tx.max()), int(ty.min()), int(ty.max())
    sample_y = np.rint(np.linspace(3, template.shape[0] - 4, 8)).astype(int)
    sample_x = np.rint(np.linspace(4, template.shape[1] - 5, 12)).astype(int)
    sample_colors = template[np.ix_(sample_y, sample_x)].astype(np.int16)
    image_h, image_w = green_mask.shape
    visited = np.zeros(green_mask.shape, dtype=np.uint8)
    best_error, best_center = float("inf"), None

    for seed_y, seed_x in zip(ys.tolist(), xs.tolist()):
        if visited[seed_y, seed_x]:
            continue
        visited[seed_y, seed_x] = 1
        stack = [(seed_y, seed_x)]
        min_x = max_x = seed_x
        min_y = max_y = seed_y
        area = 0
        while stack:
            cy, cx = stack.pop()
            area += 1
            min_x, max_x = min(min_x, cx), max(max_x, cx)
            min_y, max_y = min(min_y, cy), max(max_y, cy)
            for ny in range(max(0, cy - 1), min(image_h, cy + 2)):
                for nx in range(max(0, cx - 1), min(image_w, cx + 2)):
                    if green_mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = 1
                        stack.append((ny, nx))

        box_w, box_h = max_x - min_x + 1, max_y - min_y + 1
        aspect = box_w / max(1, box_h)
        if not (20 <= box_w <= image_w * 0.92 and 6 <= box_h <= image_h * 0.20 and
                2.4 <= aspect <= 5.5 and area >= box_w * box_h * 0.25):
            continue
        center_x = (min_x + max_x) * stride / 2
        center_y = (min_y + max_y) * stride / 2
        if not (0.20 * bgr.shape[1] <= center_x <= 0.80 * bgr.shape[1] and
                0.18 * bgr.shape[0] <= center_y <= 0.82 * bgr.shape[0]):
            continue
        scale_w = box_w * stride / max(1, tx1 - tx0 + 1)
        scale_h = box_h * stride / max(1, ty1 - ty0 + 1)
        scale = (scale_w + scale_h) / 2
        px = np.rint(center_x + (sample_x - template.shape[1] / 2) * scale).astype(int)
        py = np.rint(center_y + (sample_y - template.shape[0] / 2) * scale).astype(int)
        if px.min() < 0 or py.min() < 0 or px.max() >= bgr.shape[1] or py.max() >= bgr.shape[0]:
            continue
        observed = bgr[np.ix_(py, px)].astype(np.int16)
        error = float(np.abs(observed - sample_colors).mean())
        if error < best_error:
            best_error = error
            best_center = (round(center_x), round(center_y))

    if best_center is None or best_error > 62:
        return None
    return best_center[0], best_center[1], best_error


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


def detect_gauge(
    bgr: np.ndarray,
    yellow_history: deque[np.ndarray] | None = None,
    reference_size: tuple[int, int] = (REFERENCE_W, REFERENCE_H),
) -> Gauge | None:
    """Detect the yellow window and red bobber in the lower game HUD."""
    h, w, _ = bgr.shape
    reference_w, reference_h = reference_size
    sx, sy = w / reference_w, h / reference_h
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
                        help="button center in reference-screen pixels")
    parser.add_argument("--reference-size", nargs=2, type=int, default=(1080, 1920),
                        metavar=("WIDTH", "HEIGHT"),
                        help="reference canvas size in Android pixels (default: 1080 1920)")
    parser.add_argument("--margin", type=int, default=2, help="shrink detected zone by this many stream pixels")
    parser.add_argument("--bobber-cooldown", type=float, default=0.3,
                        help="minimum seconds between eligible taps while the bobber is detected")
    parser.add_argument("--bobber-jitter", type=float, default=0.05,
                        help="random extra delay (0..N seconds) after bobber-phase minimum cooldown")
    parser.add_argument("--interval-jitter", type=float, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--button-jitter", type=int, default=20,
                        help="random +/- tap offset in reference-screen pixels")
    parser.add_argument("--use-bait", action="store_true",
                        help="automatically use bait and confirm the green placement dialog")
    parser.add_argument("--bait-check-interval", "--bait-cooldown", dest="bait_check_interval",
                        type=float, default=60.0,
                        help="seconds between bait inventory checks (default: 60)")
    parser.add_argument("--bait-use-interval", type=float, default=3.0,
                        help="seconds to wait between confirmed bait placements (default: 3)")
    parser.add_argument("--bait-max-consecutive", type=int, default=4,
                        help="maximum confirmed placements per round (default: 4)")
    parser.add_argument("--wait-bait-cooldown", action="store_true",
                        help="wait one bait-check interval before the first bait check")
    parser.add_argument("--hold-min-ms", type=float, default=50.0, help="minimum randomized touch duration")
    parser.add_argument("--hold-max-ms", type=float, default=120.0, help="maximum randomized touch duration")
    parser.add_argument("--bobber-loss-grace", type=float, default=0.25,
                        help="seconds to tolerate brief bobber detection gaps before no-bobber tapping")
    parser.add_argument("--input-latency", type=float, default=0.045,
                        help="estimated frame processing + input-to-game delay, seconds")
    parser.add_argument("--bobber-time-offset-ms", type=float, default=0.0,
                        help="bobber tap timing offset in milliseconds; positive delays, negative advances (default: 0)")
    parser.add_argument("--max-size", type=int, default=1920, help="scrcpy longest side; 1920 keeps native resolution")
    parser.add_argument("--max-fps", type=int, default=60, help="scrcpy video frame rate")
    parser.add_argument("--debug-every", type=float, default=0.5, help="seconds between diagnostic log lines")
    args = parser.parse_args()
    if args.interval_jitter is not None:
        args.no_bobber_jitter = args.interval_jitter
    if (args.no_bobber_interval <= 0 or args.no_bobber_jitter < 0 or
            args.button_jitter < 0 or args.hold_min_ms <= 0 or
            args.hold_max_ms < args.hold_min_ms or args.bobber_cooldown <= 0 or
            args.bobber_jitter < 0 or min(args.reference_size) <= 0 or
            not math.isfinite(args.bobber_time_offset_ms) or abs(args.bobber_time_offset_ms) > 5000 or
            args.bait_check_interval <= 0 or args.bait_use_interval <= 0 or
            args.bait_max_consecutive < 1 or
            args.button[0] < 0 or args.button[1] < 0 or
            args.button[0] >= args.reference_size[0] or args.button[1] >= args.reference_size[1]):
        parser.error("intervals and canvas dimensions must be positive, jitters nonnegative, time offset within ±5000 ms, and the button within the reference canvas")

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
    # When bait automation is enabled, defer the initial fishing tap until the
    # first bait scan (and any bait-confirmation cycle) has finished. Without
    # bait automation the initial tap is sent synchronously below.
    start_pending = bool(args.start and args.use_bait)
    history: deque[tuple[float, float]] = deque(maxlen=7)
    last_marker_seen: float | None = None
    marker_inside_since: float | None = None
    last_log = 0.0
    frames = 0
    frame_times: deque[float] = deque(maxlen=120)
    yellow_history: deque[np.ndarray] = deque(maxlen=max(12, round(args.max_fps * 0.50)))
    bait_stage = "idle"
    bait_stage_started = started
    bait_next_check = started + (args.bait_check_interval if args.wait_bait_cooldown else 0.0)
    bait_next_scan = started
    bait_round_count = 0
    first_bait_check_complete = not args.use_bait

    try:
        with ScrcpyClient(config) as client, ThreadPoolExecutor(max_workers=1, thread_name_prefix="touch") as touch_worker:
            first = client.wait_until_ready(timeout=10, copy=False)
            h, w = first.shape[:2]
            reference_w, reference_h = args.reference_size
            tap_x = round(args.button[0] * w / reference_w)
            tap_y = round(args.button[1] * h / reference_h)
            print(f"视频流：{w}×{h}；按钮映射：{args.button[0]},{args.button[1]} → {tap_x},{tap_y}", flush=True)

            tap_future: Future | None = None

            def tap_button(cooldown: float) -> float:
                nonlocal last_click, next_click_allowed, tap_future
                # Keep the tap close to the button center while avoiding a
                # perfectly repeated coordinate and hold duration.
                jitter_x = round(random.uniform(-args.button_jitter, args.button_jitter) * w / reference_w)
                jitter_y = round(random.uniform(-args.button_jitter, args.button_jitter) * h / reference_h)
                x = min(w - 1, max(0, tap_x + jitter_x))
                y = min(h - 1, max(0, tap_y + jitter_y))
                hold_ms = random.uniform(args.hold_min_ms, args.hold_max_ms)
                issued = time.monotonic()
                tap_future = touch_worker.submit(client.tap, x, y, hold_seconds=hold_ms / 1000)
                last_click = issued
                next_click_allowed = issued + cooldown
                print(f"点击参数：{x},{y}；按住 {hold_ms:.1f}ms；下次最早 {cooldown:.2f}s 后", flush=True)
                return issued

            def tap_detected_target(x: int, y: int) -> float:
                nonlocal tap_future
                hold_ms = random.uniform(args.hold_min_ms, args.hold_max_ms)
                issued = time.monotonic()
                tap_future = touch_worker.submit(client.tap, x, y, hold_seconds=hold_ms / 1000)
                print(f"按住 {hold_ms:.1f}ms", flush=True)
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

            if args.start and not args.use_bait:
                print("启动钓鱼：点击", flush=True)
                interval = periodic_interval()
                last_click = tap_button(interval)
                next_periodic_click = last_click + interval
            if args.use_bait:
                first_check = "等待冷却" if args.wait_bait_cooldown else "启动后立即检查"
                if args.start:
                    print("已暂缓启动钓鱼按钮，等待首次鱼饵检查/打窝完成", flush=True)
                print(f"鱼饵自动流程：{first_check}；检测间隔 {args.bait_check_interval:.0f}s，"
                      f"每轮最多 {args.bait_max_consecutive} 次，打窝间隔 {args.bait_use_interval:.1f}s", flush=True)

            deadline = time.monotonic() + args.seconds
            for frame in client.frames(copy=False, timeout=5):
                now = time.monotonic()
                if now >= deadline:
                    break
                ready_for_touch = touch_ready()
                frames += 1
                frame_times.append(now)
                bait_checking_now = False
                if args.use_bait:
                    if bait_stage == "idle" and now >= bait_next_check and ready_for_touch:
                        bait_checking_now = True
                        bait_position = detect_bait_icon(frame, args.reference_size)
                        first_bait_check_complete = True
                        if bait_position is None:
                            bait_next_check = now + args.bait_check_interval
                            print(f"本轮未检测到鱼饵；{args.bait_check_interval:.0f}s 后再检查", flush=True)
                        else:
                            bait_x, bait_y, match_error = bait_position
                            tap_detected_target(bait_x, bait_y)
                            bait_stage = "await_confirm"
                            bait_stage_started = now
                            bait_next_scan = now
                            bait_round_count = 0
                            ready_for_touch = False
                            print(f"识别到鱼饵图标：中心=({bait_x},{bait_y})，匹配误差={match_error:.1f}；"
                                  "点击打开打窝确认", flush=True)
                    elif bait_stage == "await_confirm":
                        if now - bait_stage_started >= 12.0:
                            bait_stage = "idle"
                            bait_next_check = now + args.bait_check_interval
                            print("12s 内未识别到绿色打窝按钮；结束本轮鱼饵操作", flush=True)
                        elif now >= bait_next_scan:
                            confirm_position = detect_bait_confirm_button(frame)
                            bait_next_scan = now + 0.10
                            if confirm_position is not None and ready_for_touch:
                                confirm_x, confirm_y, match_error = confirm_position
                                tap_detected_target(confirm_x, confirm_y)
                                bait_round_count += 1
                                bait_stage = "pause_between_baits"
                                bait_stage_started = now
                                ready_for_touch = False
                                print(f"识别到绿色打窝按钮：中心=({confirm_x},{confirm_y})，"
                                      f"匹配误差={match_error:.1f}；确认第 {bait_round_count} 次", flush=True)
                    elif bait_stage == "pause_between_baits" and now - bait_stage_started >= args.bait_use_interval:
                        if bait_round_count >= args.bait_max_consecutive:
                            bait_stage = "idle"
                            bait_next_check = now + args.bait_check_interval
                            print(f"本轮已连续打窝 {bait_round_count} 次；"
                                  f"{args.bait_check_interval:.0f}s 后重新检查鱼饵", flush=True)
                        else:
                            bait_position = detect_bait_icon(frame, args.reference_size)
                            if bait_position is None:
                                bait_stage = "idle"
                                bait_next_check = now + args.bait_check_interval
                                print("鱼饵图标已消失；结束本轮打窝", flush=True)
                            elif ready_for_touch:
                                bait_x, bait_y, match_error = bait_position
                                tap_detected_target(bait_x, bait_y)
                                bait_stage = "await_confirm"
                                bait_stage_started = now
                                bait_next_scan = now
                                ready_for_touch = False
                                print(f"冷却 {args.bait_use_interval:.1f}s 后继续打窝；"
                                      f"鱼饵中心=({bait_x},{bait_y})，匹配误差={match_error:.1f}", flush=True)
                gauge = detect_gauge(frame, yellow_history, args.reference_size)
                if (start_pending and bait_stage == "idle" and first_bait_check_complete and
                        not bait_checking_now and ready_for_touch):
                    print("首次鱼饵检查/打窝完成：启动钓鱼", flush=True)
                    interval = periodic_interval()
                    last_click = tap_button(interval)
                    next_periodic_click = last_click + interval
                    start_pending = False
                if gauge is not None and gauge.marker_x is not None:
                    last_marker_seen = now
                    low, high = gauge.left + args.margin, gauge.right - args.margin
                    x = gauge.marker_x
                    inside = low <= x <= high
                    if inside:
                        if marker_inside_since is None:
                            marker_inside_since = now
                    else:
                        marker_inside_since = None
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
                    # Negative offsets advance the click relative to the current
                    # prediction; positive offsets wait after the marker enters.
                    lead_seconds = (args.input_latency + 1 / args.max_fps -
                                    args.bobber_time_offset_ms / 1000.0)
                    predicted_entry = (lead_seconds >= 0 and entering_eta is not None and
                                       entering_eta <= lead_seconds)
                    inside_delay_met = (args.bobber_time_offset_ms <= 0 or
                                        (marker_inside_since is not None and
                                         now - marker_inside_since >= args.bobber_time_offset_ms / 1000.0))
                    # Re-evaluate eligibility on every video frame. The cooldown
                    # gates touch dispatch only; it never pauses tracking or ETA updates.
                    should_click = (inside and inside_delay_met) or predicted_entry
                    if (bait_stage == "idle" and not bait_checking_now and not start_pending and should_click and
                            now >= next_click_allowed and ready_for_touch):
                        eta = 0.0 if inside else max(0.0, float(entering_eta or 0.0))
                        print(f"命中窗口：浮标 x={x:.1f}，v={velocity:+.0f}px/s，"
                              f"黄色=[{low},{high}]，预计 {eta:.3f}s 后进入；"
                              f"时机偏移={args.bobber_time_offset_ms:+.1f}ms；点击", flush=True)
                        tap_button(args.bobber_cooldown + random.uniform(0, args.bobber_jitter))
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
                        marker_inside_since = None
                        if gauge is None:
                            yellow_history.clear()

                    if (bait_stage == "idle" and not bait_checking_now and not start_pending and
                            args.start and now >= next_periodic_click and
                            now >= next_click_allowed and ready_for_touch):
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
