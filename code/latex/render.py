#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
LaTeX 渲染管道 —— 把模型生成的内容安全地编译成 PDF / PNG。

安全设计（重要，别删）：
  1. 模板固定，只替换占位符，模型碰不到导言区 —— 防止样式漂移，也挡住 \usepackage 之类的注入
  2. 模型输出一律当作「不可信代码」，编译前过滤掉读写文件 / 执行命令的危险宏
  3. xelatex 加 -no-shell-escape + 硬超时，且在隔离的临时目录里跑
  4. 文件锁做串行队列 —— 并发编译会瞬间打满 CPU，2 核机器扛不住

用法：
  # 出图片（公式卡片）
  python3 render.py --mode card --body-file body.tex --out ../out --name answer
  # 出 PDF（多页推导）
  python3 render.py --mode doc  --body-file body.tex --out ../out --name solution
"""
from __future__ import annotations

import argparse
import fcntl
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
BODY_MARK = "%%__BODY__%%"

COMPILE_TIMEOUT = 60      # 单次编译硬超时（秒）。TikZ/PGFPlots 画图比纯公式慢不少，所以给宽一点
MAX_BODY = 8000           # 注入正文的长度上限
RASTER_DPI = 300          # 图片分辨率：300 够清晰，又不至于太大

TEMPLATES = {
    "card": BASE / "template_card.tex",   # 紧凑公式卡片 -> 图片
    "doc":  BASE / "template_doc.tex",    # 多页推导 -> PDF
}

# 危险宏黑名单：读写任意文件、执行命令、篡改类别码。
# \b 结尾保证不会误伤 \inputencoding 这类正常宏。
FORBIDDEN_RE = re.compile(
    r"\\(?:write|write18|openout|openin|read|input|include|includeonly|"
    r"catcode|csname|endcsname|special|usepackage|documentclass|RequirePackage|"
    r"lstinputlisting|verbatiminput|immediate|shipout|directlua|latelua|"
    r"newread|newwrite|closein|closeout)\b",
    re.IGNORECASE,
)

BODY_RE = re.compile(r"\\begin\{document\}(.*?)\\end\{document\}", re.DOTALL)


class RenderError(RuntimeError):
    pass


def sanitize(body: str) -> str:
    """把模型输出洗成可以安全塞进模板的正文。"""
    body = body.strip()

    # 模型有时会自己写完整的 .tex，把 document 环境里的内容摘出来
    m = BODY_RE.search(body)
    if m:
        body = m.group(1).strip()

    if len(body) > MAX_BODY:
        raise RenderError(f"正文过长（{len(body)} 字符，上限 {MAX_BODY}）")

    hit = FORBIDDEN_RE.search(body)
    if hit:
        raise RenderError(f"正文含被禁止的宏：{hit.group(0)}")

    return body


def _run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def compile_tex(mode: str, body: str, out_dir: Path, name: str, keep_tex: bool) -> tuple[Path, str]:
    """编译出 PDF，返回 (pdf 路径, 编译日志)。"""
    template = TEMPLATES[mode]
    if not template.exists():
        raise RenderError(f"模板不存在：{template}")

    source = template.read_text(encoding="utf-8")
    # 占位符必须恰好出现一次：多一处就会把正文注进注释或导言区，导致诡异的编译错误
    if source.count(BODY_MARK) != 1:
        raise RenderError(f"模板中占位符 {BODY_MARK} 应恰好出现 1 次，实际 {source.count(BODY_MARK)} 次")
    source = source.replace(BODY_MARK, sanitize(body))

    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="render-", dir=str(out_dir)))

    # 用 finally 保证失败时也清干净，否则 out/ 会堆满 render-xxxx 垃圾目录
    try:
        tex_file = work / f"{name}.tex"
        tex_file.write_text(source, encoding="utf-8")

        try:
            proc = _run(
                [
                    "xelatex",
                    "-no-shell-escape",       # 关掉 \write18 执行命令的能力
                    "-interaction=nonstopmode",  # 卡住不要等人输入，直接失败
                    "-halt-on-error",
                    "-file-line-error",
                    "-output-directory", str(work),
                    str(tex_file),
                ],
                cwd=work,
                timeout=COMPILE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise RenderError(f"编译超时（超过 {COMPILE_TIMEOUT} 秒）")

        log = proc.stdout.decode("utf-8", "replace")
        pdf = work / f"{name}.pdf"

        if proc.returncode != 0 or not pdf.exists():
            # LaTeX 的报错要翻一半日志才看得到，这里只挑关键几行
            err_lines = [l for l in log.splitlines() if l.startswith("!") or "Error" in l][:8]
            detail = "\n".join(err_lines) or log[-800:]
            raise RenderError(f"xelatex 编译失败：\n{detail}")

        final_pdf = out_dir / f"{name}.pdf"
        shutil.copy2(pdf, final_pdf)

        if keep_tex:
            shutil.copy2(tex_file, out_dir / f"{name}.tex")

        return final_pdf, log
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rasterize(pdf: Path, out_dir: Path, name: str, dpi: int = RASTER_DPI) -> Path:
    """PDF -> PNG。用 Ghostscript，Linux 上同样可用（apt install ghostscript）。"""
    gs = shutil.which("gs")
    if not gs:
        raise RenderError("找不到 gs（Ghostscript），无法转图片")

    png = out_dir / f"{name}.png"
    try:
        proc = _run(
            [
                gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
                "-sDEVICE=png16m",           # 白底彩图，任何客户端都看得清
                f"-r{dpi}",
                "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                "-dFirstPage=1", "-dLastPage=1",
                f"-sOutputFile={png}",
                str(pdf),
            ],
            cwd=out_dir,
            timeout=COMPILE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RenderError("转图片超时")

    if proc.returncode != 0 or not png.exists():
        raise RenderError("Ghostscript 转图片失败：\n" + proc.stdout.decode("utf-8", "replace")[-500:])

    return png


def main() -> int:
    ap = argparse.ArgumentParser(description="LaTeX 渲染管道")
    ap.add_argument("--mode", choices=["card", "doc"], default="card",
                    help="card=紧凑卡片出图片，doc=多页排版的 PDF")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--body", help="直接传正文")
    src.add_argument("--body-file", help="从文件读正文")
    ap.add_argument("--out", default=str(BASE.parent / "out"), help="输出目录")
    ap.add_argument("--name", default="answer", help="输出文件名（不含后缀）")
    ap.add_argument("--dpi", type=int, default=RASTER_DPI)
    ap.add_argument("--keep-tex", action="store_true", help="保留中间 .tex 便于排查")
    args = ap.parse_args()

    body = args.body if args.body is not None else Path(args.body_file).read_text(encoding="utf-8")
    out_dir = Path(args.out).resolve()

    # 串行队列：同一时刻只允许一个编译，避免并发把 CPU 打满
    lock_path = BASE / ".render.lock"
    started = time.time()
    try:
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            pdf, _ = compile_tex(args.mode, body, out_dir, args.name, args.keep_tex)
            png = rasterize(pdf, out_dir, args.name, args.dpi) if args.mode == "card" else None
    except RenderError as e:
        print(f"[失败] {e}", file=sys.stderr)
        return 1

    cost = time.time() - started
    print(f"[成功] 耗时 {cost:.1f}s")
    print(f"  PDF: {pdf}")
    if png:
        print(f"  PNG: {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
