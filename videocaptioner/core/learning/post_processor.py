"""后处理模块

应用学习到的纠正和风格规则对文本进行后处理。
支持 CJK 精确匹配和拉丁文单词边界匹配。
"""

import re

from ..utils.logger import setup_logger
from .correction_dict import CorrectionDictionary
from .style_rules import StyleRuleEngine

logger = setup_logger("post_processor")

# CJK Unicode 范围
_CJK_PATTERN = re.compile(
    r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]"
)


def _contains_cjk(text: str) -> bool:
    """判断文本是否包含 CJK 字符"""
    return bool(_CJK_PATTERN.search(text))


def _case_preserving_replace(text: str, old: str, new: str) -> str:
    """大小写不敏感匹配，但保留原文大小写风格的替换

    Args:
        text: 待处理的文本
        old: 要匹配的文本
        new: 替换文本

    Returns:
        替换后的文本
    """

    def _match_case(match: re.Match) -> str:
        matched = match.group()
        if matched.isupper():
            return new.upper()
        if matched.islower():
            return new.lower()
        if matched[0].isupper():
            return new[0].upper() + new[1:] if len(new) > 1 else new.upper()
        return new

    pattern = re.compile(r"\b" + re.escape(old) + r"\b", re.IGNORECASE)
    return pattern.sub(_match_case, text)


class PostProcessor:
    """后处理器

    先应用词典纠正，再应用风格规则。
    对 CJK 文本使用精确子串匹配，对拉丁文本使用单词边界匹配。
    """

    def apply(
        self,
        text: str,
        correction_dict: CorrectionDictionary,
        style_rules: StyleRuleEngine,
    ) -> str:
        """应用后处理

        Args:
            text: 待处理的文本
            correction_dict: 纠正词典实例
            style_rules: 风格规则引擎实例

        Returns:
            处理后的文本
        """
        if not text:
            return text

        # 第一步：应用词典纠正
        text = self._apply_corrections(text, correction_dict)

        # 第二步：应用风格规则
        text = style_rules.apply_rules(text)

        return text

    def _apply_corrections(
        self, text: str, correction_dict: CorrectionDictionary
    ) -> str:
        """应用词典纠正

        Args:
            text: 待处理的文本
            correction_dict: 纠正词典实例

        Returns:
            纠正后的文本
        """
        entries = correction_dict.get_relevant_entries(limit=500)
        if not entries:
            return text

        # 按长度降序排列，优先匹配更长的文本
        entries.sort(key=lambda e: len(e["wrong"]), reverse=True)

        for entry in entries:
            wrong = entry["wrong"]
            correct = entry["correct"]
            if not wrong:
                continue

            if _contains_cjk(wrong):
                # CJK 文本：精确子串匹配
                text = text.replace(wrong, correct)
            else:
                # 拉丁文本：单词边界匹配，大小写不敏感
                try:
                    pattern = re.compile(r"\b" + re.escape(wrong) + r"\b", re.IGNORECASE)
                    if pattern.search(text):
                        text = _case_preserving_replace(text, wrong, correct)
                except re.error:
                    # 回退到简单替换
                    text = text.replace(wrong, correct)

        return text
