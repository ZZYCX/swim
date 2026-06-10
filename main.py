from __future__ import annotations

import argparse
import ctypes
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import cv2
import mss
import numpy as np
import pyautogui
import pygetwindow as gw
import pyperclip
from pywinauto.application import Application


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.08


MINIPROGRAM_URLS = {
    1: "#小程序://奥冠体育/E3fnVdNHbsY1uiv",
    2: "#小程序://奥冠体育/PLaOWaqqLhsMcWi",
    3: "#小程序://奥冠体育/vuRORt1GZFNUGft",
    4: "#小程序://奥冠体育/WkeN4N7l2i1n9Hd",
    5: "#小程序://奥冠体育/aDkqsllgLL9IjpJ",
    6: "#小程序://奥冠体育/bLo3551a5a2unxJ",
    7: "#小程序://奥冠体育/9CP49TVTmnmpRIb",
}

WEEKDAY_NAMES = {
    1: "星期一",
    2: "星期二",
    3: "星期三",
    4: "星期四",
    5: "星期五",
    6: "星期六",
    7: "星期日",
}

DEBUG_NAMES = {
    "wechat_found": "debug_01_wechat_found.png",
    "swim_chat": "debug_02_swim_chat.png",
    "before_link_send": "debug_03_before_link_send.png",
    "after_link_send": "debug_04_after_link_send.png",
    "link_detected": "debug_05_link_detected.png",
    "after_link_click": "debug_06_after_link_click.png",
    "miniprogram_page": "debug_07_miniprogram_page.png",
    "submit_button": "debug_08_submit_button.png",
    "after_click": "debug_09_after_click.png",
}


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    def clamp(self, bounds: "Rect") -> "Rect":
        return Rect(
            max(self.left, bounds.left),
            max(self.top, bounds.top),
            min(self.right, bounds.right),
            min(self.bottom, bounds.bottom),
        )


@dataclass
class Detection:
    bbox: Rect
    center: tuple[int, int]
    score: float
    label: str


@dataclass
class Context:
    chat_name: str
    debug_dir: Path
    debug_mode: str
    max_wait: int
    dry_run: bool
    wechat_window: object | None = None
    wechat_uia: object | None = None
    wechat_rect: Rect | None = None
    miniprogram_rect: Rect | None = None
    screen_origin: tuple[int, int] = (0, 0)
    last_debug_path: Path | None = None
    last_debug_image: np.ndarray | None = None
    last_debug_key: str | None = None
    link_detection: Detection | None = None
    submit_detection: Detection | None = None


class StepFailure(RuntimeError):
    def __init__(self, step: str, reason: str):
        super().__init__(reason)
        self.step = step
        self.reason = reason


CTX: Context | None = None
RUN_STARTED_AT: float | None = None


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def require_ctx() -> Context:
    if CTX is None:
        raise RuntimeError("Context was not initialized")
    return CTX


def elapsed_since_start(now: float | None = None) -> float:
    if RUN_STARTED_AT is None:
        return 0.0
    current = time.perf_counter() if now is None else now
    return max(0.0, current - RUN_STARTED_AT)


def format_log_line(message: str, wall_time: datetime, elapsed: float) -> str:
    clock = f"{wall_time:%H:%M:%S}.{wall_time.microsecond // 1000:03d}"
    return f"[{clock} +{elapsed:.3f}s] {message}"


def log(message: str, *, elapsed: float | None = None) -> None:
    measured_elapsed = elapsed_since_start() if elapsed is None else elapsed
    print(format_log_line(message, datetime.now(), measured_elapsed), flush=True)


def log_page_ready_timing() -> None:
    elapsed = elapsed_since_start()
    log(
        f"[TIMING] 从 main.py 启动到小程序页面可操作，总耗时={elapsed:.3f}秒",
        elapsed=elapsed,
    )


def fail(step: str, reason: str) -> None:
    raise StepFailure(step, reason)


def miniprogram_url_for_weekday(weekday: int) -> str:
    try:
        return MINIPROGRAM_URLS[weekday]
    except KeyError as exc:
        raise ValueError(f"weekday must be between 1 and 7, got {weekday}") from exc


def miniprogram_url_for_date(value: date) -> str:
    next_weekday = value.isoweekday() % 7 + 1
    return miniprogram_url_for_weekday(next_weekday)


def to_rect_from_pygetwindow(window: object) -> Rect:
    return Rect(
        int(window.left),
        int(window.top),
        int(window.left + window.width),
        int(window.top + window.height),
    )


def safe_window_title(window: object) -> str:
    return str(getattr(window, "title", "") or "")


def find_wechat_windows() -> list[object]:
    windows = []
    for window in gw.getAllWindows():
        title = safe_window_title(window).strip()
        if title and (title == "微信" or ("微信" in title and "图片和视频" not in title)):
            windows.append(window)
    windows.sort(key=lambda item: (safe_window_title(item) != "微信", safe_window_title(item)))
    return windows


def connect_uia_window(window: object):
    handle = getattr(window, "_hWnd", None)
    if not handle:
        return None
    app = Application(backend="uia").connect(handle=handle)
    return app.window(handle=handle)


def activate_window(window: object) -> None:
    try:
        if getattr(window, "isMinimized", False):
            window.restore()
            time.sleep(0.3)
    except Exception:
        pass
    try:
        window.activate()
    except Exception:
        pass
    time.sleep(0.4)


def find_wechat_window():
    ctx = require_ctx()
    windows = find_wechat_windows()
    if not windows:
        fail("find_wechat_window", "找不到标题包含“微信”的 Windows 微信窗口。")

    window = windows[0]
    activate_window(window)
    rect = to_rect_from_pygetwindow(window)
    if rect.width < 500 or rect.height < 400:
        fail("find_wechat_window", f"微信窗口尺寸异常：{rect.width}x{rect.height}。")

    ctx.wechat_window = window
    ctx.wechat_rect = rect
    try:
        ctx.wechat_uia = connect_uia_window(window)
    except Exception as exc:
        log(f"[WARN] UIA 连接微信窗口失败，将使用几何和截图差分兜底：{exc}")
        ctx.wechat_uia = None

    log(
        "[OK] 找到微信窗口："
        f"title={safe_window_title(window)!r}, "
        f"left={rect.left}, top={rect.top}, width={rect.width}, height={rect.height}"
    )
    return window


def capture_fullscreen_bgr() -> np.ndarray:
    ctx = require_ctx()
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            shot = sct.grab(monitor)
            ctx.screen_origin = (int(monitor["left"]), int(monitor["top"]))
            image = np.array(shot)
            return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    except Exception as exc:
        fail("screenshot_fullscreen", f"mss 全屏截图失败：{exc}")


def abs_rect_to_image_rect(rect: Rect) -> Rect:
    ox, oy = require_ctx().screen_origin
    return Rect(rect.left - ox, rect.top - oy, rect.right - ox, rect.bottom - oy)


def image_rect_to_abs_rect(rect: Rect) -> Rect:
    ox, oy = require_ctx().screen_origin
    return Rect(rect.left + ox, rect.top + oy, rect.right + ox, rect.bottom + oy)


def draw_detection(image: np.ndarray, detection: Detection, color: tuple[int, int, int]) -> None:
    box = abs_rect_to_image_rect(detection.bbox)
    cv2.rectangle(image, (box.left, box.top), (box.right, box.bottom), color, 3)
    cx, cy = detection.center
    ox, oy = require_ctx().screen_origin
    cv2.circle(image, (cx - ox, cy - oy), 6, color, -1)
    cv2.putText(
        image,
        f"{detection.label} {detection.score:.2f}",
        (box.left, max(24, box.top - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        color,
        2,
        cv2.LINE_AA,
    )


def save_debug_image(
    image: np.ndarray,
    debug_key: str,
    detections: Iterable[Detection] | None = None,
) -> None:
    ctx = require_ctx()
    output = image.copy()
    for index, detection in enumerate(detections or []):
        color = (0, 255, 0) if index == 0 else (0, 200, 255)
        draw_detection(output, detection, color)

    ctx.last_debug_image = output
    ctx.last_debug_key = debug_key

    if ctx.debug_mode == "all":
        path = ctx.debug_dir / DEBUG_NAMES.get(debug_key, f"{debug_key}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), output):
            fail("screenshot_fullscreen", f"debug 截图保存失败：{path}")
        ctx.last_debug_path = path
        log(f"[DEBUG] saved {path}")


def screenshot_fullscreen(step_name: str) -> None:
    save_debug_image(capture_fullscreen_bgr(), step_name)


def persist_failure_debug(step: str) -> Path | None:
    ctx = require_ctx()
    if ctx.debug_mode == "all" and ctx.last_debug_path is not None:
        return ctx.last_debug_path
    if ctx.last_debug_image is None:
        return None

    safe_step = "".join(char if char.isalnum() or char in "-_" else "_" for char in step)
    path = ctx.debug_dir / f"debug_failure_{safe_step}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), ctx.last_debug_image):
        log(f"[FAIL] debug screenshot save failed: {path}")
        return None

    ctx.last_debug_path = path
    return path


def uia_descendants():
    ctx = require_ctx()
    if ctx.wechat_uia is None:
        return []
    try:
        return ctx.wechat_uia.descendants()
    except Exception as exc:
        log(f"[WARN] 读取微信 UIA 元素失败：{exc}")
        return []


def element_name(element: object) -> str:
    try:
        return str(element.window_text() or "")
    except Exception:
        try:
            return str(element.element_info.name or "")
        except Exception:
            return ""


def element_auto_id(element: object) -> str:
    try:
        return str(element.element_info.automation_id or "")
    except Exception:
        return ""


def element_control_type(element: object) -> str:
    try:
        return str(element.element_info.control_type or "")
    except Exception:
        return ""


def element_rect(element: object) -> Rect | None:
    try:
        rect = element.rectangle()
        return Rect(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return None


def current_chat_is_open(chat_name: str) -> bool:
    ctx = require_ctx()
    if ctx.wechat_rect is None:
        return False
    for element in uia_descendants():
        name = element_name(element).strip()
        auto_id = element_auto_id(element)
        rect = element_rect(element)
        if name != chat_name or rect is None:
            continue
        if "current_chat_name_label" in auto_id:
            return True
        if rect.left > ctx.wechat_rect.left + int(ctx.wechat_rect.width * 0.28) and rect.top < ctx.wechat_rect.top + 120:
            return True
    return False


def open_swim_chat() -> None:
    ctx = require_ctx()
    if current_chat_is_open(ctx.chat_name):
        log(f"[OK] 当前已打开目标聊天：{ctx.chat_name}")
        screenshot_fullscreen("swim_chat")
        return

    screenshot_fullscreen("swim_chat")
    fail("open_swim_chat", f"当前微信聊天未确认为 {ctx.chat_name}；脚本不会自动搜索或切换聊天。")


def chat_content_region() -> Rect:
    ctx = require_ctx()
    if ctx.wechat_rect is None:
        fail("chat_content_region", "缺少微信窗口位置。")
    rect = ctx.wechat_rect
    return Rect(
        rect.left + int(rect.width * 0.23),
        rect.top + 75,
        rect.right - 18,
        rect.top + int(rect.height * 0.77),
    )


def point_inside(rect: Rect, bounds: Rect) -> bool:
    cx, cy = rect.center
    return bounds.left <= cx <= bounds.right and bounds.top <= cy <= bounds.bottom


def locate_chat_input_point() -> tuple[int, int]:
    ctx = require_ctx()
    if ctx.wechat_rect is None:
        fail("locate_chat_input", "缺少微信窗口位置。")

    window = ctx.wechat_rect
    min_left = window.left + int(window.width * 0.23)
    min_top = window.top + int(window.height * 0.72)
    candidates: list[Rect] = []
    for element in uia_descendants():
        if element_control_type(element) not in {"Edit", "Document"}:
            continue
        rect = element_rect(element)
        if rect is None or rect.width < window.width * 0.25 or rect.height < 35:
            continue
        if rect.left >= min_left and rect.top >= min_top and point_inside(rect, window):
            candidates.append(rect)

    if candidates:
        selected = max(candidates, key=lambda rect: rect.width * rect.height)
        point = selected.center
        log(f"[OK] 通过 UIA 定位聊天输入框：rect={selected}, point={point}")
        return point

    point = (
        window.left + int(window.width * 0.62),
        window.top + int(window.height * 0.86),
    )
    log(f"[WARN] UIA 未暴露聊天输入框，使用窗口比例坐标：point={point}")
    return point


def select_latest_link_rect(
    candidates: Iterable[tuple[str, Rect]],
    link: str,
    chat_region: Rect,
) -> Rect | None:
    min_x = chat_region.left + int(chat_region.width * 0.52)
    matches: list[tuple[Rect, Rect]] = []
    for name, rect in candidates:
        if name.strip() != link or rect.width <= 0 or rect.height <= 0:
            continue
        if not point_inside(rect, chat_region):
            continue
        if rect.width >= chat_region.width * 0.75 and rect.right >= chat_region.right - 60:
            clickable = Rect(
                max(min_x, rect.right - int(chat_region.width * 0.42)),
                rect.top,
                rect.right - int(chat_region.width * 0.06),
                rect.bottom,
            )
            matches.append((rect, clickable))
        elif rect.center[0] >= min_x:
            matches.append((rect, rect))

    if not matches:
        return None
    return max(matches, key=lambda item: (item[0].center[1], item[0].center[0]))[1]


def locate_sent_link_by_uia(link: str) -> Detection | None:
    entries = []
    for element in uia_descendants():
        rect = element_rect(element)
        if rect is not None:
            entries.append((element_name(element), rect))
    selected = select_latest_link_rect(entries, link, chat_content_region())
    if selected is None:
        return None
    return Detection(selected, selected.center, 1.0, "uia-link")


def difference_message_candidates(
    before: np.ndarray,
    after: np.ndarray,
    region: Rect,
) -> list[Detection]:
    if before.shape != after.shape or before.ndim != 3:
        return []
    bounds = Rect(0, 0, before.shape[1], before.shape[0])
    region = region.clamp(bounds)
    if region.width <= 0 or region.height <= 0:
        return []

    crop_before = before[region.top : region.bottom, region.left : region.right]
    crop_after = after[region.top : region.bottom, region.left : region.right]
    diff = cv2.absdiff(crop_before, crop_after)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 28, 255, cv2.THRESH_BINARY)
    mask[:, : int(region.width * 0.52)] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((13, 31), np.uint8), iterations=1)

    changed_ratio = float(np.count_nonzero(mask)) / float(mask.size)
    if changed_ratio < 0.0008 or changed_ratio > 0.18:
        return []

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width < 70 or height < 18:
            continue
        if width > region.width * 0.48 or height > region.height * 0.28:
            continue
        bbox = Rect(region.left + x, region.top + y, region.left + x + width, region.top + y + height)
        area = float(cv2.contourArea(contour))
        detections.append(Detection(bbox, bbox.center, area, "diff-link"))

    if not detections:
        return []
    latest_y = max(item.center[1] for item in detections)
    latest_row = [
        item
        for item in detections
        if latest_y - item.center[1] <= max(40, item.bbox.height * 1.5)
    ]
    latest_row.sort(key=lambda item: item.score, reverse=True)
    return latest_row[:1]


def locate_sent_link_by_difference(before: np.ndarray, after: np.ndarray) -> Detection | None:
    image_region = abs_rect_to_image_rect(chat_content_region())
    candidates = difference_message_candidates(before, after, image_region)
    if len(candidates) != 1:
        return None
    image_detection = candidates[0]
    bbox = image_rect_to_abs_rect(image_detection.bbox)
    return Detection(bbox, bbox.center, image_detection.score, image_detection.label)


def send_link_message(link: str) -> tuple[np.ndarray, np.ndarray]:
    ctx = require_ctx()
    input_point = locate_chat_input_point()
    before = capture_fullscreen_bgr()
    save_debug_image(before, "before_link_send")

    if ctx.dry_run:
        log(f"[DRY-RUN] 将清空输入框并发送：{link}")
        return before, before.copy()

    pyautogui.click(*input_point)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    pyperclip.copy(link)
    pyautogui.hotkey("ctrl", "v")
    pyautogui.press("enter")
    time.sleep(1.0)

    after = capture_fullscreen_bgr()
    save_debug_image(after, "after_link_send")
    log(f"[OK] 已发送次日小程序链接：{link}")
    return before, after


def locate_sent_link(link: str, before: np.ndarray, initial_after: np.ndarray) -> Detection:
    ctx = require_ctx()
    deadline = time.time() + min(5, ctx.max_wait)
    after = initial_after
    while time.time() < deadline:
        detection = locate_sent_link_by_uia(link)
        if detection is None:
            detection = locate_sent_link_by_difference(before, after)
        if detection is not None:
            ctx.link_detection = detection
            save_debug_image(after, "link_detected", [detection])
            log(f"[OK] 定位刚发送的链接：center={detection.center}, method={detection.label}")
            return detection
        time.sleep(0.4)
        after = capture_fullscreen_bgr()

    save_debug_image(after, "link_detected")
    fail("locate_sent_link", "未能通过 UIA 或截图差分可靠定位刚发送的链接，拒绝盲点。")


def click_sent_link(detection: Detection) -> None:
    pyautogui.click(*detection.center)
    time.sleep(1.0)
    screenshot_fullscreen("after_link_click")
    log(f"[OK] 已点击次日小程序链接：center={detection.center}")


def crop_by_rect(image: np.ndarray, rect: Rect) -> tuple[np.ndarray, Rect]:
    image_rect = abs_rect_to_image_rect(rect)
    full = Rect(0, 0, image.shape[1], image.shape[0])
    image_rect = image_rect.clamp(full)
    if image_rect.width <= 0 or image_rect.height <= 0:
        fail("crop_by_rect", f"裁剪区域无效：{rect}")
    return image[image_rect.top : image_rect.bottom, image_rect.left : image_rect.right], image_rect


def candidate_target_window_rect() -> Rect:
    ctx = require_ctx()
    for window in gw.getAllWindows():
        title = safe_window_title(window).strip()
        if "奥冠体育" not in title:
            continue
        rect = to_rect_from_pygetwindow(window)
        if rect.width >= 300 and rect.height >= 300:
            ctx.miniprogram_rect = rect
            return rect
    if ctx.miniprogram_rect is not None:
        return ctx.miniprogram_rect
    active = gw.getActiveWindow()
    if active is not None and safe_window_title(active).strip():
        rect = to_rect_from_pygetwindow(active)
        if rect.width >= 300 and rect.height >= 300:
            return rect
    if ctx.wechat_rect is not None:
        return ctx.wechat_rect
    full = capture_fullscreen_bgr()
    ox, oy = ctx.screen_origin
    return Rect(ox, oy, ox + full.shape[1], oy + full.shape[0])


def orange_button_candidates(image: np.ndarray, target_rect: Rect) -> list[Detection]:
    crop, crop_rect = crop_by_rect(image, target_rect)
    height, width = crop.shape[:2]
    search_local = Rect(int(width * 0.12), int(height * 0.55), width - 8, height - 8)
    search = crop[search_local.top : search_local.bottom, search_local.left : search_local.right]
    hsv = cv2.cvtColor(search, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 70, 120]), np.array([28, 255, 255]))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    detections = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 900:
            continue
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width < 70 or box_height < 26 or box_height > 120:
            continue
        ratio = box_width / max(box_height, 1)
        if not 1.8 <= ratio <= 8.5:
            continue
        image_left = crop_rect.left + search_local.left + x
        image_top = crop_rect.top + search_local.top + y
        bbox = image_rect_to_abs_rect(
            Rect(image_left, image_top, image_left + box_width, image_top + box_height)
        )
        cx, cy = bbox.center
        distance = ((target_rect.right - cx) ** 2 + (target_rect.bottom - cy) ** 2) ** 0.5
        score = float(area / 1000.0 - distance / 1000.0 + ratio / 10.0)
        detections.append(Detection(bbox, bbox.center, score, "orange-submit"))

    detections.sort(key=lambda detection: detection.score, reverse=True)
    return detections


def submit_button_candidates(image: np.ndarray, target_rect: Rect) -> list[Detection]:
    min_x = target_rect.left + int(target_rect.width * 0.55)
    min_y = target_rect.top + int(target_rect.height * 0.88)
    return [
        detection
        for detection in orange_button_candidates(image, target_rect)
        if detection.center[0] >= min_x and detection.center[1] >= min_y
    ]


def notice_dialog_candidate(image: np.ndarray, target_rect: Rect) -> Detection | None:
    min_y = target_rect.top + int(target_rect.height * 0.68)
    max_y = target_rect.top + int(target_rect.height * 0.88)
    max_center_offset = target_rect.width * 0.18
    min_width = target_rect.width * 0.30
    candidates = [
        detection
        for detection in orange_button_candidates(image, target_rect)
        if min_y <= detection.center[1] < max_y
        and detection.bbox.width >= min_width
        and abs(detection.center[0] - target_rect.center[0]) <= max_center_offset
    ]
    return candidates[0] if candidates else None


def wait_for_miniprogram_page() -> None:
    ctx = require_ctx()
    deadline = time.time() + ctx.max_wait
    best_image: np.ndarray | None = None
    notice_dismissed = False
    while time.time() < deadline:
        image = capture_fullscreen_bgr()
        target_rect = candidate_target_window_rect()
        if not notice_dismissed:
            notice = notice_dialog_candidate(image, target_rect)
            if notice is not None:
                save_debug_image(image, "miniprogram_page", [notice])
                pyautogui.click(*notice.center)
                notice_dismissed = True
                log(f"[OK] 已关闭须知弹窗：center={notice.center}")
                time.sleep(1.0)
                continue
        candidates = submit_button_candidates(image, target_rect)
        if candidates:
            detection = candidates[0]
            ctx.submit_detection = detection
            save_debug_image(image, "miniprogram_page", [detection])
            save_debug_image(image, "submit_button", [detection])
            log("[OK] 检测到小程序页面并锁定橙色提交区域。")
            log_page_ready_timing()
            return
        best_image = image
        time.sleep(1.0)

    if best_image is not None:
        save_debug_image(best_image, "miniprogram_page")
    fail("wait_for_miniprogram_page", f"{ctx.max_wait} 秒内未检测到小程序页面右下角橙色按钮。")


def detect_submit_button() -> tuple[Rect, tuple[int, int]]:
    ctx = require_ctx()
    if ctx.submit_detection is not None:
        detection = ctx.submit_detection
        log(f"[OK] 复用已验证的提交订单按钮：bbox={detection.bbox}, center={detection.center}")
        return detection.bbox, detection.center

    image = capture_fullscreen_bgr()
    target_rect = candidate_target_window_rect()
    candidates = submit_button_candidates(image, target_rect)
    if not candidates:
        save_debug_image(image, "submit_button")
        fail("detect_submit_button", "未在页面底部右侧检测到橙色“提交订单”按钮候选。")

    detection = candidates[0]
    ctx.submit_detection = detection
    save_debug_image(image, "submit_button", [detection])
    log(f"[OK] 定位提交订单按钮：bbox={detection.bbox}, center={detection.center}, score={detection.score:.3f}")
    return detection.bbox, detection.center


def click_submit_once(button_center: tuple[int, int]) -> None:
    pyautogui.click(*button_center)
    time.sleep(1.0)
    screenshot_fullscreen("after_click")
    log("[DONE] 已点击一次“提交订单”。")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows 微信小程序抢票提交订单自动化助手")
    parser.add_argument("--chat-name", default="swim", help="目标聊天名称，默认 swim")
    parser.add_argument("--debug-dir", default="./debug", help="debug 截图目录，默认 ./debug")
    parser.add_argument(
        "--debug-mode",
        choices=("all", "failure"),
        default="failure",
        help="截图模式：all 保存全部过程图，failure 仅失败保存最后截图；默认 failure",
    )
    parser.add_argument("--max-wait", type=int, default=20, help="等待小程序页面最长秒数，默认 20")
    parser.add_argument("--dry-run", action="store_true", help="只验证微信、聊天和次日链接，不发送或点击")
    parser.add_argument("--startup-probe", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global CTX, RUN_STARTED_AT
    RUN_STARTED_AT = time.perf_counter()
    set_dpi_awareness()
    args = parse_args(argv or sys.argv[1:])
    if args.startup_probe:
        log("[STARTUP-PROBE] main.py 已进入 main()")
        return 0

    CTX = Context(
        chat_name=args.chat_name,
        debug_dir=Path(args.debug_dir).resolve(),
        debug_mode=args.debug_mode,
        max_wait=max(1, args.max_wait),
        dry_run=args.dry_run,
    )
    CTX.debug_dir.mkdir(parents=True, exist_ok=True)

    today = date.today()
    weekday = today.isoweekday()
    next_weekday = weekday % 7 + 1
    link = miniprogram_url_for_date(today)
    log(
        f"[INFO] 当前日期={today.isoformat()}，当前={WEEKDAY_NAMES[weekday]}，"
        f"选择次日={WEEKDAY_NAMES[next_weekday]}，链接={link}"
    )

    try:
        find_wechat_window()
        screenshot_fullscreen("wechat_found")
        open_swim_chat()
        if CTX.dry_run:
            locate_chat_input_point()
            log("[DRY-RUN] 已验证微信窗口、目标聊天、输入框和次日链接；未清空、发送或点击。")
            return 0

        before, after = send_link_message(link)
        link_detection = locate_sent_link(link, before, after)
        click_sent_link(link_detection)
        wait_for_miniprogram_page()
        _, button_center = detect_submit_button()
        click_submit_once(button_center)
        return 0
    except StepFailure as exc:
        log(f"[FAIL] step={exc.step}")
        log(f"[FAIL] reason={exc.reason}")
        debug_path = persist_failure_debug(exc.step)
        if debug_path:
            log(f"[FAIL] debug={debug_path}")
        return 1
    except Exception as exc:
        log("[FAIL] step=unexpected")
        log(f"[FAIL] reason={exc}")
        debug_path = persist_failure_debug("unexpected")
        if debug_path:
            log(f"[FAIL] debug={debug_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
