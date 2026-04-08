"""提示词注入模块

将学习到的纠正和风格规则转换为 LLM 提示词上下文。
"""

from typing import Optional

from ..utils.logger import setup_logger
from .correction_dict import CorrectionDictionary
from .style_rules import StyleRuleEngine

logger = setup_logger("prompt_injector")


class PromptInjector:
    """提示词注入器

    将纠正词典和风格规则格式化为 LLM 可理解的上下文片段。
    """

    def build_context(
        self,
        correction_dict: CorrectionDictionary,
        style_rules: StyleRuleEngine,
        limit: int = 50,
        source: Optional[str] = None,
    ) -> str:
        """构建 LLM 提示词上下文

        Args:
            correction_dict: 纠正词典实例
            style_rules: 风格规则引擎实例
            limit: 最大纠正条目数
            source: 可选的来源过滤

        Returns:
            格式化的提示词上下文字符串，无数据时返回空字符串
        """
        parts: list[str] = []

        # 纠正条目部分
        entries = correction_dict.get_relevant_entries(limit=limit, source=source)
        if entries:
            lines = ["<learned_corrections>", "Common corrections for this content:"]
            for entry in entries:
                wrong = entry["wrong"]
                correct = entry["correct"]
                lines.append(f'- "{wrong}" should be "{correct}"')
            lines.append("</learned_corrections>")
            parts.append("\n".join(lines))

        # 风格指令部分
        directives = style_rules.get_prompt_directives()
        if directives:
            lines = ["<style_directives>"]
            for d in directives:
                lines.append(f"- {d}")
            lines.append("</style_directives>")
            parts.append("\n".join(lines))

        if not parts:
            return ""

        return "\n\n".join(parts)
