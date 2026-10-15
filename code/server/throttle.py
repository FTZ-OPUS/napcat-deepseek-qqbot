"""throttle.py —— 发送节流（风控收敛，车主 2026-10-14 定）。

**背景**：2026-10-14 凌晨 QQ 号被腾讯**冻结**，申诉后恢复。
根因是**行为**：10-13 一天发了 **104 份 PDF** + 211 次秒回 ——
QQ 风控最敏感的两件事（大量文件传输 + 高频自动回复）全占了。

**这个模块管三件事**：
  ① **每日 PDF 上限**（默认 30 份）—— 直接砍掉最大风险
  ② **每小时发送上限**（默认 15 条）—— 避免"秒回"特征
  ③ **随机发送间隔**（1.2~4.0 秒）—— 别像机器一样匀速

⚠️ **计数必须落盘** —— 内存变量一重启就清空，那样限额形同虚设
（这个坑在每日讲义上踩过：`_last_run_day` 是内存变量，重启后重复发送）。
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import config

_F = Path(config.STATE_DIR) / "throttle.json"
log = lambda m, l="INFO": print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{l}] 节流: {m}", flush=True)


def _load() -> dict:
    try:
        d = json.loads(_F.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        _F.parent.mkdir(parents=True, exist_ok=True)
        tmp = _F.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_F)
    except OSError as exc:
        log(f"计数写盘失败：{exc!r}", "WARN")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _hour() -> str:
    return time.strftime("%Y-%m-%d %H")


def allow(kind: str = "msg") -> tuple[bool, str]:
    """现在还能不能发？kind: 'pdf' / 'msg'。返回 (能否, 理由)。

    被拒绝时**不抛异常**，让调用方决定怎么说（通常礼貌告知 + 不冷场）。
    """
    if not getattr(config, "THROTTLE_ENABLED", True):
        return True, ""
    d = _load()
    if kind == "pdf":
        n = int((d.get("pdf") or {}).get(_today(), 0))
        limit = int(getattr(config, "PDF_DAILY_LIMIT", 30))
        if n >= limit:
            return False, f"今天 PDF 已经发了 {n} 份（上限 {limit}）"
    n = int((d.get("hour") or {}).get(_hour(), 0))
    limit = int(getattr(config, "SEND_HOURLY_LIMIT", 15))
    if n >= limit:
        return False, f"这一小时已经发了 {n} 条（上限 {limit}）"
    return True, ""


def note(kind: str = "msg") -> None:
    """记一笔发送。"""
    if not getattr(config, "THROTTLE_ENABLED", True):
        return
    d = _load()
    pdf = d.setdefault("pdf", {})
    hour = d.setdefault("hour", {})
    hour[_hour()] = int(hour.get(_hour(), 0)) + 1
    if kind == "pdf":
        pdf[_today()] = int(pdf.get(_today(), 0)) + 1
    # 只留最近 3 天，别无限涨
    today = _today()
    d["pdf"] = {k: v for k, v in pdf.items() if k >= time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400))}
    d["hour"] = {k: v for k, v in hour.items() if k >= time.strftime("%Y-%m-%d %H", time.localtime(time.time() - 3 * 3600))}
    _save(d)


def gap() -> float:
    """两条消息之间的随机间隔（秒）—— 别匀速，匀速最像机器。"""
    lo = float(getattr(config, "SEND_GAP_MIN", 1.2))
    hi = float(getattr(config, "SEND_GAP_MAX", 4.0))
    if hi < lo:
        hi = lo
    return random.uniform(lo, hi)


def stats() -> str:
    d = _load()
    p = int((d.get("pdf") or {}).get(_today(), 0))
    h = int((d.get("hour") or {}).get(_hour(), 0))
    return (f"今天 PDF {p}/{getattr(config,'PDF_DAILY_LIMIT',30)} ｜ "
            f"本小时发送 {h}/{getattr(config,'SEND_HOURLY_LIMIT',15)}")


def refuse_line() -> str:
    """被限流时对群友说的话（不冷场，也不硬顶）。"""
    return random.choice((
        "今天答得有点多了，我先歇口气，晚点再喊我 👍",
        "手有点酸了哈哈，剩下这几道晚点再给你算，先记着 👌",
        "这会儿先停一下，别让号被腾讯盯上 😅 过会儿再问",
    ))
