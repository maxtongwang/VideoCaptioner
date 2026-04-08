"""差异跟踪模块

通过对比编辑前后的文本，提取纠正映射关系。
支持 CJK 和拉丁文本的差异提取。
"""

import difflib
import re
import unicodedata

from ..utils.logger import setup_logger

logger = setup_logger("diff_tracker")

# CJK Unicode 范围
_CJK_PATTERN = re.compile(
    r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]"
)


def _is_cjk_char(ch: str) -> bool:
    """判断字符是否为 CJK 字符"""
    return bool(_CJK_PATTERN.match(ch))


def _is_punctuation_only(text: str) -> bool:
    """判断文本是否仅包含标点符号"""
    return all(unicodedata.category(ch).startswith("P") or ch.isspace() for ch in text)


def _is_whitespace_only_change(old: str, new: str) -> bool:
    """判断是否为仅空白字符的变更"""
    return old.strip() == new.strip()


def _expand_to_word_boundary(text: str, start: int, end: int) -> tuple[int, int]:
    """将字符范围扩展到单词边界（拉丁文本）

    Args:
        text: 完整文本
        start: 起始字符位置
        end: 结束字符位置

    Returns:
        扩展后的 (start, end) 元组
    """
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return start, end


def _expand_cjk_group(text: str, start: int, end: int) -> tuple[int, int]:
    """将字符范围扩展到连续 CJK 字符组

    Args:
        text: 完整文本
        start: 起始字符位置
        end: 结束字符位置

    Returns:
        扩展后的 (start, end) 元组
    """
    while start > 0 and _is_cjk_char(text[start - 1]):
        start -= 1
    while end < len(text) and _is_cjk_char(text[end]):
        end += 1
    return start, end


class DiffTracker:
    """差异跟踪器

    对比原始文本和编辑后文本，提取字词级别的纠正映射。
    """

    def compute_corrections(
        self, original: str, edited: str
    ) -> list[tuple[str, str]]:
        """计算原始文本和编辑文本之间的纠正映射

        Args:
            original: 原始文本
            edited: 编辑后的文本

        Returns:
            (错误文本, 正确文本) 元组列表
        """
        if not original or not edited:
            return []
        if original == edited:
            return []

        matcher = difflib.SequenceMatcher(None, original, edited)
        corrections: list[tuple[str, str]] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            old_text = original[i1:i2]
            new_text = edited[j1:j2]

            if tag == "replace":
                correction = self._process_replace(original, edited, i1, i2, j1, j2)
                if correction:
                    corrections.append(correction)
            elif tag == "delete":
                # 删除操作 — 仅跟踪非空白/非标点删除
                if not old_text.strip() or _is_punctuation_only(old_text):
                    continue
                corrections.append((old_text, ""))
            elif tag == "insert":
                # 插入操作 — 仅跟踪非空白/非标点插入
                if not new_text.strip() or _is_punctuation_only(new_text):
                    continue
                corrections.append(("", new_text))

        return self._filter_corrections(corrections)

    def _process_replace(
        self,
        original: str,
        edited: str,
        i1: int,
        i2: int,
        j1: int,
        j2: int,
    ) -> tuple[str, str] | None:
        """处理替换类型的差异

        Args:
            original: 原始全文
            edited: 编辑全文
            i1, i2: 原始文本中的范围
            j1, j2: 编辑文本中的范围

        Returns:
            (错误文本, 正确文本) 或 None
        """
        old_text = original[i1:i2]
        new_text = edited[j1:j2]

        if _is_whitespace_only_change(old_text, new_text):
            return None

        # 判断是否为 CJK 文本
        is_cjk = any(_is_cjk_char(ch) for ch in old_text + new_text)

        if is_cjk:
            ei1, ei2 = _expand_cjk_group(original, i1, i2)
            ej1, ej2 = _expand_cjk_group(edited, j1, j2)
        else:
            ei1, ei2 = _expand_to_word_boundary(original, i1, i2)
            ej1, ej2 = _expand_to_word_boundary(edited, j1, j2)

        expanded_old = original[ei1:ei2].strip()
        expanded_new = edited[ej1:ej2].strip()

        if not expanded_old or not expanded_new:
            return None
        if expanded_old == expanded_new:
            return None

        return (expanded_old, expanded_new)

    def _filter_corrections(
        self, corrections: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """过滤无效的纠正条目

        Args:
            corrections: 原始纠正列表

        Returns:
            过滤后的纠正列表
        """
        filtered: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for wrong, correct in corrections:
            wrong = wrong.strip()
            correct = correct.strip()

            # 过滤空结果
            if not wrong and not correct:
                continue
            # 过滤相同内容
            if wrong == correct:
                continue
            # 过滤仅标点变更
            if wrong and correct and _is_punctuation_only(wrong) and _is_punctuation_only(correct):
                continue
            # 去重
            pair = (wrong, correct)
            if pair in seen:
                continue
            seen.add(pair)
            filtered.append(pair)

        return filtered
