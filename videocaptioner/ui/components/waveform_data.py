# -*- coding: utf-8 -*-
"""音频波形数据提取与缓存。

从视频中提取16kHz单声道WAV，计算min/max峰值对，缓存至磁盘。
"""
import array
import hashlib
import os
import struct
import wave
from pathlib import Path
from typing import List, Tuple

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from videocaptioner.config import CACHE_PATH
from videocaptioner.core.utils.video_utils import video2audio

# 每个峰值桶的采样数
SAMPLES_PER_BUCKET = 256
# WAV读取块大小（字节）
CHUNK_BYTES = 65536


class _PeakWorker(QThread):
    """后台线程：提取WAV并计算峰值数据。"""

    peaks_ready = pyqtSignal(object, int, int)  # peaks, sample_rate, total_frames
    progress = pyqtSignal(int)  # 0-100
    error = pyqtSignal(str)  # 错误消息

    def __init__(self, video_path: str, parent: QObject = None):
        super().__init__(parent)
        self._video_path = video_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            self._process()
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))

    def _process(self) -> None:
        cache_key = hashlib.md5(self._video_path.encode()).hexdigest()
        waveform_dir = Path(CACHE_PATH) / "waveforms"
        waveform_dir.mkdir(parents=True, exist_ok=True)

        peaks_path = waveform_dir / f"{cache_key}.peaks"
        wav_path = waveform_dir / f"{cache_key}.wav"

        # 检查缓存
        if peaks_path.exists():
            peaks, sample_rate, total_frames = self._load_peaks(peaks_path)
            if not self._cancelled:
                self.peaks_ready.emit(peaks, sample_rate, total_frames)
            return

        # 提取WAV
        if not self._cancelled:
            self.progress.emit(0)
            success = video2audio(self._video_path, output=str(wav_path))
            if not success:
                if not self._cancelled:
                    self.error.emit("音频提取失败")
                return

        if self._cancelled:
            return

        # 读取WAV计算峰值
        peaks, sample_rate, total_frames = self._compute_peaks(str(wav_path))
        if self._cancelled:
            return

        # 保存缓存
        self._save_peaks(peaks_path, peaks, sample_rate, total_frames)

        if not self._cancelled:
            self.peaks_ready.emit(peaks, sample_rate, total_frames)

    def _compute_peaks(self, wav_path: str) -> Tuple[List[Tuple[int, int]], int, int]:
        """从WAV文件计算min/max峰值对。"""
        with wave.open(wav_path, "rb") as wf:
            sample_rate = wf.getframerate()
            total_frames = wf.getnframes()
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()

            peaks: List[Tuple[int, int]] = []
            bucket_min = 32767
            bucket_max = -32768
            bucket_count = 0
            frames_read = 0

            while frames_read < total_frames:
                if self._cancelled:
                    return peaks, sample_rate, total_frames

                # 按字节块读取
                chunk_samples = CHUNK_BYTES // (n_channels * sampwidth)
                remaining = total_frames - frames_read
                to_read = min(chunk_samples, remaining)

                raw_bytes = wf.readframes(to_read)
                if not raw_bytes:
                    break

                actual_samples = len(raw_bytes) // (n_channels * sampwidth)
                frames_read += actual_samples

                # 解包为int16采样
                samples = array.array("h", raw_bytes)

                # 如果是多声道，只取第一声道
                for i in range(0, len(samples), n_channels):
                    val = samples[i]
                    if val < bucket_min:
                        bucket_min = val
                    if val > bucket_max:
                        bucket_max = val
                    bucket_count += 1

                    if bucket_count >= SAMPLES_PER_BUCKET:
                        peaks.append((bucket_min, bucket_max))
                        bucket_min = 32767
                        bucket_max = -32768
                        bucket_count = 0

                # 更新进度
                pct = int(frames_read * 100 / total_frames) if total_frames > 0 else 100
                self.progress.emit(min(pct, 100))

            # 处理最后不满一个桶的采样
            if bucket_count > 0:
                peaks.append((bucket_min, bucket_max))

        return peaks, sample_rate, total_frames

    @staticmethod
    def _save_peaks(
        path: Path,
        peaks: List[Tuple[int, int]],
        sample_rate: int,
        total_frames: int,
    ) -> None:
        """保存峰值数据到二进制文件。

        格式: [sample_rate: 4B int][total_frames: 4B int][pairs of (min, max): 2B+2B each]
        """
        with open(path, "wb") as f:
            f.write(struct.pack("<II", sample_rate, total_frames))
            for mn, mx in peaks:
                f.write(struct.pack("<hh", mn, mx))

    @staticmethod
    def _load_peaks(path: Path) -> Tuple[List[Tuple[int, int]], int, int]:
        """从缓存文件加载峰值数据。"""
        with open(path, "rb") as f:
            data = f.read()

        sample_rate, total_frames = struct.unpack_from("<II", data, 0)
        offset = 8
        peaks: List[Tuple[int, int]] = []
        while offset + 4 <= len(data):
            mn, mx = struct.unpack_from("<hh", data, offset)
            peaks.append((mn, mx))
            offset += 4

        return peaks, sample_rate, total_frames


class WaveformDataProvider(QObject):
    """提供视频音频的峰值数据。"""

    peaks_ready = pyqtSignal(object, int, int)  # peaks, sample_rate, total_frames
    progress = pyqtSignal(int)  # 0-100
    error = pyqtSignal(str)  # 错误消息

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._worker: _PeakWorker = None

    def load(self, video_path: str) -> None:
        """开始后台加载峰值数据。"""
        self.cancel()
        self._worker = _PeakWorker(video_path, self)
        self._worker.peaks_ready.connect(self.peaks_ready)
        self._worker.progress.connect(self.progress)
        self._worker.error.connect(self.error)
        self._worker.start()

    def cancel(self) -> None:
        """取消正在运行的工作线程。"""
        if self._worker is not None:
            self._worker.cancel()
            self._worker.quit()
            self._worker.wait(3000)
            self._worker = None
