"""msglog.py —— 群消息流水（每日分析报告的数据源，车主 2026-10-13 定）。

**为什么记这个**：车主想要每日分析报告（谁@得多、谁最满意…）。
`service.log` 只有"决定回复"那一部分，**没被回复的消息完全没记录**，
而"谁经常@我但我不理"恰恰是最该看的数据。

**记什么**：每条群消息一行 JSONL（`state/msglog/<日期>.jsonl`）
    ts / time / user_id / name / text / at（是否@了机器人）/ img / reply（机器人是否回了）

**为什么用 JSONL**：追加写、崩溃不坏、单行可读、按天分文件好清理。
**任何异常都只记日志**，绝不影响机器人。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import config

_DIR = Path(config.STATE_DIR) / "msglog"
KEEP_DAYS = 30


def log(msg: str, level: str = "INFO") -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] 消息流水: {msg}", flush=True)


def _path(day: str | None = None) -> Path:
    return _DIR / f"{day or time.strftime('%Y-%m-%d')}.jsonl"


def record(user_id, name: str, text: str, at_bot: bool, has_img: bool) -> None:
    """记一条群消息。"""
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "uid": str(user_id), "name": (name or "")[:24],
               "txt": (text or "")[:500], "at": bool(at_bot), "img": bool(has_img)}
        with _path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        log(f"写入失败（不影响回答）：{exc!r}", "WARN")


def mark_reply(ts: int) -> None:
    """标记「这个时刻机器人回复了」（报告里用来算被回复率）。"""
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(ts), "kind": "reply"}
        with _path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load(day: str | None = None) -> list[dict]:
    """读某天的流水（坏行跳过）。"""
    p = _path(day)
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except OSError:
        return []
    return out


def cleanup(keep: int = KEEP_DAYS) -> None:
    """只留最近 N 天（默认 30）。"""
    try:
        if not _DIR.exists():
            return
        files = sorted(_DIR.glob("*.jsonl"))
        for f in files[:-max(1, keep)]:
            f.unlink(missing_ok=True)
            log(f"清理旧流水：{f.name}")
    except Exception as exc:
        log(f"清理失败：{exc!r}", "WARN")


def stats() -> str:
    if not _DIR.exists():
        return "还没开始记录"
    fs = sorted(_DIR.glob("*.jsonl"))
    if not fs:
        return "还没开始记录"
    n = len(load(fs[-1].stem))
    return f"{len(fs)} 天，最近一天 {n} 条"
