"""会话历史模块

保存和管理字幕处理的历史快照，支持回溯对比和人工纠正提取。
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ...config import LEARNING_PATH
from ..utils.logger import setup_logger

logger = setup_logger("session_history")

MAX_SESSIONS = 100
PRUNE_DAYS = 30


class SessionHistory:
    """会话历史管理器

    记录每次字幕处理的快照，支持按视频查询和对比连续快照。
    """

    def __init__(self, sessions_dir: Optional[Path] = None):
        """初始化会话历史管理器

        Args:
            sessions_dir: 会话存储目录，默认使用 LEARNING_PATH / "sessions"
        """
        self._dir = sessions_dir or (LEARNING_PATH / "sessions")
        self._dir.mkdir(parents=True, exist_ok=True)

    def save_snapshot(
        self,
        task_id: str,
        video_path: str,
        stage: str,
        asr_json: dict,
    ) -> Path:
        """保存处理快照

        Args:
            task_id: 任务ID
            video_path: 视频文件路径
            stage: 处理阶段名称
            asr_json: ASR 数据的 JSON 表示

        Returns:
            保存的快照文件路径
        """
        self._auto_prune()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_name = Path(video_path).stem if video_path else "unknown"
        # 清理文件名中的非法字符
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in video_name)
        filename = f"{safe_name}_{stage}_{timestamp}.json"

        snapshot = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "video_path": video_path,
            "stage": stage,
            "snapshot": {"asr_json": asr_json},
        }

        filepath = self._dir / filename
        filepath.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.debug(f"已保存会话快照: {filepath.name}")
        return filepath

    def list_sessions(
        self, video_path: Optional[str] = None
    ) -> list[dict]:
        """列出会话元数据（不加载完整快照）

        Args:
            video_path: 可选的视频路径过滤

        Returns:
            会话元数据列表
        """
        sessions: list[dict] = []
        for f in sorted(self._dir.glob("*.json")):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                meta = {
                    "file": f.name,
                    "task_id": raw.get("task_id"),
                    "timestamp": raw.get("timestamp"),
                    "video_path": raw.get("video_path"),
                    "stage": raw.get("stage"),
                }
                if video_path and meta["video_path"] != video_path:
                    continue
                sessions.append(meta)
            except Exception:
                logger.warning(f"无法读取会话文件: {f.name}")
        return sessions

    def load_session(self, session_file: str) -> dict:
        """加载完整会话快照

        Args:
            session_file: 会话文件名

        Returns:
            完整的会话快照数据
        """
        filepath = self._dir / session_file
        if not filepath.exists():
            logger.warning(f"会话文件不存在: {session_file}")
            return {}
        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except Exception:
            logger.exception(f"加载会话文件失败: {session_file}")
            return {}

    def get_human_corrections(
        self, video_path: str
    ) -> list[tuple[str, str]]:
        """对比同一视频的连续快照，提取人工纠正

        Args:
            video_path: 视频文件路径

        Returns:
            (旧文本, 新文本) 元组列表
        """
        sessions = self.list_sessions(video_path=video_path)
        if len(sessions) < 2:
            return []

        corrections: list[tuple[str, str]] = []
        for i in range(len(sessions) - 1):
            prev = self.load_session(sessions[i]["file"])
            curr = self.load_session(sessions[i + 1]["file"])

            prev_texts = self._extract_texts(prev)
            curr_texts = self._extract_texts(curr)

            # 对比相同索引位置的文本
            for idx in range(min(len(prev_texts), len(curr_texts))):
                old = prev_texts[idx]
                new = curr_texts[idx]
                if old != new and old.strip() and new.strip():
                    corrections.append((old, new))

        return corrections

    def _extract_texts(self, session_data: dict) -> list[str]:
        """从会话数据中提取文本列表

        Args:
            session_data: 完整会话数据

        Returns:
            文本列表
        """
        asr_json = session_data.get("snapshot", {}).get("asr_json", {})
        segments = asr_json.get("segments", [])
        return [seg.get("text", "") for seg in segments]

    def _auto_prune(self) -> None:
        """自动修剪：移除超过30天的会话，保持最多100个"""
        files = sorted(self._dir.glob("*.json"), key=os.path.getmtime)
        cutoff = datetime.now() - timedelta(days=PRUNE_DAYS)

        # 移除过期文件
        for f in files:
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
                    logger.debug(f"已清理过期会话: {f.name}")
            except Exception:
                pass

        # 超过上限时移除最旧的
        files = sorted(self._dir.glob("*.json"), key=os.path.getmtime)
        if len(files) > MAX_SESSIONS:
            for f in files[: len(files) - MAX_SESSIONS]:
                try:
                    f.unlink()
                    logger.debug(f"已清理溢出会话: {f.name}")
                except Exception:
                    pass
