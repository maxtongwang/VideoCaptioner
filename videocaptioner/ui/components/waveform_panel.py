# -*- coding: utf-8 -*-
"""波形面板容器。

包含工具栏（播放/暂停、时间标签、缩放指示器）、WaveformTimelineWidget和水平滚动条。
"""
import os
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)

from videocaptioner.core.utils.video_utils import get_video_info
from videocaptioner.ui.common.signal_bus import signalBus
from videocaptioner.ui.components.waveform_data import WaveformDataProvider
from videocaptioner.ui.components.waveform_widget import WaveformTimelineWidget


class WaveformPanel(QWidget):
    """波形面板容器，组合工具栏+波形组件+滚动条。"""

    # 重新暴露WaveformTimelineWidget的所有信号
    seek_requested = pyqtSignal(int)
    subtitle_selected = pyqtSignal(int)
    subtitle_time_changed = pyqtSignal(int, int, int)
    subtitle_add_requested = pyqtSignal(int)
    subtitle_delete_requested = pyqtSignal(int)
    subtitle_split_requested = pyqtSignal(int)
    subtitle_copy_requested = pyqtSignal(int)
    subtitle_paste_requested = pyqtSignal(int)
    subtitle_edit_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setMinimumHeight(120)

        self._is_playing = False
        self._data_provider: Optional[WaveformDataProvider] = None

        self._init_ui()
        self._setup_connections()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)

        self.play_button = QPushButton("▶")
        self.play_button.setFixedSize(28, 28)
        self.play_button.setToolTip("播放/暂停")
        toolbar.addWidget(self.play_button)

        self.time_label = QLabel("00:00.00 / 00:00.00")
        self.time_label.setStyleSheet("color: #cccccc; font-family: monospace; font-size: 11px;")
        toolbar.addWidget(self.time_label)

        toolbar.addStretch()

        self.zoom_label = QLabel("x1.0")
        self.zoom_label.setStyleSheet("color: #999999; font-family: monospace; font-size: 10px;")
        toolbar.addWidget(self.zoom_label)

        layout.addLayout(toolbar)

        # 波形组件
        self.waveform_widget = WaveformTimelineWidget(self)
        layout.addWidget(self.waveform_widget, 1)

        # 水平滚动条
        self.scrollbar = QScrollBar(Qt.Horizontal, self)
        self.scrollbar.setMinimum(0)
        self.scrollbar.setMaximum(0)
        layout.addWidget(self.scrollbar)

    def _setup_connections(self) -> None:
        # 播放按钮
        self.play_button.clicked.connect(self._on_play_clicked)

        # 滚动条 -> 波形组件
        self.scrollbar.valueChanged.connect(self._on_scrollbar_changed)

        # 重新暴露波形组件信号
        self.waveform_widget.seek_requested.connect(self.seek_requested)
        self.waveform_widget.subtitle_selected.connect(self.subtitle_selected)
        self.waveform_widget.subtitle_time_changed.connect(self.subtitle_time_changed)
        self.waveform_widget.subtitle_add_requested.connect(self.subtitle_add_requested)
        self.waveform_widget.subtitle_delete_requested.connect(self.subtitle_delete_requested)
        self.waveform_widget.subtitle_split_requested.connect(self.subtitle_split_requested)
        self.waveform_widget.subtitle_copy_requested.connect(self.subtitle_copy_requested)
        self.waveform_widget.subtitle_paste_requested.connect(self.subtitle_paste_requested)
        self.waveform_widget.subtitle_edit_requested.connect(self.subtitle_edit_requested)

        # 监听波形组件的更新以同步滚动条
        self.waveform_widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        """监听波形组件的resize和paint事件来同步滚动条。"""
        if obj is self.waveform_widget:
            etype = event.type()
            # Resize or Paint -> 同步滚动条
            if etype in (event.Resize, event.Paint):
                self._sync_scrollbar()
                self._update_zoom_label()
        return super().eventFilter(obj, event)

    # ── 公共方法 ──

    def load_video(self, video_path: str) -> None:
        """加载视频音频并提取峰值数据。"""
        if not video_path or not os.path.isfile(video_path):
            return

        # 获取视频时长
        info = get_video_info(video_path)
        if info and hasattr(info, "duration_seconds") and info.duration_seconds > 0:
            duration_ms = int(info.duration_seconds * 1000)
            self.waveform_widget.set_duration_ms(duration_ms)
            self._update_time_label(0, duration_ms)

        # 启动峰值提取
        if self._data_provider:
            self._data_provider.cancel()

        self._data_provider = WaveformDataProvider(self)
        self._data_provider.peaks_ready.connect(self.waveform_widget.set_peaks)
        self._data_provider.error.connect(self._on_extraction_error)
        self._data_provider.load(video_path)

    def set_subtitle_data(self, data: dict) -> None:
        """设置字幕数据。"""
        self.waveform_widget.set_subtitle_data(data)

    def set_playback_position(self, position_ms: int) -> None:
        """更新播放位置。"""
        self.waveform_widget.set_playback_position(position_ms)
        duration_ms = int(self.waveform_widget.total_duration_ms)
        self._update_time_label(position_ms, duration_ms)

    # ── 内部方法 ──

    def _on_play_clicked(self) -> None:
        if self._is_playing:
            signalBus.video_pause.emit()
            self.play_button.setText("▶")
            self._is_playing = False
        else:
            signalBus.video_play.emit()
            self.play_button.setText("⏸")
            self._is_playing = True

    def _on_scrollbar_changed(self, value: int) -> None:
        self.waveform_widget.scroll_offset_ms = float(value)

    def _sync_scrollbar(self) -> None:
        """同步滚动条范围与波形组件状态。"""
        total = int(self.waveform_widget.total_duration_ms)
        visible = int(self.waveform_widget.visible_duration_ms)
        offset = int(self.waveform_widget.scroll_offset_ms)

        max_val = max(0, total - visible)
        self.scrollbar.blockSignals(True)
        self.scrollbar.setMaximum(max_val)
        self.scrollbar.setPageStep(max(1, visible))
        self.scrollbar.setValue(min(offset, max_val))
        self.scrollbar.blockSignals(False)

    def _update_zoom_label(self) -> None:
        ppm = self.waveform_widget.pixels_per_ms
        # 归一化为倍率显示
        zoom = ppm / 0.1 if ppm > 0 else 1.0
        self.zoom_label.setText(f"x{zoom:.1f}")

    def _update_time_label(self, position_ms: int, duration_ms: int) -> None:
        pos_str = self._format_time(position_ms)
        dur_str = self._format_time(duration_ms)
        self.time_label.setText(f"{pos_str} / {dur_str}")

    @staticmethod
    def _format_time(ms: int) -> str:
        if ms < 0:
            ms = 0
        total_s = ms // 1000
        frac = (ms % 1000) // 10
        m = total_s // 60
        s = total_s % 60
        return f"{m:02d}:{s:02d}.{frac:02d}"

    def _on_extraction_error(self, msg: str) -> None:
        """音频提取错误处理。"""
        self.time_label.setText(f"Error: {msg}")
