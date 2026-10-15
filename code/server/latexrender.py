"""LaTeX 渲染的 Python 包装 —— 真正干活的是 latex/render.py。

安全边界在这里：数学正文来自模型，属于不可信内容，
所以交给 render.py 做宏过滤 + -no-shell-escape + 超时。
"""
from __future__ import annotations

import re
import subprocess
import sys
import uuid
from pathlib import Path

import config

RENDER_TIMEOUT = 180   # TikZ/PGFPlots 绘图比纯公式慢，给足余量

_CJK = re.compile(r"[\u4e00-\u9fff]")


class RenderError(RuntimeError):
    pass


def _ensure_math_mode(body: str) -> str:
    """兜底：模型经常忘记写 $...$ 或 \\[...\\]，裸写公式会导致 Missing $ inserted。

    只在「整段都是纯数学、且没有任何定界符」时才自动包上 \\[...\\]。
    含中文的混排内容不敢乱包——那会把正文塞进数学模式，错得更离谱。
    """
    if any(tok in body for tok in ("$", "\\[", "\\]", "\\(", "\\begin{")):
        return body
    if _CJK.search(body):
        return body
    return "\\[\n" + body + "\n\\]"


# 模型有时把换行写成**字面的两个字符**「反斜杠 + n」，直接进 .tex 就炸。
# ⚠️ 必须加「后面不跟字母」这个条件 —— \n 是 \neq / \nabla / \newline /
#    \node 这些命令的开头，无脑替换会把 \neq 变成 eq。
_LITERAL_NEWLINE = re.compile(r"(?<!\\)\\n(?![A-Za-z])")


def _fix_literal_newlines(body: str) -> str:
    """把模型写的字面 `\\n` 换成真换行。

    为什么需要：模型在 JSON 里写 `\\\\n`（本想表示换行），JSON 解析后变成
    **字面的两个字符「反斜杠 + n」**，直接进 .tex 就编译失败：

        l.64 \\section*{题目}\\n设 $0<a_n<1$...
        Undefined control sequence.

    真实事故（2026-10-11）：群友让人批改作业，渲染失败 →
    「所有片段都没发成功」，但收尾话还说「订正版附上了」，
    群友追问「批改完了PDF咋不发」。一天内犯了两次。

    安全性：负向先行断言 (?![A-Za-z]) 保证只有「后面是中文/标点/空白/行尾」
    才替换。实测 \\neq / \\nabla / \\newline / \\node 全部原样保留。
    """
    return _LITERAL_NEWLINE.sub("\n", body)


def render(latex_body: str, mode: str = "card", style: str | None = None) -> Path:
    """把 LaTeX 正文渲染成图片(card/longimg)或 PDF(doc)，返回产物路径。

    style 默认取 config.LATEX_DEFAULT_STYLE（车主 2026-10-11 定：默认「风格 2」
    彩色框版式）。card 模板没有框的区别，风格 2 对 card 自动回退普通模板。
    """
    if style is None:
        style = config.LATEX_DEFAULT_STYLE
    # longimg = 一张竖长图（群友说「要图片」时用，比 PDF 好读；见 latex/template_longimg.tex）
    if mode not in ("card", "doc", "longimg"):
        mode = "card"
    if not latex_body.strip():
        raise RenderError("LaTeX 正文为空")

    # 先修字面 \n（模型转义残留），再做数学模式兜底
    latex_body = _fix_literal_newlines(latex_body.strip())
    latex_body = strip_toolcomplaint(latex_body)
    latex_body = _ensure_math_mode(latex_body)

    name = f"ans_{uuid.uuid4().hex[:10]}"
    body_file = config.STATE_DIR / f"{name}.tex"
    body_file.write_text(latex_body, encoding="utf-8")

    cmd = [
        sys.executable,
        str(config.LATEX_DIR / "render.py"),
        "--mode", mode,
        "--body-file", str(body_file),
        "--out", str(config.OUT_DIR),
        "--name", name,
        "--style", str(style),
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=RENDER_TIMEOUT, cwd=str(config.LATEX_DIR)
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"渲染超时（{RENDER_TIMEOUT}s）") from exc

    if proc.returncode != 0:
        raise RenderError((proc.stderr or proc.stdout or "").strip()[-400:])

    # card 和 longimg 都出 PNG；doc 出 PDF
    out = config.OUT_DIR / f"{name}.{'png' if mode in ('card', 'longimg') else 'pdf'}"
    if not out.exists():
        raise RenderError(f"渲染脚本没报错但产物不存在: {out}")

    body_file.unlink(missing_ok=True)
    return out

# ══════════ 清洗：模型不该说的「工具/身份」话（车主 2026-10-14 定）══════════
# 实测：gpt-6-astra 会在正文里写
#   「说明：当前没有可调用的 astra 工具，下面直接用求导法给出完整解答。」
# 这种话对群友毫无价值、还显得很怪（它以为自己"应该有工具"）。
# 教师身份的模型更不该暴露"我是个 AI / 我没有工具"这类自曝身份的话。
_TOOLCOMPLAINT = re.compile(
    r'^[^。\n]{0,40}'
    r'(没有.{0,12}(可调用|可用|相应)?的.{0,8}工具'
    r'|没有.{0,6}(工具|插件|function|call)'
    r'|无法(调用|访问|使用).{0,8}工具'
    r'|工具.{0,4}(不可用|不存在|没准备好)'
    r'|当前没有.{0,20}工具'
    r'|作为(一个)?AI'
    r'|我(是|作为)(一个)?(AI|人工智能|语言模型|大模型)'
    r'|我(没有|不能).{0,10}(工具|联网|实时)'
    # 自报身份（模型被身份提示词带偏时会这么说）
    r'|我是.{0,14}(一个|的)?[^。]{0,8}(助手|AI|人工智能|模型|开发环境|机器人)'
    r'|我(由|是).{0,20}(OpenAI|Anthropic|Google|Microsoft|Kimi|Kiro|小米|字节)'
    r'|我的.{0,8}(知识|训练).{0,6}(截止|数据|于)'
    # 「我无法核实路由/后台」这类自我怀疑（2026-10-14 真实事故）
    r'|(无法|不能|没法|难以)(核实|确定|保证|指定|确认).{0,14}(路由|后台|哪个模型|是不是)'
    r'|(路由|后台).{0,10}(情况|记录|是不是|有没有)'
    r'|(答出来|答案).{0,10}(不能|无法).{0,12}(证据|证明)'
    r')',
    re.IGNORECASE,
)


def strip_toolcomplaint(latex: str) -> str:
    """删掉模型自曝身份/抱怨工具/怀疑路由的句子，其余原样返回。

    ⚠️ **按句删，不只按行删**（2026-10-14 修正）：
    实测模型会把真内容和废话写成**同一行**——
      「这题证明三个子空间两两正交。后台是不是 Astra 我无法核实，先把证明写清楚。」
    只按整行匹配的话，true内容会跟着废话一起被留下（或一起被删）。
    所以先按中文句号/换行切句，**逐句判断**，命中才丢那半句。
    """
    if not latex:
        return latex
    _PROTECT = ("\\begin{", "\\end{", "\\addplot", "\\draw", "\\node",
                "\\frac", "\\int", "\\sum", "$$", "\\[")

    def _clean_one(seg: str) -> str:
        """清理一行（可能含多句）。"""
        if not seg.strip():
            return seg
        if any(k in seg for k in _PROTECT):
            return seg                       # 含公式/图的行一律不动
        # 按中文句号切句，逐句判断
        out, buf = [], ""
        for ch in seg:
            buf += ch
            if ch in "。！？":
                if not _TOOLCOMPLAINT.search(buf):
                    out.append(buf)
                buf = ""
        if buf and not _TOOLCOMPLAINT.search(buf):
            out.append(buf)
        return "".join(out).strip()

    out_lines = []
    for ln in latex.splitlines():
        _st = ln.strip()
        if _st and not any(k in ln for k in _PROTECT) and _TOOLCOMPLAINT.search(_st):
            # 整行命中 → 逐句清（可能整行清空，也可能留半句）
            cleaned = _clean_one(ln)
            if cleaned.strip():
                out_lines.append(cleaned)
            continue
        out_lines.append(_clean_one(ln) if _st and not any(k in ln for k in _PROTECT) else ln)
    return "\n".join(out_lines)

