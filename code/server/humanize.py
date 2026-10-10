"""拟人化节奏 —— 这部分决定"像不像人"。

要点：
  1. 打字时间跟回复长度正相关（长的想得久），但带随机抖动
  2. 分段发送，段间有停顿 —— 真人不会一口气发一大段
  3. 偶尔"看了不回"，尤其不是点名问的时候
  4. 绝不秒回
"""
from __future__ import annotations

import random
import re
import time

import config

# 句子切分：中文标点 + 换行
_SPLIT_RE = re.compile(r"(?<=[。！？!?…；;])|\n+")


def typing_delay(text: str) -> float:
    """按长度估算"打字"耗时，夹在合理区间内。"""
    base = config.TYPING_BASE + len(text) * config.TYPING_PER_CHAR
    base = min(base, config.TYPING_MAX)
    # ±35% 抖动，避免每次节奏一模一样
    return max(0.8, base * random.uniform(0.65, 1.35))


def sleep_typing(text: str) -> float:
    d = typing_delay(text)
    time.sleep(d)
    return d


def split_long_text(text: str, limit: int = 90) -> list[str]:
    """把过长的一条拆成几条，像真人手打会分次发。"""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    pieces = [p for p in _SPLIT_RE.split(text) if p and p.strip()]
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if len(buf) + len(piece) <= limit:
            buf += piece
        else:
            if buf.strip():
                chunks.append(buf.strip())
            buf = piece
    if buf.strip():
        chunks.append(buf.strip())

    # 太碎就合并，避免连发七八条刷屏
    if len(chunks) > config.MAX_PARTS:
        merged = []
        step = len(chunks) / config.MAX_PARTS
        for i in range(config.MAX_PARTS):
            group = chunks[int(i * step): int((i + 1) * step)]
            if group:
                merged.append("".join(group))
        chunks = merged
    return chunks


def gap_between_parts() -> float:
    lo, hi = config.GAP_BETWEEN_PARTS
    return random.uniform(lo, hi)


def should_drop(was_mentioned: bool, prob: float | None = None) -> bool:
    """非点名的情况下，偶尔装没看见。prob 由 rampup 按阶段提供。"""
    if was_mentioned:
        return False
    if prob is None:
        prob = config.DROP_PROB
    return random.random() < prob


def reading_delay(text: str) -> float:
    """"看到消息"到"开始打字"之间的反应时间。"""
    d = 0.5 + len(text) * 0.012
    return min(d, 3.0) * random.uniform(0.7, 1.5)
