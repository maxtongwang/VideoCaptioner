"""纠正词典模块

管理学习到的纠正条目，支持持久化存储、频率统计和自动纠正。
"""

import json
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ...config import LEARNING_PATH
from ..utils.logger import setup_logger

logger = setup_logger("correction_dict")

SCHEMA_VERSION = 1
MAX_ENTRIES = 5000
PRUNE_DAYS = 90


class CorrectionDictionary:
    """纠正词典

    存储和管理从编辑中学习到的纠正映射关系。
    支持线程安全的读写、频率统计、自动修剪。
    """

    def __init__(self, path: Optional[Path] = None):
        """初始化纠正词典

        Args:
            path: 词典文件路径，默认使用 LEARNING_PATH / "corrections.json"
        """
        self._path = path or (LEARNING_PATH / "corrections.json")
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        """从磁盘加载词典，损坏时回退到空词典"""
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != SCHEMA_VERSION:
                logger.warning("词典 schema 版本不匹配，重置为空词典")
                self._data = {}
                return
            self._data = raw.get("entries", {})
        except Exception:
            logger.exception("加载纠正词典失败，回退到空词典")
            self._data = {}

    def _save(self) -> None:
        """保存词典到磁盘，写入前创建 .bak 备份"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            bak = self._path.with_suffix(".json.bak")
            shutil.copy2(self._path, bak)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "entries": self._data,
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _prune(self) -> None:
        """修剪条目：移除超过90天未使用的条目，超过上限时按频率淘汰"""
        cutoff = (datetime.now() - timedelta(days=PRUNE_DAYS)).isoformat()
        # 移除过期条目
        expired = [k for k, v in self._data.items() if v.get("last_seen", "") < cutoff]
        for k in expired:
            del self._data[k]
        # 超过上限时按频率淘汰
        if len(self._data) > MAX_ENTRIES:
            sorted_keys = sorted(
                self._data, key=lambda k: self._data[k].get("frequency", 0)
            )
            to_remove = sorted_keys[: len(self._data) - MAX_ENTRIES]
            for k in to_remove:
                del self._data[k]

    def add_entry(
        self,
        wrong: str,
        correct: str,
        source_video: Optional[str] = None,
        speaker_id: Optional[str] = None,
        source: str = "llm_optimize",
    ) -> None:
        """添加或更新纠正条目

        Args:
            wrong: 错误文本
            correct: 正确文本
            source_video: 来源视频路径
            speaker_id: 说话人ID
            source: 错误来源阶段（asr / llm_optimize / llm_split / human）
        """
        if not wrong or not correct or wrong == correct:
            return
        now = datetime.now().isoformat()
        with self._lock:
            if wrong in self._data:
                entry = self._data[wrong]
                entry["frequency"] += 1
                entry["last_seen"] = now
                entry["correct"] = correct
                if source_video and source_video not in entry["source_videos"]:
                    entry["source_videos"].append(source_video)
            else:
                self._data[wrong] = {
                    "correct": correct,
                    "frequency": 1,
                    "first_seen": now,
                    "last_seen": now,
                    "source_videos": [source_video] if source_video else [],
                    "speaker_id": speaker_id,
                    "category": "",
                    "source": source,
                    "auto_learned": True,
                }
            self._prune()
            self._save()

    def remove_entry(self, wrong: str) -> None:
        """移除纠正条目

        Args:
            wrong: 要移除的错误文本
        """
        with self._lock:
            if wrong in self._data:
                del self._data[wrong]
                self._save()

    def lookup(self, text: str) -> Optional[str]:
        """精确查找纠正结果

        Args:
            text: 待查找的文本

        Returns:
            纠正后的文本，未找到返回 None
        """
        with self._lock:
            entry = self._data.get(text)
            return entry["correct"] if entry else None

    def get_relevant_entries(
        self, limit: int = 50, source: Optional[str] = None
    ) -> list[dict]:
        """获取相关纠正条目，按频率降序排列

        Args:
            limit: 最大返回数量
            source: 可选的来源过滤

        Returns:
            纠正条目列表
        """
        with self._lock:
            items = list(self._data.items())

        if source:
            items = [(k, v) for k, v in items if v.get("source") == source]
        items.sort(key=lambda x: x[1].get("frequency", 0), reverse=True)
        result = []
        for wrong, entry in items[:limit]:
            result.append({"wrong": wrong, **entry})
        return result

    def apply_corrections(self, text: str) -> str:
        """应用所有纠正替换到文本

        Args:
            text: 待纠正的文本

        Returns:
            纠正后的文本
        """
        with self._lock:
            entries = dict(self._data)
        # 按长度降序排列，优先匹配更长的文本
        for wrong in sorted(entries, key=len, reverse=True):
            correct = entries[wrong]["correct"]
            if wrong in text:
                text = text.replace(wrong, correct)
        return text
