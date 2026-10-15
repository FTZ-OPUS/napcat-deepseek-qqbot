"""DeepSeek 调用 —— 让模型自己判断"要不要接话"以及"要不要排版公式"。

关键点：用 JSON 输出模式拿到结构化结果，而不是事后正则猜。
"""
from __future__ import annotations

import json
import re
import sys
import time

import requests

import config
import persona
import polib
import traplib
import usage

_ENDPOINT = f"{config.DEEPSEEK_BASE_URL}/chat/completions"


def log_issue(msg: str) -> None:
    """brain 里没有主循环的 log()，这里单独写一条，方便排查。"""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [WARN] brain: {msg}"
    print(line, file=sys.stderr, flush=True)
    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ============================================================
#  静态提示词片段（集中放这里，方便启动时自检）
# ============================================================

STYLE2_HINT = """
【排版版式说明 —— 默认就是「风格 2」彩色框版】
所有 PDF（doc 模式）渲染都用彩色框模板，tcolorbox 已加载好，可以直接用：
- **题目**用 `\\begin{problembox}` 框住（蓝色框）：
    `\\begin{problembox}[题目]` … `\\end{problembox}`
- **定理/引理/结论**用 `\\begin{theorembox}` 框住（红色框）：
    `\\begin{theorembox}[定理]` … `\\end{theorembox}`（括号里可以写「定理」「引理」「推论」）
- 想强调易错点可以用 `\\begin{notebox}[注意]`（橙色框）

- ⚠️ **解答过程绝对不要用框！** 直接当普通正文写，白底黑字。
  这是车主明确要求的 —— 题目和解答都是色块的话，整页没有视觉对比，
  反而看不出哪里是题目、哪里是解答。**框只用来框"输入"，不框"输出"。**
- ⚠️ **框环境（problembox/theorembox/notebox）只许用在 doc/PDF 里**：
  card 图片模板没有加载这些环境，在 card 里写会直接编译失败。
  card 图片的排版走模板自带样式，正常写公式就行。
- 配色可用模板里定义的：ptblue / ptred / ptgold / ptgreen / ptpurple / ptteal / ptorange
- 画图时也可以用这些颜色（比如 `\\draw[ptblue, very thick]`）
"""

PDF_HINT = """
【本次硬性要求：输出 PDF】
提问者明确要求用 PDF 形式回复。请把完整的解答合并成 **一个** math 片段，mode 固定填 "doc"。
- 中文讲解直接写在 LaTeX 正文里当普通段落，公式用 \\[...\\] 包起来
- 不要再输出 text 片段，所有内容都进 PDF
- 正文里不要出现 \\documentclass / \\usepackage / \\begin{document}
- 结构建议：先说思路一段，再给关键公式，再给结论
- ⚠️ 什么情况才允许不输出 PDF：**只有这条消息里根本没有具体问题/任务**，
  纯粹在聊 PDF 这个格式的时候（问你能不能读 PDF、评价别人的 PDF、
  让别人去发 PDF）—— 那就正常用文字回，别硬造文档。
- ⚠️ 反过来（车主 2026-10-11 钉死的规矩）：只要消息里**带着具体的问题或任务**、
  又提到要 PDF，那就是要「PDF 版的解答」，**必须输出 mode="doc"**——
  哪怕解答只有几行、很短，也不许只回文字。
"""

# 带图时用。__N__ 会被替换成实际张数（用 replace 而不是 format，
# 免得文案里的花括号跟 format 语法打架）
IMAGE_HINT_TEMPLATE = """
【注意：这条消息带了 __N__ 张图片，你**能直接看到**】
- 请认真看图再开口。如果是别人发的解题步骤、代码、笔记截图并让你批改，
  就逐步核对：哪一步对、哪一步错、错在什么地方，别含糊带过
- 严禁说"我看不到图片""请把图片转成文字"这类话 —— 你确实能看到
- 图上文字看不清的部分，就直说看不清，不要瞎猜
- ⚠️ **先把题面逐字符抄准再动笔**：下标、上标、求和/积分的上下限、正负号、
  每一个条件（连续？可导？二阶导连续？非零？有界？）都要抄对。
  **这是最容易出错的地方** —— 群友反馈过「好多下标识别的不对」。
- ⚠️ **绝对不许臆加题目没写的条件。** 题目没说二阶导连续，就**不能默认它连续**；
  没说可导、非零、有界，就不能假设。真实事故：有人把不连续的 f'' 默认成连续，
  整个证明白做，被指出来才改对。
  题目条件不全时，**明说「题目里没给 X」**，然后分情况讨论或说明你按什么理解 ——
  **不许默默补一个条件当成题目的一部分**。
- 抄完题在心里复检一遍：把抄录和图上原文逐个符号比一遍，确认没抄错再解。
"""

# 单纯形表验算没过时，带着错误清单让模型重算（tablecheck.revise 回调用）。
# __ISSUES__ / __LATEX__ 由 revise_table 用 replace 填（别用 format，花括号打架）
TABLE_FIX_PROMPT = """
【表格数字校对任务】
下面这段数学解答的 LaTeX 正文里，单纯形表已经被程序用精确分数验算过，发现这些错误：

__ISSUES__

—— 原正文如下 ——

__LATEX__

—— 原正文结束 ——

请把每一处都改对。要求：
- 先逐条对照上面的错误清单，再从头用 σ=c−z 约定把每张表重算一遍
  （σ_j = c_j − z_j，目标值 z = c_B·b，θ = b/a，换基：主元行归一化、其余行消元）
- 只改数字和由数字导出的结论（σ 行 / z 值 / θ 列 / c_B / 入基出基的文字说明）
- 表格结构、文字讲解、排版命令**全部保持原样**，不要增删表、不要换环境
- 前面的表改了，后面所有表跟着重算，别只改被抓到的那一处
- 如果重算后发现题目其实无可行解或无界，就明确改口说明，不要硬凑一个解
- 输出的 LaTeX 会被原样编译，反斜杠正常写

只输出一个 JSON 对象，格式：{"latex": "修正后的完整 LaTeX 正文"}
"""


def _assert_prompt_fragments_clean() -> None:
    """启动自检：静态提示词里不许出现控制字符。

    ⚠️ 为什么必须有这个问题：在满是中文和 LaTeX 的提示词里，反斜杠是雷区。
    写成 `\\toprule` 是对的，写成 `\toprule` 就被 Python 转成「制表符 + oprule」，
    `\bottomrule` 变成「退格符 + ottomrule」。这种损坏**不报错、不崩溃**，
    模型只是悄悄读到垃圾还照着做 —— persona.py 里刚踩过一次，很难从输出反推原因。

    这里**只查静态文案，不查拼接后的完整提示词**：
      群聊上下文和用户消息是拼进去的，那些内容里出现制表符是正常的，不能误报。
      而静态文案一律用空格缩进，所以出现制表符或退格符必然是转义损坏。

    注意 brain.py 不能照搬 persona.py 的「扫源文件」做法 ——
    这个文件里有正当的单反斜杠（正则、以及 _ESCAPE_MAP 里的 r"\\textbackslash{}"）。
    所以这里只在运行时检查静态文案的值。
    """
    forbidden = re.compile(r"[\x00-\x09\x0b-\x1f]")   # 允许换行(LF)和回车(CR)，其余控制字符都算损坏
    for label, text in (("PDF_HINT", PDF_HINT), ("IMAGE_HINT_TEMPLATE", IMAGE_HINT_TEMPLATE),
                        ("TABLE_FIX_PROMPT", TABLE_FIX_PROMPT)):
        hits = forbidden.findall(text)
        if hits:
            codes = ", ".join(sorted({hex(ord(c)) for c in hits}))
            raise ValueError(
                f"brain.py 的 {label} 里含控制字符（{codes}）——"
                f"几乎肯定是某个 LaTeX 反斜杠写成了单个、被 Python 转义掉了。"
                f"提示词里的反斜杠必须写成两个。"
            )


_assert_prompt_fragments_clean()

# longimg = 一张竖长图（群友要图片时用）。
# ⚠️ 真实事故（2026-10-12）：忘了把 longimg 加进来 → 模型明明按提示词返回了
#    mode="longimg"，却在归一化时被**悄悄改成 card**，长图根本出不来。
#    排查花了半天 —— 提示词、渲染、发送全都对，问题在这个集合里。
_VALID_MODES = {"card", "doc", "longimg"}

# 文件名里不能出现的字符（会破坏 QQ 文件上传，甚至被当成路径分隔符）
_BAD_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_filename(raw) -> str:
    """清洗模型给的文件名。允许中文，挡掉路径分隔符和控制字符。"""
    name = _BAD_FILENAME_RE.sub("", str(raw or "")).strip().strip(".")
    name = name[:40].strip()
    for suffix in (".pdf", ".PDF", ".Pdf"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()

# LaTeX 特殊字符转义。用于「用户要 PDF 但模型只给了纯文字」时，
# 把中文讲解安全地塞进 PDF —— 不转义的话 % _ & # 这些字符会直接编译失败。
_ESCAPE_MAP = [
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"), ("_", r"\_"),
    ("{", r"\{"), ("}", r"\}"),
    ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}"),
]


def _escape_latex(s: str) -> str:
    for raw, safe in _ESCAPE_MAP:
        s = s.replace(raw, safe)
    return s


def _coerce_pdf(parts: list[dict]) -> list[dict]:
    """用户明确要 PDF 时的兜底：保证最终一定产出一个 doc 片段。

    正常情况模型会听话直接给 doc；这里是它没听话时的保险。
    ⚠️ 例外（车主 2026-10-11 定的）：模型一个公式都没给、只回纯文字，
    多半是它用了 PDF_HINT 里的出口——判断了「这条其实不是在要文件」
    （真实案例：有人问"你能不能阅读 pdf"，被硬掰成一份 PDF 文档）。
    聊天就该是聊天，纯文字回答放行，别做成文件。
    """
    # 要 PDF 的时候，表情包/视频没有意义（全都要塞进文档里）
    parts = [p for p in parts if p["kind"] in ("text", "math")]

    if any(p["kind"] == "math" and p["mode"] == "doc" for p in parts):
        return parts

    if not any(p["kind"] == "math" for p in parts):
        return parts          # 纯文字回答：模型判断不需要文件，尊重它

    maths = [p for p in parts if p["kind"] == "math"]
    texts = [p["text"] for p in parts if p["kind"] == "text"]

    if maths:
        merged = "\n\n".join(p["latex"] for p in maths)
        if texts:
            head = "\n\n".join(_escape_latex(t) for t in texts)
            merged = head + "\n\n" + merged
        caption = next(
            (p.get("caption", "") for p in parts if p["kind"] == "math" and p.get("caption")), ""
        )
        fname = next(
            (p.get("filename", "") for p in parts if p["kind"] == "math" and p.get("filename")), ""
        )
        return [{"kind": "math", "mode": "doc", "latex": merged,
                 "caption": caption, "filename": fname}]

    if texts:
        body = "\n\n".join(_escape_latex(t) for t in texts)
        return [{"kind": "math", "mode": "doc", "latex": body, "caption": "", "filename": ""}]

    return parts


def _extract_json(raw: str) -> dict:
    """模型偶尔会裹上 ```json 或前后加话，这里做容错剥离。"""
    s = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        s = s[start:end + 1]
    return json.loads(s)


def _normalize(result: dict, pdf_requested: bool = False) -> dict:
    """清洗模型的输出，让它一定符合我们下游的预期。"""
    action = str(result.get("action", "ignore")).lower()
    if action not in ("reply", "ignore"):
        action = "ignore"

    parts_in = result.get("parts")
    parts = []
    if isinstance(parts_in, list):
        for item in parts_in:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind == "text":
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append({"kind": "text", "text": text})
            elif kind == "math":
                latex = str(item.get("latex", "")).strip()
                if not latex:
                    continue
                mode = item.get("mode") if item.get("mode") in _VALID_MODES else "card"
                parts.append({
                    "kind": "math",
                    "mode": mode,
                    "latex": latex,
                    "caption": str(item.get("caption", "")).strip(),
                    # PDF 的显示文件名由模型按任务类型起（批改过程 / 解题过程 / 推导过程…）
                    "filename": _safe_filename(item.get("filename")),
                })
            elif kind in ("sticker", "video"):
                # 让机器人能从素材库里发一张表情包 / 一个视频
                # （具体发哪个由 media.py 轮换决定，模型不需要知道素材内容）
                parts.append({"kind": kind})
            if len(parts) >= config.MAX_PARTS:
                break

    if pdf_requested:
        parts = _coerce_pdf(parts)

    if not parts:
        action = "ignore"

    return {
        "action": action,
        "parts": parts,
        "reason": str(result.get("reason", ""))[:200],
        # 发完文件后单独 @ 提问者的收尾话，纯聊天时为空
        "closing": str(result.get("closing", "")).strip()[:120],
    }


# ══════════ 抄题复检（车主 2026-10-12 定）══════════
# 只在两种情况触发（见 main.wants_recognition_recheck）：
#   ① 要求「详细/严谨」解题 ② 二次追问（说上次解错了 / 让重做）
TRANSCRIBE_PROMPT = """【抄题复检任务】
把图里的题目**逐字符原样抄录**出来。这不是解题，只是**核对题面**。

要求：
1. 下标、上标、指数、分数、求和/积分的上下限 —— 一个都不能抄错
2. **题目写的每一个条件都要抄全**（连续？可导？二阶导连续？非零？有界？正负？）
3. **绝对不许补充题目没写的内容。** 题目没说某阶导数连续，就不许写「连续」
4. 看不清的地方写 [看不清]，**不要猜**
5. 只输出题面本身 —— 不要解题、不要点评、不要加标题

直接输出抄录结果。"""


def _post_llm(provider: dict, payload: dict, timeout: int = 180,
              kind: str = "decide"):
    """**统一的 LLM 调用出口** —— 顺手把 token 用量记进 usage。

    为什么要统一：以前 5 个地方各写一遍 `requests.post`，
    想统计用量就得改 5 处、还容易漏掉一处。全部走这里，一处挂钩。

    返回 `requests.Response`；网络错误返回 None（调用方按原来的方式处理）。
    """
    try:
        resp = requests.post(
            f"{provider['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {provider['key']}",
                     "Content-Type": "application/json"},
            json=payload, timeout=timeout)
    except requests.RequestException as exc:
        # ⚠️⚠️ 网络异常也分两种（2026-10-15 修正）：
        #   · **连不上**（ConnectionError / ConnectTimeout）→ 厂商真挂了 → 熔断
        #   · **读超时**（ReadTimeout）→ 只是这个模型想得久 → **绝不熔断**
        #     教训：GPT6.1SOL 本来就要跑 30~90 秒，一次读超时就把整个 jieyouai
        #     熔断，连完全正常的 ASTRA 也一起跳过 —— 跟 DeepSeek 那次一个性质。
        _name = type(exc).__name__
        _is_conn = isinstance(exc, (requests.ConnectionError, requests.ConnectTimeout)) \
            or "Connection" in _name or "ConnectTimeout" in _name
        try:
            import linehealth
            if _is_conn:
                linehealth.note_failure(provider, f"连不上 {_name}（厂商级）")
            else:
                log_issue(f"⏱ {provider.get('name')} 读超时（{_name}）—— 只是慢，"
                          f"**不熔断厂商**，继续试下一条")
        except Exception:
            pass
        return None
    # ⚠️⚠️ 失败要**分成两类**（车主 2026-10-15：「这两个要区分一下」）：
    #
    #   A. **厂商级故障**（网络错、超时、5xx）→ 这家整体挂了 → **熔断整个厂商**
    #   B. **密钥级问题**（401/402/403/429）→ 只是**这把 key** 不行
    #      （余额用完 / 被限流 / 没权限）→ **绝不能熔断厂商** ❌
    #
    # 为什么这条最要紧：备用2 余额只剩 ¥0.24，很快会返回 402。
    # 如果 402 也熔断厂商，就会把**备用1/3/4 三个好好的官方号一起跳过** ——
    # 一次欠费把整个厂商拖下水。这是 2026-10-15 复查时发现的隐患。
    _KEY_LEVEL = {401, 402, 403, 429}
    if resp is not None and resp.status_code != 200:
        _code = resp.status_code
        if _code in _KEY_LEVEL:
            # 只记一笔日志，不动熔断
            try:
                _hint = {401: "密钥无效/未授权", 402: "余额不足",
                         403: "无权限", 429: "被限流"}.get(_code, "")
                log_issue(f"⚠️ {provider.get('name')} 返回 HTTP {_code}（{_hint}）"
                          f"—— 这是**这把 key 的问题**，不熔断整个厂商，继续试下一条")
                try:
                    import linehealth
                    linehealth.note_key_problem(provider, _code, _hint)
                except Exception:
                    pass
            except Exception:
                pass
        elif 500 <= _code <= 599:
            # 5xx = 服务端自己出错 → 厂商级故障 → 熔断
            try:
                import linehealth
                linehealth.note_failure(provider, f"HTTP {_code}（厂商级故障）")
            except Exception:
                pass
        else:
            # 其它非 200（4xx 里没列到的）→ 记一笔，但不熔断
            try:
                log_issue(f"⚠️ {provider.get('name')} 返回 HTTP {_code}（不熔断，继续下一条）")
            except Exception:
                pass
    # 只记成功的调用（失败的没产生费用）
    try:
        if resp.status_code == 200:
            usage.record(provider.get("name", "?"), provider.get("model", ""),
                         (resp.json() or {}).get("usage"), kind)
            try:
                import linehealth
                linehealth.note_success(provider)      # 通了就清掉该厂商的熔断
            except Exception:
                pass
    except Exception:
        pass
    return resp


def _try_transcribe(provider: dict, content: list, timeout: int = 180) -> str:
    """用某条线路抄一次题。成功返回文本，失败返回空串。"""
    payload = {
        "model": provider["model"],
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 3000,
    }
    if provider.get("extra"):
        payload.update(provider["extra"])
    resp = _post_llm(provider, payload, timeout=timeout, kind="transcribe")
    if resp is None or resp.status_code != 200:
        return ""
    try:
        return (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return ""


def transcribe_problem(image_urls: list[str], prefer_vision_api: bool = False) -> str:
    """识图复检：**单独跑一次**「把题面逐字符抄下来」。

    `prefer_vision_api=True` 时**先走识图 API（Gemini）**（车主 2026-10-13 定）——
    用在**手写草稿**上：群友反映 Gemini 认手写更准，而 DeepSeek 更快（0.8s vs 8.9s）。
    所以正常图片/PDF 不调这个参数，照旧走 DeepSeek。

    ⚠️ **识图 API 只负责"抄题"，解答仍然交给 DeepSeek** ——
    这是车主定的分工：识图归识图，答疑归答疑。

    ⚠️ 任何失败都返回空串，**绝不让复检本身把回答搞挂** —— 调用方按「没复检」继续。
    """
    if not image_urls:
        return ""
    content = [{"type": "text", "text": TRANSCRIBE_PROMPT}]
    content += [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]

    _MIN_USEFUL = 8          # 少于这个字数认为"没读出来"

    def _looks_useless(s: str) -> bool:
        _s = (s or "").strip()
        if len(_s) < _MIN_USEFUL:
            return True
        # ⚠️ 「看不清」也算失败 —— 实测 DeepSeek 对着渲染坏掉的图会老实回
        #    「[看不清] [看不清] …」，字符数够长、看着"有内容"，但其实是废的。
        for w in ("无法识别", "看不到", "没有图片", "未提供图片", "无法查看",
                  "看不清", "无法辨认", "模糊不清"):
            if w in _s[:30]:
                return True
        return False

    def _ask_vision_api() -> str:
        if not config.VISION_API_KEY:
            return ""
        vp = {"name": "识图API", "base_url": config.VISION_API_BASE,
              "key": config.VISION_API_KEY, "model": config.VISION_API_MODEL,
              "extra": {}}
        t0 = time.time()
        txt = _try_transcribe(vp, content, timeout=240) or ""
        if txt and not _looks_useless(txt):
            log_issue(f"识图 API（{config.VISION_API_MODEL}）抄题成功："
                      f"{len(txt)} 字符，{time.time() - t0:.1f}s")
            return txt
        return ""

    # ══════════ 识图分两种模式（车主 2026-10-14 定）══════════
    # 模式二（受限期，现在）：**全用 DeepSeek**，不碰 Gemini。
    #     车主：「政治题一般手写的很少，现在模式二你就默认都 DeepSeek 识别就行」
    # 模式一（恢复后）：原来的分工 —— 手写稿优先 Gemini（群友反馈认手写更准），
    #     其余走 DeepSeek，两条路互相兜底。
    #     ⚠️ 这段代码**不能删** —— 车主说「我说恢复你再恢复到上次那个路线」。
    _mode2_only_deepseek = (getattr(config, "BOT_MODE", 2) == 2)

    if _mode2_only_deepseek:
        # 识图：按 config.PROVIDERS 原序（DeepSeek 优先），但
        # ⚠️ 2026-10-15 加熔断：DeepSeek 官方挂了就直接跳过，落到小米/千帆/火山。
        #    车主：「你发现一个 DeepSeek 用不了，那基本上就全用不了了，
        #          代表官方你就直接切掉……自动切换备用路线，千万不要我说。」
        _cands = list(config.PROVIDERS)
        try:
            import linehealth
            _cands = linehealth.order(_cands)
        except Exception:
            pass
        for provider in _cands:
            _try = _try_transcribe(provider, content)
            if _try and not _looks_useless(_try):
                log_issue(f"抄题（{provider['name']}·{provider['model']}）成功：{len(_try)} 字符")
                return _try

        # 🛟 兜底：**所有线路都读不出来**时，用 Gemini 识图 API 再试一次
        #    （车主：Gemini 和小米都能识图 —— 这俩都挂了的概率极低）
        log_issue("抄题：所有常规线路都没读出内容，用 Gemini 识图 API 兜底")
        _g = _ask_vision_api()
        if _g:
            return _g
        log_issue("抄题失败（含 Gemini 兜底），按无复检继续")
        return ""

    # ── 以下只在模式一执行 ──
    # ① 手写稿 → 先试识图 API（Gemini）
    if prefer_vision_api:
        _v = _ask_vision_api()
        if _v:
            return _v
        log_issue("识图 API 没给出内容，回退到普通线路")

    # ② 普通线路（DeepSeek）
    for provider in config.PROVIDERS:
        _try = _try_transcribe(provider, content)
        if _try and not _looks_useless(_try):
            log_issue(f"抄题复检成功（{provider['name']}，{len(_try)} 字符）")
            return _try

    # ③ 模式一才有的最后兜底：DeepSeek 也没读出来 → 再给 Gemini 一次机会
    _g = _ask_vision_api()
    if _g:
        return _g
    log_issue("抄题复检失败（DeepSeek 和 Gemini 都没给出内容），按无复检继续")
    return ""


# ══════════ LaTeX 自动修复（渲染失败后自己救，绝不让群友重发）══════════
# 车主 2026-10-12 定：「我们办事最好只办一次，不要让群众跑几趟。」
# 真实事故：群友发手写图让批改，渲染挂了 → 机器人回「麻烦你再发我一次，我重做」
#          → 群友就不太愿意再发了。**这是最不该发生的事。**
LATEX_FIX_PROMPT = """【LaTeX 修复任务】
下面这段 LaTeX 正文在 xelatex 编译时报错了，请把它改到**能编译通过**。

报错信息（可能被截断）：
__ERROR__

要求：
1. **只修导致编译失败的地方**，数学内容和结论一个字都不要改
2. 常见原因：环境没配对（\\begin/\\end 不匹配）、用了模板没加载的宏包或命令、
   花括号/圆括号不配对、数学模式定界符漏了或多了
3. 如果用了模板不支持的命令，**换成等效的常见 LaTeX 写法**
   （例如 \\xrightarrow{f} 可以写成 \\to 配文字说明）
4. 不要加 \\documentclass / \\usepackage / \\begin{document}
5. **只输出修好的正文本身** —— 不要解释、不要用代码块包裹

—— 出错的正文 ——
__LATEX__
—— 正文结束 ——"""


def fix_latex(latex: str, error: str) -> str:
    """渲染失败后让模型自己修一遍 LaTeX。返回修好的正文；修不了就返回空串。

    ⚠️ 调用方拿到非空返回值才重试渲染；**任何失败都返回空串**，
    绝不让"修复"这一步把流程搞挂。

    线路顺序 = config.PROVIDERS，第一条是 DeepSeek 主线路。
    """
    if not latex or not error:
        return ""
    prompt = (LATEX_FIX_PROMPT
              .replace("__ERROR__", str(error)[:1200])
              .replace("__LATEX__", latex))
    for provider in config.PROVIDERS:
        payload = {
            "model": provider["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 16000,
        }
        if provider.get("extra"):
            payload.update(provider["extra"])
        resp = _post_llm(provider, payload, timeout=180, kind="fix_latex")
        if resp is None:
            continue
        if resp.status_code != 200:
            continue
        try:
            txt = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            continue
        if not txt:
            continue
        # 去掉可能被包上的 ```latex ``` 代码块
        m = re.match(r"^```(?:latex|tex)?\s*(.*?)\s*```$", txt, re.S)
        if m:
            txt = m.group(1).strip()
        if txt and txt != latex:
            log_issue(f"LaTeX 自动修复成功（{provider['name']}，{len(txt)} 字符）")
            return txt
    log_issue("LaTeX 自动修复失败（所有线路都没给出可用内容）")
    return ""


def judge_suggestion(item: dict) -> dict | None:
    """判断一条群消息是不是「对机器人的建议」，是的话整理成人可读的条目。

    给 feedback.py 的异步提炼用（见那里的两段式设计）。
    **任何失败都返回 None**，调用方会把原条目留着下次再试。
    """
    import feedback                                  # 延迟导入，避免循环依赖
    prompt = (feedback.ENRICH_PROMPT
              .replace("__SENDER__", str(item.get("sender", "")))
              .replace("__TEXT__", str(item.get("text", "")))
              .replace("__QUOTED__", str(item.get("quoted") or "（无）"))
              .replace("__RECENT__", "\n".join(item.get("recent") or []) or "（无）")
              .replace("__REPLY__", str(item.get("bot_reply") or "（未记录）")))
    for provider in config.PROVIDERS:
        payload = {
            "model": provider["model"],
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
            "max_tokens": 800,
        }
        if provider.get("extra"):
            payload.update(provider["extra"])
        resp = _post_llm(provider, payload, timeout=90, kind="judge")
        if resp is None:
            continue
        if resp.status_code != 200:
            continue
        try:
            content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
            if not content:
                continue
            return json.loads(content)
        except Exception:
            continue
    return None


def decide(context: str, latest: str, mentioned: bool,
           pdf_requested: bool = False, style_hint: str = "",
           image_urls: list[str] | None = None,
           bot_name: str = "", style_variant: str = "",
           max_tokens: int | None = None, force_mode: str = "",
           extra_card: bool = False,
           providers: list | None = None) -> dict:
    """问模型：这条要不要接？怎么接？返回 {"action", "parts", "reason"}。

    image_urls 是 data-url 形式的图片，非空时走多模态请求。

    force_mode 非空时**强制本次用这个 mode 出 math 片段**（card/doc/longimg）。
    为什么要这个参数：光在 context 里写一句「请用 longimg」，模型会当耳旁风
    （真实事故 2026-10-12：群友说「用图片发出来」，结果只回了一行文字，连图都没有）。
    所以把硬要求放在提示词**最末尾、最显眼**的位置。
    bot_name 是机器人当前的真实昵称/群名片，用来替换人格提示词里的占位符 ——
    不传就用兜底名（见 persona.DEFAULT_BOT_NAME）。
    """
    if not config.DEEPSEEK_API_KEY:
        return {"action": "ignore", "parts": [], "reason": "缺少 API key"}

    # 被点名的待遇和其他消息完全不同：不是「可以考虑」，是「必须接住」
    if mentioned:
        hint = ("这条消息**点名提到了你**。你必须回复——action 只能是 \"reply\"，不允许是 \"ignore\"。"
                "哪怕问题很模糊、或者你觉得没什么可说的，也要用一句自然简短的话接住，别冷场。")
    else:
        hint = "这条消息没有点名你。只在确实能提供价值、或者话题正好接得上时才开口。"

    # 融入阶段的行为提示（刚进群 vs 老熟人，说话方式和主动性不一样）
    style_block = f"\n【当前状态】{style_hint}\n" if style_hint else ""

    # 静态片段都在模块顶部，启动时已自检过（见 _assert_prompt_fragments_clean）
    pdf_hint = PDF_HINT if pdf_requested else ""
    # 风格 2：告诉模型可以用彩色框（但**只有题目/定理能框，解答别框**）
    if style_variant == "2":
        pdf_hint += STYLE2_HINT
    image_hint = (IMAGE_HINT_TEMPLATE.replace("__N__", str(len(image_urls)))
                  if image_urls else "")

    # 🪤 数学分析陷阱库（车主 2026-10-12 定）：按题目关键词挑**相关**的几条注入。
    #    没命中就返回空串，**不占任何 token** —— 闲聊和小问题完全不受影响。
    #    治的是「把题目没写的条件当成有」（条件臆造），今天那个二阶导事故就是这类。
    trap_hint = traplib.hint(latest)

    # 🏛 考研政治考点库（车主 2026-10-12 定）：按题目关键词取相关考点。
    #    为什么政治题特别需要它：政治考点碎、易混点多，靠模型自带记忆容易漏。
    #    同样**没命中就返回空串**，不占 token。
    politics_hint = polib.hint(latest)

    # ⚠️ 强制 mode：必须放在**最末尾**（模型对提示词结尾的指令最敏感）
    force_block = ""
    if force_mode:
        _what = {"longimg": "一张竖长图（mode 填 longimg）",
                 "doc": "一个 PDF 文件（mode 填 doc）",
                 "card": "一张小图（mode 填 card）"}.get(force_mode, f"mode={force_mode}")
        force_block = (
            f"\n\n【⚠️⚠️⚠️ 最高优先级：本次**必须**输出一个 mode=\"{force_mode}\" 的 math 片段】\n"
            f"群友明确要的是{_what}。\n"
            f"**只回一行文字 = 完全没完成这次任务**，群友会非常失望。\n"
            f"你**必须**把题目抄全、解答写完整（写详细，不要怕长），该画图就写 tikz 代码，"
            f"然后放进 mode=\"{force_mode}\" 的 math 片段里。\n"
            f"这条要求**优先于**其他任何考虑（包括「简短一点」「几句话就够」的倾向）。")
    # ⭐ 车主 2026-10-15 定：政治选择题要 PDF 时，PDF 后面还得再跟一张关系图。
    # 原话：「政治选择题即使对方要求 PDF，那你也得在 PDF 后面你也跟一个你渲染
    #        出来的图片，用那个 tikz 那种……那个箭头最好有字啊，就一个箭头，
    #        比如决定啊，反作用啊。如果你认为实在没得写，那就可以不写。」
    if extra_card:
        force_block += (
            "\\n\\n【⚠️⚠️⚠️ 还要**再输出一个 mode=\"card\" 的 math 片段**】\\n"
            "这次是**两个片段**，顺序不能反：\\n"
            "  ① mode=\"doc\"  —— PDF 正文（按上面那条要求写）\\n"
            "  ② mode=\"card\" —— **一张关系图**，跟在 PDF 后面单独发\\n"
            "图的内容：把这道题里几个概念之间的关系画出来。\\n"
            "⚠️ **箭头必须写字**（车主明确要求）—— 比如：\\n"
            "    \\\\draw[rarr, rbA] (a) -- node[above,font=\\\\scriptsize]{决定} (b);\\n"
            "  政治题常见的箭头标注：决定 / 反作用 / 决定于 / 包括 / 表现为 / 派生出\\n"
            "  **实在没得写的才留空**，但先想一遍有没有词可写。\\n"
            "⚠️ 图用**手写 TikZ 风格**（rbox/rarr 那套），别用自动布局的 smartdiagram。")

    # ⚠️⚠️ 数形结合：出文件时**强烈要求画图**（车主 2026-10-14 反馈）
    # 车主原话：「你答数学的时候，你注意一下数形结合……如果有函数图像的题，
    # 你就绘个图，适合数形结合，你就绘个图辅助一下理解。
    # 如果实在没法数形结合的那就不用做。」
    #
    # 为什么之前没画：persona 里那条规则写得很细，但埋在两千多行提示词的中段，
    # 模型根本不执行（实测最近 6 份 PDF 一张图都没有）。
    # 所以照 force_mode 的成功经验，把要求挪到**提示词最末尾**——
    # 模型对结尾的指令最敏感。
    draw_block = ""
    if pdf_requested or force_mode in ("doc", "longimg"):
        draw_block = (
            "\n\n【⚠️⚠️ 数形结合：本次**必须认真考虑画图**】\n"
            "在写解答之前，先问自己一句：**这题配一张图会不会让人一眼就懂？**\n"
            "如果会 —— **必须在正文里写 tikzpicture / pgfplots 代码**，不能只写公式。\n"
            "典型该画的情形：\n"
            "  · 函数图像 / 单调性 / 极值 / 凹凸（pgfplots 画曲线）\n"
            "  · 积分面积 / 两条曲线之间 / 旋转体截面\n"
            "  · 不等式区域 / 可行域 / 线性规划\n"
            "  · 几何关系 / 三角形 / 圆 / 坐标系 / 向量\n"
            "  · 数列收敛趋势 / 夹逼 / 振荡\n"
            "  · 概率分布 / 密度曲线\n"
            "写法（模板已加载 tikz+pgfplots，配色用 plotA/plotB/plotC/shade）：\n"
            "  \\begin{tikzpicture}\\begin{axis}[axis lines=middle,xlabel={$x$},ylabel={$y$},\n"
            "      width=8cm,height=5.5cm,samples=200]\n"
            "  \\addplot[thick,plotA,domain=-2:2]{x^2};\n"
            "  \\node[plotA] at (axis cs:1.6,3.4) {\\small $y=x^2$};\n"
            "  \\end{axis}\\end{tikzpicture}\n"
            "⚠️ 只有**纯代数化简、纯计算、纯逻辑推导**这类确实没法配图的，才不画。\n"
            "⚠️ 画图是**加分项**：该画不画，这份解答就「干瘪」了；不该画硬画，才显得外行。\n"
            "\n【⚠️⚠️ 不许在正文里提工具和身份】\n"
            "严禁出现这类句子（写出来 = 废话，直接删掉）：\n"
            "  · 「当前没有可调用的 XXX 工具」「没有联网工具」「无法调用工具」\n"
            "  · 「我是 AI / 我是语言模型 / 我的知识截止到 XXXX 年」\n"
            "  · 「我是 XXX，一个开发环境 / 助手」这类自报身份的话\n"
            "  · ⚠️ 尤其禁止聊**路由/后台**：「我无法核实后台是不是 Astra」「路由情况看后台记录」\n"
            "    「答出来不能当作路由成功的证据」—— 这类自我怀疑**一个字都不许写**\n"
            "群友只关心**题目怎么解**。你是谁、有没有工具、能不能联网 —— "
            "一概不提，直接上干货。")

    user_prompt = f"""下面是群里最近的聊天记录，按时间从早到晚：

{context or '（暂无历史）'}

—— 以上是背景。下面是需要你判断的最新一条消息 ——

{latest}

{hint}
{trap_hint}
{politics_hint}
{style_block}{pdf_hint}{image_hint}
请决定要不要接这条话。注意：群里不需要每条都回，只在能提供价值、或者被点名时才回。
如果要回，就用群友的口吻回，需要展示公式时才用 math 片段。{force_block}{draw_block}"""

    # 有图就构造多模态 content 数组，没图就用普通字符串（省得走不一样的分支）
    if image_urls:
        user_content: object = [{"type": "text", "text": user_prompt}]
        user_content += [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
    else:
        user_content = user_prompt

    # 把人格提示词里的昵称占位符换成当前真实名字（车主可能改过名）
    system_prompt = (persona.PERSONA.replace(
        persona.BOT_NAME_PLACEHOLDER,
        (bot_name or "").strip() or persona.DEFAULT_BOT_NAME,
    ) + "\n\n" + persona.OUTPUT_RULES)
    last_reason = "没有配置任何可用的 API 线路"

    # 输出预算：要出一份 PDF（尤其是运筹学那种好几张单纯形表的题）时，
    # 1500 token 根本不够 —— 会写到一半被截断，JSON 不完整 → 整条回复报废。
    # 实测两阶段法完整解需要 3000+ 输出 token。
    # max_tokens 参数给 PDF 任务用：不强制 PDF（让模型按体量选文字/图片/PDF）
    # 但保留大预算，防止它选了长解答却被截断。
    # PDF 模式给足预算，否则长解答会被截断（实测官方 deepseek-chat 接受 16000，
    # 这里取 12000 留点余量；截断比写短更糟 —— 截断会直接判为失败换线路）
    # 2026-10-12 提到 16000：要求「详细/严谨」时正文更长，20000 字符上限要配得上
    max_tokens = max_tokens or (16000 if pdf_requested else 3000)
    # ⚠️ 强制出长图/PDF 时同样要给足预算 ——
    #    真实事故（2026-10-12）：群友要长图，主路径默认只给 3000，
    #    长图内容写到一半被截断 → JSON 不完整 → 只能回兜底话术，图没出来。
    if force_mode and max_tokens < 12000:
        max_tokens = 16000

    # 依次尝试各条线路：主线路挂了就自动换备用，不让机器人哑掉。
    # ⚠️ 2026-10-15 加：**熔断的厂商排到最后**（比如 DeepSeek 官方挂了，
    #    就直接落到小米/千帆/火山，不再挨个撞超时）。参考 linehealth.py。
    _cands = list(providers if providers else config.PROVIDERS)
    try:
        import linehealth
        _cands = linehealth.order(_cands)
    except Exception:
        pass
    for provider in _cands:
        payload = {
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 1.05,
            "max_tokens": max_tokens,
        }
        # 线路专属参数（比如火山方舟必须 thinking=disabled，否则正文可能是空的）
        if provider.get("extra"):
            payload.update(provider["extra"])
        # 点名线（GPT/ASTRA）是"想得久"的模型，给更长的超时；
        # 普通线保持 180s，避免群友等太久。
        _to = 300 if (provider.get("name") or "").startswith("GPT") else 180
        resp = _post_llm(provider, payload, timeout=_to, kind="decide")
        if resp is None:
            last_reason = f"{provider['name']} 网络错误"
            continue

        if resp.status_code != 200:
            last_reason = f"{provider['name']} HTTP {resp.status_code}: {resp.text[:150]}"
            continue

        try:
            body = resp.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]

            # ⚠️ 截断必须单独识别，不能当成"解析失败"往下换线路。
            # 截断是 max_tokens 不够导致的，换哪条线路都会同样截断 ——
            # 以前会因此白烧四次调用，最后还返回空。
            if choice.get("finish_reason") == "length":
                last_reason = (f"{provider['name']} 输出被 max_tokens={max_tokens} 截断，"
                               f"JSON 不完整。这通常是要求的输出太长，换线路也一样")
                log_issue(last_reason)
                break

            # 空正文单独识别 —— 以前只会报"无法解析"，看不出真正原因。
            # 典型场景：推理模型把 max_tokens 全用在思考上，正文一个字都没有。
            if not (content or "").strip():
                last_reason = (
                    f"{provider['name']} 返回空正文（finish_reason="
                    f"{choice.get('finish_reason')}）。若是推理模型，"
                    f"通常是思考吃光了 max_tokens 配额")
                log_issue(last_reason)
                continue

            result = _normalize(_extract_json(content), pdf_requested)
        except Exception as exc:
            last_reason = f"{provider['name']} 返回内容无法解析: {exc}"
            continue

        result["provider"] = provider["name"]
        return result

    return {"action": "ignore", "parts": [], "reason": last_reason}


def revise_table(latex: str, issues: list[str]) -> str | None:
    """单纯形表验算没过 → 带着错误清单让模型重算一遍。

    只干这一件事：返回修正后的完整 LaTeX 正文；任何失败返回 None
    （调用方会走兜底，不会让机器人哑掉）。max_tokens 给到和 PDF 同级 ——
    要回传的是整篇正文，被截断的 JSON 等于白改。
    """
    if not config.DEEPSEEK_API_KEY or not (issues and latex.strip()):
        return None

    issue_text = "\n".join(f"{k}. {s}" for k, s in enumerate(issues, 1))
    user_prompt = (TABLE_FIX_PROMPT
                   .replace("__ISSUES__", issue_text)
                   .replace("__LATEX__", latex))

    for provider in config.PROVIDERS:
        payload = {
            "model": provider["model"],
            "messages": [{"role": "user", "content": user_prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,          # 校对任务要稳，不要发散
            "max_tokens": 8000,
        }
        if provider.get("extra"):
            payload.update(provider["extra"])

        resp = _post_llm(provider, payload, timeout=120, kind="tablecheck")
        if resp is None:
            continue
        if resp.status_code != 200:
            continue

        try:
            choice = resp.json()["choices"][0]
            content = choice["message"]["content"]
            # 截断的 JSON 解不出来也修不了，换线路同样截断，直接放弃这条线路
            if choice.get("finish_reason") == "length" or not (content or "").strip():
                continue
            fixed = _extract_json(content).get("latex")
        except Exception:
            continue

        if isinstance(fixed, str) and fixed.strip() \
                and fixed.strip() != latex.strip():
            return fixed.strip()
    return None
