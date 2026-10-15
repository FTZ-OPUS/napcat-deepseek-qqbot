"""feedback.py —— 重要用户建议反馈收集（车主 2026-10-12 定）

**核心是「纠错」** —— 车主原话：
> 「我的意思就类似于纠错吧，**别人指出你错误了**，这就属于我说的这种建议，
>  其他类似情况也算。」

所以采集的重点是**群友指出机器人哪里做错了**，包括：
  ① **解答/识别错了**（算错、抄错下标、把题目没写的条件当成有 —— 最要命的一类）
  ② **交付有毛病**（太简略、没抄题、格式乱、该发 PDF 没发）
  ③ **行为该改**（下次记得…、希望你…）

**为什么做**：这些反馈散落在聊天里，稍纵即逝。
车主希望**每天能收到一份整理好的反馈**，据此决定下一步改什么。

**两段式设计**（关键：判断要智能，但不能拖慢主流程）：

  ① **即时抓取**（廉价、无 LLM）：消息命中关键词预筛 → 立刻把**原始事实**落盘
       时间 / 谁说的 / 原话 / 引用了什么 / 最近几条上下文 / 机器人当时怎么答的
  ② **异步提炼**（一次 LLM 调用，在主循环 tick 里做）：
       确认「这确实是在纠错/提建议」+ 分类 + 写清
       **错在哪 · 为什么会错 · 应该怎么改**
       判完就把条目重写成可读版；判为「不是建议」的直接丢掉（避免噪音积压）

**落盘位置**：`重要用户建议反馈/YYYY-MM-DD.md`（按天一个文件，追加式）
             `重要用户建议反馈/_待提炼.json`（还没过 LLM 的原始条目）

**绝不打扰主流程**：任何异常都只记日志，绝不让采集本身影响机器人回答。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import config

# 建议的**关键词预筛**（宁可多抓，交给 ② 去伪存真）
# 只做初筛，真正的判断在 LLM 那一步
SUGGEST_HINTS = (
    # ── ① 直接指出错误（最核心，车主说的就是这类）──
    "解错", "算错", "做错", "答错", "写错", "看错", "抄错", "读错", "识别错",
    "理解错", "错了", "不对", "有误", "有问题", "条件", "下标", "符号",
    # ── ② 交付/质量毛病 ──
    "太简", "不详细", "不严谨", "写完整", "写详细", "详细点", "具体点",
    "没抄题", "抄题", "格式", "看不懂",
    # ── ③ 要求改 / 行为建议 ──
    "改一下", "改改", "重写", "重做", "重新", "订正", "修正", "完善", "补充",
    "建议", "下次", "以后", "记得", "注意", "希望", "最好",
    # ── ④ 追问缘由（往往就是在质疑你）──
    "你怎么", "你咋", "为啥", "为什么", "莫名其妙", "离谱",
)
# 明确不是建议的（闲聊/客套）—— 命中就跳过，减少无谓的 LLM 调用
NOT_SUGGEST_HINTS = ("谢谢", "感谢", "厉害", "牛", "6", "哈哈", "笑死", "可以可以", "好的")

_DIR_NAME = "重要用户建议反馈"


def feedback_dir() -> Path:
    d = Path(config.STATE_DIR).parent / _DIR_NAME
    return d


def _pending_path() -> Path:
    return feedback_dir() / "_待提炼.json"


def looks_like_suggestion(text: str) -> bool:
    """关键词预筛：**只是初筛**，真判断交给 LLM（见 enrich_pending）。"""
    t = (text or "").strip()
    if len(t) < 4:
        return False
    if any(h in t for h in NOT_SUGGEST_HINTS):
        return False
    return any(h in t for h in SUGGEST_HINTS)


def _load_pending() -> list[dict]:
    try:
        return json.loads(_pending_path().read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_pending(items: list[dict]) -> None:
    try:
        d = feedback_dir()
        d.mkdir(parents=True, exist_ok=True)
        _pending_path().write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        log(f"待提炼队列写入失败：{exc}", "WARN")


def log(msg: str, level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] 建议反馈: {msg}"
    print(line, flush=True)


def record(sender: str, text: str, quoted: str = "",
           recent: list[str] | None = None, bot_reply: str = "") -> None:
    """① 即时抓取：把**原始事实**立刻落盘（无 LLM、不阻塞）。"""
    try:
        item = {
            "ts": time.time(),
            "day": time.strftime("%Y-%m-%d"),
            "clock": time.strftime("%H:%M"),
            "sender": sender,
            "text": (text or "").strip()[:1500],
            "quoted": (quoted or "").strip()[:1500],
            "recent": [r[:400] for r in (recent or [])][-6:],
            "bot_reply": (bot_reply or "").strip()[:800],
        }
        items = _load_pending()
        items.append(item)
        _save_pending(items[-200:])          # 只留最近 200 条，防无限增长
        log(f"抓到一条疑似建议（{sender}）：{item['text'][:40]}")
    except Exception as exc:                  # 绝不影响主流程
        log(f"记录失败（不影响回答）：{exc!r}", "WARN")


def attach_reply(sender: str, reply: str, window: float = 900.0) -> None:
    """把机器人的回答补进最近的待提炼条目（同一个人、10 分钟内）。

    为什么需要：建议往往是"先看到机器人的回答 → 再吐槽"，
    而 record() 在回答之前就跑了。所以回答完再回头补一笔，
    LLM 提炼时才知道「他是在说哪次回答」。
    """
    try:
        items = _load_pending()
        now = time.time()
        for it in reversed(items):
            if it.get("sender") == sender and now - float(it.get("ts") or 0) <= window \
                    and not it.get("bot_reply"):
                it["bot_reply"] = (reply or "")[:800]
                _save_pending(items)
                log(f"已补上机器人回答（{sender}）")
                return
    except Exception as exc:
        log(f"补回答失败（不影响回答）：{exc!r}", "WARN")


# ══════════ ② 异步提炼（在 tick 里做，一次一条）══════════

ENRICH_PROMPT = """你在帮一个数学答疑 QQ 机器人整理「用户纠错反馈」。

**重点：群友指出机器人哪里做错了。** 不是普通提问，也不是客套。
下面这条是**疑似**纠错。请你判断并整理。

【谁说】__SENDER__
【原话】__TEXT__
【他引用了什么（往往是机器人发的那份 PDF）】__QUOTED__
【最近上下文】__RECENT__
【机器人当时的回答】__REPLY__

严格输出一个 JSON 对象（不要别的文字）：
{
  "is_suggestion": true/false,
  "kind": "解答错误|识别错误|条件臆造|太简略|没抄题|格式问题|行为建议|其他",
  "what": "他指的是**哪一次回答/哪一道题**（一句话，具体点）",
  "wrong": "机器人**错在哪**（如果从上下文能看出来）",
  "why": "**为什么**会错（识别看错了？自己加强了条件？推理跳步？没说清？）",
  "fix": "**应该怎么改**（给开发 AI 的具体动作）",
  "severity": "高|中|低"
}

判断标准：
- 「你解错了」「条件识别错了」「下标抄错了」「太简略了」「怎么没抄题」→ 是
- 「这题怎么做」「谢谢」「厉害」→ 不是
- **拿不准时倾向 false**（宁缺毋滥，别让车主看一堆噪音）
- severity：影响**正确性**=高，影响**体验**=中，纯偏好=低

★ 特别注意「条件臆造」这一类 —— 例如题目说「f'' 存在但不必连续」，
  机器人却默认它连续。这是最严重的一类错误，务必识别出来并写清。"""


def enrich_pending(limit: int = 1) -> int:
    """② 把待提炼队列里的条目过一遍 LLM，写成可读版。返回处理条数。

    ⚠️ 一次只处理少量（默认 1 条），且**任何失败都保留原条目等下次再试**。
    """
    items = _load_pending()
    if not items:
        return 0
    import brain                                    # 延迟导入，避免循环依赖

    done = 0
    for _ in range(min(limit, len(items))):
        item = items[0]
        try:
            verdict = brain.judge_suggestion(item)
        except Exception as exc:
            log(f"提炼失败（保留待下次）：{exc!r}", "WARN")
            break
        items.pop(0)
        if verdict and verdict.get("is_suggestion"):
            _append_markdown(item, verdict)
            log(f"已收录建议（{verdict.get('kind')}）：{verdict.get('summary','')[:40]}")
        else:
            log(f"判定不是建议，丢掉：{item['text'][:30]}")
        done += 1
    _save_pending(items)
    return done


def _append_markdown(item: dict, v: dict) -> None:
    """把一条建议追加到当天的 Markdown 文件里（人可读）。"""
    d = feedback_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{item['day']}.md"
    if not f.exists():
        f.write_text(f"# 用户建议反馈 · {item['day']}\n\n"
                     "> 自动采集整理。每条含：谁说的、原话、机器人当时怎么答的、给开发 AI 的建议。\n\n",
                     encoding="utf-8")
    recent = "\n".join(f"> - {r}" for r in (item.get("recent") or []))
    block = f"""
---

## {item['clock']} · {item['sender']} · {v.get('kind','其他')} · 严重度 {v.get('severity','中')}

**他指的是**：{v.get('what') or v.get('summary','')}

**原话**：
> {item['text']}

**他引用的内容**：
> {item.get('quoted') or '（无）'}

**机器人当时的回答**：
> {item.get('bot_reply') or '（未记录）'}

**最近的上下文**：
{recent or '> （无）'}

### 🔍 复盘
- **错在哪**：{v.get('wrong') or '（未记明）'}
- **为什么会错**：{v.get('why') or '（未记明）'}

### 💡 给开发 AI 的建议
{v.get('fix') or v.get('detail','')}

"""
    with f.open("a", encoding="utf-8") as fh:
        fh.write(block)


def summary() -> str:
    """给主循环日志用的一句话状态。"""
    d = feedback_dir()
    days = sorted(p.name for p in d.glob("*.md")) if d.exists() else []
    pend = len(_load_pending())
    return f"已收录 {len(days)} 天，待提炼 {pend} 条"
