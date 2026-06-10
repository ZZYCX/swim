from __future__ import annotations

import argparse
import ctypes
import glob
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import mss
import numpy as np
import pyautogui
import pygetwindow as gw
from pywinauto import Desktop
from pywinauto.application import Application


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.08


DEBUG_NAMES = {
    "wechat_found": "debug_01_wechat_found.png",
    "swim_chat": "debug_02_swim_chat.png",
    "qr_detected": "debug_03_qr_detected.png",
    "image_viewer": "debug_04_image_viewer.png",
    "context_menu": "debug_05_context_menu.png",
    "after_qr_recognition": "debug_06_after_qr_recognition.png",
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
    no_ocr: bool
    max_wait: int
    dry_run: bool
    project_root: Path
    wechat_window: object | None = None
    wechat_uia: object | None = None
    wechat_rect: Rect | None = None
    viewer_window: object | None = None
    viewer_rect: Rect | None = None
    screen_origin: tuple[int, int] = (0, 0)
    last_debug_path: Path | None = None
    qr_detection: Detection | None = None
    submit_detection: Detection | None = None


class StepFailure(RuntimeError):
    def __init__(self, step: str, reason: str):
        super().__init__(reason)
        self.step = step
        self.reason = reason


CTX: Context | None = None


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


def log(message: str) -> None:
    print(message, flush=True)


def fail(step: str, reason: str) -> None:
    raise StepFailure(step, reason)


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
        if not title:
            continue
        if title == "微信" or ("微信" in title and "图片和视频" not in title):
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
        log(f"[WARN] UIA 连接微信窗口失败，将使用视觉检测兜底：{exc}")
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
    ctx = require_ctx()
    ox, oy = ctx.screen_origin
    return Rect(rect.left - ox, rect.top - oy, rect.right - ox, rect.bottom - oy)


def image_rect_to_abs_rect(rect: Rect) -> Rect:
    ctx = require_ctx()
    ox, oy = ctx.screen_origin
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
) -> Path:
    ctx = require_ctx()
    name = DEBUG_NAMES.get(debug_key, f"{debug_key}.png")
    path = ctx.debug_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)

    output = image.copy()
    for index, detection in enumerate(detections or []):
        color = (0, 255, 0) if index == 0 else (0, 200, 255)
        draw_detection(output, detection, color)

    if not cv2.imwrite(str(path), output):
        fail("screenshot_fullscreen", f"debug 截图保存失败：{path}")
    ctx.last_debug_path = path
    log(f"[DEBUG] saved {path}")
    return path


def screenshot_fullscreen(step_name: str) -> Path:
    image = capture_fullscreen_bgr()
    return save_debug_image(image, step_name)


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
        in_title_band = (
            rect.left > ctx.wechat_rect.left + int(ctx.wechat_rect.width * 0.28)
            and rect.top < ctx.wechat_rect.top + 120
        )
        if in_title_band:
            return True
    return False


def find_search_edit():
    for element in uia_descendants():
        if element_control_type(element) != "Edit":
            continue
        name = element_name(element).strip()
        auto_id = element_auto_id(element)
        if name == "搜索" or "search" in auto_id.lower():
            return element
    return None


def find_session_item(chat_name: str):
    ctx = require_ctx()
    if ctx.wechat_rect is None:
        return None
    candidates = []
    for element in uia_descendants():
        if element_control_type(element) != "ListItem":
            continue
        name = element_name(element)
        rect = element_rect(element)
        if rect is None or chat_name not in name:
            continue
        if rect.right <= ctx.wechat_rect.left + int(ctx.wechat_rect.width * 0.42):
            score = 1.0 if name.strip().startswith(chat_name) else 0.5
            candidates.append((score, rect.top, element))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2] if candidates else None


def click_element_center(element: object, reason: str) -> None:
    ctx = require_ctx()
    rect = element_rect(element)
    if rect is None or rect.width <= 0 or rect.height <= 0:
        fail(reason, "目标 UI 元素没有可靠边界，拒绝点击。")
    x, y = rect.center
    if ctx.dry_run:
        log(f"[DRY-RUN] would click {reason} at ({x}, {y})")
        return
    pyautogui.click(x, y)


def open_swim_chat():
    ctx = require_ctx()
    if current_chat_is_open(ctx.chat_name):
        log(f"[OK] 当前已打开目标聊天：{ctx.chat_name}")
        screenshot_fullscreen("swim_chat")
        return

    if ctx.dry_run:
        screenshot_fullscreen("swim_chat")
        fail("open_swim_chat", f"dry-run 模式下不会输入或点击，且当前未确认打开 {ctx.chat_name}。")

    search = find_search_edit()
    if search is None:
        fail("open_swim_chat", "找不到微信左侧搜索框。")

    click_element_center(search, "open_swim_chat.search")
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.write(ctx.chat_name, interval=0.02)
    time.sleep(0.8)

    item = find_session_item(ctx.chat_name)
    if item is None:
        fail("open_swim_chat", f"搜索后找不到目标聊天：{ctx.chat_name}")
    click_element_center(item, "open_swim_chat.session_item")
    time.sleep(0.8)

    ctx.wechat_uia = connect_uia_window(ctx.wechat_window)
    if not current_chat_is_open(ctx.chat_name):
        screenshot_fullscreen("swim_chat")
        fail("open_swim_chat", f"无法确认目标聊天已打开：{ctx.chat_name}")

    log(f"[OK] 已打开目标聊天：{ctx.chat_name}")
    screenshot_fullscreen("swim_chat")


def crop_by_rect(image: np.ndarray, rect: Rect) -> tuple[np.ndarray, Rect]:
    image_rect = abs_rect_to_image_rect(rect)
    full = Rect(0, 0, image.shape[1], image.shape[0])
    image_rect = image_rect.clamp(full)
    if image_rect.width <= 0 or image_rect.height <= 0:
        fail("crop_by_rect", f"裁剪区域无效：{rect}")
    return image[image_rect.top : image_rect.bottom, image_rect.left : image_rect.right], image_rect


def chat_content_region() -> Rect:
    ctx = require_ctx()
    if ctx.wechat_rect is None:
        fail("detect_qr_image_in_chat", "缺少微信窗口位置。")
    rect = ctx.wechat_rect
    return Rect(
        rect.left + int(rect.width * 0.30),
        rect.top + 75,
        rect.right - 18,
        rect.top + int(rect.height * 0.77),
    )


def template_paths() -> list[Path]:
    ctx = require_ctx()
    candidates = []
    for root in (ctx.project_root / "images", ctx.project_root.parent / "images"):
        candidates.extend(Path(path) for path in glob.glob(str(root / "*.jpg")))
        candidates.extend(Path(path) for path in glob.glob(str(root / "*.png")))
    return sorted(set(candidates))


def match_templates(crop: np.ndarray, crop_rect: Rect) -> Detection | None:
    gray_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray_crop = cv2.GaussianBlur(gray_crop, (3, 3), 0)
    best: Detection | None = None
    for template_path in template_paths():
        template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            continue
        template = cv2.GaussianBlur(template, (3, 3), 0)
        for scale in np.linspace(0.08, 0.42, 44):
            resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            th, tw = resized.shape[:2]
            if tw < 55 or th < 55 or tw >= gray_crop.shape[1] or th >= gray_crop.shape[0]:
                continue
            result = cv2.matchTemplate(gray_crop, resized, cv2.TM_CCOEFF_NORMED)
            _, score, _, max_loc = cv2.minMaxLoc(result)
            if best is None or score > best.score:
                image_left = crop_rect.left + max_loc[0]
                image_top = crop_rect.top + max_loc[1]
                bbox = image_rect_to_abs_rect(Rect(image_left, image_top, image_left + tw, image_top + th))
                best = Detection(
                    bbox=bbox,
                    center=bbox.center,
                    score=float(score),
                    label=f"template:{template_path.name}",
                )
    if best and best.score >= 0.42:
        return best
    return None


def high_frequency_score(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    edges = cv2.Canny(gray, 80, 180)
    edge_density = float(np.count_nonzero(edges)) / float(edges.size)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 1000.0
    return edge_density + min(lap_var, 2.0)


def green_icon_candidates(crop: np.ndarray, crop_rect: Rect) -> list[Detection]:
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 75, 80]), np.array([95, 255, 255]))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    detections = []
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 80:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if not (0.55 <= w / max(h, 1) <= 1.8):
            continue
        icon_cx = x + w / 2
        icon_cy = y + h / 2
        for factor in (4.5, 5.5, 6.5, 7.5):
            size = int(max(w, h) * factor)
            if size < 80 or size > 380:
                continue
            left = int(icon_cx - size * 0.78)
            top = int(icon_cy - size * 0.78)
            right = left + size
            bottom = top + size
            if left < 0 or top < 0 or right >= crop.shape[1] or bottom >= crop.shape[0]:
                continue
            patch = gray[top:bottom, left:right]
            freq = high_frequency_score(patch)
            white_ratio = float(np.mean(patch > 225))
            black_ratio = float(np.mean(patch < 60))
            if freq < 0.12 or white_ratio < 0.25 or black_ratio < 0.04:
                continue
            image_box = Rect(crop_rect.left + left, crop_rect.top + top, crop_rect.left + right, crop_rect.top + bottom)
            abs_box = image_rect_to_abs_rect(image_box)
            detections.append(
                Detection(
                    bbox=abs_box,
                    center=abs_box.center,
                    score=float(freq + white_ratio + black_ratio),
                    label="green-icon-qr",
                )
            )
    detections.sort(key=lambda det: det.score, reverse=True)
    return detections


def square_texture_candidates(crop: np.ndarray, crop_rect: Rect) -> list[Detection]:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        31,
        4,
    )
    contours, _ = cv2.findContours(255 - binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detections = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 80 or h < 80 or w > 380 or h > 380:
            continue
        ratio = w / max(h, 1)
        if not (0.72 <= ratio <= 1.28):
            continue
        left = max(0, x - 12)
        top = max(0, y - 12)
        right = min(crop.shape[1], x + w + 12)
        bottom = min(crop.shape[0], y + h + 12)
        patch = gray[top:bottom, left:right]
        freq = high_frequency_score(patch)
        white_ratio = float(np.mean(patch > 225))
        black_ratio = float(np.mean(patch < 70))
        if freq < 0.15 or white_ratio < 0.20 or black_ratio < 0.05:
            continue
        image_box = Rect(crop_rect.left + left, crop_rect.top + top, crop_rect.left + right, crop_rect.top + bottom)
        abs_box = image_rect_to_abs_rect(image_box)
        detections.append(
            Detection(
                bbox=abs_box,
                center=abs_box.center,
                score=float(freq + white_ratio + black_ratio),
                label="square-texture-qr",
            )
        )
    detections.sort(key=lambda det: det.score, reverse=True)
    return detections


def detect_qr_image_in_chat() -> tuple[Rect, tuple[int, int]]:
    ctx = require_ctx()
    image = capture_fullscreen_bgr()
    region = chat_content_region()
    crop, crop_rect = crop_by_rect(image, region)

    detection = match_templates(crop, crop_rect)
    if detection is None:
        generic = green_icon_candidates(crop, crop_rect) + square_texture_candidates(crop, crop_rect)
        generic.sort(key=lambda det: det.score, reverse=True)
        detection = generic[0] if generic else None

    if detection is None:
        save_debug_image(image, "qr_detected")
        fail("detect_qr_image_in_chat", "在微信聊天区域内未找到二维码图片候选。")

    ctx.qr_detection = detection
    save_debug_image(image, "qr_detected", [detection])
    log(f"[OK] 定位二维码图片：bbox={detection.bbox}, center={detection.center}, score={detection.score:.3f}")
    return detection.bbox, detection.center


def find_image_viewer_window() -> object | None:
    for window in gw.getAllWindows():
        title = safe_window_title(window).strip()
        if "图片和视频" in title or "Image" in title:
            return window
    return None


def open_qr_image_viewer(qr_center: tuple[int, int]):
    ctx = require_ctx()
    if ctx.dry_run:
        image = capture_fullscreen_bgr()
        save_debug_image(image, "image_viewer")
        log(f"[DRY-RUN] would open QR image viewer by double-clicking {qr_center}")
        return None

    pyautogui.doubleClick(qr_center[0], qr_center[1])
    time.sleep(1.5)

    viewer = find_image_viewer_window()
    image = capture_fullscreen_bgr()
    save_debug_image(image, "image_viewer")
    if viewer is None:
        fail("open_qr_image_viewer", "点击二维码后未检测到“图片和视频”查看器窗口。")

    activate_window(viewer)
    ctx.viewer_window = viewer
    ctx.viewer_rect = to_rect_from_pygetwindow(viewer)
    if ctx.viewer_rect.width < 300 or ctx.viewer_rect.height < 300:
        fail("open_qr_image_viewer", f"图片查看器窗口尺寸异常：{ctx.viewer_rect.width}x{ctx.viewer_rect.height}")

    log(f"[OK] 图片查看器已打开：rect={ctx.viewer_rect}")
    return viewer


def desktop_menu_items() -> list[object]:
    try:
        desktop = Desktop(backend="uia")
        items = []
        for window in desktop.windows():
            try:
                for element in window.descendants(control_type="MenuItem"):
                    name = element_name(element).strip()
                    if name:
                        items.append(element)
            except Exception:
                continue
        return items
    except Exception as exc:
        log(f"[WARN] 读取右键菜单 UIA 元素失败：{exc}")
        return []


def click_recognize_menu_by_uia() -> bool:
    targets = ("识别图中二维码", "识别二维码")
    deadline = time.time() + 2.0
    while time.time() < deadline:
        for item in desktop_menu_items():
            name = element_name(item).strip()
            if any(target in name for target in targets):
                click_element_center(item, "click_recognize_qr_menu.uia_menu")
                log(f"[OK] 已通过 UIA 点击菜单项：{name}")
                return True
        time.sleep(0.2)
    return False


def detect_context_menu_rect(image: np.ndarray, click_point: tuple[int, int]) -> Rect | None:
    ox, oy = require_ctx().screen_origin
    px, py = click_point[0] - ox, click_point[1] - oy
    search = Rect(px - 260, py - 380, px + 430, py + 560).clamp(Rect(0, 0, image.shape[1], image.shape[0]))
    crop = image[search.top : search.bottom, search.left : search.right]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = cv2.inRange(gray, 235, 255)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if area < 3500 or w < 90 or h < 110 or w > 420 or h > 580:
            continue
        candidates.append((area, Rect(search.left + x + ox, search.top + y + oy, search.left + x + w + ox, search.top + y + h + oy)))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1] if candidates else None


def synthetic_context_menu_rect(image: np.ndarray, click_point: tuple[int, int]) -> Rect | None:
    ctx = require_ctx()
    ox, oy = ctx.screen_origin
    px, py = click_point[0] - ox, click_point[1] - oy
    candidate = Rect(px - 8, py - 8, px + 275, py + 430).clamp(Rect(0, 0, image.shape[1], image.shape[0]))
    if candidate.width < 210 or candidate.height < 300:
        return None

    crop = image[candidate.top : candidate.bottom, candidate.left : candidate.right]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # The WeChat image-viewer menu contains many short dark text/icon strokes
    # arranged as horizontal rows. A plain QR background does not form this
    # regular row projection in the menu text band.
    text_band = gray[:, int(candidate.width * 0.10) : int(candidate.width * 0.78)]
    dark = text_band < 120
    row_projection = dark.sum(axis=1)
    active_rows = row_projection > max(8, text_band.shape[1] * 0.04)
    groups = []
    start = None
    for idx, active in enumerate(active_rows):
        if active and start is None:
            start = idx
        elif not active and start is not None:
            if idx - start >= 3:
                groups.append((start, idx))
            start = None
    if start is not None and len(active_rows) - start >= 3:
        groups.append((start, len(active_rows)))

    expected_menu_area = gray[: int(candidate.height * 0.88), :]
    light_ratio = float(np.mean(expected_menu_area > 225))
    if len(groups) < 8 or light_ratio < 0.45:
        return None

    return image_rect_to_abs_rect(candidate)


def fallback_click_recognize_menu(menu_rect: Rect) -> bool:
    ctx = require_ctx()
    if menu_rect.height < 110 or menu_rect.width < 90:
        return False
    x = menu_rect.left + int(menu_rect.width * 0.55)
    # WeChat image viewer context menu places "识别图中二维码" below
    # "定位到聊天位置" and above "使用系统默认方式打开".
    y = menu_rect.top + int(menu_rect.height * 0.68)
    if ctx.dry_run:
        log(f"[DRY-RUN] would click fallback recognize menu row at ({x}, {y}), menu={menu_rect}")
        return True
    pyautogui.click(x, y)
    log(f"[OK] 已使用菜单相对位置兜底点击：menu={menu_rect}, point=({x}, {y})")
    return True


def click_recognize_qr_menu():
    ctx = require_ctx()
    if ctx.viewer_rect is None:
        fail("click_recognize_qr_menu", "缺少图片查看器窗口位置。")

    click_point = ctx.viewer_rect.center
    if ctx.dry_run:
        image = capture_fullscreen_bgr()
        save_debug_image(image, "context_menu")
        log(f"[DRY-RUN] would right-click image viewer center {click_point}")
        return

    pyautogui.click(click_point[0], click_point[1], button="right")
    time.sleep(0.5)
    image = capture_fullscreen_bgr()
    save_debug_image(image, "context_menu")

    menu_rect = detect_context_menu_rect(image, click_point)
    if menu_rect is None:
        menu_rect = synthetic_context_menu_rect(image, click_point)
    if menu_rect is None:
        fail("click_recognize_qr_menu", "右键菜单已截图，但无法定位“识别二维码”菜单区域。")
    if not fallback_click_recognize_menu(menu_rect):
        fail("click_recognize_qr_menu", f"右键菜单区域不满足兜底点击条件：{menu_rect}")

    time.sleep(4.0)
    screenshot_fullscreen("after_qr_recognition")


def candidate_target_window_rect() -> Rect:
    ctx = require_ctx()
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


def orange_submit_candidates(image: np.ndarray, target_rect: Rect) -> list[Detection]:
    crop, crop_rect = crop_by_rect(image, target_rect)
    h, w = crop.shape[:2]
    search_local = Rect(int(w * 0.48), int(h * 0.70), w - 8, h - 8)
    search = crop[search_local.top : search_local.bottom, search_local.left : search_local.right]
    hsv = cv2.cvtColor(search, cv2.COLOR_BGR2HSV)
    lower1 = np.array([0, 70, 120])
    upper1 = np.array([28, 255, 255])
    mask = cv2.inRange(hsv, lower1, upper1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    detections = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 900:
            continue
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < 70 or bh < 26 or bh > 120:
            continue
        ratio = bw / max(bh, 1)
        if not (1.8 <= ratio <= 8.5):
            continue
        image_left = crop_rect.left + search_local.left + x
        image_top = crop_rect.top + search_local.top + y
        bbox = image_rect_to_abs_rect(Rect(image_left, image_top, image_left + bw, image_top + bh))
        cx, cy = bbox.center
        dist = ((target_rect.right - cx) ** 2 + (target_rect.bottom - cy) ** 2) ** 0.5
        score = float(area / 1000.0 - dist / 1000.0 + ratio / 10.0)
        detections.append(Detection(bbox=bbox, center=bbox.center, score=score, label="orange-submit"))

    detections.sort(key=lambda det: det.score, reverse=True)
    return detections


def wait_for_miniprogram_page():
    ctx = require_ctx()
    deadline = time.time() + ctx.max_wait
    best: Detection | None = None
    best_image: np.ndarray | None = None
    while time.time() < deadline:
        image = capture_fullscreen_bgr()
        target_rect = candidate_target_window_rect()
        candidates = orange_submit_candidates(image, target_rect)
        if candidates:
            best = candidates[0]
            best_image = image
            save_debug_image(image, "miniprogram_page", [best])
            log("[OK] 检测到小程序页面候选橙色提交区域。")
            return
        best_image = image
        time.sleep(1.0)

    if best_image is not None:
        save_debug_image(best_image, "miniprogram_page")
    fail("wait_for_miniprogram_page", f"{ctx.max_wait} 秒内未检测到小程序页面右下角橙色按钮。")


def detect_submit_button() -> tuple[Rect, tuple[int, int]]:
    ctx = require_ctx()
    image = capture_fullscreen_bgr()
    target_rect = candidate_target_window_rect()
    candidates = orange_submit_candidates(image, target_rect)
    if not candidates:
        save_debug_image(image, "submit_button")
        fail("detect_submit_button", "未在页面底部右侧检测到橙色“提交订单”按钮候选。")

    detection = candidates[0]
    ctx.submit_detection = detection
    save_debug_image(image, "submit_button", [detection])
    log(f"[OK] 定位提交订单按钮：bbox={detection.bbox}, center={detection.center}, score={detection.score:.3f}")
    return detection.bbox, detection.center


def click_submit_once(button_center: tuple[int, int]):
    ctx = require_ctx()
    if ctx.dry_run:
        image = capture_fullscreen_bgr()
        save_debug_image(image, "after_click")
        log(f"[DRY-RUN] would click submit once at {button_center}")
        return

    pyautogui.click(button_center[0], button_center[1])
    time.sleep(1.0)
    screenshot_fullscreen("after_click")
    log("[DONE] 已点击一次“提交订单”。")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows 微信小程序抢票提交订单自动化助手")
    parser.add_argument("--chat-name", default="swim", help="目标聊天名称，默认 swim")
    parser.add_argument("--debug-dir", default="./debug", help="debug 截图目录，默认 ./debug")
    parser.add_argument("--no-ocr", action="store_true", default=True, help="兼容参数：默认不依赖 OCR")
    parser.add_argument("--max-wait", type=int, default=10, help="等待小程序页面最长秒数，默认 10")
    parser.add_argument("--dry-run", action="store_true", help="只检测，不执行鼠标/键盘点击")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global CTX
    set_dpi_awareness()
    args = parse_args(argv or sys.argv[1:])
    project_root = Path(__file__).resolve().parent
    CTX = Context(
        chat_name=args.chat_name,
        debug_dir=Path(args.debug_dir).resolve(),
        no_ocr=args.no_ocr,
        max_wait=max(1, args.max_wait),
        dry_run=args.dry_run,
        project_root=project_root,
    )
    CTX.debug_dir.mkdir(parents=True, exist_ok=True)

    try:
        find_wechat_window()
        screenshot_fullscreen("wechat_found")
        open_swim_chat()
        _, qr_center = detect_qr_image_in_chat()
        if CTX.dry_run:
            open_qr_image_viewer(qr_center)
            log("[DRY-RUN] 已完成可见状态检测；后续打开图片、识别二维码、点击提交订单均未执行。")
            return 0
        open_qr_image_viewer(qr_center)
        click_recognize_qr_menu()
        wait_for_miniprogram_page()
        _, button_center = detect_submit_button()
        click_submit_once(button_center)
        return 0
    except StepFailure as exc:
        log(f"[FAIL] step={exc.step}")
        log(f"[FAIL] reason={exc.reason}")
        if CTX and CTX.last_debug_path:
            log(f"[FAIL] debug={CTX.last_debug_path}")
        return 1
    except Exception as exc:
        log(f"[FAIL] step=unexpected")
        log(f"[FAIL] reason={exc}")
        if CTX and CTX.last_debug_path:
            log(f"[FAIL] debug={CTX.last_debug_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
