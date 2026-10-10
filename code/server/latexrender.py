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


def render(latex_body: str, mode: str = "card") -> Path:
    """把 LaTeX 正文渲染成图片(card)或 PDF(doc)，返回产物路径。"""
    if mode not in ("card", "doc"):
        mode = "card"
    if not latex_body.strip():
        raise RenderError("LaTeX 正文为空")

    latex_body = _ensure_math_mode(latex_body.strip())

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
