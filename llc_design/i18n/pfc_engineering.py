"""Translations introduced by the PFC Engineering Workspace V2 shell.

Kept as a small feature catalogue while the PFC workspace is being decomposed;
the aggregate catalogue merges these entries with the established language
catalogues so shell-string completeness remains enforced by CI.
"""

CATALOGUES: dict[str, dict[str, str]] = {
    "en": {
        "保存 TTPL 会话": "Save TTPL Session",
        "打开 TTPL 会话": "Open TTPL Session",
        "计算功率级": "Calculate Power Stage",
        "应用到 Control / AC / Switching": "Apply to Control / AC / Switching",
    },
    "ja": {
        "保存 TTPL 会话": "TTPL セッションを保存",
        "打开 TTPL 会话": "TTPL セッションを開く",
        "计算功率级": "パワーステージを計算",
        "应用到 Control / AC / Switching": "Control / AC / Switching に適用",
    },
    "ko": {
        "保存 TTPL 会话": "TTPL 세션 저장",
        "打开 TTPL 会话": "TTPL 세션 열기",
        "计算功率级": "파워 스테이지 계산",
        "应用到 Control / AC / Switching": "Control / AC / Switching에 적용",
    },
}

__all__ = ["CATALOGUES"]
