"""学习引擎模块

通过记录编辑差异、维护纠正词典和风格规则，
持续改进字幕处理的准确性。
"""

import threading
from typing import Optional

from ...config import LEARNING_PATH
from ..utils.logger import setup_logger
from .correction_dict import CorrectionDictionary
from .diff_tracker import DiffTracker
from .post_processor import PostProcessor
from .prompt_injector import PromptInjector
from .session_history import SessionHistory
from .style_rules import StyleRuleEngine

logger = setup_logger("learning_engine")

_engine_instance: Optional["LearningEngine"] = None
_engine_lock = threading.Lock()


def get_learning_engine() -> "LearningEngine":
    """获取学习引擎单例

    Returns:
        LearningEngine 单例实例
    """
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = LearningEngine()
    return _engine_instance


class LearningEngine:
    """学习引擎

    组合纠正词典、差异跟踪、会话历史、风格规则、
    提示词注入和后处理模块，提供统一的学习 API。
    """

    def __init__(self) -> None:
        """初始化学习引擎，确保数据目录存在"""
        LEARNING_PATH.mkdir(parents=True, exist_ok=True)

        self.correction_dict = CorrectionDictionary()
        self.diff_tracker = DiffTracker()
        self.session_history = SessionHistory()
        self.style_rules = StyleRuleEngine()
        self.prompt_injector = PromptInjector()
        self.post_processor = PostProcessor()

        logger.debug("学习引擎初始化完成")

    def record_edit(
        self,
        old_text: str,
        new_text: str,
        source_video: Optional[str] = None,
        source: str = "llm_optimize",
    ) -> None:
        """记录一次编辑，提取纠正并存入词典

        Args:
            old_text: 编辑前的文本
            new_text: 编辑后的文本
            source_video: 来源视频路径
            source: 错误来源阶段（asr / llm_optimize / llm_split / human）
        """
        if not old_text or not new_text or old_text == new_text:
            return

        corrections = self.diff_tracker.compute_corrections(old_text, new_text)
        if not corrections:
            return

        for wrong, correct in corrections:
            if wrong and correct:
                self.correction_dict.add_entry(
                    wrong=wrong,
                    correct=correct,
                    source_video=source_video,
                    source=source,
                )

        # 尝试从纠正批次中检测风格模式
        self.style_rules.detect_patterns(corrections)

        logger.debug(f"记录编辑: 提取到 {len(corrections)} 条纠正")

    def get_prompt_context(
        self, limit: int = 50, source: Optional[str] = None
    ) -> str:
        """获取 LLM 提示词上下文

        Args:
            limit: 最大纠正条目数
            source: 可选的来源过滤

        Returns:
            格式化的提示词上下文字符串
        """
        return self.prompt_injector.build_context(
            self.correction_dict,
            self.style_rules,
            limit=limit,
            source=source,
        )

    def post_process(self, text: str) -> str:
        """对文本应用后处理（词典纠正 + 风格规则）

        Args:
            text: 待处理的文本

        Returns:
            处理后的文本
        """
        return self.post_processor.apply(
            text, self.correction_dict, self.style_rules
        )

    def save_session_snapshot(
        self,
        task_id: str,
        video_path: str,
        stage: str,
        asr_json: dict,
    ) -> None:
        """保存会话快照

        Args:
            task_id: 任务ID
            video_path: 视频文件路径
            stage: 处理阶段名称
            asr_json: ASR 数据的 JSON 表示
        """
        self.session_history.save_snapshot(task_id, video_path, stage, asr_json)

    def get_corrections_for_stage(self, source: str) -> dict:
        """获取特定阶段的纠正条目

        Args:
            source: 来源阶段（asr / llm_optimize / llm_split / human）

        Returns:
            以错误文本为键、正确文本为值的字典
        """
        entries = self.correction_dict.get_relevant_entries(source=source)
        return {e["wrong"]: e["correct"] for e in entries}
