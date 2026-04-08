# -*- coding: utf-8 -*-
"""波形时间线组件。

QPainter渲染波形、字幕块、播放游标、时间标尺。
完整鼠标交互：拖拽边缘/主体、缩放、平移、右键菜单、复制粘贴、双击编辑。
"""
import array
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt5.QtWidgets import QAction, QMenu, QWidget

# 常量
MIN_PIXELS_PER_MS = 0.05
MAX_PIXELS_PER_MS = 10.0
EDGE_HIT_ZONE_PX = 6
RULER_HEIGHT = 24
SNAP_MS = 10
ZOOM_FACTOR = 1.2
ZOOM_DEBOUNCE_MS = 50

# 颜色
COLOR_BG = QColor("#1e1e2e")
COLOR_WAVEFORM = QColor("#3a7bd5")
COLOR_RULER_TEXT = QColor("#888888")
COLOR_RULER_TICK = QColor("#444444")
COLOR_BLOCK_FILL = QColor(100, 180, 255, 60)
COLOR_BLOCK_BORDER = QColor("#5599dd")
COLOR_BLOCK_SELECTED_FILL = QColor(255, 200, 80, 80)
COLOR_BLOCK_SELECTED_BORDER = QColor("#ffcc44")
COLOR_CURSOR = QColor("#ff4444")
COLOR_PLACEHOLDER = QColor(255, 255, 255, 120)


class WaveformTimelineWidget(QWidget):
    """波形时间线渲染与交互组件。"""

    # 信号
    seek_requested = pyqtSignal(int)  # position_ms
    subtitle_selected = pyqtSignal(int)  # 0-based row index
    subtitle_time_changed = pyqtSignal(int, int, int)  # index, start_ms, end_ms
    subtitle_add_requested = pyqtSignal(int)  # position_ms
    subtitle_delete_requested = pyqtSignal(int)  # index
    subtitle_split_requested = pyqtSignal(int)  # index
    subtitle_copy_requested = pyqtSignal(int)  # index
    subtitle_paste_requested = pyqtSignal(int)  # position_ms
    subtitle_edit_requested = pyqtSignal(int)  # index

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        # 峰值数据
        self._peaks: List[Tuple[int, int]] = []
        self._peak_min_arr: array.array = array.array("h")
        self._peak_max_arr: array.array = array.array("h")
        self._sample_rate: int = 16000
        self._total_frames: int = 0

        # 时间轴
        self._duration_ms: int = 0
        self._scroll_offset_ms: float = 0.0
        self._pixels_per_ms: float = 0.1

        # 字幕数据
        self._subtitle_data: Dict = {}
        self._selected_index: int = -1

        # 播放游标
        self._playback_position_ms: int = 0

        # 像素缓存
        self._waveform_pixmap: Optional[QPixmap] = None
        self._pixmap_dirty: bool = True

        # 缩放防抖
        self._zoom_timer: QTimer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._rebuild_pixmap)

        # 拖拽状态
        self._drag_mode: str = ""  # "left_edge", "right_edge", "block_body", ""
        self._drag_index: int = -1
        self._drag_start_x: float = 0.0
        self._drag_orig_start_ms: int = 0
        self._drag_orig_end_ms: int = 0
        self._drag_visual_start_ms: int = 0  # 拖拽时的视觉状态，不修改实际数据
        self._drag_visual_end_ms: int = 0

    # ── 公共方法 ──

    def set_peaks(self, peaks: list, sample_rate: int, total_frames: int) -> None:
        """设置峰值数据。"""
        self._peaks = peaks
        self._sample_rate = sample_rate
        self._total_frames = total_frames

        # 紧凑int16存储
        self._peak_min_arr = array.array("h", [p[0] for p in peaks])
        self._peak_max_arr = array.array("h", [p[1] for p in peaks])

        if sample_rate > 0 and total_frames > 0:
            self._duration_ms = int(total_frames * 1000 / sample_rate)

        self._fit_zoom()
        self._pixmap_dirty = True
        self.update()

    def set_subtitle_data(self, data: dict) -> None:
        """设置字幕数据（与SubtitleTableModel._data相同格式）。"""
        self._subtitle_data = data or {}
        self.update()

    def set_duration_ms(self, duration_ms: int) -> None:
        """设置总时长（毫秒）。"""
        self._duration_ms = duration_ms
        self._fit_zoom()
        self._pixmap_dirty = True
        self.update()

    def set_playback_position(self, position_ms: int) -> None:
        """设置播放游标位置（轻量刷新）。"""
        self._playback_position_ms = position_ms
        self.update()

    def set_selected_index(self, index: int) -> None:
        """设置选中的字幕块索引（-1=无选中）。"""
        self._selected_index = index
        self.update()

    # ── 坐标转换 ──

    def ms_to_x(self, ms: int) -> float:
        return (ms - self._scroll_offset_ms) * self._pixels_per_ms

    def x_to_ms(self, x: float) -> int:
        return int(x / self._pixels_per_ms + self._scroll_offset_ms)

    # ── 属性 ──

    @property
    def scroll_offset_ms(self) -> float:
        return self._scroll_offset_ms

    @scroll_offset_ms.setter
    def scroll_offset_ms(self, value: float) -> None:
        value = max(0.0, min(value, max(0.0, self._duration_ms - self.visible_duration_ms)))
        if value != self._scroll_offset_ms:
            self._scroll_offset_ms = value
            self._pixmap_dirty = True
            self.update()

    @property
    def pixels_per_ms(self) -> float:
        return self._pixels_per_ms

    @property
    def visible_duration_ms(self) -> float:
        if self._pixels_per_ms <= 0:
            return 0.0
        return self.width() / self._pixels_per_ms

    @property
    def total_duration_ms(self) -> float:
        return float(self._duration_ms)

    # ── 内部方法 ──

    def _fit_zoom(self) -> None:
        """默认缩放：适配整个时长到组件宽度。"""
        w = self.width()
        if w <= 0:
            w = 800
        if self._duration_ms > 0:
            self._pixels_per_ms = max(MIN_PIXELS_PER_MS, min(w / self._duration_ms, MAX_PIXELS_PER_MS))
        self._scroll_offset_ms = 0.0

    def _rebuild_pixmap(self) -> None:
        """重建波形像素缓存。"""
        self._pixmap_dirty = True
        self.update()

    def _build_waveform_pixmap(self) -> None:
        """绘制波形到QPixmap缓存。"""
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        pm = QPixmap(w, h)
        pm.fill(COLOR_BG)

        if not self._peak_min_arr:
            self._waveform_pixmap = pm
            self._pixmap_dirty = False
            return

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing, False)
        pen = QPen(COLOR_WAVEFORM, 1)
        painter.setPen(pen)

        wave_top = RULER_HEIGHT
        wave_h = h - RULER_HEIGHT
        mid_y = wave_top + wave_h / 2.0
        if wave_h <= 0:
            painter.end()
            self._waveform_pixmap = pm
            self._pixmap_dirty = False
            return

        scale = wave_h / 65536.0  # int16范围 -32768..32767

        # 每个桶对应的毫秒
        if self._sample_rate > 0:
            ms_per_bucket = 256 * 1000.0 / self._sample_rate
        else:
            ms_per_bucket = 16.0  # fallback

        num_peaks = len(self._peak_min_arr)

        for px in range(w):
            ms = self.x_to_ms(float(px))
            bucket_idx = int(ms / ms_per_bucket) if ms_per_bucket > 0 else 0

            if bucket_idx < 0 or bucket_idx >= num_peaks:
                continue

            mn = self._peak_min_arr[bucket_idx]
            mx = self._peak_max_arr[bucket_idx]

            y_min = int(mid_y - mx * scale)
            y_max = int(mid_y - mn * scale)

            if y_min == y_max:
                y_max = y_min + 1

            painter.drawLine(px, y_min, px, y_max)

        painter.end()
        self._waveform_pixmap = pm
        self._pixmap_dirty = False

    def _draw_ruler(self, painter: QPainter) -> None:
        """绘制时间标尺。"""
        w = self.width()
        painter.setPen(QPen(COLOR_RULER_TICK, 1))
        painter.drawLine(0, RULER_HEIGHT - 1, w, RULER_HEIGHT - 1)

        if self._duration_ms <= 0:
            return

        # 自动计算刻度间隔
        visible_ms = self.visible_duration_ms
        if visible_ms <= 0:
            return

        # 目标每100-200像素一个标签
        target_px = 150
        target_ms = target_px / self._pixels_per_ms if self._pixels_per_ms > 0 else 1000

        # 吸附到整数间隔
        intervals = [100, 200, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000, 600000]
        tick_ms = intervals[0]
        for iv in intervals:
            if iv >= target_ms:
                tick_ms = iv
                break
        else:
            tick_ms = intervals[-1]

        font = QFont("monospace", 9)
        painter.setFont(font)

        start_ms = int(self._scroll_offset_ms // tick_ms) * tick_ms
        ms = start_ms
        while ms <= self._scroll_offset_ms + visible_ms + tick_ms:
            x = self.ms_to_x(ms)
            if 0 <= x <= w:
                painter.setPen(QPen(COLOR_RULER_TICK, 1))
                painter.drawLine(int(x), RULER_HEIGHT - 8, int(x), RULER_HEIGHT - 1)

                painter.setPen(QPen(COLOR_RULER_TEXT))
                label = self._format_time_label(ms)
                painter.drawText(int(x) + 3, RULER_HEIGHT - 10, label)
            ms += tick_ms

    @staticmethod
    def _format_time_label(ms: int) -> str:
        """格式化时间标签。"""
        total_seconds = ms // 1000
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def _draw_subtitle_blocks(self, painter: QPainter) -> None:
        """绘制字幕块。"""
        if not self._subtitle_data:
            return

        h = self.height()
        block_top = RULER_HEIGHT + 2
        block_h = h - RULER_HEIGHT - 4

        font = QFont("sans-serif", 8)
        painter.setFont(font)

        for i, (key, seg) in enumerate(self._subtitle_data.items()):
            # 拖拽中使用视觉状态，不读取实际数据
            if self._drag_mode and i == self._drag_index:
                start_ms = self._drag_visual_start_ms
                end_ms = self._drag_visual_end_ms
            else:
                start_ms = seg.get("start_time", 0)
                end_ms = seg.get("end_time", 0)

            x1 = self.ms_to_x(start_ms)
            x2 = self.ms_to_x(end_ms)

            # 跳过不可见的块
            if x2 < 0 or x1 > self.width():
                continue

            is_selected = (i == self._selected_index)

            if is_selected:
                fill = COLOR_BLOCK_SELECTED_FILL
                border = COLOR_BLOCK_SELECTED_BORDER
            else:
                fill = COLOR_BLOCK_FILL
                border = COLOR_BLOCK_BORDER

            rect = QRectF(x1, block_top, x2 - x1, block_h)
            painter.fillRect(rect, fill)
            painter.setPen(QPen(border, 1))
            painter.drawRect(rect)

            # 文本标签
            text = seg.get("original_subtitle", "")
            if text and (x2 - x1) > 20:
                painter.setPen(QPen(QColor(255, 255, 255, 180)))
                clip_rect = QRectF(x1 + 2, block_top + 2, x2 - x1 - 4, block_h - 4)
                painter.drawText(clip_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text)

    def _draw_placeholder(self, painter: QPainter) -> None:
        """绘制占位文本。"""
        painter.setPen(QPen(COLOR_PLACEHOLDER))
        font = QFont("sans-serif", 12)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, "Load a video to see waveform")

    # ── 绘制事件 ──

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)

        if self._duration_ms <= 0 and not self._peaks:
            # 无数据，显示占位
            painter.fillRect(self.rect(), COLOR_BG)
            self._draw_placeholder(painter)
            painter.end()
            return

        # Layer 1: 波形像素缓存
        if self._pixmap_dirty or self._waveform_pixmap is None:
            self._build_waveform_pixmap()

        if self._waveform_pixmap:
            painter.drawPixmap(0, 0, self._waveform_pixmap)

        # Layer 2: 时间标尺
        self._draw_ruler(painter)

        # Layer 3: 字幕块
        self._draw_subtitle_blocks(painter)

        # Layer 4: 播放游标
        x = self.ms_to_x(self._playback_position_ms)
        if 0 <= x <= self.width():
            painter.setPen(QPen(COLOR_CURSOR, 2))
            painter.drawLine(int(x), 0, int(x), self.height())

        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._pixmap_dirty = True

    # ── 命中测试 ──

    def _hit_test(self, x: float, y: float) -> Tuple[str, int]:
        """命中测试：返回 (类型, 索引)。"""
        if not self._subtitle_data:
            return ("empty", -1)

        for i, (key, seg) in enumerate(self._subtitle_data.items()):
            start_ms = seg.get("start_time", 0)
            end_ms = seg.get("end_time", 0)

            x1 = self.ms_to_x(start_ms)
            x2 = self.ms_to_x(end_ms)

            block_top = RULER_HEIGHT + 2
            block_h = self.height() - RULER_HEIGHT - 4

            if y < block_top or y > block_top + block_h:
                continue

            if abs(x - x1) <= EDGE_HIT_ZONE_PX and x1 - EDGE_HIT_ZONE_PX <= x <= x2:
                return ("left_edge", i)
            if abs(x - x2) <= EDGE_HIT_ZONE_PX and x1 <= x <= x2 + EDGE_HIT_ZONE_PX:
                return ("right_edge", i)
            if x1 < x < x2:
                return ("block", i)

        return ("empty", -1)

    def _get_subtitle_segment(self, index: int) -> Optional[dict]:
        """获取指定索引的字幕段。"""
        if not self._subtitle_data:
            return None
        values = list(self._subtitle_data.values())
        if 0 <= index < len(values):
            return values[index]
        return None

    @staticmethod
    def _snap_to_grid(ms: int) -> int:
        """吸附到10ms网格。"""
        return round(ms / SNAP_MS) * SNAP_MS

    # ── 鼠标事件 ──

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        x = event.x()
        y = event.y()
        hit_type, hit_idx = self._hit_test(x, y)

        if hit_type == "empty":
            ms = self.x_to_ms(float(x))
            ms = max(0, min(ms, self._duration_ms))
            self.seek_requested.emit(ms)
        elif hit_type == "block":
            self._selected_index = hit_idx
            self.subtitle_selected.emit(hit_idx)
            # 开始拖拽body
            seg = self._get_subtitle_segment(hit_idx)
            if seg:
                self._drag_mode = "block_body"
                self._drag_index = hit_idx
                self._drag_start_x = x
                self._drag_orig_start_ms = seg["start_time"]
                self._drag_orig_end_ms = seg["end_time"]
                self._drag_visual_start_ms = seg["start_time"]
                self._drag_visual_end_ms = seg["end_time"]
            self.update()
        elif hit_type in ("left_edge", "right_edge"):
            self._selected_index = hit_idx
            self.subtitle_selected.emit(hit_idx)
            seg = self._get_subtitle_segment(hit_idx)
            if seg:
                self._drag_mode = hit_type
                self._drag_index = hit_idx
                self._drag_start_x = x
                self._drag_orig_start_ms = seg["start_time"]
                self._drag_orig_end_ms = seg["end_time"]
                self._drag_visual_start_ms = seg["start_time"]
                self._drag_visual_end_ms = seg["end_time"]
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.x()
        y = event.y()

        if self._drag_mode and self._drag_index >= 0:
            dx_ms = self.x_to_ms(float(x)) - self.x_to_ms(self._drag_start_x)

            if self._drag_mode == "left_edge":
                new_start = self._snap_to_grid(self._drag_orig_start_ms + dx_ms)
                new_start = max(0, min(new_start, self._drag_orig_end_ms - SNAP_MS))
                self._drag_visual_start_ms = new_start
                self._drag_visual_end_ms = self._drag_orig_end_ms
            elif self._drag_mode == "right_edge":
                new_end = self._snap_to_grid(self._drag_orig_end_ms + dx_ms)
                new_end = max(self._drag_orig_start_ms + SNAP_MS, min(new_end, self._duration_ms))
                self._drag_visual_start_ms = self._drag_orig_start_ms
                self._drag_visual_end_ms = new_end
            elif self._drag_mode == "block_body":
                duration = self._drag_orig_end_ms - self._drag_orig_start_ms
                new_start = self._snap_to_grid(self._drag_orig_start_ms + dx_ms)
                new_start = max(0, min(new_start, self._duration_ms - duration))
                self._drag_visual_start_ms = new_start
                self._drag_visual_end_ms = new_start + duration

            self.update()
            return

        # 更新光标形状
        hit_type, _ = self._hit_test(x, y)
        if hit_type in ("left_edge", "right_edge"):
            self.setCursor(QCursor(Qt.SizeHorCursor))
        elif hit_type == "block":
            self.setCursor(QCursor(Qt.OpenHandCursor))
        else:
            self.setCursor(QCursor(Qt.ArrowCursor))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._drag_mode and self._drag_index >= 0:
            # 释放时才更新实际数据
            self.subtitle_time_changed.emit(
                self._drag_index, self._drag_visual_start_ms, self._drag_visual_end_ms
            )
            self._drag_mode = ""
            self._drag_index = -1
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        hit_type, hit_idx = self._hit_test(event.x(), event.y())
        if hit_type in ("block", "left_edge", "right_edge"):
            self.subtitle_edit_requested.emit(hit_idx)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return

        modifiers = event.modifiers()

        if modifiers & Qt.ControlModifier:
            # Ctrl+滚轮：缩放
            mouse_ms = self.x_to_ms(float(event.x()))
            if delta > 0:
                new_ppm = self._pixels_per_ms * ZOOM_FACTOR
            else:
                new_ppm = self._pixels_per_ms / ZOOM_FACTOR

            new_ppm = max(MIN_PIXELS_PER_MS, min(new_ppm, MAX_PIXELS_PER_MS))

            # 保持鼠标位置对应的时间不变
            self._scroll_offset_ms = mouse_ms - event.x() / new_ppm
            self._pixels_per_ms = new_ppm
            self._scroll_offset_ms = max(0.0, self._scroll_offset_ms)

            # 防抖重建pixmap
            self._zoom_timer.start(ZOOM_DEBOUNCE_MS)
            self._pixmap_dirty = True
            self.update()
        else:
            # 普通滚轮或Shift+滚轮：水平平移
            pan_ms = 50.0 / self._pixels_per_ms if self._pixels_per_ms > 0 else 100
            if delta > 0:
                self.scroll_offset_ms = self._scroll_offset_ms - pan_ms
            else:
                self.scroll_offset_ms = self._scroll_offset_ms + pan_ms

        event.accept()

    def contextMenuEvent(self, event) -> None:
        x = event.x()
        y = event.y()
        hit_type, hit_idx = self._hit_test(x, y)

        menu = QMenu(self)

        if hit_type == "empty":
            ms = self.x_to_ms(float(x))
            ms = max(0, min(ms, self._duration_ms))
            add_action = QAction("Add subtitle here", self)
            add_action.triggered.connect(lambda: self.subtitle_add_requested.emit(ms))
            menu.addAction(add_action)
        elif hit_type in ("block", "left_edge", "right_edge"):
            delete_action = QAction("Delete", self)
            delete_action.triggered.connect(lambda: self.subtitle_delete_requested.emit(hit_idx))
            menu.addAction(delete_action)

            split_action = QAction("Split", self)
            split_action.triggered.connect(lambda: self.subtitle_split_requested.emit(hit_idx))
            menu.addAction(split_action)

            copy_action = QAction("Copy", self)
            copy_action.triggered.connect(lambda: self.subtitle_copy_requested.emit(hit_idx))
            menu.addAction(copy_action)

        menu.exec_(event.globalPos())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key_Delete and self._selected_index >= 0:
            self.subtitle_delete_requested.emit(self._selected_index)
            event.accept()
        elif modifiers == Qt.ControlModifier and key == Qt.Key_C and self._selected_index >= 0:
            self.subtitle_copy_requested.emit(self._selected_index)
            event.accept()
        elif modifiers == Qt.ControlModifier and key == Qt.Key_V:
            ms = self._playback_position_ms
            self.subtitle_paste_requested.emit(ms)
            event.accept()
        else:
            super().keyPressEvent(event)
