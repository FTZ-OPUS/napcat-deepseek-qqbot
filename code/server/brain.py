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
【本次排版用「风格 2」—— 彩色框版】
提问者点名要了这个风格，模板里已经加载好 tcolorbox，可以直接用：
- **题目**用 `\\begin{problembox}` 框住（蓝色框）：
    `\\begin{problembox}[题目]` … `\\end{problembox}`
- **定理/引理/结论**用 `\\begin{theorembox}` 框住（红色框）：
    `\\begin{theorembox}[定理]` … `\\end{theorembox}`（括号里可以写「定理」「引理」「推论」）
- 想强调易错点可以用 `\\begin{notebox}[注意]`（橙色框）

- ⚠️ **解答过程绝对不要用框！** 直接当普通正文写，白底黑字。
  这是车主明确要求的 —— 题目和解答都是色块的话，整页没有视觉对比，
  反而看不出哪里是题目、哪里是解答。**框只用来框"输入"，不框"输出"。**
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

_VALID_MODES = {"card", "doc"}

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


def decide(context: str, latest: str, mentioned: bool,
           pdf_requested: bool = False, style_hint: str = "",
           image_urls: list[str] | None = None,
           bot_name: str = "", style_variant: str = "",
           max_tokens: int | None = None) -> dict:
    """问模型：这条要不要接？怎么接？返回 {"action", "parts", "reason"}。

    image_urls 是 data-url 形式的图片，非空时走多模态请求。
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

    user_prompt = f"""下面是群里最近的聊天记录，按时间从早到晚：

{context or '（暂无历史）'}

—— 以上是背景。下面是需要你判断的最新一条消息 ——

{latest}

{hint}
{style_block}{pdf_hint}{image_hint}
请决定要不要接这条话。注意：群里不需要每条都回，只在能提供价值、或者被点名时才回。
如果要回，就用群友的口吻回，需要展示公式时才用 math 片段。"""

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
    max_tokens = max_tokens or (8000 if pdf_requested else 3000)

    # 依次尝试各条线路：主线路挂了就自动换备用，不让机器人哑掉
    for provider in config.PROVIDERS:
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
        endpoint = f"{provider['base_url']}/chat/completions"

        try:
            resp = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {provider['key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=180,
            )
        except requests.RequestException as exc:
            last_reason = f"{provider['name']} 网络错误: {exc}"
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

        try:
            resp = requests.post(
                f"{provider['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {provider['key']}",
                    "Content-Type": "application/json",
                },
                json=payload, timeout=120,
            )
        except requests.RequestException:
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
