"""polib.py —— 考研政治答题工具箱（考点库查询）。

**为什么建这个**：群友问政治题时，模型靠自带的记忆答，
容易漏考点、搞混易混点。有一份**结构化的考点库**，答题时按题目关键词
查出相关考点注入提示词，比现场搜网精确得多。

**数据**：`docs/考研政治答题工具箱/考点库.db`（SQLite）
        由 `tools/build_politics_db.py` 从 `考点数据.json` 生成

**⚠️ 版权**：库里的内容全部是**用自己的话概括的考点知识**（知识点本身是公共知识），
**不含任何教材/大纲原文**。官方大纲是付费出版物，我们不复制不分发。

**查法**：用中文 n-gram + 关键词打分（和 traplib 同一套思路）。
     为什么不用 SQL 的 FTS5：**它默认不能正确切分中文**，
     整个句子会被当成一个 token，检索基本失效。几百条数据在内存里打分也很快。

**任何异常都返回空**，绝不让考点库影响回答。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import config

MAX_INJECT_CHARS = 2600
MAX_ENTRIES = 4

# 数据文件位置：本地在 qq-bot/docs/…，服务器上 config.BASE 就是机器人根目录
_CANDIDATES = (
    Path(config.BASE) / "docs" / "考研政治答题工具箱" / "考点库.db",
    Path(config.BASE).parent / "docs" / "考研政治答题工具箱" / "考点库.db",
)

_cache: list[dict] | None = None
_cache_mtime: float = 0.0


def db_path() -> Path | None:
    for p in _CANDIDATES:
        if p.exists():
            return p
    return None


def load(force: bool = False) -> list[dict]:
    """读全库到内存（带 mtime 缓存，题库更新后自动重载）。"""
    global _cache, _cache_mtime
    p = db_path()
    if not p:
        return []
    try:
        mt = p.stat().st_mtime
        if _cache is not None and not force and mt == _cache_mtime:
            return _cache
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT subject,chapter,point,content,confusable,exam,keywords FROM points")]
        finally:
            con.close()
        _cache, _cache_mtime = rows, mt
        return rows
    except Exception:
        return []


def _tokens(s: str) -> set[str]:
    """粗分词：中文 2~4 字片段 + 英文词。够用就好，不引第三方分词。"""
    out: set[str] = set()
    for w in re.findall(r"[A-Za-z]{2,}", s or ""):
        out.add(w.lower())
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", s or ""):
        for n in (2, 3, 4):
            for i in range(len(seg) - n + 1):
                out.add(seg[i:i + n])
    return out


def relevant(text: str, limit: int = MAX_ENTRIES,
             max_chars: int = MAX_INJECT_CHARS) -> list[dict]:
    """挑跟这道题最相关的考点。按 关键词 > 考点名 > 正文 加权打分。"""
    rows = load()
    if not rows or not (text or "").strip():
        return []
    q = _tokens(text)
    if not q:
        return []
    scored = []
    for r in rows:
        kws = [k for k in (r.get("keywords") or "").split() if k]
        s = 0
        kw_hit = 0
        for k in kws:
            if not k:
                continue
            kt = _tokens(k)
            # 关键词命中两种算法：
            #   ① 整个关键词原样出现在题干里
            #   ② 关键词的 n-gram 有 60% 以上出现在题干里（照顾「三大先进思潮」vs「三大思潮」这类说法差异）
            hit = (k in (text or "")) or (kt and len(q & kt) / len(kt) >= 0.6)
            if hit:
                kw_hit += 1
        # ⚠️ 关键词权重必须**远大于**正文 n-gram ——
        #    否则只有几条考点时，一个「马克思主义」就能把每条都刷上榜，
        #    排序完全失准（真实踩坑 2026-10-13：问「直接理论来源」，
        #    排第一的却是「基本特征」）。
        s += 12 * kw_hit
        pt = _tokens(r.get("point") or "")
        pt_hit = len(q & pt)
        s += 2 * pt_hit
        # 正文 n-gram 权重最低，而且**封顶**，防止长正文靠体量刷分
        s += min(len(q & _tokens(r.get("content") or "")), 8)
        # 纯靠正文噪声命中的不算（必须命中关键词或考点名）
        if s > 0 and (kw_hit or pt_hit):
            scored.append((s, r))
    if not scored:
        return []
    scored.sort(key=lambda x: -x[0])
    picked, used = [], 0
    for _, r in scored[:limit]:
        block = render_one(r)
        if used + len(block) > max_chars and picked:
            break
        picked.append(r)
        used += len(block)
    return picked


def render_one(r: dict) -> str:
    parts = [f"【{r.get('subject','')}·{r.get('chapter','')}】{r.get('point','')}"]
    if r.get("content"):
        parts.append(f"  核心：{r['content']}")
    if r.get("confusable"):
        parts.append(f"  ⚠️ 易混：{r['confusable']}")
    if r.get("exam"):
        parts.append(f"  考法：{r['exam']}")
    return "\n".join(parts)


def hint(text: str) -> str:
    """给 brain.decide 用的注入块。没命中就返回空串（不占 token）。"""
    try:
        rows = relevant(text)
        if not rows:
            return ""
        body = "\n\n".join(render_one(r) for r in rows)
        return ("\n\n【考研政治考点库 —— 这几条跟本题相关，答的时候照着讲，别漏考点】\n"
                "**这些是考点提要，不是标准答案。用你自己的话讲清楚，"
                "该展开的展开、该举例的举例；和分析题一样，把原理和材料挂上钩。**\n"
                f"{body}")
    except Exception:
        return ""


def stats() -> str:
    rows = load()
    if not rows:
        return "（考点库还没建，或读不到）"
    subs = {}
    for r in rows:
        subs[r.get("subject", "?")] = subs.get(r.get("subject", "?"), 0) + 1
    return (f"{len(rows)} 个考点 ｜ "
            + " ／ ".join(f"{k} {v}" for k, v in sorted(subs.items(), key=lambda x: -x[1])))
