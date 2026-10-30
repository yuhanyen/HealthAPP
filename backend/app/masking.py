"""顯示用遮罩：據點、商家、家屬畫面只顯示「王○英」這類部分姓名。"""

from __future__ import annotations


def mask_name(name: str) -> str:
    name = name.strip()
    if len(name) <= 1:
        return "○"
    if len(name) == 2:
        return f"{name[0]}○"
    return f"{name[0]}{'○' * (len(name) - 2)}{name[-1]}"
