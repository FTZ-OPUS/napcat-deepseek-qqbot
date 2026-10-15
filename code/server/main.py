#!/usr/bin/env python3
"""-e阶导- 主循环。

设计：
  - 轮询 get_group_msg_history（每 2 秒，取最近 30 条）
  - message_id 不单调，所以用「已见集合」去重，绝不用大小比较
  - 首次启动把历史消息全部标记已读，避免回复陈年旧账
  - 回复前先判断触发条件 → 冷却检查 → "看了不回" → 反应延迟 → 问模型 → 分段发送
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import time
import traceback
import uuid
from pathlib import Path

import brain
import config
import daily
import msglog
import report
import politics_push
import feedback
import humanize
import latexrender
import media
import onebot
import pdf2md
import persona
import pmchat
import rampup
import safety
import tablecheck
import throttle
import vision

# 出现这些词，说明大概率是数学问题
MATH_WORDS = [
    "积分", "极限", "导数", "微分", "矩阵", "行列式", "级数", "收敛", "证明", "求导",
    "方程", "函数", "概率", "期望", "向量", "特征值", "傅里叶", "泰勒", "洛必达",
    "定理", "不等式", "组合", "数列", "几何", "三角", "对数", "复数", "拓扑",
    "群论", "数论", "素数", "Manim", "manim", "公式", "推导",
]

# 这些说法配合「被点名」才算「要求 PDF」
PDF_HINTS = ("pdf", "发文件", "要文档", "导出文档", "排版文件")

# 被点名但模型仍不肯接话时的兜底 —— 保证「@ 必回」这个承诺不落空
MENTION_FALLBACK = ["在的，你说", "嗯？我在", "怎么了？", "在，说吧"]

# 发了 PDF 但模型没给收尾话时的兜底（保证一定会 @ 提问者说一声）
# ⚠️ 长请求的「先吱一声」话术（车主 2026-10-12 定）
#
# 为什么需要：抄题复检 + 详细解答实测要 15~46 秒，这期间机器人**一个字都不发**，
# 群里只能干等，会以为它没理人。先发一句人话，让群友知道「它在认真看」。
# 车主原话：「你就发一句让我想想，正在认真识别图片就可以了…
#            我识别有点太机械化了，你就说正在认真看图」
#
# 口径：说人话、别机械；**只在真的会慢的时候发**（现在只有复检那条路径），
# 普通请求 1 秒就答完了，再吱一声就是废话。
THINKING_LINES = [
    "让我想想，这张图我仔细看看",
    "稍等，我把图看仔细点",
    "嗯…这题我认真看一下图",
    "让我先仔细看看图，马上",
    "这图我得看细一点，稍等一下",
    "等一下哈，我把题面看清了再答",
]

# ⚠️ 什么都没发出去时的**诚实**话术（车主 2026-10-12 定）
#
# 真实事故：渲染失败后收尾话还说"发你了 / PDF 发出来了"，群友只能再催一遍。
# 原则：**宁可承认失败，也不说假话** —— 信任比功能缺失贵得多。
FAIL_CLOSING = [
    "不好意思，这份没排出来（我这边渲染失败了），你再说一声我重发一遍",
    "刚才那份没生成成功，麻烦你再发我一次，我重做",
    "这份我排版的时候挂了，没发出来。你再 @ 我一次我重试",
]

CLOSING_FALLBACK = ["过程发你了，你看看", "弄好了，有问题再喊我", "发你了"]


# 群里流行的「喊 PDF 机器人」格式：**开头 /pdf + 引用一张题目图**。
# 这不是 @ 某个具体的人，而是"喊所有会做 PDF 的机器人"（凛音同学等都会接）。
# 车主 2026-10-12 定：**这种也要接** —— 同一张卷子同时答，谁答得好群友用谁。
# (?![a-zA-Z0-9]) 是必要的：否则「/pdfx」「/pdf2」这种也会被当成 /pdf。
# 后面跟中文/标点/空白/行尾都算合法（"/pdf详细解答" 这种没空格的也认）。
PDF_CMD_RE = re.compile(r"^[\s]*[/／]\s*pdf(?![a-zA-Z0-9])", re.I)


# ── 点名专用线的触发（车主 2026-10-14 定）──
# 群友**说了模型名**才路由过去（「用 opus」「claude 算一下」「gpt 6.1 sol 看看」），
# 其余一律走 DeepSeek 保速度。
#
# ⚠️ 之前写过一长串模糊词（难题/好难/换个模型/用最强的…），**太容易误触发** ——
#    普通题被升到点名线就要等 10~90 秒。车主明确要求：**只认模型名**。
# 触发词定义在 config.NAMED_PROVIDERS 里（每条线自带 hints），这里只做匹配。


# ── 点名线的「播报」文案（车主 2026-10-14 定）──
# 群友点名后先说一句「已路由给 XX 回答」，再说答案 ——
# 让群友知道**确实是那个模型在答**，比闷声回一句更真实。
ROUTE_LINES = {
    # ⚠️ 这些话说在**调用成功之后**（见 14-41 的说明），所以用「算好了/结果是」这种
    #    事后口径，不能用「稍等/马上」——那会变成"说完才去算"，跟事实不符。
    "Opus5.5": (
        "这条是 Opus 5.5 算的 —— 慢是慢点，但稳",
        "喏，换成 Opus 5.5 跑了一遍，结果在下面",
        "用 Opus 5.5 给你算的，你看看",
    ),
    "GPT6.1SOL": (
        "已经成功路由给 GPT 6.1 SOL 了，答案在下面",
        "已路由给 GPT 6.1 SOL 作答，你看结果",
        "成功转给 GPT 6.1 SOL 了，稍等我贴答案",
    ),
    "GPT6ASTRA": (
        "已经成功路由给 GPT 6 ASTRA 了，答案在下面",
        "已路由给 GPT 6 ASTRA 作答，你看结果",
        "成功转给 GPT 6 ASTRA 了，稍等我贴答案",
    ),
}

# 点名线挂了、降级到普通线路时说的话 —— **必须说实话**，不能让群友以为
# 是 Opus 答的。降级本身没问题，骗人才有问题。
FALLBACK_LINES = (
    "{name} 那边这会儿连不上，我先用常规线路给你答了",
    "{name} 暂时不通，先用常规线路答，你看结果就行",
)


def announce_named_line(group_id: int, line: dict, answered_by: str) -> str:
    """**调用成功之后**才播报。返回 PDF 里要印的小字（没走点名线就返回空串）。

    车主 2026-10-14 定（这是最要紧的一条）：
    > 「你要确保那个 API 能调用成功……**确保这次能请求成功以后，你再回复那句话**……
    >  不要骗人」

    ⚠️ 所以播报**必须挪到 `brain.decide()` 成功返回之后** —— 之前是调用前就发，
    一旦那条中转站 429/404 挂了、自动降级到 DeepSeek，群里就会看到
    「这题请 Opus 5.5 出手」而答案其实是 DeepSeek 给的 —— **骗人**。
    （跟"说假话：文件发了"是同一类错误。）

    现在分三种情况：
      · 点名线成功      → 播报「这条是 Opus 5.5 算的」，PDF 印小字
      · 点名线挂了降级   → 播报「XX 那边连不上，先用常规线路答了」（说实话），PDF 不印小字
      · 没点名          → 不播报
    """
    name = line.get("name") or ""
    model = line.get("model") or ""
    pretty = {"Opus5.5": "Opus 5.5", "GPT6.1SOL": "GPT 6.1 SOL",
              "GPT6ASTRA": "GPT 6 ASTRA"}.get(name, model or name)

    if answered_by != name:
        # 点名线没答成 → **说实话**，别让群友以为是它答的
        try:
            spoken = random.choice(FALLBACK_LINES).format(name=pretty)
            humanize.sleep_typing(spoken)
            onebot.send_group_text(group_id, spoken)
            log(f"  📣 点名线未答成，已如实播报：{spoken}")
        except Exception as exc:
            log(f"  降级播报失败（不影响回答）：{exc!r}", "WARN")
        return ""

    try:
        opts = ROUTE_LINES.get(name)
        spoken = random.choice(opts) if opts else f"这条是 {pretty} 算的"
        humanize.sleep_typing(spoken)
        onebot.send_group_text(group_id, spoken)
        log(f"  📣 已播报（确认由「{answered_by}」作答）：{spoken}")
    except Exception as exc:
        log(f"  路由播报失败（不影响回答）：{exc!r}", "WARN")
    return f"本题由 {pretty} 协助作答"


def pick_named_line(text: str) -> dict | None:
    """群友点名了吗？点名了就返回那条线。

    ⚠️ 大小写不敏感（opus / OPUS / Claude / GPT 都认）。
    ⚠️ 多条命中时**取最先出现的那条**（按文本里的位置），符合直觉：
       「用 claude 对比一下 gpt」→ 走 claude。
    """
    s = (text or "").lower()
    best, pos = None, 10 ** 9
    for line in getattr(config, "NAMED_PROVIDERS", []) or []:
        for h in (line.get("hints") or ()):
            i = s.find(h)
            if i != -1 and i < pos:
                best, pos = line, i
    return best


def providers_for(text: str) -> tuple:
    """按群友的话选线路。返回 (线路列表, 是否走了点名线)。

    ⚠️ 走点名线时**把普通线路接在后面兜底** —— 那两条中转站线路实测会 429/404，
    不能只留一条。
    """
    line = pick_named_line(text)
    if line:
        return [line] + list(config.PROVIDERS), True
    return list(config.PROVIDERS), False


def is_pdf_command(text: str, has_image: bool = False) -> bool:
    """是不是「/pdf …」这种公开喊话格式？**以 /pdf 开头就接，不看有没有图。**

    为什么认这个格式：群友用它"喊"群里的 PDF 机器人，**不 @ 任何具体的人**。
    以前只认 @，所以这种请求全被 MENTION_ONLY 挡掉了；而凛音同学会回 —— 等于白送用户。

    ⚠️ **2026-10-13 车主更新：去掉「必须带图」这个条件。**
    真实场景（群里有截图）：
        群友：「/pdf 中国近代史到新中国成立前马克思主义中国化的进程」  ← 纯文字，没图
        凛音同学：答了
        我们：没答 ❌  ← 白送用户
    车主原话：「**只要出现了 /pdf 开头的问题，你都要回答**，那个凛音同学都会自动回答。」

    原来要求带图是怕"空喊"，但 `/pdf` 本来就是群里的**约定暗号** ——
    开头写了 `/pdf` 就是明确在喊，纯文字（比如让总结知识点）也一样要接。
    `has_image` 参数保留只为兼容调用方，**不再参与判断**。

    ⚠️ 只在**没有 @ 别人**时才生效（addressed_to_others 那道门禁照旧）：
    别人明确 @ 了某个机器人，我们不去抢。
    """
    return bool(PDF_CMD_RE.match(text or ""))


def wants_pdf(text: str, mentioned: bool = False) -> bool:
    """判断这条消息是不是在「跟机器人要一份 PDF」。

    ⚠️ 必须同时满足两个条件：① 点名了机器人 ② 内容提到 pdf 之类。
    只满足②绝对不能触发 —— 出过一次事故：群里有人聊天时说
    「凛音别上来就甩PDF啊」，机器人把这句话当成"要 PDF"，
    在没有题目的情况下硬出了一份内容完全对不上的文档，非常尴尬。

    PDF 是一种交付形式，只有「对着机器人提出」才算要求。
    没被点名时，顶多是在讨论 PDF 这个格式而已。
    """
    if not mentioned:
        return False
    lowered = text.lower()
    return any(h in lowered for h in PDF_HINTS)

STATE_FILE = config.STATE_DIR / "cursor.json"
MAX_SEEN = 5000

# ---- 机器人自己的名字（动态识别，别写死）----
# 车主改过好几次名（-e阶导- → e阶导 …），写死字符串的话改一次名它就不认识自己了。
# 所以启动时和每隔半小时都从 NapCat 抓一次当前昵称/群名片，自动派生写法。
BOT_NAMES: set[str] = set(config.BOT_ALIASES)
# 当前「主名字」（群名片优先），用来替换人格提示词里的昵称占位符 ——
# 不然模型会拿写死的旧名字自我介绍。
BOT_DISPLAY_NAME: str = persona.DEFAULT_BOT_NAME

# PDF 解题任务的防刷冷却（内存态即可：重启清零无害）
_pdf_task_last: dict[int, float] = {}

# 「没提任务、就想让人读读」的默认请求 —— 车主 2026-10-11 定的：
# 此时不当解题任务办，写一份阅读总结报告（像人写的，不抄原文不复述推导）
_PDF_SUMMARY_REQUEST = (
    "请通读这份资料，写一份阅读总结报告：这份资料讲的是什么、整体结构和主线、"
    "核心内容与主要结论、有哪些值得注意或精彩的地方。像人写给朋友的阅读报告，"
    "简明扼要，篇幅大半页到一页即可——不要照抄原文，不要复述每一步推导，"
    "也不要把全文再转述一遍。输出文件名用「阅读总结」"
)
# 只说「阅读一下」「看看」这类短语的 = 也是想让人读读，归总结模式；
# 带了具体对象/任务的（"每页只做第一题"）不匹配，照常走解题
_PDF_READ_ONLY_RE = re.compile(
    r"^(?:请|帮忙|麻烦)?(?:阅读|读一下|读一读|看看|看一下|总结一下|概述一下|概括一下)"
    r"[一上下吧呗哈～!！。，,.\s]*$")
_names_refreshed_at = 0.0
NAMES_REFRESH_INTERVAL = 1800


def _name_variants(raw: str) -> set[str]:
    """从一个名字派生「别人可能怎么叫它」的各种写法。

    "-e阶导-"  →  {"-e阶导-", "e阶导", "阶导"}
    "e阶导"    →  {"e阶导", "阶导"}
    """
    out: set[str] = set()
    name = (raw or "").strip()
    if not name:
        return out
    out.add(name)
    # 去掉首尾的装饰符号
    core = re.sub(r"^[\s\-_~·.。、|]+|[\s\-_~·.。、|]+$", "", name)
    if core:
        out.add(core)
    # 再去掉开头的拉丁字母/数字："e阶导" → "阶导"
    stripped = re.sub(r"^[A-Za-z0-9]+", "", core).strip()
    if stripped:
        out.add(stripped)
    # 抽最后一段连续汉字 —— 名字里常混着字母和装饰，
    # 但群友实际叫的时候往往只叫中文那部分。
    # "e的e阶导" → "阶导"；"小e阶导" → "阶导"
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", core):
        out.add(run)
    return {x for x in out if len(x) >= 2}


def refresh_bot_names(force: bool = False) -> None:
    """从 NapCat 抓机器人当前的名字，更新「被点名的写法」集合 + 主名字。"""
    global BOT_NAMES, BOT_DISPLAY_NAME, _names_refreshed_at
    now = time.time()
    if not force and now - _names_refreshed_at < NAMES_REFRESH_INTERVAL:
        return
    _names_refreshed_at = now

    names: set[str] = set(config.BOT_ALIASES)
    # 群名片是群友实际看到并照着叫的名字，优先级最高
    display = ""
    for gid in config.ALLOWED_GROUPS:
        try:
            data = onebot.call("get_group_member_info",
                               group_id=gid, user_id=config.BOT_QQ).get("data") or {}
            card = str(data.get("card") or "").strip()
            if card and not display:
                display = card
            names |= _name_variants(card)
            names |= _name_variants(str(data.get("nickname") or ""))
        except Exception as exc:
            log(f"抓群名片失败（群 {gid}）：{exc}", "WARN")
    try:
        names |= _name_variants(str(onebot.get_login_info().get("nickname") or ""))
    except Exception as exc:
        log(f"抓昵称失败（不影响兜底）：{exc}", "WARN")

    if display and display != BOT_DISPLAY_NAME:
        BOT_DISPLAY_NAME = display
    if names and names != BOT_NAMES:
        BOT_NAMES = names
        log(f"名字识别集：{sorted(names)}｜自我介绍用「{BOT_DISPLAY_NAME}」")


STYLE2_HINT_LIST = list(config.STYLE2_HINTS)


# 批改类请求的触发词。这类任务天生要一份能留档的报告，不该走「纯打字」。
GRADE_HINTS = ["批改", "批阅", "订正", "改一下", "看看对不对", "检查一下",
               "检查下", "帮我看看", "对不对"]


# ── 抄题复检的触发词（车主 2026-10-12 定）──
# 为什么只在这两种情况做：复检要多跑一次视觉调用、慢 1.5~2 秒。
# 而机器人最大的卖点是「快」—— 不能对所有图片请求都开。
DETAIL_HINTS = ("详细", "严谨", "完整", "一步步", "逐步", "认真", "细致",
                "严格", "别简略", "不要简略", "别省略", "写全", "说清楚")
REASK_HINTS = ("解错", "做错", "算错", "不对", "错了", "有问题", "重新",
               "再解", "重做", "再算", "上一版", "上次", "你刚才", "刚才那")


# 「对着这份 PDF 改」的意图词（车主 2026-10-12 定）
#
# 真实事故：群友引用机器人发的《批改过程.pdf》说「没说二阶导连续啊 你咋用了 快重写」，
# 机器人**只回了一句文字、没出新 PDF** —— 因为 PDF 任务里传的是 pdf_requested=False，
# 模型按体量自选，觉得几句话能说清就选打字了。
# **但群友引用一份 PDF 让你改，期待的就是一份新 PDF。**
PDF_REVISE_HINTS = ("改", "重写", "重做", "重新", "订正", "修正", "完善", "补充",
                    "写完整", "写详细", "太简", "新pdf", "新 pdf", "再发一份",
                    "校对", "修一下", "更新")


# 「群友要图片」的信号（车主 2026-10-12 定）
# 命中就把解答改成**竖长图**（longimg），而不是 PDF —— 群里直接翻着看，不用下载。
# ⚠️ 只认"要图片当输出"的说法，不是看见「图片」两个字就触发（否则聊天里提一句
#    「这张图片」都会被误判）。
LONGIMG_HINTS = (
    # 车主指定的标准说法（2026-10-12）：「本题解答需要图片」
    "本题解答需要图片", "解答需要图片", "需要图片",
    "长图", "图片形式", "用图片", "要图片", "发图片", "图片吧", "图片发",
    "发个图", "发张图", "截图", "别发pdf", "不要pdf", "不用pdf", "别用pdf",
    "以图片", "出图片", "图上", "做成图", "画成图", "图发我",
)
# 要发长图之前先回一句（车主 2026-10-12 定：「回他一句说图片正在渲染，然后直接发图」）
IMG_WORKING_LINES = (
    "图片正在渲染，稍等一下",
    "图我在画了，马上发你",
    "正在渲染图片，稍等哈",
    "这图画起来要点时间，马上好",
)


def wants_long_image(text: str) -> bool:
    """群友是不是要**图片形式**的输出（而不是 PDF）？"""
    low = (text or "").lower()
    return any(h in low for h in LONGIMG_HINTS)


def wants_pdf_revise(text: str, mentioned: bool) -> bool:
    """这条引用 PDF 的消息，是不是在让机器人**改这份 PDF**？

    命中就让 PDF 任务强制出 PDF（不许只回文字）—— 见 PDF_REVISE_HINTS 的注释。
    """
    if not mentioned:
        return False
    low = (text or "").lower()
    return any(h in low for h in PDF_REVISE_HINTS)


def wants_recognition_recheck(text: str, has_image: bool,
                              mentioned: bool) -> tuple[bool, str]:
    """要不要对图片额外做一次「抄题复检」？（返回 (要不要, 为什么)）

    只有两种情况（车主 2026-10-12 定）：

      ① **要求「详细/严谨」解题** —— 这种答案要经得起推敲，值得多花一次视觉调用
      ② **二次追问**（说上次解错了 / 让重做）—— 上次很可能就是识别抄错了，
         这是最强的"该复查一遍"信号

    为什么不全开：复检 = 多一次视觉调用 + 慢 1.5~2 秒，而「快」是机器人的核心卖点。
    针对的是两个真实反馈：「好多下标识别的不对」「臆加了题目没写的条件」。

    必须同时**被点名 + 带图**才成立 —— 没图的纯文字不存在"识别"问题。
    """
    if not (mentioned and has_image):
        return False, ""
    t = text or ""
    if any(h in t for h in REASK_HINTS):
        return True, "二次追问（上次可能识别抄错了）"
    if any(h in t for h in DETAIL_HINTS):
        return True, "要求详细/严谨解答"
    return False, ""


def wants_grading_report(text: str, mentioned: bool, has_image: bool) -> bool:
    """是不是「被点名 + 带图 + 让批改作业」？

    为什么要代码强制、而不用提示词：提示词里把规则写了两遍，实测模型
    **仍然选纯打字**（2026-10-11 23:27 实测：批改请求 → 4 条文字 + 1 张图，PDF×0）。
    而 `pdf_requested=True` 会注入 PDF_HINT（"必须输出 mode=doc"），
    这个机制实测 100% 有效 —— 所以复用同一个出口。

    为什么批改必须出报告（车主 2026-10-11 定的）：
    1. 批改报告群友要留档、要打印、要逐条对照
    2. 纯打字那条路会被拆成好几条消息刷屏 ——
       实测一次 200 字的批改被拆成 5 条消息，比一份 PDF 烦人得多
    """
    if not (mentioned and has_image):
        return False
    return any(h in (text or "") for h in GRADE_HINTS)


def wants_style2(text: str) -> bool:
    """群里有人点名要「风格 2」的排版吗？

    风格 2 = 题目/定理用彩色框框住，**解答保持白底黑字不框**。
    不说就用默认风格（全白底黑字）。车主 2026-10-10 定的。
    """
    low = (text or "").lower()
    return any(h.lower() in low for h in STYLE2_HINT_LIST)


def is_name_called(text: str) -> bool:
    """文本里有没有叫机器人的名字（不带 @ 段的那种叫法）。"""
    return any(n in text for n in BOT_NAMES)


# ---------------------------------------------------------------- 日志

def log(msg: str, level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 状态

class State:
    def __init__(self) -> None:
        raw = {}
        if STATE_FILE.exists():
            try:
                raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                log("状态文件损坏，重新开始", "WARN")
        self.seen_order: list = list(raw.get("seen", []))
        self.seen: set = set(self.seen_order)
        self.seeded: set = set(raw.get("seeded", []))
        self.last_reply: dict = {int(k): v for k, v in (raw.get("last_reply") or {}).items()}
        self.last_cold_sticker: float = float(raw.get("last_cold_sticker", 0) or 0)
        # 最近「主动闲聊」发言的时间戳，用于小时级限流（被点名的不计）
        self.chat_times: list = [float(x) for x in (raw.get("chat_times") or [])]

    def mark(self, message_id) -> None:
        if message_id in self.seen:
            return
        self.seen.add(message_id)
        self.seen_order.append(message_id)

    def save(self) -> None:
        if len(self.seen_order) > MAX_SEEN:
            drop = self.seen_order[:-MAX_SEEN]
            self.seen_order = self.seen_order[-MAX_SEEN:]
            self.seen.difference_update(drop)
        payload = {
            "seen": self.seen_order,
            "seeded": sorted(self.seeded),
            "last_reply": {str(k): v for k, v in self.last_reply.items()},
            "last_cold_sticker": self.last_cold_sticker,
            # 只留最近 1 小时的，文件不会无限涨
            "chat_times": [t for t in self.chat_times if t > time.time() - 3600],
        }
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)


# ---------------------------------------------------------------- 触发判断

def is_question(text: str) -> bool:
    if text.rstrip().endswith(("?", "？")):
        return True
    return any(h in text for h in ("吗？", "呢？", "怎么", "为什么", "如何", "请问", "求"))


def addressed_to_others(message, text: str) -> bool:
    """这条消息是不是在跟别人说话？

    两种情况都算：
      1. @ 了别人（@ 段里的 QQ 不是机器人自己）
      2. 纯文本里点了别的机器人/群友的名（比如"凛音别上来就甩PDF啊"）

    第 2 条是事故教训：那句话里"凛音"是纯文本不是 @ 段，
    结果机器人既没被点名、又看到"PDF"就冒出来发文档了。
    """
    bot = str(config.BOT_QQ)
    for qq in onebot.at_targets(message):
        # @全体成员 不算"跟别人说话"
        if qq not in (bot, "all", "0"):
            return True
    for name in config.IGNORE_NAMES:
        if name and name in text:
            return True
    return False


def is_image_only(text: str, has_image: bool) -> bool:
    """这条消息是不是「一张裸图」—— 只有图，一个字都没打。

    车主明确要求：裸图一律不主动接，只有被 @ 才回。
    （带字的图不算裸图，那是一条正常聊天消息，可以走闲聊插嘴。）
    """
    if not has_image:
        return False
    cleaned = text.replace("[图片]", "").replace("[表情]", "").strip()
    return not cleaned


# ══════════════ 回复策略闸门（车主 2026-10-14 定）══════════════
# **背景**：账号刚经历"冻结 → 申诉 → 全端强制下线"，现在只恢复最小功能。
# 车主原话：
#   「现在是有人艾特你，你再回答……**艾特你且说了用 GPT 或 gpt，你再调那个线路**。
#    艾特你不说用 GPT，就是常规那个也不要用，你就**只负责答 GPT 的难题**就可以了。
#    那个 PDF 跟之前一样，下面要标注是 GPT 做的……
#    **别人问你政治的题目，你可以用 Deepseek 那个答一下**……
#    别人**不艾特包括 /pdf 都不回**，目前先是这样。」
#
# 所以现在只有两条通路：
#   ① 被 @ 且文本里出现 gpt  → 走 GPT 6.1 SOL
#   ② 被 @ 且判定为政治题     → 走 DeepSeek（常规线路）
# 其余**一律不发声** —— 包括普通 @、包括 /pdf 喊话。
POLITICS_WORDS = (
    "政治", "马原", "毛中特", "史纲", "思修", "考研政治",
    "马克思主义", "唯物", "唯心", "辩证法", "认识论", "矛盾", "实践",
    "剩余价值", "资本", "商品", "剥削", "生产力", "生产关系", "上层建筑", "经济基础",
    "社会主义", "中国特色", "五位一体", "四个全面", "主要矛盾", "改革开放",
    "近代史", "党史", "辛亥", "抗日", "新中国成立", "巴黎公社", "十月革命",
    "道德", "法治", "理想信念", "核心价值观", "法律", "权利", "义务",
)


def looks_political(text: str) -> bool:
    """是不是政治题？先看关键词，再看考点库能不能命中。"""
    s = text or ""
    if any(w in s for w in POLITICS_WORDS):
        return True
    try:
        import polib
        return bool(polib.relevant(s))
    except Exception:
        return False


# ⭐ 在线测试的识别（车主 2026-10-15 定）
#   @ + 这类短语、且没有别的实质内容 → 回一句「在线」让车主确认机器人活着。
#   ⚠️ 必须是**整句就是这类词**，避免把「测试一下这个极限」这种正常提问误判。
_PING_RE = re.compile(
    r"^[\s,，。.、!！?？~～]*"
    r"(测试[一下]*|测一下|测测|检查一下|在吗|在么|在线吗|在线么|还在吗|"
    r"还活着吗|活着吗|ping|test|hi|hello)"
    r"[\s,，。.、!！?？~～]*$",
    re.IGNORECASE,
)


# 剥掉开头的 @提及（@1234567 / @昵称）和不可见字符
_PING_STRIP = re.compile(r"^(?:\s*@[^\s@]+)+\s*")


# ⭐ 「测试连通性」——真去云端挨个打一遍所有 API，回一份报告（车主 2026-10-15 定）
#   车主：「我如果对你说句测试连通性，你能不能直接在云端测一下，然后回复我……
#          回复我各个 API 接口的连通性，并标注它们是什么模型？」
_CONN_RE = re.compile(
    r"^[\s,，。.、!！?？~～]*"
    r"(测试?连通性|连通性测试?|测连通|测一下连通性|检查连通性|"
    r"测试?线路|线路体检|体检|健康检查)"
    r"[\s,，。.、!！?？~～]*$",
    re.IGNORECASE,
)


def _is_conncheck(text: str) -> bool:
    """是不是「测试连通性」。"""
    s = _PING_STRIP.sub("", (text or "").strip()).strip()
    if not s or len(s) > 16:
        return False
    return bool(_CONN_RE.match(s))


def _is_ping(text: str) -> bool:
    """是不是「在线测试」而不是一个真问题。

    ⚠️ 2026-10-15 踩坑：闸门收到的 text 是 **带 @ 前缀** 的
    （形如 "@<BOT_QQ> 测试"），正则要求整句匹配就会失败 ——
    所以先把开头的 @提及 剥掉再判。
    """
    s = _PING_STRIP.sub("", (text or "").strip()).strip()
    if not s or len(s) > 16:          # 太长的一律当正常提问
        return False
    return bool(_PING_RE.match(s))


def reply_policy(text: str, mentioned: bool, to_others: bool,
                 has_image: bool = False, keyword_text: str | None = None,
                 pdf_requested: bool = False) -> dict:
    """现在这条消息该不该回、走哪条线路。返回 {"allow":bool, "kind":str, "why":str}。

    ⚠️ 这个函数是**唯一的决策入口** —— 所有回复路径都必须先过它。
    以后再改策略（比如放开普通 @），只改这里一个地方。

    has_image=True 且文字判不出来时，返回 need_ocr=True ——
    调用方应该**先把图识出来**，再拿识别结果重新判一次。
    （车主 2026-10-14：「每次问你题目，你记得看一下那个图片…
      如果是政治题就答」—— 政治题常常只发图不写字。）
    """
    # ⭐ 模式一（2026-10-15 车主重新定义，逐步恢复）：
    #   车主原话：「模式一除了那一条不要回复，其他都回复。就是 /PDF 你就会回答，
    #             哪怕没有 at。现在还是只有艾特你才会打，只不过不说 GPT
    #             你就用 Deepseek 打就行了。」
    #
    #   翻译成规则：
    #     ✅ @ + astra         → GPT 6 ASTRA
    #     ✅ @ + gpt           → GPT 6.1 SOL
    #     ✅ @ + 政治题         → DeepSeek
    #     ✅ @ + 其它（没说 gpt）→ **DeepSeek**（模式二是不回，这里改成答）
    #     ✅ /pdf 喊话（没 @）   → 回答
    #     🚫 没 @ 且不是 /pdf    → 不回（「那一条不要回复」）
    #     🚫 @ 了别的机器人      → 不抢
    if getattr(config, "BOT_MODE", 2) == 1:
        _kt1 = keyword_text if keyword_text is not None else text
        if to_others:
            return {"allow": False, "kind": "", "need_ocr": False,
                    "why": "模式一：在 @ 别的机器人，不抢"}
        # ⚠️ 2026-10-15 车主更正：「我说没有艾特，只有一个 /PDF，**不用回**。
        #    之前你忘了回的，就是现在也不用补回，从现在开始就行，
        #    那种 /PDF 千万不要回。」
        #    → 所以模式一也是：**没 @ 就一律不回**（/pdf 喊话也不例外）。
        if not mentioned:
            return {"allow": False, "kind": "", "need_ocr": False,
                    "why": "模式一：没被 @"}
        _s1 = (_kt1 or "").lower()
        if "astra" in _s1:
            return {"allow": True, "kind": "astra", "need_ocr": False, "why": "点名要 ASTRA"}
        if "gpt" in _s1:
            return {"allow": True, "kind": "gpt", "need_ocr": False,
                    "why": "点名要 GPT（默认 6.1 SOL）"}
        if looks_political(text):
            return {"allow": True, "kind": "politics", "need_ocr": False,
                    "why": "政治题，走常规线路"}
        # 其余一律用常规线路（DeepSeek 优先）答 —— 这是模式一和模式二最大的差别
        return {"allow": True, "kind": "normal", "need_ocr": False,
                "why": "模式一：常规问题，走 DeepSeek"}
    # ── 以下只在模式二（受限）生效 ──
    if not mentioned:
        return {"allow": False, "kind": "", "why": "模式二：没被 @（现在 /pdf 喊话也不接了）"}
    if to_others:
        return {"allow": False, "kind": "", "why": "在 @ 别的机器人，不抢"}
    # ⚠️ 判断「点名哪个模型」只认 keyword_text（群友自己打的那句话）；
    #    没传就退回用 text（兼容旧调用）。
    #    政治题那一路仍然看完整的 text —— 因为政治题可能整张图都是题面。
    # ⭐ 在线测试（车主 2026-10-15 定）：
    #   「我要艾特你，如果说句测试没有问问题，你也回答一下在线，行吧？在群里，
    #     这样方便我判断，以后都这样。」
    #   所以：@ + 只有一句「测试」而没有实际问题时 → 直回一句「在线」。
    #   走**代码直回**（不调模型），秒回、零成本、永远不受线路故障影响。
    _kt = text if keyword_text is None else keyword_text
    if _is_conncheck(_kt):
        return {"allow": True, "kind": "conncheck", "why": "线路连通性体检"}
    if _is_ping(_kt):
        return {"allow": True, "kind": "ping", "why": "在线测试"}

    s = ((keyword_text if keyword_text is not None else text) or "").lower()
    # ⚠️ astra 必须**先判**：更具体的关键词优先。
    #    否则「用 astra 而不是 gpt」会被 gpt 抢先命中。
    if "astra" in s:
        return {"allow": True, "kind": "astra", "why": "点名要 ASTRA"}
    if "gpt" in s:
        return {"allow": True, "kind": "gpt", "why": "点名要 GPT（默认 6.1 SOL）"}
    if looks_political(text):
        return {"allow": True, "kind": "politics", "why": "政治题，走常规线路"}
    # 有图但文字判不出来 → 先识图再判一次（政治题常常只发图）
    if has_image:
        return {"allow": False, "kind": "", "need_ocr": True,
                "why": "文字看不出，需要先识图"}
    return {"allow": False, "kind": "", "why": "被 @ 但既没提 gpt 也不是政治题"}


def should_reply(text: str, mentioned: bool, params: dict,
                 image_only: bool = False, to_others: bool = False) -> tuple[bool, str]:
    """判断这条消息要不要接。

    ⚠️ 车主定的规矩（别改）：
        **只有被 @ 了才回**；@ 别人、或者点名别的机器人的消息，一律不参与。
        "被 @ + 让解题 / 要 PDF"是最典型的场景，但也包括 @ 之后的正常对话。
        没被点名时一律不主动搭话（闲聊插嘴默认关闭，见 config.IDLE_CHAT_ENABLED）。

    关于图片：图片不是触发条件。只有「引用图 + @ 机器人」才需要回，
    图片在被 @ 之后作为回答的素材（见 vision.extract 的回溯逻辑）。
    """
    if mentioned:
        return True, "被点名"
    # 车主规矩：只有被 @ 才回。没点名的一律不接 —— 管它是不是问句、有没有数学词。
    if config.MENTION_ONLY:
        return False, ""
    # 在跟别人说话 —— 不管内容是啥都别插嘴
    if to_others:
        return False, ""
    if image_only:
        return False, ""
    # 注意：wants_pdf 要求「被点名」才成立，而上面被点名已经返回了，
    # 所以这里不需要再判断 PDF —— 没被点名时说 PDF 只是聊天内容。
    if is_question(text) and random.random() < params["question"]:
        return True, "问句"
    if any(w in text for w in MATH_WORDS) and random.random() < params["math"]:
        return True, "数学相关"
    # 凌晨 idle 会被置 0，等于完全不主动搭话
    if params["idle"] > 0 and random.random() < params["idle"]:
        return True, "闲聊插嘴"
    return False, ""


def sender_name(msg: dict) -> str:
    sender = msg.get("sender") or {}
    return sender.get("card") or sender.get("nickname") or str(sender.get("user_id", "?"))


def build_context(history: list[dict], upto_index: int) -> str:
    start = max(0, upto_index - config.CONTEXT_SIZE)
    lines = []
    for m in history[start:upto_index]:
        text = onebot.message_text(m.get("message"))
        if text:
            lines.append(f"{sender_name(m)}: {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 发送

def send_parts(group_id: int, parts: list[dict], style: str = "",
               asker: str = "", model_note: str = "") -> tuple[bool, bool]:
    """发送片段。**返回 (有没有发出去任何东西, 有没有真的发出 PDF)。**

    ⚠️ 返回值必须被调用方用上 —— 收尾话要说"发你了"，只能基于**实际发出去的**，
    不能基于模型"计划要发什么"。真实事故（2026-10-12，一天三次）：

        11:26:26 [WARN] 所有片段都没发成功
        11:26:29 收尾：发你了              ← 假话
        11:27:13 群友：PDF发群里啊          ← 群友催了才发

    根因就是这里原来返回 None，收尾逻辑只能猜。
    """
    sent_any = False
    sent_doc = False          # 真的发出了 PDF（doc）—— 收尾话只看这个
    for idx, part in enumerate(parts[: config.MAX_PARTS]):
        # 🛡 风控节流（车主 2026-10-14 定，起因：号被冻结）——
        #    超限就礼貌收尾停下，不硬顶（104 份 PDF/天 是这次的直接教训）。
        _kind = "pdf" if part.get("kind") == "math" and part.get("mode") == "doc" else "msg"
        _ok, _why = throttle.allow(_kind)
        if not _ok:
            log(f"  🛡 触发节流，停止发送：{_why}", "WARN")
            try:
                onebot.send_group_text(group_id, throttle.refuse_line())
            except Exception:
                pass
            break
        if idx > 0:
            # 用随机间隔（1.2~4.0s）代替固定间隔 —— 匀速最像机器
            time.sleep(throttle.gap())

        if part["kind"] == "text":
            for j, chunk in enumerate(humanize.split_long_text(part["text"])):
                if j > 0:
                    time.sleep(humanize.gap_between_parts())
                delay = humanize.sleep_typing(chunk)
                from latexrender import strip_toolcomplaint as _stc
                _stc_chunk = _stc(chunk)
                if not _stc_chunk.strip():
                    throttle.note("msg")
                    continue
                onebot.send_group_text(group_id, _stc_chunk)
                sent_any = True
                log(f"  发送文字 {len(chunk)} 字（打字 {delay:.1f}s）")

        elif part["kind"] == "math":
            caption = part.get("caption", "")
            # 🏷 点名线的「小字标注」（车主 2026-10-14 定）——
            #    群友点名用了 Opus/GPT，就在**成品里**也留一行小字说明，
            #    比只在聊天里说一句更"算数"（车主原话：「这样更真实一点」）。
            _latex = part["latex"]
            if model_note:
                # ⚠️ 必须用 \parbox 包一层让它**自动换行** ——
                #    card 模式的页面只有 ~107pt 宽，一行长文字会被**横向裁掉**
                #    （实测「本题由 Opus 5.5 协助作答」在 card 下完全不显示）。
                _latex = _latex + (
                    "\n\n\\par\\vspace{2mm}\\begin{center}{\\scriptsize\\textcolor{gray}{"
                    "\\parbox{0.86\\linewidth}{\\centering "
                    + model_note + "}}}\\end{center}")
            try:
                path = latexrender.render(_latex, part["mode"], style=style)
            except Exception as exc:
                # ⚠️ 渲染失败 → **先让模型自己修一遍再重试**，别回头找群友重发。
                # 车主 2026-10-12 定：「我们办事最好只办一次，不要让群众跑几趟。」
                # 真实事故：渲染挂了 → 机器人回「麻烦你再发我一次」→ 群友就不太愿意再发了。
                # 自动修是**第一道**手段；实在修不好才走诚实告知（最后手段）。
                log(f"  渲染失败：{str(exc)[:600]}", "ERROR")
                log("  🔧 尝试自动修复 LaTeX（不让群友重发）…")
                fixed = brain.fix_latex(_latex, str(exc))
                if fixed:
                    try:
                        path = latexrender.render(fixed, part["mode"], style=style)
                        log("  ✅ 自动修复成功，PDF 照常发出（群友不用重发）")
                    except Exception as exc2:
                        log(f"  🔴 自动修复后仍然失败：{str(exc2)[:400]}", "ERROR")
                        continue
                else:
                    log("  🔴 自动修复没拿到可用内容", "ERROR")
                    continue

            if caption:
                humanize.sleep_typing(caption)
                onebot.send_group_text(group_id, caption)

            # card 和 longimg 都是**图片**（longimg 是一张竖长图，群里翻着看不用下载）
            if part["mode"] in ("card", "longimg"):
                onebot.send_group_image(group_id, path)
                # ⚠️ 别在这里无条件 path.stat() —— 测试里 path 是桩路径，stat 会抛
                #    FileNotFoundError 把整个发送流程搞挂（真实踩坑，test_closing_honesty 挂了）。
                try:
                    _kb = f"（{path.stat().st_size // 1024} KB）"
                except OSError:
                    _kb = ""
                log(f"  发送{'长图' if part['mode'] == 'longimg' else '公式图片'}"
                    f"：{path.name}{_kb}")
            else:
                # PDF 显示名由模型按任务类型起（批改过程 / 解题过程 / 推导过程…）
                fname = (part.get("filename") or "").strip() or "解答过程"
                if not fname.lower().endswith(".pdf"):
                    fname += ".pdf"
                onebot.send_group_file(group_id, path, name=fname)
                log(f"  发送 PDF：{path.name} → 群文件显示名「{fname}」")
                sent_doc = True
                # 📚 登记进「每日答疑讲义」（同一个人 + 同名 = 修正版，会自动覆盖）
                try:
                    daily.record(str(asker or "群友"), fname, path)
                except Exception as _exc:
                    log(f"  讲义登记失败（不影响发文件）：{_exc!r}", "WARN")
            sent_any = True

        elif part["kind"] in ("sticker", "video"):
            image_mode = part["kind"] == "sticker"
            path = media.pick("image" if image_mode else "video")
            if not path:
                log(f"  素材库是空的（{part['kind']}），跳过", "WARN")
                continue
            # 发之前稍停一下，像人在相册里挑一张
            time.sleep(random.uniform(0.8, 2.2))
            if image_mode:
                onebot.send_group_image(group_id, path)
                log(f"  发送表情包：{path.name}")
            else:
                onebot.send_group_video(group_id, path)
                log(f"  发送视频：{path.name}")
            sent_any = True

    if not sent_any:
        log("  所有片段都没发成功", "WARN")
    return sent_any, sent_doc

def maybe_cold_sticker(group_id: int, history: list[dict], state: State) -> None:
    """群里冷场太久时，偶尔丢个表情包活跃一下。

    只在「已经融入」（idle > 0）、非凌晨静默、且距上次冷场表情包超过冷却时间时才考虑。
    默认阈值保守：冷场 60 分钟以上、每 3 小时最多一次。
    """
    if config.COLD_STICKER_PROB <= 0:
        return
    params = rampup.effective()
    if params["quiet"] or params["idle"] <= 0:
        return
    if not history:
        return

    last_ts = max(int(m.get("time", 0)) for m in history)
    silence_min = (time.time() - last_ts) / 60.0
    if silence_min < config.COLD_SILENCE_MIN:
        return

    now = time.time()
    if now - state.last_cold_sticker < config.COLD_STICKER_COOLDOWN:
        return
    if random.random() > config.COLD_STICKER_PROB:
        return

    path = media.pick("image")
    if not path:
        return

    state.last_cold_sticker = now
    log(f"群{group_id} 冷场 {silence_min:.0f} 分钟，发个表情包活跃一下：{path.name}")
    time.sleep(random.uniform(1.0, 3.0))
    onebot.send_group_image(group_id, path)


def _pdf_cache_cleanup(cache_dir: Path) -> None:
    """删掉超过 TTL 的 PDF MD 缓存（车主定的：留半小时，怕群友回头追问）。"""
    if not cache_dir.exists():
        return
    now = time.time()
    for f in cache_dir.iterdir():
        try:
            if now - f.stat().st_mtime > config.PDF_TASK_CACHE_TTL:
                f.unlink()
        except OSError:
            continue


# ---------------------------------------------------------------- PDF 解题任务

def find_quoted_pdf(message) -> dict | None:
    """reply 引用的那条消息若是 PDF 群文件，返回它的 file 段。

    群友的操作姿势：把 PDF 发进群 → 长按引用 → @机器人写要求。
    引用段只带 message_id，要回查原消息才知道是不是 PDF。
    """
    if not isinstance(message, list):
        return None
    for seg in message:
        if not isinstance(seg, dict) or seg.get("type") != "reply":
            continue
        target = (seg.get("data") or {}).get("id")
        if not target:
            continue
        try:
            ref = onebot.get_msg(target)
        except Exception as exc:
            log(f"  查引用消息失败：{exc}", "WARN")
            continue
        for s2 in (ref.get("message") or []):
            if not isinstance(s2, dict) or s2.get("type") != "file":
                continue
            d2 = s2.get("data") or {}
            name = str(d2.get("file") or d2.get("file_name") or "")
            if name.lower().endswith(".pdf"):
                return s2
    return None


def handle_pdf_task(group_id: int, msg: dict, text: str, file_seg: dict) -> None:
    """PDF 任务全流程：下载 → 转 MD（本地零 token）→ 解题 → 渲染 PDF 发回。

    花钱的只有解题那一次 LLM 调用（输入 ≈ 全文 token），所以有独立的
    防刷冷却（PDF_TASK_COOLDOWN）和长度上限（PDF_TASK_MAX_CHARS）。
    """
    now = time.time()
    remain = config.PDF_TASK_COOLDOWN - (now - _pdf_task_last.get(group_id, 0))
    if remain > 0:
        log(f"群{group_id} PDF 任务冷却中（还剩 {remain:.0f}s），忽略")
        return
    _pdf_task_last[group_id] = now

    data = file_seg.get("data") or {}
    fname = str(data.get("file") or data.get("file_name") or "文件.pdf")
    file_id = str(data.get("file_id") or "")
    url = str(data.get("url") or "")
    asker = (msg.get("sender") or {}).get("user_id")
    # 群友的要求 = 本条消息的文字（去掉引用占位和 @ 痕迹）；不写就默认解题
    request = re.sub(r"（引用内容：.*?）", "", text)
    request = re.sub(r"@\d+", "", request).strip()
    # 没写要求 / 只说「阅读一下」这类 → 阅读总结模式（车主定的：像人读资料写报告，
    # 不当解题任务办）；带了具体任务的（"每页只做第一题"）才照常解题
    if not request or _PDF_READ_ONLY_RE.match(request):
        summary_mode = True
        request = _PDF_SUMMARY_REQUEST
    else:
        summary_mode = False
    log(f"群{group_id} PDF 任务：{sender_name(msg)} 引用《{fname}》"
        f"｜模式：{'阅读总结' if summary_mode else '按要求解题'}"
        f"｜要求：{request if summary_mode else request[:40]}")

    def fail(why: str) -> None:
        log(f"  PDF 任务失败：{why}")
        line = f"{why}，你看看文件对不对，或者过会儿再喊我"
        try:
            humanize.sleep_typing(line)
            if asker:
                onebot.send_group_at(group_id, asker, line)
            else:
                onebot.send_group_text(group_id, line)
        except Exception as exc:
            log(f"  失败通知也发不出去：{exc}", "WARN")

    if not url:
        fail("这个文件拿不到下载地址")
        return

    # 下载 → 转 MD。转换纯本地零 token；转好的 MD 缓存半小时 ——
    # 群友回头追问同一个 PDF 时直接命中，跳过下载和转换
    cache_dir = config.OUT_DIR / "pdfmd_cache"
    cache_key = hashlib.sha1(f"{group_id}:{file_id or fname}".encode()).hexdigest()[:16]
    md_path = cache_dir / f"{cache_key}.md"
    meta_path = cache_dir / f"{cache_key}.json"
    try:
        _pdf_cache_cleanup(cache_dir)
    except Exception as exc:
        log(f"  缓存清理出错（不影响任务）：{exc!r}", "WARN")

    md = stats = None
    if md_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            md = md_path.read_text(encoding="utf-8")
            stats = meta["stats"]
            log(f"  命中 {max(0, int((now - meta['ts']) // 60))} 分钟前的缓存"
                f"（{meta['fname']}），跳过下载转换")
        except Exception as exc:
            md = stats = None
            log(f"  缓存读取失败，重新下载转换：{exc!r}", "WARN")

    if md is None:
        dest = config.OUT_DIR / f"pdfdl_{uuid.uuid4().hex[:8]}.pdf"
        try:
            onebot.download_file(url, dest, max_bytes=config.PDF_TASK_MAX_BYTES)
            md, stats = pdf2md.pdf_to_md(dest, max_chars=config.PDF_TASK_MAX_CHARS,
                                         max_pages=config.PDF_TASK_MAX_PAGES)
            # 写缓存（写不进去不影响本次任务）
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                md_path.write_text(md, encoding="utf-8")
                meta_path.write_text(json.dumps(
                    {"fname": fname, "ts": time.time(), "stats": stats},
                    ensure_ascii=False), encoding="utf-8")
            except OSError as exc:
                log(f"  MD 缓存写入失败（不影响本次任务）：{exc}", "WARN")
        except Exception as exc:
            fail(f"文件处理失败（{str(exc)[:60]}）")
            return
        finally:
            dest.unlink(missing_ok=True)

    if stats["chars"] < 40:
        fail("这份 PDF 转不出文字，多半是扫描件/图片型的")
        return

    cut = "，超长已截断" if stats["truncated"] else ""
    log(f"  转 MD 完成：{stats['pages']}/{stats['page_count']} 页｜"
        f"{stats['chars']} 字符｜兜底 {stats['fallback_pages']} 页{cut}")

    # 输出形式由模型按体量选（车主定的：不要啥都发 PDF）——
    # 但保留大输出预算，防止它选了长解答却被 max_tokens 截断
    form_hint = (
        "\n\n【输出形式，按回答的体量自己选，不要什么都做成 PDF】\n"
        "- 几句话就能说清 → 只用 text 片段直接打字回复，别渲染任何文件\n"
        "- 要展示公式/推导，但内容一页以内 → math 片段 mode 用 \"card\""
        "（排版成一张图片发群里）\n"
        "- 完整解答确实超过一页 → 才用 mode \"doc\"（渲染成 PDF）\n"
        "- 阅读总结类 → mode \"doc\"，文件名「阅读总结」\n"
        "- ⚠️ 唯一例外（优先级最高）：群友的要求里**明确说了要 PDF/要文件**，"
        "那就必须用 mode \"doc\" 发 PDF——哪怕解答很短也照发，体量规则让位")
    context = (f"【群友上传的 PDF《{fname}》（共 {stats['page_count']} 页，"
               f"本次处理 {stats['pages']} 页，已转 Markdown{cut}）】\n{md}"
               + form_hint)
    # ⚠️ 「让改这份 PDF」必须出新 PDF —— 不能只回一句文字（见 wants_pdf_revise 的注释）
    revise = wants_pdf_revise(request, True)
    if revise:
        log("  📄 判定为「改这份 PDF」→ 强制出新 PDF（不许只回文字）")
        context += ("\n\n【⚠️ 这次是让**修改这份 PDF**，不是重新阅读】\n"
                    "必须输出一个 mode=\"doc\" 的 math 片段（一份**修改后的新 PDF**），"
                    "把对方指出的问题改掉、其余内容保持完整。**不许只回文字说明。**")
    _p2 = reply_policy(request, True, False)
    if not _p2["allow"]:
        fail("这条我先不接")
        return
    if _p2["kind"] in ("gpt", "astra") and getattr(config, "NAMED_PROVIDERS", None):
        _want = "GPT6ASTRA" if _p2["kind"] == "astra" else "GPT6.1SOL"
        _g = [x for x in config.NAMED_PROVIDERS if x["name"] == _want]
        _rest = list(config.PROVIDERS)
        try:
            import linehealth
            _rest = linehealth.order(_rest)
        except Exception:
            pass
        _provs, _hard = ([_g[0]] + _rest, True) if _g else (_rest, False)
    else:
        _provs, _hard = list(config.PROVIDERS), False
    result = brain.decide(context, f"{sender_name(msg)}: {request}",
                          mentioned=True, pdf_requested=revise, max_tokens=16000,
                          style_hint=rampup.effective()["style"],
                          bot_name=BOT_DISPLAY_NAME, providers=_provs)
    # ⚠️ 播报必须在**调用成功之后** —— 调用前就喊"请 Opus 出手"，
    #    一旦它 429/404 降级到 DeepSeek，群里看到的就是假话（车主 2026-10-14 强调）。
    _model_note = announce_named_line(group_id, _provs[0], result.get("provider") or "") if _hard else ""
    if result["action"] != "reply" or not result["parts"]:
        fail("我没解出来，这题有点超纲")
        return

    _run_tablecheck(result)
    # ⚠️ 与主回复路径同一套规矩：收尾话基于**实际发出去的**，不是模型"计划要发什么"
    sent_any, sent_doc = send_parts(group_id, result["parts"], style="",
                                    asker=str(asker or ""),
                                    model_note=_model_note)

    # ① 什么都没发出去 → 诚实告知，绝不说"发了"
    if not sent_any:
        log("  PDF 任务：所有片段都没发成功 → 发诚实告知", "WARN")
        if asker:
            honest = random.choice(FAIL_CLOSING)
            time.sleep(random.uniform(0.5, 1.2))
            humanize.sleep_typing(honest)
            try:
                onebot.send_group_at(group_id, asker, honest)
                log(f"  诚实告知 @{asker}：{honest[:40]}")
            except Exception as exc:
                log(f"  诚实告知发送失败：{exc}", "WARN")
        return

    # ② 计划了 PDF 却没发出 PDF（部分成功）→ 诚实话术覆盖，不说假话
    planned_doc = any(p.get("kind") == "math" and p.get("mode") == "doc"
                      for p in result["parts"])
    closing = (result.get("closing") or "").strip()
    if planned_doc and not sent_doc:
        log("  PDF 任务：计划发 PDF 但没发出去 → 收尾改用诚实告知", "WARN")
        closing = random.choice(FAIL_CLOSING)
    elif sent_doc and not closing:
        closing = random.choice(CLOSING_FALLBACK)
    if closing and asker:
        time.sleep(random.uniform(0.8, 2.0))
        humanize.sleep_typing(closing)
        try:
            onebot.send_group_at(group_id, asker, closing)
            log(f"  收尾 @{asker}：{closing[:40]}")
        except Exception as exc:
            log(f"  收尾消息发送失败：{exc}", "WARN")


def _run_tablecheck(result: dict) -> None:
    """单纯形表算术自检：验算 → 代码修 σ 行 → 带错反馈让模型重算 → 兜底标注。

    主回复和 PDF 任务两条路径共用。只动 math 片段的 latex，
    任何故障都放行原文（绝不让自检搞哑机器人）。
    """
    note = ""
    if config.TABLECHECK_ENABLED:
        try:
            result["parts"], note = tablecheck.enforce(
                result["parts"], revise=brain.revise_table,
                log=lambda m: log(f"  {m}"),
                max_revise=config.TABLECHECK_MAX_REVISE)
        except Exception as exc:
            log(f"  表格自检故障（放行原文）：{exc!r}", "WARN")
        if note:
            # 标注并进已有的文字片段；没有文字位且还有空位就补一段
            text_parts = [p for p in result["parts"] if p.get("kind") == "text"]
            if text_parts:
                text_parts[0]["text"] = (text_parts[0]["text"] + " " + note)[:120]
            elif len(result["parts"]) < config.MAX_PARTS:
                result["parts"].append({"kind": "text", "text": note})
            else:
                log(f"  表格自检标注没地方放，只记日志：{note}")


# ---------------------------------------------------------------- 单条消息处理

def handle_message(group_id: int, msg: dict, history: list[dict], index: int, state: State) -> None:
        # ⚠️⚠️ 硬保险（车主 2026-10-15 定）：「千万不要补回」
    #   不管什么原因（重启、游标丢失、程序卡顿后追赶），
    #   **超过 STALE_MSG_SECONDS 的旧消息一律不处理** ——
    #   哪怕它是 @ 或 /pdf。防止刷屏、防止回到高强度模式、防止封控。
    try:
        _mt = int(msg.get("time") or 0)
        if _mt:
            _age = time.time() - _mt
            if _age > getattr(config, "STALE_MSG_SECONDS", 300):
                log(f"  ⏭ 消息已过期 {_age:.0f} 秒（超过 {getattr(config, 'STALE_MSG_SECONDS', 300)}s），跳过不回")
                return
    except Exception:
        pass
    
# 🛌 静默休息模式（车主 2026-10-14 定，起因：号被冻结）——
    #    收到消息也**一个字都不发**。注意：**不登出 QQ**，保持在线，
    #    这样账号看着就是个"在线但不说话的普通用户"，比登出安全得多。
    if getattr(config, "REST_MODE", False):
        return

    message = msg.get("message")
    text = onebot.message_text(message)

    # 周末平安值守：车主本人的开启/关闭/状态命令。最早拦截、群里只吞不回
    # （回复一律走私信），非车主发相关词完全不触发（装聋，不暴露功能）。
    if config.SAFETY_CHECK_ENABLED and safety.feature_live():
        sender_qq = (msg.get("sender") or {}).get("user_id")
        if text and safety.handle_owner_command(text, sender_qq, via_group=True):
            state.last_reply[group_id] = time.time()
            return

    # 取图（包含 reply 引用的那条消息里的图）—— 这是「能看图」的关键一步
    quoted_text, image_urls = vision.extract(message)
    # ⚠️ 车主 2026-10-15 定：判断「有没有点名 GPT/ASTRA」**只看群友自己打的那句话**。
    #    引用内容里出现 gpt（比如引用了别人说「用gpt」的消息）、
    #    或者图片里抄出来的字里有 gpt —— 都**不算点名**。
    #    真实事故：群友正常 @ 机器人问问题，机器人却回「已经丢给 GPT 做了」——
    #    因为被引用那条消息里提到了 GPT。
    _own_text = text
    if quoted_text:
        text = f"{text}（引用内容：{quoted_text}）".strip()

    # 📝 建议反馈采集（车主 2026-10-12 定）：命中关键词就**先抓原始事实**落盘，
    #    真正的判断和摘要交给 tick 里的 LLM 异步做（见 feedback.py 的两段式设计）。
    #    ⚠️ 放在最前面 —— 就算这条消息机器人不回复，建议也不能丢。
    #    任何异常都只记日志，绝不影响回答。
    try:
        _raw_text = onebot.message_text(message) or ""
        if feedback.looks_like_suggestion(_raw_text):
            _recent = []
            for _m in (history or [])[-6:]:
                _nm = sender_name(_m)
                _tx = (onebot.message_text(_m.get("message")) or "").strip()
                if _tx:
                    _recent.append(f"{_nm}: {_tx[:120]}")
            feedback.record(
                sender=sender_name(msg), text=text,
                quoted=quoted_text or "",
                recent=_recent, bot_reply="")
    except Exception as _exc:
        log(f"  建议采集出错（不影响回答）：{_exc!r}", "WARN")

    # 📊 群消息流水（每日分析报告的数据源）——
    #    service.log 只记"决定回复"的那些，**没被回复的消息完全没痕迹**，
    #    而"谁经常@我但我不理"恰恰是最该看的数据。
    try:
        msglog.record((msg.get("sender") or {}).get("user_id"), sender_name(msg),
                      onebot.message_text(message) or "",
                      str(config.BOT_QQ) in onebot.at_targets(message),
                      bool(image_urls))
    except Exception as _exc:
        log(f"  消息流水写入出错：{_exc!r}", "WARN")

    ats = onebot.at_targets(message)
    mentioned = str(config.BOT_QQ) in ats or is_name_called(text)
    # 在跟别人说话（@了别人 / 点名了别的机器人）—— 一律不参与
    to_others = addressed_to_others(message, text)

    # ⭐ 车主 2026-10-12 定：群友开头写「/pdf」+ 引用图片 = 公开喊 PDF 机器人。
    #    这种消息**不 @ 任何人**，以前会被 MENTION_ONLY 全挡掉 —— 现在等同于被点名。
    #    凛音同学会回，我们也要回：同一张卷子同时答，谁答得好群友用谁。
    # /pdf 喊话触发：模式一恢复原来的行为，模式二关闭
    # （车主 2026-10-14：「别人不艾特包括 /pdf 都不回」—— 那只针对模式二）
    # ⚠️ 2026-10-15 车主定：**/pdf 喊话不再等同于被点名** ——
    #    「没有艾特，只有一个 /PDF，不用回……那种 /PDF 千万不要回。」
    #    所以两种模式都不再把 pdf_shout 抬成 mentioned。
    #    （pdf_shout 本身还留着 —— 它表示"群友想要 PDF 形式的交付"，
    #      在**被 @ 的前提下**仍然影响出不出文件。）
    pdf_shout = is_pdf_command(text, bool(image_urls)) if getattr(config, "BOT_MODE", 2) == 1 else False

    # ══════════ 策略闸门：所有回复的唯一入口 ══════════
    # 只有两条通路：@ + gpt → GPT 线路 ｜ @ + 政治题 → 常规线路
    # 其余一律不发声（含普通 @、含 /pdf）
    _pol = reply_policy(text, mentioned, to_others, has_image=bool(image_urls),
                        keyword_text=_own_text, pdf_requested=pdf_shout)

    # ⚠️⚠️ 有图就要先识图（车主 2026-10-15 定，两种情形都要）：
    #   ① GPT/ASTRA 线路 —— 车主实测：「GPT 那两个虽然能识图，但自从接了这个中转
    #      以后识图就不太好」，所以**先用国内模型识图，再把文字转给 GPT**。
    #   ② 闸门判不出（政治题常常只发图不写字）—— 识完再判一次。
    #
    #   踩坑记录：这段条件原来写的是 `not _pol["allow"] and need_ocr`，
    #   结果 GPT 那条路 allow=True，整段被跳过 —— 图就直接甩给 GPT 了 ❌
    _need_ocr = bool(image_urls) and (
        _pol.get("need_ocr")                      # 情形②：判不出，要识图才能判
        or (_pol["allow"] and _pol["kind"] in ("gpt", "astra"))   # 情形①：GPT 线必须先识图
    )
    if _need_ocr:
        log(f"  🔍 先识图（原因：{_pol.get('why') or _pol['kind']}）→ 国内模型抄题")
        _ocr = ""
        try:
            _ocr = brain.transcribe_problem(list(image_urls)) or ""
        except Exception as exc:
            log(f"  识图失败（不影响后续）：{exc}", "WARN")
        if _ocr.strip():
            log(f"  识图结果：{_ocr.strip()[:70]}")
            # ⭐ 关键：把图片**摘掉**，只把抄出来的文字交给下一步 ——
            #    GPT 线路拿到的就是纯文字（不再直接甩图给它）。
            image_urls = []
            # 文字也并进 text，这样历史上下文和后续判断都能看到题面
            text = (text + "\n【图片题面（识图结果）】\n" + _ocr).strip()
            _pol = reply_policy(text, mentioned, to_others, has_image=False,
                                keyword_text=_own_text, pdf_requested=pdf_shout)
        else:
            log("  识图没拿到文字，按原样判")
    if not _pol["allow"]:
        log(f"  🚫 策略闸门拦下：{_pol['why']}｜{text[:36]}")
        return
    log(f"  ✅ 策略闸门放行：{_pol['why']}")

    # ⭐ 连通性体检：真去云端挨个打一遍，然后回报告
    if _pol.get("kind") == "conncheck":
        log("  🩺 线路连通性体检开始")
        try:
            onebot.send_group_text(group_id, "好，我去挨个打一遍所有线路，稍等十几秒…")
        except Exception:
            pass
        try:
            import conncheck
            _report = conncheck.check_all()
        except Exception as exc:
            _report = f"体检失败：{type(exc).__name__} {exc}"
            log(f"  体检异常：{exc}", "WARN")
        try:
            for _chunk in (_report[i:i + 900] for i in range(0, len(_report), 900)):
                humanize.sleep_typing(_chunk)
                onebot.send_group_text(group_id, _chunk)
        except Exception as exc:
            log(f"  体检报告发送失败：{exc}", "WARN")
        state.last_reply[group_id] = time.time()
        return

    # ⭐ 在线测试：**代码直回**，不调模型（秒回、零成本、不受线路故障影响）
    if _pol.get("kind") == "ping":
        import random as _rnd
        _line = _rnd.choice(("在的", "在线 ✅", "在，说吧", "我在", "在呢，你说"))
        log(f"  🟢 在线测试 → 回「{_line}」")
        try:
            humanize.sleep_typing(_line)
            onebot.send_group_text(group_id, _line)
        except Exception as exc:
            log(f"  在线测试回复失败：{exc}", "WARN")
        state.last_reply[group_id] = time.time()
        return

    # PDF 任务：群友引用群文件里的 PDF 并 @ 机器人。放在空文字拦截之前 ——
    # 「只引用不写字」也算一次有效请求（默认解题）
    if config.PDF_TASK_ENABLED and mentioned and not to_others:
        file_seg = find_quoted_pdf(message)
        if file_seg is not None:
            handle_pdf_task(group_id, msg, text, file_seg)
            state.last_reply[group_id] = time.time()
            return

    if not text and not image_urls:
        return
    # 必须先算出 mentioned，PDF 请求只有「对着机器人提」才算数
    # 批改类（带图 + 让批改）由代码强制当「要 PDF」处理，见 wants_grading_report 的注释
    pdf_requested = (wants_pdf(text, mentioned)
                     or wants_grading_report(text, mentioned, bool(image_urls))
                     or pdf_shout)     # /pdf 格式本身就明说要 PDF 交付
    # 排版风格：车主 2026-10-11 定，默认就是「风格 2」彩色框版式
    # （原来群里说「风格2」才切换，现在永远用它；wants_style2 保留只为兼容）
    style_variant = config.LATEX_DEFAULT_STYLE
    params = rampup.effective()

    ok, reason = should_reply(text, mentioned, params,
                              image_only=is_image_only(text, bool(image_urls)),
                              to_others=to_others)
    if not ok:
        return

    now = time.time()
    elapsed = now - state.last_reply.get(group_id, 0)
    # 被 @ 时只受极小的防爆走间隔约束，不会被同群冷却吞掉
    floor = config.MENTION_MIN_INTERVAL if mentioned else params["interval"]
    if elapsed < floor:
        log(f"群{group_id} 冷却中（还剩 {floor - elapsed:.0f}s），跳过：{text[:30]}")
        return

    # 小时级硬刹车：只约束「主动闲聊」，被点名的回复不受限（那是群友要的，不能拒）
    if not mentioned:
        cutoff = now - 3600
        state.chat_times[:] = [t for t in state.chat_times if t > cutoff]
        if len(state.chat_times) >= config.MAX_CHAT_PER_HOUR:
            log(f"群{group_id} 本小时主动发言已达上限 "
                f"{config.MAX_CHAT_PER_HOUR} 条，这条不接：{text[:30]}")
            return

    # 人家明确要文件、或者带了图，不能装没看见
    if humanize.should_drop(mentioned or pdf_requested or bool(image_urls), params["drop"]):
        log(f"群{group_id} 装没看见（{reason}）：{text[:30]}")
        state.last_reply[group_id] = now
        return

    log(f"群{group_id} 决定回复（{reason}{' + PDF' if pdf_requested else ''}"
        f"{' + %d张图' % len(image_urls) if image_urls else ''}｜{params['phase']}）"
        f"{sender_name(msg)}: {text[:40]}")

    context = build_context(history, index)
    time.sleep(humanize.reading_delay(text))

    # 🔍 抄题复检（车主 2026-10-12 定）：只在「要求详细/严谨」或「二次追问说上次解错了」时做。
    #    单独跑一次视觉调用把题面逐字符抄下来，塞进上下文让主调用对照 ——
    #    治的是两个真实反馈：下标识别错、臆加题目没写的条件。
    #    复检走的是 config.PROVIDERS 第一条（DeepSeek 主线路），车主指定。
    # 🖼 手写草稿 → 走**识图 API**（车主 2026-10-13 定）
    #    群友反映草稿纸上的手写题识别难度大，别的机器人（用 Gemini）认得更准，
    #    人都跑去那边批改了。所以：**手写先用 Gemini 把题抄准，再交给 DeepSeek 解答**。
    #    正常图片/PDF 不碰这条路（DeepSeek 快得多：实测 0.8s vs 8.9s）。
    _handwriting = False
    if config.VISION_HANDWRITING and image_urls:
        try:
            _handwriting = vision.any_handwritten(image_urls)
        except Exception as _exc:
            log(f"  手写判定出错（按非手写处理）：{_exc!r}", "WARN")
        if _handwriting:
            log("  ✍️ 判定为手写草稿 → 用识图 API（Gemini）抄题，解答仍交给 DeepSeek")

    recheck, why = wants_recognition_recheck(text, bool(image_urls), mentioned)
    if _handwriting:
        # 手写稿**必须**先抄准 —— 读错了后面全错，这一步不能省
        recheck, why = True, "手写草稿（走识图 API）"
    if recheck:
        log(f"  🔍 触发抄题复检（{why}）→ 先单独抄一遍题面")
        # 先吱一声：这条路要 15~46 秒，不能群里一片安静（车主 2026-10-12 定）
        try:
            _line = random.choice(THINKING_LINES)
            humanize.sleep_typing(_line)
            onebot.send_group_text(group_id, _line)
            log(f"  先吱一声：{_line}")
        except Exception as exc:
            log(f"  先吱一声失败（不影响回答）：{exc}", "WARN")
        _t0 = time.time()
        problem_text = brain.transcribe_problem(
            image_urls, prefer_vision_api=_handwriting)
        if problem_text:
            context += ("\n\n【⚠️ 抄题复检结果 —— 这是先把图逐字符抄下来的题面】\n"
                        "解题时拿它和原图**互相核对**；两处不一致就停下来确认，"
                        "**绝对不许按「看起来应该是」去补题目没写的条件**。\n"
                        f"{problem_text}")
            log(f"  🔍 复检完成（{time.time() - _t0:.1f}s，{len(problem_text)} 字符）→ 已并入上下文")
        else:
            log("  🔍 复检没拿到内容，按无复检继续", "WARN")

    # 🖼 群友要图片 → **强制** longimg（竖长图）。
    #    ⚠️ 光在 context 里写一句没用（真实事故：群友说「用图片发出来」，
    #    模型只回了一行文字，连图都没出），必须走 force_mode 放到提示词最末尾。
    _force_mode = ""
    if wants_long_image(text):
        log("  🖼 群友要图片形式 → 强制 longimg 竖长图")
        _force_mode = "longimg"

    # 模式一：走原来的点名线逻辑（认 claude/opus/gpt/sol/6.1 等全部触发词）
    if getattr(config, "BOT_MODE", 2) == 1:
        _provs, _hard = providers_for(text)
    elif _pol["kind"] in ("gpt", "astra") and getattr(config, "NAMED_PROVIDERS", None):
        _want = "GPT6ASTRA" if _pol["kind"] == "astra" else "GPT6.1SOL"
        _g = [x for x in config.NAMED_PROVIDERS if x["name"] == _want]
        _provs, _hard = ([_g[0]] + list(config.PROVIDERS), True) if _g else (list(config.PROVIDERS), False)
    else:
        # 政治题等：按 config.PROVIDERS 原始顺序（DeepSeek 优先），
        # ⚠️ 但熔断的厂商会被排到最后（见 linehealth.py）——
        # DeepSeek 官方挂了就自动落到小米/千帆/火山，不用人工干预。
        try:
            import linehealth
            _provs = linehealth.order(list(config.PROVIDERS))
        except Exception:
            _provs = list(config.PROVIDERS)
        _hard = False
    # ⚠️ 判断"这次要出 PDF"必须用 pdf_requested，**不能用 _force_mode** ——
    #    _force_mode 只会被设成 "longimg"（只给"要图片"用），永远不等于 "doc"。
    #    2026-10-15 踩到：写成 _force_mode == "doc" 导致这段代码永远不执行。
    _pre_card = bool(pdf_requested and looks_political(text))
    result = brain.decide(context, f"{sender_name(msg)}: {text}", mentioned,
                          pdf_requested, params["style"], image_urls=image_urls,
                          bot_name=BOT_DISPLAY_NAME, style_variant=style_variant,
                          force_mode=_force_mode, providers=_provs, extra_card=_pre_card)
    # ⚠️ 强制 mode 必须**校验**：模型会当耳旁风。
    #    真实事故（2026-10-12）：群友说「用图片发出来」，模型只回了一行文字，
    #    图根本没出 —— 只靠提示词约束不住，必须程序兜底。
    #    校验不过就**重摇一次**（同样的硬要求，换个随机结果通常会照做）。
    # ⭐ 政治题要 PDF 时，**必须再配一张关系图**（车主 2026-10-15 定）
    _want_card = bool(pdf_requested and looks_political(text))   # 同上，用 pdf_requested
    if _want_card and any(p.get("kind") == "math" and p.get("mode") == "doc"
                          for p in (result.get("parts") or [])) \
            and not any(p.get("kind") == "math" and p.get("mode") == "card"
                        for p in (result.get("parts") or [])):
        log("  ⚠️ 出了 PDF 但没配关系图，重试一次（车主要求 PDF 后面跟一张带字箭头的图）", "WARN")
        _r2 = brain.decide(context, f"{sender_name(msg)}: {text}", mentioned,
                           pdf_requested, params["style"], image_urls=image_urls,
                           bot_name=BOT_DISPLAY_NAME, style_variant=style_variant,
                           force_mode=_force_mode, providers=_provs, extra_card=True)
        if any(p.get("kind") == "math" and p.get("mode") == "card"
               for p in (_r2.get("parts") or [])):
            log("  ✅ 重试成功，PDF + 关系图都出了")
            result = _r2
        else:
            log("  ⚠️ 重试还是没出关系图，就发 PDF", "WARN")

    if _force_mode and not any(p.get("kind") == "math" and p.get("mode") == _force_mode
                               for p in (result.get("parts") or [])):
        log(f"  ⚠️ 模型没按要求出 {_force_mode}，重试一次", "WARN")
        _retry = brain.decide(context, f"{sender_name(msg)}: {text}", mentioned,
                              pdf_requested, params["style"], image_urls=image_urls,
                              bot_name=BOT_DISPLAY_NAME, style_variant=style_variant,
                              # ⚠️ 必须带上同一条线路列表 —— 不带的话重试会走默认
                              #    （DeepSeek），而播报已经说了"Opus 算的" → 骗人
                              force_mode=_force_mode, providers=_provs)
        if any(p.get("kind") == "math" and p.get("mode") == _force_mode
               for p in (_retry.get("parts") or [])):
            log(f"  ✅ 重试成功，出了 {_force_mode}")
            result = _retry
        else:
            log(f"  ⚠️ 重试也没出 {_force_mode}，就用第一次的结果", "WARN")
    state.last_reply[group_id] = time.time()

    # 📣 点名播报 —— **必须放在这里**（所有 decide 都跑完、result 定稿之后）。
    #    车主 2026-10-14 强调：「确保这次能请求成功以后，你再回复那句话……不要骗人」。
    #    放早了（调用前，或者重试前）都会出现「说是 Opus 答的、其实是降级线路答的」。
    _model_note = announce_named_line(group_id, _provs[0], result.get("provider") or "") if _hard else ""

    # 主线路挂了会自动换备用，这里留个痕迹方便排查
    provider = result.get("provider")
    # ⚠️ 要拿**本次实际用的那条列表**的第一条来比，不能拿 config.PROVIDERS[0] ——
    #    走难题线时列表第一条是 Opus5.5，而 config.PROVIDERS[0] 是"主线路"，
    #    结果 Opus5.5 明明答了，日志却写「主线路不可用，本次由 Opus5.5 作答」，
    #    自相矛盾（真实踩坑 2026-10-14，第一次接完 Opus 就撞上）。
    _first = (_provs or config.PROVIDERS or [{"name": ""}])[0]["name"]
    if provider and provider != _first:
        log(f"  ⚠ 「{_first}」不可用，本次由「{provider}」作答")

    if result["action"] != "reply" or not result["parts"]:
        # 「@ 必回」是硬承诺：模型不接，就自己接一句，绝不冷场
        if mentioned:
            line = random.choice(MENTION_FALLBACK)
            log(f"  被点名但模型没接话，发兜底回应：{line}")
            humanize.sleep_typing(line)
            onebot.send_group_text(group_id, line)
        else:
            log(f"  模型选择不接话：{result.get('reason','')[:80]}")
        return

    # 单纯形表算术自检（与 PDF 任务共用同一套）
    _run_tablecheck(result)

    # ⚠️ 收尾话必须基于**实际发出去的**（sent_doc），不能基于模型"计划要发什么"。
    # 真实事故（2026-10-12，一天三次）：渲染失败 → 什么都没发出去 →
    # 收尾话却说"发你了" → 群友只能再催一遍。
    # 给建议反馈用的一句话概括（这次回答是什么形态、多长）
    parts_summary = "；".join(
        (f"PDF《{p.get('filename') or '解答'}》{len(p.get('latex') or '')}字符"
         if p.get("kind") == "math" and p.get("mode") == "doc"
         else f"公式图 {len(p.get('latex') or '')}字符" if p.get("kind") == "math"
         else f"文字「{str(p.get('text') or '')[:60]}」")
        for p in (result.get("parts") or []))[:700]

    # 📊 标记「这个时刻机器人回复了」（报告算被回复率用）
    try:
        msglog.mark_reply(int(msg.get("time") or time.time()))
    except Exception:
        pass

    # asker 要提前算 —— send_parts 登记每日讲义时要用（讲义按人+文件名去重）
    asker = (msg.get("sender") or {}).get("user_id")
    # 🖼 群友点名要图片 → 先把 caption 换成一句「图片正在渲染」
    #    （车主定的：回他一句说图片正在渲染，然后直接发图）
    if _force_mode == "longimg":
        for _p in (result.get("parts") or []):
            if _p.get("kind") == "math" and _p.get("mode") == "longimg":
                _p["caption"] = random.choice(IMG_WORKING_LINES)
                break

    sent_any, sent_doc = send_parts(group_id, result["parts"], style=style_variant,
                                    asker=str(asker or ""),
                                    model_note=_model_note)
    # 记一笔「主动发言」，供小时级限流用（被点名的不算）
    if not mentioned:
        state.chat_times.append(time.time())

    # 📝 把这次回答补进建议反馈条目（如果有人刚吐槽过）——
    #    这样 LLM 提炼时才知道「他是在说哪次回答」。任何异常都不影响回答。
    try:
        if parts_summary:
            feedback.attach_reply(sender_name(msg), parts_summary)
    except Exception as _exc:
        log(f"  补回答到反馈条目出错：{_exc!r}", "WARN")

    # ① 什么都没发出去 → 发诚实告知，绝不说"发了"
    if not sent_any:
        log("  所有片段都没发成功 → 发诚实告知（不说「发了」）", "WARN")
        if asker:
            honest = random.choice(FAIL_CLOSING)
            time.sleep(random.uniform(0.5, 1.2))
            humanize.sleep_typing(honest)
            try:
                onebot.send_group_at(group_id, asker, honest)
                log(f"  诚实告知 @{asker}：{honest[:40]}")
            except Exception as exc:
                log(f"  诚实告知发送失败：{exc}", "WARN")
        return

    # ② ⚠️ 「部分成功」也要说真话（2026-10-12 第二次事故）：
    #    模型计划里**有 PDF**、但 PDF 没发出去（渲染失败），此时：
    #      · sent_any=True（文字发出去了）→ 走不到上面那条诚实分支
    #      · 而模型自己给的收尾话（"批注看完了…"）会照样发出去 → 又是假话
    #    所以这里必须拦截：**只要计划了 PDF 却没发出 PDF，就用诚实话术覆盖收尾。**
    planned_doc = any(p.get("kind") == "math" and p.get("mode") == "doc"
                      for p in result["parts"])
    closing = (result.get("closing") or "").strip()
    if planned_doc and not sent_doc:
        log("  计划发 PDF 但没发出去 → 收尾改用诚实告知（不说「发了」）", "WARN")
        closing = random.choice(FAIL_CLOSING)
    elif sent_doc and not closing:
        closing = random.choice(CLOSING_FALLBACK)
    if closing and asker:
        time.sleep(random.uniform(0.8, 2.0))
        humanize.sleep_typing(closing)
        try:
            onebot.send_group_at(group_id, asker, closing)
            log(f"  收尾 @{asker}：{closing[:40]}")
        except Exception as exc:
            log(f"  收尾消息发送失败：{exc}", "WARN")


# ---------------------------------------------------------------- 主循环

def loop_group(group_id: int, state: State) -> None:
    messages = onebot.get_group_msg_history(group_id, count=30)
    if not messages:
        return
    history = sorted(messages, key=lambda m: m.get("time", 0))

    if group_id not in state.seeded:
        for m in history:
            state.mark(m.get("message_id"))
        state.seeded.add(group_id)
        state.save()
        log(f"群 {group_id} 首次运行：已把 {len(history)} 条历史标记为已读，不回旧消息")
        return

    for index, msg in enumerate(history):
        mid = msg.get("message_id")
        if mid in state.seen:
            continue
        state.mark(mid)

        if str((msg.get("sender") or {}).get("user_id")) == str(config.BOT_QQ):
            continue
        if time.time() - int(msg.get("time", 0)) > 300:
            continue

        try:
            handle_message(group_id, msg, history, index, state)
        except Exception as exc:
            log(f"处理消息出错：{exc}\n{traceback.format_exc()[:600]}", "ERROR")

    try:
        maybe_cold_sticker(group_id, history, state)
    except Exception as exc:
        log(f"冷场表情包判断出错：{exc}", "WARN")

    state.save()


def main() -> int:
    log("=" * 64)
    log(f"启动 | bot_qq={config.BOT_QQ} | 群={config.ALLOWED_GROUPS} | 轮询={config.POLL_INTERVAL}s")

    try:
        info = onebot.get_login_info()
        log(f"当前登录：{info.get('user_id')} 「{info.get('nickname')}」")
    except Exception as exc:
        log(f"拿不到登录信息，NapCat 可能没起来：{exc}", "ERROR")
        return 1

    # 抓一次当前名字（昵称 + 各群名片），派生"被点名的写法"
    refresh_bot_names(force=True)

    state = State()
    failures = 0
    last_phase = None

    while True:
        # 名字可能被改（改昵称/群名片），定期重新抓一次，不用重启
        try:
            refresh_bot_names()
        except Exception:
            pass

        # 融入阶段变了就记一笔，方便事后对日志
        try:
            params = rampup.effective()
            if params["phase"] != last_phase:
                note = "  [凌晨静默，不主动搭话]" if params["quiet"] else ""
                log(f"行为阶段：{params['phase']}（已过 {params['elapsed_h']}h）"
                    f"｜插嘴 {params['idle']}｜数学词 {params['math']}｜问句 {params['question']}"
                    f"｜间隔 {params['interval']}s{note}")
                last_phase = params["phase"]
        except Exception as exc:
            log(f"读取融入阶段失败：{exc}", "WARN")

        try:
            for group_id in config.ALLOWED_GROUPS:
                loop_group(group_id, state)
            try:
                safety.maybe_tick(config.ALLOWED_GROUPS[0])
            except Exception as exc:
                log(f"安全值守 tick 异常：{exc}", "WARN")
            try:
                pmchat.maybe_tick()
            except Exception as exc:
                log(f"私信问答 tick 异常：{exc}", "WARN")
            # 📝 建议反馈：把待提炼队列过一遍 LLM（一次一条，失败留待下次）
            try:
                feedback.enrich_pending(limit=1)
            except Exception as exc:
                log(f"建议反馈提炼异常：{exc}", "WARN")
            # 📚 每日答疑讲义：到点（默认 00:10）汇总前一天并发群，每天一次
            try:
                (None if getattr(config, 'REST_MODE', False) else daily.maybe_run(config.ALLOWED_GROUPS[0]))
            except Exception as exc:
                log(f"每日讲义异常：{exc}", "WARN")
            # 🏛 每日政治知识点推荐：每天到点（默认 08:00）推一条，标记落盘
            try:
                (None if getattr(config, 'REST_MODE', False) else politics_push.maybe_run(config.ALLOWED_GROUPS[0]))
            except Exception as exc:
                log(f"政治推荐异常：{exc}", "WARN")
            # 📊 每日群使用分析报告：每晚（默认 23:30）生成一份存盘，车主随时可读
            try:
                (None if getattr(config, 'REST_MODE', False) else report.maybe_run())
            except Exception as exc:
                log(f"日报异常：{exc}", "WARN")
            failures = 0
        except KeyboardInterrupt:
            log("收到中断，退出")
            state.save()
            return 0
        except Exception as exc:
            failures += 1
            log(f"主循环异常（第 {failures} 次）：{exc}", "ERROR")
            if failures == 5:
                log("连续失败 5 次，等待 30 秒再继续", "WARN")
                time.sleep(30)
            time.sleep(min(config.POLL_INTERVAL * failures, 15))
            continue

        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
