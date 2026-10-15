"""politics_push.py —— 每日政治知识点推荐（车主 2026-10-13 定）。

**车主原话**：
> 「每天你可以选个时间给大家推荐一些重要的知识点。」

**做什么**：每天固定时间，从考研政治考点库里挑一条**还没推过的**考点，
让模型写成一条**群聊口吻**的推荐（附一张关系图，用预置的彩色方框+箭头样式），
发到群里。

**为什么轮着推**：考点库会越攒越多，**随机挑会重复**，所以记「已推送」清单，
按顺序轮（`state/politics_sent.json`）。

**⚠️ 去重标记必须落盘** —— 内存变量一重启就丢，会一天推好几次
（这个坑在每日讲义上踩过：`_last_run_day` 是内存变量，服务反复重启发了三次）。

**任何异常都只记日志**，绝不影响机器人正常答疑。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import config
import polib

_SENT = Path(config.STATE_DIR) / "politics_sent.json"
_last_run_day = ""


def log(msg: str, level: str = "INFO") -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] 政治推荐: {msg}", flush=True)


def _load_sent() -> list[str]:
    try:
        return json.loads(_SENT.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_sent(items: list[str]) -> None:
    try:
        _SENT.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SENT.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_SENT)
    except OSError as exc:
        log(f"已推清单写入失败：{exc}", "WARN")


def pick_next() -> dict | None:
    """挑一条**还没推过**的考点；全推完了就从头轮（不清空，避免重复轰炸）。"""
    rows = polib.load()
    if not rows:
        return None
    sent = set(_load_sent())
    key = lambda r: f"{r.get('subject')}|{r.get('chapter')}|{r.get('point')}"
    fresh = [r for r in rows if key(r) not in sent]
    if fresh:
        return fresh[0]
    # 全推过一轮了 → 重新开始
    log("考点都推过一轮了，从头再轮")
    _save_sent([])
    return rows[0]


PUSH_PROMPT = """你是考研政治答疑群里的一个热心群友（机器人「__BOT__」），
现在要给群里**推荐一个今天的政治考点**。

【今天推的考点】
科目：__SUBJECT__
章节：__CHAPTER__
考点：__POINT__
核心内容：__CONTENT__
易混点：__CONFUSABLE__
常见考法：__EXAM__

要求：
1. 用**群聊口吻**写，像老同学给你划重点，**不要写成讲义**。
   开头一句话点出「今天这个点为什么重要/容易考」，然后几句话讲清楚。
2. 长度控制在 **150~260 字**，别啰嗦。
3. **必须配一张关系图**（如果这个考点适合画的话；关系/层级/流程/对比类都适合）。
   图用 mode="card"，照下面的预置样式写：
   \\node[rbox=rbA] (a) {概念A};
   \\node[rbox=rbB, right=2.4cm of a] (b) {概念B};
   \\draw[rarr, rbA] (a) -- node[above,font=\\scriptsize]{决定} (b);
   \\draw[rarrR, rbR] (b) to[bend left=30] node[below,font=\\scriptsize]{反作用} (a);
   （配色 rbA蓝/rbB绿/rbC橙/rbD紫，反向虚线用 rarrR + rbR 红）
   图要**简洁**，4~6 个方框以内，别塞太多字。宽度用 \\resizebox{\\linewidth}{!}{...} 包住防溢出。
4. **最后加一句互动**，比如问大家「这个点你们常错在哪」，让人愿意接话。
5. 输出顺序：**先文字 → 再图 → 再一句文字**（图夹在中间）。

严格输出 JSON（不要别的文字）：
{"parts":[{"kind":"text","text":"..."},
          {"kind":"math","mode":"card","latex":"..."},
          {"kind":"text","text":"..."}],
 "reason":"一句话说明这张图画的是什么"}"""


def build_message(row: dict) -> dict | None:
    """让模型把这个考点写成一串 parts（文字→图→文字）。"""
    import brain
    prompt = (PUSH_PROMPT
              .replace("__BOT__", config.BOT_DISPLAY_NAME if hasattr(config, "BOT_DISPLAY_NAME") else "e阶导")
              .replace("__SUBJECT__", str(row.get("subject", "")))
              .replace("__CHAPTER__", str(row.get("chapter", "")))
              .replace("__POINT__", str(row.get("point", "")))
              .replace("__CONTENT__", str(row.get("content", "")))
              .replace("__CONFUSABLE__", str(row.get("confusable") or "（无）"))
              .replace("__EXAM__", str(row.get("exam") or "（无）")))
    for provider in config.PROVIDERS:
        payload = {"model": provider["model"],
                   "messages": [{"role": "user", "content": prompt}],
                   "response_format": {"type": "json_object"},
                   "temperature": 1.0, "max_tokens": 8000}
        if provider.get("extra"):
            payload.update(provider["extra"])
        resp = brain._post_llm(provider, payload, timeout=180, kind="politics_push")
        if resp is None or resp.status_code != 200:
            continue
        try:
            data = json.loads(resp.json()["choices"][0]["message"].get("content") or "")
            parts = (data or {}).get("parts") or []
            if parts:
                return {"parts": parts, "reason": data.get("reason", "")}
        except Exception:
            continue
    log("所有线路都没生成出内容", "WARN")
    return None


def push_now(group_id: int, force: bool = False) -> bool:
    """立刻推一条（手动触发 / 定时触发共用）。"""
    import onebot
    row = pick_next()
    if not row:
        log("考点库是空的（去 docs/考研政治答题工具箱/ 灌数据）")
        return False
    log(f"挑中：{row.get('subject')}·{row.get('chapter')} → {row.get('point')}")
    built = build_message(row)
    if not built:
        return False
    ok_any = False
    for p in built["parts"]:
        try:
            if p.get("kind") == "text" and str(p.get("text") or "").strip():
                onebot.send_group_text(group_id, str(p["text"]).strip())
                ok_any = True
                time.sleep(0.8)
            elif p.get("kind") == "math" and p.get("latex"):
                import latexrender
                path = latexrender.render(p["latex"], p.get("mode") or "card", style="2")
                onebot.send_group_image(group_id, path)
                ok_any = True
                time.sleep(0.8)
        except Exception as exc:
            log(f"发某一段失败（继续）：{exc!r}", "WARN")
    if ok_any:
        sent = _load_sent()
        sent.append(f"{row.get('subject')}|{row.get('chapter')}|{row.get('point')}")
        _save_sent(sent)
        log(f"✅ 已推送到群里（累计推过 {len(sent)} 条）")
    return ok_any


def maybe_run(group_id: int, now: float | None = None) -> None:
    """主循环 tick 挂载点：每天到点推一条（**每天一次**，标记落盘）。"""
    global _last_run_day
    if not getattr(config, "POLITICS_DAILY_ENABLED", False):
        return
    now = now if now is not None else time.time()
    lt = time.localtime(now)
    day = time.strftime("%Y-%m-%d", lt)
    # ⚠️ 用**落盘标记**去重，不能只用内存变量（重启会重发，讲义上踩过这个坑）
    marker = Path(config.STATE_DIR) / f"politics_pushed_{day}.marker"
    if marker.exists() or _last_run_day == day:
        return
    if (lt.tm_hour, lt.tm_min) < (getattr(config, "POLITICS_DAILY_HOUR", 8),
                                  getattr(config, "POLITICS_DAILY_MINUTE", 0)):
        return
    _last_run_day = day
    try:
        if push_now(group_id):
            marker.write_text(str(int(now)), encoding="utf-8")
    except Exception as exc:
        log(f"推送异常：{exc!r}", "ERROR")


def stats() -> str:
    rows = polib.load()
    sent = len(_load_sent())
    return f"考点 {len(rows)} 条，已推 {sent} 条"
