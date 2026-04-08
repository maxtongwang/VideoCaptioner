"""风格规则引擎模块

管理自动学习的风格规则，支持正则后处理和 LLM 提示词注入。
"""

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Optional

from ...config import LEARNING_PATH
from ..utils.logger import setup_logger

logger = setup_logger("style_rules")

# 需要检测的常见模式阈值
_PATTERN_THRESHOLD = 3


class StyleRuleEngine:
    """风格规则引擎

    存储和管理后处理正则规则与 LLM 提示指令。
    支持从纠正模式中自动学习新规则。
    """

    def __init__(self, path: Optional[Path] = None):
        """初始化风格规则引擎

        Args:
            path: 规则文件路径，默认使用 LEARNING_PATH / "style_rules.json"
        """
        self._path = path or (LEARNING_PATH / "style_rules.json")
        self._lock = threading.Lock()
        self._rules: list[dict] = []
        self._prompt_directives: list[str] = []
        self._load()

    def _load(self) -> None:
        """从磁盘加载规则"""
        if not self._path.exists():
            self._rules = []
            self._prompt_directives = []
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._rules = raw.get("rules", [])
            self._prompt_directives = raw.get("prompt_directives", [])
        except Exception:
            logger.exception("加载风格规则失败，回退到空规则")
            self._rules = []
            self._prompt_directives = []

    def _save(self) -> None:
        """保存规则到磁盘"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rules": self._rules,
            "prompt_directives": self._prompt_directives,
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_rule(self, rule_dict: dict) -> str:
        """添加风格规则

        Args:
            rule_dict: 规则定义字典，包含 description, type, pattern, replacement 等

        Returns:
            规则ID
        """
        rule_id = rule_dict.get("id") or str(uuid.uuid4())[:8]
        rule = {
            "id": rule_id,
            "description": rule_dict.get("description", ""),
            "type": rule_dict.get("type", "post_process"),
            "pattern": rule_dict.get("pattern", ""),
            "replacement": rule_dict.get("replacement", ""),
            "confidence": rule_dict.get("confidence", 0.5),
            "occurrences": rule_dict.get("occurrences", 1),
            "auto_learned": rule_dict.get("auto_learned", True),
        }

        # 如果是 prompt_directive 类型，同时添加到指令列表
        if rule["type"] == "prompt_directive" and rule["description"]:
            if rule["description"] not in self._prompt_directives:
                self._prompt_directives.append(rule["description"])

        self._rules.append(rule)
        self._save()
        return rule_id

    def remove_rule(self, rule_id: str) -> None:
        """移除风格规则

        Args:
            rule_id: 规则ID
        """
        self._rules = [r for r in self._rules if r.get("id") != rule_id]
        self._save()

    def get_prompt_directives(self) -> list[str]:
        """获取 LLM 提示指令列表

        Returns:
            指令字符串列表
        """
        return list(self._prompt_directives)

    def detect_patterns(
        self, corrections: list[tuple[str, str]]
    ) -> None:
        """分析纠正批次，检测重复模式并自动学习规则

        Args:
            corrections: (错误文本, 正确文本) 元组列表
        """
        if not corrections:
            return

        # 检测尾部标点删除模式
        trailing_punct_count = 0
        for wrong, correct in corrections:
            if wrong and correct and len(wrong) > len(correct):
                suffix = wrong[len(correct):]
                if wrong.startswith(correct) and all(
                    not c.isalnum() for c in suffix
                ):
                    trailing_punct_count += 1

        if trailing_punct_count >= _PATTERN_THRESHOLD:
            # 检查是否已有类似规则
            has_rule = any(
                r.get("description", "").startswith("自动学习: 移除尾部标点")
                for r in self._rules
            )
            if not has_rule:
                self.add_rule({
                    "description": "自动学习: 移除尾部标点",
                    "type": "prompt_directive",
                    "pattern": r"[。！？.!?]+$",
                    "replacement": "",
                    "confidence": 0.7,
                    "occurrences": trailing_punct_count,
                    "auto_learned": True,
                })
                logger.info(
                    f"自动学习规则: 移除尾部标点 (出现 {trailing_punct_count} 次)"
                )

        # 检测重复空格压缩模式
        space_collapse_count = sum(
            1 for w, c in corrections if "  " in w and "  " not in c
        )
        if space_collapse_count >= _PATTERN_THRESHOLD:
            has_rule = any(
                r.get("description", "").startswith("自动学习: 压缩多余空格")
                for r in self._rules
            )
            if not has_rule:
                self.add_rule({
                    "description": "自动学习: 压缩多余空格",
                    "type": "post_process",
                    "pattern": r"  +",
                    "replacement": " ",
                    "confidence": 0.8,
                    "occurrences": space_collapse_count,
                    "auto_learned": True,
                })
                logger.info(
                    f"自动学习规则: 压缩多余空格 (出现 {space_collapse_count} 次)"
                )

    def apply_rules(self, text: str) -> str:
        """应用后处理正则规则到文本

        Args:
            text: 待处理的文本

        Returns:
            处理后的文本
        """
        for rule in self._rules:
            if rule.get("type") != "post_process":
                continue
            pattern = rule.get("pattern", "")
            replacement = rule.get("replacement", "")
            if not pattern:
                continue
            try:
                text = re.sub(pattern, replacement, text)
            except re.error:
                logger.warning(f"无效的正则表达式: {pattern}")
        return text
