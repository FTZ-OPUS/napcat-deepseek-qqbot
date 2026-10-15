"""traplib.py —— 数学分析陷阱库（车主 2026-10-12 定）

**核心思路**：数学分析真正难的不是题型，是**条件的细微差别**。
机器人最容易犯的错是**把题目没写的条件当成有**（条件臆造）。
所以最值钱的知识不是"题解"，而是「**陷阱 + 反例 + 正确做法**」。

**数据**：`docs/数学分析陷阱库.md`（人可读、可直接手改、追加一节就生效）

**用法**：解题前把**相关**的几条注入提示词。为什么只挑相关的：
  全量 3.7k 字符 ≈ 1.2k token，而现在整个 prompt 才 2.2k token ——
  每次都全塞会让成本和延迟涨 50%+。按关键词挑 3~6 条，通常 300~800 字符就够。

**任何异常都返回空**，绝不让陷阱库本身影响回答。
"""
from __future__ import annotations

import re
from pathlib import Path

import config

# 数据文件位置。
# ⚠️ 注意：`config.BASE` 在**服务器上**是机器人根目录（deploy.sh 把 server/*.py 平铺过去），
#    但在**本地开发时**是 `qq-bot/server/` —— 两边不一样，所以父目录也要试。
_CANDIDATES = (
    Path(config.BASE) / "docs" / "数学分析陷阱库.md",
    Path(config.BASE).parent / "docs" / "数学分析陷阱库.md",
    Path(config.BASE) / "数学分析陷阱库.md",
    Path(config.BASE).parent / "数学分析陷阱库.md",
)

# 注入上限（字符）。宁可少而准，不要多而杂。
MAX_INJECT_CHARS = 2200
MAX_ENTRIES = 6


def _path() -> Path | None:
    for p in _CANDIDATES:
        if p.exists():
            return p
    return None


def load() -> list[dict]:
    """解析陷阱库。每条 = {title, tags, trap, counter, right, source}。

    格式约定（见 docs/数学分析陷阱库.md）：
        ## 标题
        - 标签: a b c
        - 陷阱: ...
        - 反例: ...
        - 正确做法: ...
        - 来源: ...
    字段缺了也不报错，能给多少给多少。
    """
    p = _path()
    if not p:
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    items: list[dict] = []
    for block in re.split(r"\n## ", "\n" + text)[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        if title.startswith("#") or not title:
            continue
        it = {"title": title, "tags": [], "trap": "", "counter": "",
              "right": "", "source": ""}
        # ⚠️ 字段值是**可以多行的**（缩进的续行要接上去）。
        #    真实 bug（2026-10-12）：新加的「有精确解却去搞数值积分」条目内容写了好几行，
        #    老解析器只读单行 → 注入时内容被截断，最关键的 π/2 精确解全丢了。
        cur = None
        for ln in lines[1:]:
            m = re.match(r"\s*[-*]\s*(标签|陷阱|反例|正确做法|来源)\s*[:：]\s*(.*)", ln)
            if m:
                cur = m.group(1)
                v = m.group(2).strip()
                if cur == "标签":
                    it["tags"] = [x for x in re.split(r"[ ,，、]+", v) if x]
                elif cur == "陷阱":
                    it["trap"] = v
                elif cur == "反例":
                    it["counter"] = v
                elif cur == "正确做法":
                    it["right"] = v
                else:
                    it["source"] = v
                continue
            # 续行：缩进且不是新字段/新标题 → 接到当前字段后面
            if cur and ln.strip() and not ln.lstrip().startswith("#"):
                key = {"陷阱": "trap", "反例": "counter", "正确做法": "right",
                       "来源": "source"}.get(cur)
                if key:
                    it[key] = (it[key] + " " + ln.strip()).strip()
        if it["trap"] or it["right"]:
            items.append(it)
    return items


def _tokens(s: str) -> set[str]:
    """粗分词：中文按 2~4 字片段、英文/符号按词。够用就好，不引第三方分词。"""
    out: set[str] = set()
    for w in re.findall(r"[A-Za-z]{2,}", s):
        out.add(w.lower())
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", s):
        for n in (2, 3, 4):
            for i in range(len(seg) - n + 1):
                out.add(seg[i:i + n])
    return out


def relevant(text: str, max_chars: int = MAX_INJECT_CHARS,
             max_entries: int = MAX_ENTRIES) -> list[dict]:
    """挑跟这条消息最相关的陷阱条目（按命中关键词数排序，受条数和字符预算限制）。"""
    items = load()
    if not items or not (text or "").strip():
        return []
    q = _tokens(text)
    scored = []
    for it in items:
        key = " ".join([it["title"]] + it["tags"])
        kt = _tokens(key)
        hit = len(q & kt)
        if hit:
            scored.append((hit, it))
    if not scored:
        return []
    scored.sort(key=lambda x: -x[0])
    picked, used = [], 0
    for _, it in scored[:max_entries]:
        block = render_one(it)
        if used + len(block) > max_chars and picked:
            break
        picked.append(it)
        used += len(block)
    return picked


def render_one(it: dict) -> str:
    parts = [f"【陷阱】{it['title']}"]
    if it["trap"]:
        parts.append(f"  · {it['trap']}")
    if it["counter"]:
        parts.append(f"  · 反例/依据：{it['counter']}")
    if it["right"]:
        parts.append(f"  · 正确做法：{it['right']}")
    return "\n".join(parts)


def hint(text: str) -> str:
    """给 brain.decide 用的注入块。没命中就返回空串（不占 token）。"""
    try:
        items = relevant(text)
        if not items:
            return ""
        body = "\n\n".join(render_one(it) for it in items)
        return ("\n\n【⚠️ 数学分析陷阱库 —— 这几条跟本题相关，解题时务必避开】\n"
                "**这些是踩过的坑（含真实事故）。如果本题的条件跟下面某条冲突，"
                "以题目写明的为准，绝对不许按「通常情况」把它补强。**\n"
                f"{body}")
    except Exception:
        return ""


def stats() -> str:
    items = load()
    p = _path()
    return f"{len(items)} 条" + (f"（{p.name}）" if p else "（数据文件缺失）")
