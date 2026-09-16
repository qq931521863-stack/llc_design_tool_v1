"""Translations introduced by the PFC Engineering Workspace V2 shell.

Kept as a small feature catalogue while the PFC workspace is being decomposed;
the aggregate catalogue merges these entries with the established language
catalogues so shell-string completeness remains enforced by CI.
"""

CATALOGUES: dict[str, dict[str, str]] = {
    "en": {
        "计算功率级": "Calculate Power Stage",
        "应用到 Control / AC / Switching": "Apply to Control / AC / Switching",
    },
    "ja": {
        "计算功率级": "パワーステージを計算",
        "应用到 Control / AC / Switching": "Control / AC / Switching に適用",
    },
    "ko": {
        "计算功率级": "파워 스테이지 계산",
        "应用到 Control / AC / Switching": "Control / AC / Switching에 적용",
    },
}

__all__ = ["CATALOGUES"]
