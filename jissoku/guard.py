"""LLMに渡す前の安全確認（プロンプトインジェクションと個人情報）。"""
from __future__ import annotations

import re

NAME_OK = re.compile(r"^[\w\u3040-\u30ff\u3400-\u9fffー・･()（）\-－／/ 　]{1,40}$")
INJECTION_MARKERS = ("無視して", "以下の指示", "上記の指示", "ignore previous", "ignore all", "system:",
                     "assistant:", "```", "<|", "|>", "プロンプト")
PII_PATTERNS = {
    "メールアドレス": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "電話番号": re.compile(r"(?<!\d)0\d{1,4}-\d{1,4}-\d{3,4}(?!\d)"),
    "12桁の番号（個人番号の可能性）": re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)"),
}


def check_name(value: str) -> str | None:
    """業務名・区分名・条件名として受け付けられるかを確かめ、問題があれば理由を返す。"""
    low = value.lower()
    for mk in INJECTION_MARKERS:
        if mk.lower() in low:
            return f"指示文とみられる語を含む（{mk}）"
    if not NAME_OK.match(value):
        return "使えない文字を含むか、40文字を超えている"
    return None


def find_pii(text: str) -> list:
    return [name for name, pat in PII_PATTERNS.items() if pat.search(text)]
