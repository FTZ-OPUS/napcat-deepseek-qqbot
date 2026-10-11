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
    """把 LaTeX 正文渲染成图片(card)或 PDF(doc)，返回产物路径。

    style 默认取 config.LATEX_DEFAULT_STYLE（车主 2026-10-11 定：默认「风格 2」
    彩色框版式）。card 模板没有框的区别，风格 2 对 card 自动回退普通模板。
    """
    if style is None:
        style = config.LATEX_DEFAULT_STYLE
    if mode not in ("card", "doc"):
        mode = "card"
    if not latex_body.strip():
        raise RenderError("LaTeX 正文为空")

    # 先修字面 \n（模型转义残留），再做数学模式兜底
    latex_body = _fix_literal_newlines(latex_body.strip())
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

    out = config.OUT_DIR / f"{name}.{'png' if mode == 'card' else 'pdf'}"
    if not out.exists():
        raise RenderError(f"渲染脚本没报错但产物不存在: {out}")

    body_file.unlink(missing_ok=True)
    return out
