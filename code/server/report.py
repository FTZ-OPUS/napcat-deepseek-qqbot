"""report.py —— 每日群使用分析报告（车主 2026-10-13 定）。

**车主的要求**：
> 「以后每天出一个报告，分析一下**哪个群友对你最满意**，
>  哪个群友**使用你比较多**，就**艾特你的次数**。
>  还有你认为**也需要分析的地方**，你也可以自己分析一下。」

**数据源**（都是本地已落盘的，不额外调用 API）：
  · `state/msglog/<日期>.jsonl` —— 当天每条群消息（谁说的、有没有@我、有没有图）
  · `state/usage.json`           —— token 用量与费用
  · `重要用户建议反馈/<日期>.md`   —— 当天的纠错反馈（十四之十五那套）
  · `state/daily/<日期>/`        —— 当天答疑 PDF

**满意度怎么算**（没有问卷，只能从话语里读）：
  · **正面信号**：谢谢/感谢/对了/没错/牛/厉害/可以/辛苦了/懂了/明白了/赞/6
  · **负面信号**：错了/不对/算错/解错/有问题/看不懂/太简/重来
  · **净满意度 = 正面 − 负面**；纠错反馈库里的条目额外算重（那是明确指错）
  ⚠️ 这只是**启发式**，不是真满意度 —— 报告里会写明，别当成精确指标。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import config
import msglog

_DIR = Path(config.STATE_DIR) / "reports"

POS = ("谢谢", "感谢", "多谢", "对了", "没错", "正确", "牛", "厉害", "可以啊",
       "辛苦了", "懂了", "明白了", "学到了", "赞", "强", "稳", "有用", "好用")
NEG = ("错了", "不对", "算错", "解错", "做错", "有问题", "看不懂", "没看懂",
       "太简", "太简略", "不详细", "重来", "重做", "重新解", "漏了", "少了")

# 话题关键词 —— 用来大致看当天聊了什么
TOPICS = {
    "极限": ("极限", "趋于", "lim"), "导数/微分": ("导数", "微分", "求导", "二阶导"),
    "积分": ("积分", "原函数", "不定积分", "定积分"), "级数": ("级数", "收敛", "发散", "幂级数"),
    "中值定理": ("中值定理", "罗尔", "拉格朗日", "柯西", "泰勒"),
    "线性代数": ("矩阵", "行列式", "特征值", "特征向量", "秩", "二次型", "范德蒙"),
    "概率统计": ("概率", "期望", "方差", "分布", "假设检验"),
    "运筹学": ("单纯形", "线性规划", "对偶", "最优解"),
    "政治-马原": ("马原", "唯物", "辩证法", "剩余价值", "资本", "商品", "实践"),
    "政治-毛中特": ("毛中特", "社会主义", "中国特色", "五位一体", "主要矛盾"),
    "政治-史纲": ("近代史", "党史", "辛亥", "抗日", "新中国成立"),
    "政治-思修": ("思修", "道德", "法治", "理想信念"),
}


# 已知的机器人昵称（config.IGNORE_NAMES 之外再兜一层）
_BOT_NAMES = ("e阶导", "凛音", "muse", "Gandalf", "bot", "Bot")


def _day_pdf_titles(day: str) -> list[str]:
    """取当天答疑 PDF 的文件名（按内容命名，是**最好的话题来源**）。"""
    try:
        d = Path(config.STATE_DIR) / "daily" / day
        if not d.exists():
            return []
        out = []
        for f in d.glob("*.pdf"):
            name = re.sub(r"^\d+_", "", f.stem)      # 去掉序号前缀
            out.append(name)
        return out
    except Exception:
        return []


def _count_replies(day: str) -> list:
    """从 service.log 数当天机器人回复了几次。

    ⚠️ 为什么不数流水的 reply 标记：那个只有**实时流量**才写，
    回填的历史数据一条都没有 —— 第一版报告因此显示「机器人回复 0 次」。
    日志里「决定回复」是**每次真正回复都会打**的，可靠得多。
    """
    import re as _re
    logf = Path(config.STATE_DIR).parent / "service.log"
    if not logf.exists():
        return []
    out = []
    try:
        for line in logf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if f"] 群" not in line or "决定回复" not in line:
                continue
            m = _re.match(r"^(\d{4}-\d{2}-\d{2}) ", line)
            if m and m.group(1) == day:
                out.append(line)
    except OSError:
        return []
    return out


def log(msg: str, level: str = "INFO") -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] 日报: {msg}", flush=True)


def _sent_corrections(day: str) -> list[str]:
    """读当天「重要用户建议反馈」里收录的纠错条目（标题行）。"""
    try:
        d = Path(config.STATE_DIR).parent / "重要用户建议反馈"
        f = d / f"{day}.md"
        if not f.exists():
            return []
        out = []
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^## (\d{2}:\d{2}) · (.+?) · (.+?) ·", line)
            if m:
                out.append(f"{m.group(1)} {m.group(2)}（{m.group(3)}）")
        return out
    except Exception:
        return []


def build(day: str = "") -> str:
    """生成某天的分析报告（Markdown 文本）。"""
    day = day or time.strftime("%Y-%m-%d")
    rows = msglog.load(day)
    chats = [r for r in rows if r.get("kind") != "reply"]

    # ⚠️ 必须把**机器人自己**剔出去 —— 否则「e阶导」会以 186 条发言
    #    冲上榜首，还会得出「负面反馈集中在：e阶导」这种荒谬结论
    #    （真实踩坑 2026-10-13 第一版报告）。
    _bots = {str(getattr(config, "BOT_QQ", ""))} | set(_BOT_NAMES)
    chats = [r for r in chats
             if str(r.get("uid")) not in _bots
             and not any(b and b in str(r.get("name") or "") for b in _BOT_NAMES)]

    # 回复次数**从 service.log 数**，不用流水的 reply 标记 ——
    #    标记只有实时流量才有，回填的历史数据全没有（第一版报告因此显示「回复 0 次」）。
    replies = _count_replies(day)

    L: list[str] = []
    L.append(f"# e阶导 · 群使用日报 {day}")
    L.append("")
    if not chats:
        L.append("> 当天没有记录到群消息（流水是 2026-10-13 才开始记的）。")
        return "\n".join(L)

    # ── 总览 ──
    users = {}
    for r in chats:
        u = users.setdefault(r["uid"], {"name": r.get("name") or u"?", "msgs": 0,
                                        "at": 0, "img": 0, "pos": 0, "neg": 0,
                                        "chars": 0, "first": r["ts"], "last": r["ts"]})
        u["msgs"] += 1
        u["at"] += 1 if r.get("at") else 0
        u["img"] += 1 if r.get("img") else 0
        t = r.get("txt") or ""
        u["chars"] += len(t)
        u["pos"] += sum(1 for w in POS if w in t)
        u["neg"] += sum(1 for w in NEG if w in t)
        u["last"] = max(u["last"], r["ts"])

    L.append(f"- 群消息 **{len(chats)}** 条，来自 **{len(users)}** 位群友")
    L.append(f"- 机器人回复 **{len(replies)}** 次")
    L.append(f"- 被 @ / 被点名 **{sum(1 for r in chats if r.get('at'))}** 次")
    L.append(f"- 带图消息 **{sum(1 for r in chats if r.get('img'))}** 条")
    L.append("")

    # ── 使用最多（按 @ 次数）──
    L.append("## 谁用得最多（按 @ 次数）")
    L.append("")
    L.append("| 群友 | @我 | 发言 | 带图 | 字数 |")
    L.append("|---|---|---|---|---|")
    for u in sorted(users.values(), key=lambda x: (-x["at"], -x["msgs"]))[:10]:
        L.append(f"| {u['name']} | **{u['at']}** | {u['msgs']} | {u['img']} | {u['chars']} |")
    L.append("")

    # ── 满意度 ──
    L.append("## 谁最满意（正/负面话语统计）")
    L.append("")
    L.append("> ⚠️ **启发式指标**，不是问卷。从「谢谢/对了/厉害」和「错了/不对/太简」这类词里读，")
    L.append("> 只能看趋势，别当成精确分数。")
    L.append("")
    L.append("| 群友 | 正面 | 负面 | 净 | 备注 |")
    L.append("|---|---|---|---|---|")
    for u in sorted(users.values(), key=lambda x: -(x["pos"] - x["neg"]))[:10]:
        net = u["pos"] - u["neg"]
        note = ""
        if net >= 2: note = "😀 很满意"
        elif net == 1: note = "🙂 满意"
        elif net <= -2: note = "😟 多次不满"
        elif net == -1: note = "🤔 有小意见"
        L.append(f"| {u['name']} | {u['pos']} | {u['neg']} | **{net:+d}** | {note} |")
    L.append("")

    # ── 纠错反馈 ──
    corr = _sent_corrections(day)
    if corr:
        L.append(f"## 当天纠错反馈（{len(corr)} 条）")
        L.append("")
        for c in corr:
            L.append(f"- {c}")
        L.append("")

    # ── 话题分布 ──
    L.append("## 当天话题分布")
    L.append("")
    # ⚠️ 光看聊天文字**测不准** —— 群里提问基本都是「@e阶导 来[图片]」，
    #    真正的题目在图里，正文一个关键词都没有（第一版报告因此只剩 2 条）。
    #    改用**当天答疑 PDF 的文件名**：那是机器人按内容命名的，信息量大得多。
    corpus = [(r.get("txt") or "") for r in chats]
    titles = _day_pdf_titles(day)
    corpus += titles
    hits = []
    for topic, kws in TOPICS.items():
        n = sum(1 for c in corpus if any(k in c for k in kws))
        if n:
            hits.append((n, topic))
    if hits:
        for n, topic in sorted(hits, reverse=True):
            bar = "█" * min(30, n)
            L.append(f"- {topic:<12} {bar} {n}")
    else:
        L.append("- （没识别出明显话题）")
    L.append("")

    # ── token / 费用 ──
    try:
        import usage
        L.append("## 用量与费用")
        L.append("")
        rep = usage.report(1)
        for line in rep.splitlines():
            if day in line or "合计" in line or "日均" in line:
                L.append("```")
                L.append(line.rstrip())
                L.append("```")
                break
        d = json.loads((Path(config.STATE_DIR) / "usage.json").read_text(encoding="utf-8"))
        rec = (d.get("days") or {}).get(day)
        if rec:
            calls = sum(rec.get("calls", {}).values())
            L.append(f"- 调用 **{calls}** 次 ｜ 输入 **{rec.get('prompt_tokens',0):,}** tok "
                     f"｜ 输出 **{rec.get('completion_tokens',0):,}** tok")
            L.append(f"- 缓存命中 **{rec.get('cache_hit',0):,}** "
                     f"（{rec.get('cache_hit',0)/max(1,rec.get('prompt_tokens',1))*100:.0f}%）")
            top = sorted((rec.get("calls") or {}).items(), key=lambda x: -x[1])[:3]
            L.append("- 线路：" + " ／ ".join(f"{k} {v} 次" for k, v in top))
        L.append("")
    except Exception as exc:
        log(f"用量段落失败：{exc!r}", "WARN")

    # ── 我自己的分析 ──
    L.append("## 我的观察（自动分析）")
    L.append("")
    obs = []
    # 僵尸粉：@ 了但发言很少 / 或只发表情
    silent = [u for u in users.values() if u["at"] >= 3 and u["chars"] < 20]
    if silent:
        obs.append(f"- 💤 **{len(silent)} 位群友 @ 我好几次但几乎不打字**（"
                   + "、".join(u["name"] for u in silent[:4]) + "）—— 多是直接甩图，正常")
    # 只发图比例
    imgr = sum(1 for r in chats if r.get("img")) / max(1, len(chats))
    if imgr > 0.25:
        obs.append(f"- 🖼 **带图消息占 {imgr*100:.0f}%** —— 群里以「甩图问题」为主，"
                   f"识图质量直接决定体感，手写那条路（Gemini）要盯紧")
    # 负面集中的
    bad = [u for u in users.values()
           if u["neg"] > u["pos"] and u["neg"] >= 2
           and not any(b in (u["name"] or "") for b in _BOT_NAMES)]
    if bad:
        obs.append("- ⚠️ **负面反馈集中在：" + "、".join(u["name"] for u in bad[:4])
                   + "** —— 这几位说的题值得单独回看，多半是真错了")
    # 深夜活跃
    late = sum(1 for r in chats if time.localtime(r["ts"]).tm_hour in (23, 0, 1))
    if late > len(chats) * 0.15:
        obs.append(f"- 🌙 **{late} 条消息在 23:00~02:00** —— 深夜是活跃高峰，"
                   f"这个时段要保证不掉线")
    # 冷清
    if len(chats) < 30:
        obs.append("- 📉 **当天很冷清**（<30 条）—— 可能是没活动，也可能是我太安静了")
    if not obs:
        obs.append("- 当天数据没什么异常")
    L.extend(obs)
    L.append("")
    L.append("---")
    L.append(f"*报告生成时间：{time.strftime('%Y-%m-%d %H:%M')}*")
    return "\n".join(L)


def save(day: str = "") -> Path | None:
    """生成并存盘，返回文件路径。"""
    day = day or time.strftime("%Y-%m-%d")
    txt = build(day)
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        f = _DIR / f"{day}.md"
        f.write_text(txt, encoding="utf-8")
        log(f"已生成日报：{f.name}")
        return f
    except OSError as exc:
        log(f"写盘失败：{exc}", "WARN")
        return None


# ── 每天定时生成（挂在 tick 上）──
_last_day = ""


def maybe_run(now: float | None = None) -> None:
    global _last_day
    if not getattr(config, "REPORT_ENABLED", True):
        return
    now = now if now is not None else time.time()
    lt = time.localtime(now)
    day = time.strftime("%Y-%m-%d", lt)
    if _last_day == day:
        return
    if (lt.tm_hour, lt.tm_min) < (getattr(config, "REPORT_HOUR", 23),
                                  getattr(config, "REPORT_MINUTE", 30)):
        return
    _last_day = day
    save(day)
    msglog.cleanup()


def stats() -> str:
    fs = sorted(_DIR.glob("*.md")) if _DIR.exists() else []
    return f"{len(fs)} 份日报" + (f"，最近 {fs[-1].stem}" if fs else "")
