from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Iterable, TextIO


MAIN_LOG_PATTERN = re.compile(r"^\[\d{2}:\d{2}:\d{2}\.\d{3} \+\d+\.\d{3}s\]")
TARGET_TIME_PATTERN = re.compile(r"^\d{2}:\d{2}:\d{2}$")
DEFAULT_LATE_TOLERANCE_SECONDS = 10.0
PROBE_COUNT = 3


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    launched_at: datetime
    first_main_log_at: datetime | None


class TeeLogger:
    def __init__(self, log_path: Path, console: TextIO | None = None):
        self.console = console or sys.stdout
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.file = log_path.open("a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        self.file.close()

    def __enter__(self) -> "TeeLogger":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _write(self, line: str) -> None:
        console_encoding = getattr(self.console, "encoding", None)
        console_line = line
        if console_encoding:
            console_line = line.encode(console_encoding, errors="replace").decode(console_encoding)
        print(console_line, file=self.console, flush=True)
        print(line, file=self.file, flush=True)

    def scheduler(self, message: str, now: datetime | None = None) -> None:
        current = now or datetime.now()
        self._write(f"[{current:%Y-%m-%d %H:%M:%S}.{current.microsecond // 1000:03d}] [SCHEDULER] {message}")

    def child(self, line: str) -> None:
        self._write(f"[MAIN] {line.rstrip()}")


def parse_target_time(value: str) -> clock_time:
    if not TARGET_TIME_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("时间必须使用 HH:MM:SS 格式，例如 00:00:00")
    try:
        return datetime.strptime(value, "%H:%M:%S").time()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"无效时间：{value}") from exc


def nearest_target_datetime(now: datetime, target_time: clock_time) -> datetime:
    today_target = datetime.combine(now.date(), target_time)
    candidates = (
        today_target - timedelta(days=1),
        today_target,
        today_target + timedelta(days=1),
    )
    return min(candidates, key=lambda candidate: abs((candidate - now).total_seconds()))


def is_too_late(now: datetime, target: datetime, tolerance_seconds: float) -> bool:
    return (now - target).total_seconds() > tolerance_seconds


def build_main_command(
    uv_path: Path,
    project_root: Path,
    *,
    startup_probe: bool = False,
    main_dry_run: bool = False,
) -> list[str]:
    command = [str(uv_path), "run", "python", str(project_root / "main.py")]
    if startup_probe:
        command.append("--startup-probe")
    if main_dry_run:
        command.append("--dry-run")
    return command


def resolve_uv_path(value: str | None) -> Path:
    candidate = value or shutil.which("uv")
    if not candidate:
        raise FileNotFoundError("找不到 uv.exe，请安装 uv 或通过 --uv-path 指定绝对路径。")
    path = Path(candidate).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"uv 路径不存在：{path}")
    return path


def is_desktop_unlocked() -> bool:
    if sys.platform != "win32":
        return True

    user32 = ctypes.windll.user32
    desktop_readobjects = 0x0001
    desktop_switchdesktop = 0x0100
    handle = user32.OpenInputDesktop(
        0,
        False,
        desktop_readobjects | desktop_switchdesktop,
    )
    if not handle:
        return False

    try:
        name_length = ctypes.c_uint(0)
        uoi_name = 2
        user32.GetUserObjectInformationW(handle, uoi_name, None, 0, ctypes.byref(name_length))
        if name_length.value == 0:
            return False
        buffer = ctypes.create_unicode_buffer(name_length.value)
        if not user32.GetUserObjectInformationW(
            handle,
            uoi_name,
            buffer,
            ctypes.sizeof(buffer),
            ctypes.byref(name_length),
        ):
            return False
        return buffer.value.casefold() == "default"
    finally:
        user32.CloseDesktop(handle)


def run_child_process(
    command: list[str],
    project_root: Path,
    logger: TeeLogger,
    *,
    probe_marker: str | None = None,
) -> tuple[ProcessResult, float | None]:
    launched_at = datetime.now()
    started = time.perf_counter()
    first_main_log_at: datetime | None = None
    marker_latency: float | None = None

    process = subprocess.Popen(
        command,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\r\n")
        received_at = datetime.now()
        logger.child(line)
        if first_main_log_at is None and MAIN_LOG_PATTERN.match(line):
            first_main_log_at = received_at
        if probe_marker and marker_latency is None and probe_marker in line:
            marker_latency = time.perf_counter() - started

    returncode = process.wait()
    return ProcessResult(returncode, launched_at, first_main_log_at), marker_latency


def calibrate_startup(
    uv_path: Path,
    project_root: Path,
    logger: TeeLogger,
    count: int = PROBE_COUNT,
) -> tuple[float, list[float]]:
    measurements: list[float] = []
    command = build_main_command(uv_path, project_root, startup_probe=True)
    for index in range(1, count + 1):
        result, latency = run_child_process(
            command,
            project_root,
            logger,
            probe_marker="[STARTUP-PROBE]",
        )
        if result.returncode != 0 or latency is None:
            raise RuntimeError(f"第 {index} 次启动探测失败，exit_code={result.returncode}")
        measurements.append(latency)
        logger.scheduler(f"启动探测 {index}/{count}：{latency:.3f} 秒")
    return statistics.median(measurements), measurements


def wait_until(target: datetime) -> None:
    timer_period_enabled = False
    if sys.platform == "win32":
        timer_period_enabled = ctypes.windll.winmm.timeBeginPeriod(1) == 0
    try:
        while True:
            remaining = (target - datetime.now()).total_seconds()
            if remaining <= 0:
                return
            if remaining > 1.0:
                time.sleep(min(remaining - 0.5, 0.5))
            elif remaining > 0.05:
                time.sleep(min(remaining / 2, 0.02))
            else:
                time.sleep(min(remaining, 0.001))
    finally:
        if timer_period_enabled:
            ctypes.windll.winmm.timeEndPeriod(1)


def format_measurements(values: Iterable[float]) -> str:
    return ", ".join(f"{value:.3f}s" for value in values)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="微信抢票每日精确定时调度器")
    parser.add_argument("--target-time", required=True, type=parse_target_time, help="main.py 目标启动时间 HH:MM:SS")
    parser.add_argument("--wake-lead-seconds", type=int, default=60, help="任务计划提前唤起秒数，默认 60")
    parser.add_argument("--late-tolerance-seconds", type=float, default=10.0, help="允许晚到秒数，默认 10")
    parser.add_argument("--uv-path", help="uv.exe 绝对路径；默认从 PATH 查找")
    parser.add_argument("--main-dry-run", action="store_true", help="正式触发时给 main.py 传入 --dry-run")
    parser.add_argument("--log-dir", default="./logs", help="日志目录，默认 ./logs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    project_root = Path(__file__).resolve().parent
    log_dir = Path(args.log_dir)
    if not log_dir.is_absolute():
        log_dir = project_root / log_dir
    now = datetime.now()
    target = nearest_target_datetime(now, args.target_time)
    log_path = log_dir / f"{target:%Y-%m-%d}.log"

    with TeeLogger(log_path) as logger:
        try:
            logger.scheduler(
                f"目标 main.py 启动时间={target:%Y-%m-%d %H:%M:%S}，"
                f"任务提前唤起={args.wake_lead_seconds}秒，晚到容限={args.late_tolerance_seconds:.1f}秒"
            )

            if is_too_late(now, target, args.late_tolerance_seconds):
                late_by = (now - target).total_seconds()
                logger.scheduler(f"已错过目标时间 {late_by:.3f} 秒，超过容限，取消执行。")
                return 2
            if not is_desktop_unlocked():
                logger.scheduler("桌面未解锁或不在 Default 交互桌面，取消执行。")
                return 3

            uv_path = resolve_uv_path(args.uv_path)
            logger.scheduler(f"项目目录={project_root}")
            logger.scheduler(f"uv 路径={uv_path}")

            startup_buffer, measurements = calibrate_startup(uv_path, project_root, logger)
            logger.scheduler(
                f"启动探测结果=[{format_measurements(measurements)}]，"
                f"采用中位数缓冲={startup_buffer:.3f}秒"
            )

            launch_at = target - timedelta(seconds=startup_buffer)
            now = datetime.now()
            if is_too_late(now, target, args.late_tolerance_seconds):
                late_by = (now - target).total_seconds()
                logger.scheduler(f"校准完成后已错过目标时间 {late_by:.3f} 秒，超过容限，取消执行。")
                return 2
            if now < launch_at:
                logger.scheduler(f"等待至正式进程拉起时刻={launch_at:%Y-%m-%d %H:%M:%S.%f}")
                wait_until(launch_at)
            else:
                logger.scheduler("已到正式进程拉起时刻，立即执行。")

            command = build_main_command(
                uv_path,
                project_root,
                main_dry_run=args.main_dry_run,
            )
            logger.scheduler(f"执行命令={' '.join(command)}")
            result, _ = run_child_process(command, project_root, logger)
            logger.scheduler(f"正式进程拉起时刻={result.launched_at:%Y-%m-%d %H:%M:%S.%f}")
            if result.first_main_log_at is not None:
                deviation = (result.first_main_log_at - target).total_seconds()
                logger.scheduler(
                    f"首条 main.py 日志时刻={result.first_main_log_at:%Y-%m-%d %H:%M:%S.%f}，"
                    f"相对目标偏差={deviation:+.3f}秒"
                )
            else:
                logger.scheduler("未检测到 main.py 标准时间日志。")
            logger.scheduler(f"main.py 退出码={result.returncode}")
            return result.returncode
        except Exception as exc:
            logger.scheduler(f"调度失败：{exc}")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
