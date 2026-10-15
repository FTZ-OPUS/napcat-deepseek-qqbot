"""daily.py —— 每日答疑讲义（车主 2026-10-12 定）

**做什么**：把当天所有答疑 PDF 合成一份长讲义（带封面），
           凌晨固定时间发群里；车主可以直接存到电脑。

**名**：`e阶导每日答疑讲义-YYYY-MM-DD.pdf`

**为什么 00:10 汇总前一天**：群里 23:00 最活跃（实测 54 条/小时）、00:00 还有 25 条。
23:50 会切掉尾巴；00:10 完整覆盖，而且不在「凌晨静默」（1:00–8:00）时段内。

**修正版怎么覆盖**：按 **(提问者, 文件名)** 去重，**取最后一次**。
修正时文件名通常不变（都叫「批改过程.pdf」），所以改对的版本会自动盖掉旧的。

**落盘**：`state/daily/YYYY-MM-DD/`
           `index.json`（当天的清单）+ `副本 PDF`（另存一份，防 out/ 被清）

**任何异常都只记日志**，绝不影响机器人回答。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import config

_DIR = Path(config.STATE_DIR) / "daily"
_last_run_day = ""          # 本次进程内已汇总过哪天（防重复）
# 同一个(人, 文件名)在这个时间窗内视为同一次任务的修正版（秒）
_DEDUPE_WINDOW = 1800.0


def log(msg: str, level: str = "INFO") -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] 每日讲义: {msg}", flush=True)


def _day_dir(day: str) -> Path:
    return _DIR / day


def _index_path(day: str) -> Path:
    return _day_dir(day) / "index.json"


def _load_index(day: str) -> list[dict]:
    try:
        return json.loads(_index_path(day).read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_index(day: str, items: list[dict]) -> None:
    d = _day_dir(day)
    d.mkdir(parents=True, exist_ok=True)
    _index_path(day).write_text(json.dumps(items, ensure_ascii=False, indent=1),
                                encoding="utf-8")


def record(asker: str, filename: str, pdf: Path, summary: str = "",
           asker_name: str = "") -> None:
    """① 每发出一份答疑 PDF 就登记一笔（由 send_parts 调）。

    同一天里 **(提问者, 文件名)** 相同的视为**同一次任务的修正版** → 覆盖。
    """
    try:
        if not pdf or not Path(pdf).exists():
            return
        day = time.strftime("%Y-%m-%d")
        d = _day_dir(day)
        d.mkdir(parents=True, exist_ok=True)
        items = _load_index(day)
        fname = (filename or "解答过程").strip()
        if not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        # 去重：同一个人 + 同一个文件名 + **时间接近** → 视为同一次任务的修正版，替换
        # ⚠️ 加时间窗很重要：否则同一个人间隔几小时发两份都叫「批改过程.pdf」的
        #    不同任务会被错误合并。
        now = time.time()
        items = [it for it in items
                 if not (it["asker"] == asker and it["name"] == fname
                         and now - float(it.get("ts") or 0) <= _DEDUPE_WINDOW)]
        seq = len(items) + 1
        copy = d / f"{seq:02d}_{fname}"
        shutil.copy2(pdf, copy)
        items.append({
            "ts": time.time(),
            "clock": time.strftime("%H:%M"),
            "asker": asker,
            "name": fname,
            "file": copy.name,
            "summary": (summary or "")[:300],
            "is_math": is_math_content(fname),
            # 目录要用的一句话概括；提问者昵称只用于**题目那页**的页眉
            "asker_name": (asker_name or "").strip()[:24],
        })
        _save_index(day, items)
        log(f"登记 {asker}「{fname}」（当天第 {len(items)} 份）")
    except Exception as exc:
        log(f"登记失败（不影响发 PDF）：{exc!r}", "WARN")


_SEND_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}).*?发送 PDF：(\S+\.pdf) → 群文件显示名「([^」]+)」")
_DECIDE_RE = re.compile(r"决定回复（[^）]*）([^:：]+): ")
_CLOSING_RE = re.compile(r"收尾 @(\d+)")


def backfill(day: str = "", log_path: Path | None = None) -> int:
    """从 service.log 回溯补录某天的答疑 PDF。返回补录条数。

    **为什么需要**：登记挂钩是 2026-10-12 晚上才上线的，
    当天之前发的 PDF 一条都没记上（当天实测发了 54 份、登记 0 条）。
    有了它，**上线当天也能出完整讲义**。

    解析依据：
      `决定回复（…）<名字>: …`  → 当时在跟谁说话
      `发送 PDF：ans_xxx.pdf → 群文件显示名「名字.pdf」` → 发了什么
      `收尾 @<qq>`              → 收尾 @ 的是谁（用来补 asker）
    """
    day = day or time.strftime("%Y-%m-%d")
    log_path = log_path or (Path(config.BASE) / "service.log")
    if not log_path.exists():
        log(f"找不到 {log_path}，无法回溯", "WARN")
        return 0
    out_dir = Path(config.OUT_DIR)
    sender = ""
    pending: list[dict] = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = _DECIDE_RE.search(line)
        if m:
            sender = m.group(1).strip()
            continue
        m = _SEND_RE.search(line)
        if m:
            d, clock, ans, disp = m.group(1), m.group(2), m.group(3), m.group(4)
            if d != day:
                continue
            src = out_dir / ans
            if not src.exists():
                continue
            pending.append({"clock": clock, "ans": ans, "name": disp,
                            "asker": sender or "群友", "src": src, "qq": ""})
            continue
        m = _CLOSING_RE.search(line)
        if m and pending and not pending[-1]["qq"]:
            pending[-1]["qq"] = m.group(1)
            if pending[-1]["asker"] in ("", "群友"):
                pending[-1]["asker"] = f"QQ{m.group(1)}"

    if not pending:
        log(f"{day} 日志里没有可补录的 PDF")
        return 0

    d = _day_dir(day)
    d.mkdir(parents=True, exist_ok=True)
    items = _load_index(day)
    added = 0
    for c in pending:
        # 跳过已经登记过的（按 ans 文件名判定，最可靠）
        if any(it.get("ans") == c["ans"] for it in items):
            continue
        # 去重：同人 + 同名 + 时间接近 → 新的一版覆盖旧的
        hh, mm, ss = (int(x) for x in c["clock"].split(":"))
        ts = time.mktime(time.strptime(f"{day} {hh:02d}:{mm:02d}:{ss:02d}", "%Y-%m-%d %H:%M:%S"))
        items = [it for it in items
                 if not (it["asker"] == c["asker"] and it["name"] == c["name"]
                         and abs(ts - float(it.get("ts") or 0)) <= _DEDUPE_WINDOW)]
        seq = len(items) + 1
        copy = d / f"{seq:02d}_{c['name']}"
        try:
            shutil.copy2(c["src"], copy)
        except OSError as exc:
            log(f"补录复制失败 {c['ans']}：{exc}", "WARN")
            continue
        items.append({"ts": ts, "clock": c["clock"][:5], "asker": c["asker"],
                      "name": c["name"], "file": copy.name, "ans": c["ans"],
                      "summary": "", "backfilled": True,
                      "is_math": is_math_content(c["name"])})
        added += 1
    items.sort(key=lambda x: x.get("ts") or 0)
    # 重新编号，保证文件名前缀跟顺序一致
    for i, it in enumerate(items, 1):
        it["file"] = f"{i:02d}_" + it["name"]
    _save_index(day, items)
    log(f"回溯补录 {day}：新增 {added} 条，合计 {len(items)} 条")
    return added


def _target_day(now: float) -> str:
    """该汇总哪一天 = **最近一个已经结束的完整自然日**（也就是昨天）。

    ⚠️ **永远不返回"今天"** —— 今天还没过完，出来的是残缺讲义。

    真实事故（2026-10-12）：原来写的是"中午前算昨天、中午后算今天"，
    结果服务在 20:03 重启（`_last_run_day` 是内存变量，重启即丢），
    tick 判断 `(20,03) >= (00,10)` 成立 → **把当天还没结束的半截讲义发进群了**，
    而且因为反复重启，前后发了两次。

    修法就是这一条：**只汇总已经结束的日子**，配合 `.sent` 持久标记（见 maybe_run）。
    """
    return time.strftime("%Y-%m-%d", time.localtime(now - 86400))


def _sent_marker(day: str) -> Path:
    return _day_dir(day) / ".sent"


# ══════════ 内容筛选：只收数学（车主 2026-10-12 定）══════════
# 车主原话：
#   「只含解题类，还有部分数学知识的讲解，如果涉及解题和证明，你可以放上去。
#     那其他知识讲解，什么政治啊、经济学的，你就不用放了。」
#
# 判断策略：**先排除，再要求数学信号**（名字里两类词都有的，按排除走）。
# 只用关键词（免费、确定、可测）；如果哪天发现不准，再加一次 LLM 批量判定的兜底。
NON_MATH_HINTS = (
    # 政治 / 经济 / 文科
    "政治", "马原", "毛概", "毛中特", "思修", "法基", "史纲", "党史", "近代史",
    "资本", "剩余价值", "有机构成", "商品经济", "唯物", "辩证法", "生产力",
    "生产关系", "剥削", "帝国主义", "经济学", "微观经济", "宏观经济", "金融",
    "会计", "管理", "法学", "哲学", "社会学", "教育学", "心理学",
    # 招生 / 择校 / 备考信息（是"信息"不是"数学内容"）
    "招生", "择校", "院校", "分数线", "复试线", "报录比", "考情", "备考",
    "经验贴", "攻略", "网课", "课程", "班型", "资料汇总", "参考书",
    # 闲聊 / 生活
    "天气", "闲聊", "表情", "图片", "推荐", "好不好吃", "游戏",
)
# 数学内容的正向信号（命中任一即视为数学）
MATH_HINTS = (
    "数学", "定理", "证明", "积分", "极限", "导数", "微分", "级数", "收敛",
    "数列", "矩阵", "行列式", "特征", "二次型", "线性", "代数", "函数",
    "连续", "可导", "单调", "有界", "一致", "中值", "泰勒", "罗尔", "柯西",
    "洛必达", "不等", "向量", "曲线", "曲面", "概率", "期望", "方差",
    "傅里叶", "幂级数", "重积分", "广义积分", "含参", "隐函数", "极值",
    "单纯形", "对偶", "最优", "λ-矩阵", "jordan", "标准形", "相似", "正交",
    "秩", "解空间", "子空间", "多项式", "整除", "辗转相除", "互素", "余式",
    # 交付形态词（答疑 PDF 基本都是这三类，是强信号）
    "题", "解答", "解法", "讲解", "批改", "订正", "思路", "推导", "证明过程",
    "例", "例题", "习题", "练习", "详解", "精讲",
)


# ── 正文关键词（比文件名强得多，实测这两组分得很开）──
MATH_IN_TEXT = (
    "定理", "证明", "求证", "求", "设", "函数", "积分", "导数", "极限", "收敛",
    "矩阵", "行列式", "向量", "空间", "特征值", "特征向量", "多项式", "不等式",
    "方程组", "连续", "可导", "微分", "级数", "内积", "正定", "对称", "秩",
    "线性", "概率", "期望", "方差", "洛必达", "罗尔", "拉格朗日", "柯西",
    "泰勒", "傅里叶", "格林", "高斯", "斯托克斯", "单纯形", "最优解", "子空间",
    "映射", "逆矩阵", "相似", "正交", "规范形", "标准形", "曲面积分", "曲线积分",
)
NONMATH_IN_TEXT = (
    "资本", "剩余价值", "使用价值", "劳动力", "生产力", "生产关系", "商品",
    "货币", "剥削", "唯物", "辩证法", "帝国主义", "阶级", "所有制", "计划经济",
    "市场经济", "不变资本", "可变资本", "有机构成", "政治", "马原", "毛概",
    "招生", "择校", "复试", "分数线", "报录比", "备考",
)


def _pdf_head_text(pdf: Path, pages: int = 2) -> str:
    """取 PDF 前几页的纯文本（用来判断内容学科）。取不到就返回空串。"""
    try:
        r = subprocess.run(["pdftotext", "-f", "1", "-l", str(pages), str(pdf), "-"],
                           capture_output=True, text=True, timeout=25)
        return (r.stdout or "")[:4000]
    except Exception:
        return ""


def classify(pdf: Path | None, name: str) -> bool:
    """判断这份 PDF 是不是**数学的解题/证明/知识讲解**（车主 2026-10-12 定）。

    两段判断（实测文件名单独不够用）：
      ① **正文**（`pdftotext` 前 2 页）—— 数数数学词和政治经济词哪边多，更可信
      ② **文件名** —— 正文拿不到时的兜底

    真实误判案例（都是文件名单独判会错的）：
      · 「三种构成关系讲解」文件名像数学 → 正文全是「资本/不变资本 c/可变资本 v」→ 政治
      · 「例9.4与例9.14详解」文件名没数学词 → 正文是「内积空间/正定实对称矩阵」→ 数学
    """
    nm = str(name or "")
    nm_low = nm.lower()
    # 文件名里有明确的非数学信号 → 直接排除（最省事、最可靠）
    if any(h.lower() in nm_low for h in NON_MATH_HINTS):
        return False
    txt = _pdf_head_text(pdf) if pdf else ""
    if txt:
        m = sum(txt.count(h) for h in MATH_IN_TEXT)
        nm_ = sum(txt.count(h) for h in NONMATH_IN_TEXT)
        if m > nm_:
            return True
        if nm_ > m:
            return False
    # 正文判不出来（或没有正文）→ 退回文件名
    return any(h.lower() in nm_low for h in MATH_HINTS)




_TEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
                "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
                "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def is_math_content(name: str) -> bool:
    """**只看文件名**的便宜版（登记时用；build 时用带正文的 classify，更准）。"""
    return classify(None, name)


def _tex_escape(s: str) -> str:
    """把**用户提供的文本**（昵称、文件名）转成能安全塞进 LaTeX 的形式。

    ⚠️ 真实事故（2026-10-12 第一期讲义）：封面直接拼了原始昵称，结果
        · 群友昵称带 emoji「👆」→ Missing character: There is no 👆 in font
        · 群友昵称带下划线「小意_OK」→ `_` 是数学模式字符 → Missing $ inserted
    两个错都会让**整张封面渲不出来**。
    **凡是要进 LaTeX 的用户文本，一律先过这个函数。**
    """
    out = []
    for ch in str(s or ""):
        if ch in _TEX_SPECIAL:
            out.append(_TEX_SPECIAL[ch])
        elif ord(ch) > 0xFFFF or 0x1F000 <= ord(ch) <= 0x1FAFF:
            # emoji / 非 BMP 字符：LaTeX 默认字体没有字形，直接去掉（留个占位）
            out.append("·")
        elif ord(ch) < 0x20:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


# 「太泛」的文件名 —— 这些不能当题目概括用，得从正文里提
_GENERIC_NAMES = {"解题过程", "批改过程", "解答过程", "推导过程", "证明过程",
                  "题目", "答案", "解析", "习题", "详解", "过程", "解答", "讲解"}


def _brief_of(it: dict, pdf: Path | None = None) -> str:
    """这道题的**一句话概括**（目录用）。

    优先用文件名 —— 模型给的文件名通常就是概括（「拉格朗日中值定理证明」）；
    文件名太泛（「批改过程」）时，从 PDF 正文首句里提。
    """
    nm = re.sub(r"\.pdf$", "", str(it.get("name") or "").strip(), flags=re.I).strip()
    if nm and nm not in _GENERIC_NAMES and len(nm) >= 3:
        return nm
    txt = _pdf_head_text(pdf, pages=1) if pdf else ""
    # 从正文里取第一句像题目的
    for raw in (txt or "").splitlines():
        s = raw.strip()
        s = re.sub(r"^[（(【\[\s]*(题目|例题|习题|问题)[）)】\]\s:：]*", "", s)
        s = re.sub(r"\s+", " ", s)
        if len(s) >= 6:
            return s[:26] + ("…" if len(s) > 26 else "")
    return nm or "（未命名）"


def _page_count(pdf: Path) -> int:
    try:
        import pymupdf
        d = pymupdf.open(pdf)
        n = len(d)
        d.close()
        return n
    except Exception:
        return 0


def _stamp_merged(pdf: Path, ranges: list[tuple], work: Path) -> bool:
    """给**合并好的**讲义按题盖章：每一页顶部标 `第 N 题　<一句话概括>`。

    ⚠️ 为什么必须放在**压缩之后**（真实踩坑 2026-10-13）：
       一开始是先盖章再压缩，结果 **Ghostscript 压缩把页眉整段丢了** ——
       页眉用的是 PyMuPDF 内置 CJK 字体，`gs -dPDFSETTINGS=/ebook`
       处理时把它丢掉了。实测：
           盖章后单文件   ✅ 有页眉
           过一遍 gs      🔴 丢了
           过一遍 pdfunite ✅ 还在
       所以顺序必须是：**合并 → 压缩 → 再盖章**。

    ranges: [(题号, item, 起始页, 结束页), ...]（页码从 1 开始，含封面页数）
    """
    if not ranges:
        return False
    # ⚠️ 这个函数是在 build() 清理完 _build 之后才调的 —— 得自己把目录建回来
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"临时目录建不了，跳过盖章：{exc!r}", "WARN")
        return False
    try:
        import pymupdf
    except Exception:
        log("没有 pymupdf，跳过盖章", "WARN")
        return False
    try:
        doc = pymupdf.open(pdf)
    except Exception as exc:
        log(f"盖章打不开文件：{exc!r}", "WARN")
        return False
    ok = 0
    try:
        for num, it, first, last in ranges:
            brief = it.get("brief") or ""
            who = (it.get("asker_name") or it.get("asker") or "").strip()
            # 补录兜底会写成 "QQ123456" —— 那是号码不是昵称，别显示
            if re.fullmatch(r"QQ\d+", who):
                who = ""
            # 纯 emoji 昵称也别显示
            if who and not any(c.isalnum() or '\u4e00' <= c <= '\u9fff' for c in who):
                who = ""
            for pno in range(first, last + 1):
                if pno < 1 or pno > len(doc):
                    continue
                page = doc[pno - 1]
                head = f"第 {num} 题　{brief}" + ("（续）" if pno > first else "")
                try:
                    page.insert_text((56, 38), head, fontsize=10.5,
                                     fontname="china-s", color=(0.30, 0.32, 0.38))
                    if pno == first and who:
                        page.insert_text((56, 54), f"提问：{who}", fontsize=8.5,
                                         fontname="china-s", color=(0.58, 0.60, 0.66))
                    page.draw_line(pymupdf.Point(56, 62),
                                   pymupdf.Point(page.rect.width - 56, 62),
                                   color=(0.86, 0.88, 0.92), width=0.7)
                    ok += 1
                except Exception as exc:
                    log(f"第 {num} 题第 {pno} 页盖章失败（继续）：{exc!r}", "WARN")
        tmp = work / "stamped_final.pdf"
        doc.save(tmp)
    finally:
        doc.close()
    if ok and tmp.exists() and tmp.stat().st_size > 1000:
        tmp.replace(pdf)          # 用盖好章的替换成品
        log(f"已盖章：{ok} 页（每页标注第 N 题）")
        return True
    log("盖章没成功，保留未盖章版本", "WARN")
    return False


def _make_cover(day: str, items: list[dict], out_dir: Path) -> Path | None:
    """封面 + **本期目录**（车主 2026-10-13 重做）。

    目录格式：`序号  一句话概括 ……………… 时间`
    车主的要求：
      · **不放提问者名字、不放 QQ 号**（提问者只在题目自己那页出现）
      · 开头直接是**题目内容的一句话概括**（如「拉格朗日中值定理」），不是文件名
      · 标时间
      · 做漂亮一点
    """
    try:
        import latexrender
        L = [
            "\\begin{center}",
            "{\\Huge\\bfseries e阶导每日答疑讲义}\\\\[12pt]",
            f"{{\\Large {day}}}\\\\[8pt]",
            f"{{\\large\\textcolor{{gray}}{{共 {len(items)} 份解答}}}}",
            "\\end{center}",
            "\\vspace{10mm}",
            "\\noindent\\textcolor{gray}{\\rule{\\textwidth}{0.8pt}}",
            "\\vspace{5mm}",
            "\\section*{本期目录}",
            "\\vspace{2mm}",
        ]
        for i, it in enumerate(items, 1):
            brief = _tex_escape(it.get("brief") or _brief_of(it))
            clock = _tex_escape(it.get("clock") or "")
            # \hfill 把时间推到右边 —— 比一行硬排好看
            L.append(
                f"\\noindent\\textbf{{{i}.}}\\hspace{{6pt}}{brief}"
                f"\\hfill{{\\small\\textcolor{{gray}}{{{clock}}}}}\\par"
                f"\\vspace{{3.2pt}}")
            L.append("\\noindent\\textcolor{gray}{\\rule{\\textwidth}{0.25pt}}"
                     "\\vspace{2.4pt}")
        L += [
            "\\vspace{6mm}",
            "\\noindent\\textcolor{gray}{\\rule{\\textwidth}{0.8pt}}",
            "\\vspace{4mm}",
            "\\begin{center}\\small\\textcolor{gray}{"
            "由 e阶导 自动汇总 · 内容由 AI 生成，仅供参考}\\end{center}",
        ]
        return latexrender.render("\n".join(L), "doc", style="")
    except Exception as exc:
        log(f"封面生成失败（讲义仍会出，只是没封面）：{exc!r}", "WARN")
        return None


def build(day: str = "") -> Path | None:
    """② 合并出讲义 PDF。返回文件路径；没内容返回 None。"""
    day = day or _target_day(time.time())
    items = _load_index(day)
    if not items:
        return None
    # 只收录数学的解题/证明/知识讲解（政治经济、招生择校这些不收）
    # ⚠️ **每次 build 都按当前规则重新判定**，不看存储的 is_math ——
    #    两个原因：① 补录进来的旧条目没有这个字段 ② 以后调词表能立刻生效
    total = len(items)
    items = [it for it in items
             if classify(_day_dir(day) / it.get("file", ""), it.get("name", ""))]
    skipped = total - len(items)
    if not items:
        log(f"{day} 有 {total} 条但都不是数学内容，不发讲义")
        return None
    if skipped:
        log(f"筛掉 {skipped} 条非数学内容，收录 {len(items)} 条")
    d = _day_dir(day)
    parts = [d / it["file"] for it in items if (d / it["file"]).exists()]
    if not parts:
        return None
    parts = parts[:config.DAILY_DIGEST_MAX_PARTS]
    log(f"合并 {day}：{len(parts)} 份 → 一份讲义")

    work = d / "_build"
    work.mkdir(exist_ok=True)
    use = items[:len(parts)]
    # 每题算一个「一句话概括」（给目录和页眉用）
    for it in use:
        if not it.get("brief"):
            it["brief"] = _brief_of(it, d / str(it.get("file") or ""))

    seq: list[Path] = []
    cover = _make_cover(day, use, work)
    if cover:
        seq.append(cover)
    seq += parts
    # 记下每题的页码范围 —— 盖章要等**压缩之后**再做（见 _stamp_merged 的说明）
    ranges: list[tuple] = []
    page = _page_count(cover) + 1 if cover else 1
    for i, it in enumerate(use, 1):
        n = _page_count(d / str(it.get("file") or "")) or 1
        ranges.append((i, it, page, page + n - 1))
        page += n

    out = d / f"e阶导每日答疑讲义-{day}.pdf"
    try:
        subprocess.run(["pdfunite", *[str(p) for p in seq], str(out)],
                       check=True, capture_output=True, timeout=120)
    except FileNotFoundError:
        log("没有 pdfunite，改用 Ghostscript", "WARN")
        try:
            subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                            f"-sOutputFile={out}", *[str(p) for p in seq]],
                           check=True, capture_output=True, timeout=180)
        except Exception as exc:
            log(f"合并失败：{exc!r}", "ERROR")
            return None
    except Exception as exc:
        log(f"合并失败：{exc!r}", "ERROR")
        return None
    if not out.exists() or out.stat().st_size < 1000:
        log("合并产物异常（太小或不存在）", "ERROR")
        return None
    shutil.rmtree(work, ignore_errors=True)

    # 结果太大就用 gs 压一道（实测 69 页 4MB → 压完明显小；失败就保留原文件）
    size_mb = out.stat().st_size / 1048576
    if size_mb > config.DAILY_DIGEST_COMPRESS_MB:
        small = out.with_name(out.stem + "_c.pdf")
        try:
            subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                            "-dPDFSETTINGS=/ebook", f"-sOutputFile={small}", str(out)],
                           check=True, capture_output=True, timeout=300)
            if small.exists() and 1000 < small.stat().st_size < out.stat().st_size:
                log(f"已压缩：{size_mb:.1f}MB → {small.stat().st_size/1048576:.1f}MB")
                small.replace(out)
            else:
                small.unlink(missing_ok=True)
        except Exception as exc:
            log(f"压缩失败（保留原文件）：{exc!r}", "WARN")
    # ⚠️ 盖章必须放在**压缩之后**（gs 会把 PyMuPDF 盖的 CJK 页眉丢掉）
    _stamp_merged(out, ranges, work)

    log(f"✅ 讲义出好了：{out.name}（{out.stat().st_size // 1024} KB）")

    # 📦 存一份到 _archive/ —— 车主以后要"再合成一个总讲义"，
    #    日清理只删原始分册，**成品必须留着**（这是总讲义的原料）。
    try:
        arch = _DIR / "_archive"
        arch.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, arch / out.name)
        log(f"已存档：_archive/{out.name}")
    except OSError as exc:
        log(f"存档失败（不影响发群）：{exc}", "WARN")
    return out


def maybe_run(group_id: int, now: float | None = None) -> None:
    """③ 主循环 tick 挂载点：到点就汇总并发送（每天一次）。"""
    global _last_run_day
    if not config.DAILY_DIGEST_ENABLED:
        return
    now = now if now is not None else time.time()
    day = _target_day(now)          # = 昨天（已结束的完整自然日）

    # ⚠️ 已发标记必须**持久化**：原来只用内存变量 `_last_run_day`，
    #    服务一重启就丢，于是每重启一次就重发一次（10-12 那天发了两次）。
    if _sent_marker(day).exists() or _last_run_day == day:
        return

    # 时间没到 → 不动。注意这里比的是"触发时刻"，配合"只汇总昨天"，
    # 无论几点重启都不会发出残缺讲义。
    lt = time.localtime(now)
    if (lt.tm_hour, lt.tm_min) < (config.DAILY_DIGEST_HOUR, config.DAILY_DIGEST_MINUTE):
        return
    _last_run_day = day
    _cleanup()
    try:
        pdf = build(day)
    except Exception as exc:
        log(f"汇总异常：{exc!r}", "ERROR")
        return
    if not pdf:
        log(f"{day} 没有可收录的答疑内容，不发讲义")
        try:
            _sent_marker(day).write_text("empty", encoding="utf-8")
        except OSError:
            pass
        return
    try:
        import onebot
        onebot.send_group_file(group_id, pdf,
                               name=f"e阶导每日答疑讲义-{day}.pdf")
        log(f"已发群里：{pdf.name}")
        # 落一个持久标记，重启也不会重发
        try:
            _sent_marker(day).write_text(str(int(time.time())), encoding="utf-8")
        except OSError as exc:
            log(f"已发标记写不进去（可能会重发）：{exc}", "WARN")
    except Exception as exc:
        log(f"发送失败：{exc!r}", "ERROR")


def _cleanup() -> None:
    """留最近 N 天的原始 PDF，老的删掉（车主定：先别急着删）。"""
    try:
        keep = max(1, config.DAILY_KEEP_DAYS)
        if not _DIR.exists():
            return
        # ⚠️ `_archive/` 里是**成品讲义**，永远不在这里清（它另有一套保留策略）
        days = sorted(p for p in _DIR.iterdir() if p.is_dir() and p.name != "_archive")
        for p in days[:-keep]:
            shutil.rmtree(p, ignore_errors=True)
            log(f"清理过期讲义目录：{p.name}")
    except Exception as exc:
        log(f"清理失败（不影响功能）：{exc!r}", "WARN")


def archive_count() -> int:
    a = _DIR / "_archive"
    return len(list(a.glob("*.pdf"))) if a.exists() else 0


def stats() -> str:
    if not _DIR.exists():
        return "还没开始记录"
    days = sorted(p.name for p in _DIR.iterdir() if p.is_dir() and p.name != "_archive")
    return (f"{len(days)} 天记录" + (f"，最近 {days[-1]}" if days else "")
            + f"，成品存档 {archive_count()} 份")
