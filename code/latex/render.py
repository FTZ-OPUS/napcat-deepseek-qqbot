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
# 注入正文的长度上限。
# ⚠️ 2026-10-12 从 8000 提到 16000：群友让"一次解 10 道题"时正文自然超过 8000，
#    渲染直接失败 → 再触发收尾说假话（两条 bug 是连着的）。
#    实测参考：16000 字符的正文 xelatex 编译约 2~4 秒，离 60 秒超时很远，放开很安全。
# 2026-10-12 二次上调 16000 → 20000：重要用户反馈「解答有点简洁」、
#    要求详细/严谨时答案自然更长。实测 20000 字符编译约 2~5 秒，离 60 秒超时很远。
MAX_BODY = 20000
RASTER_DPI = 300          # 图片分辨率：300 够清晰，又不至于太大

TEMPLATES = {
    "card": BASE / "template_card.tex",   # 紧凑公式卡片 -> 图片
    "doc":  BASE / "template_doc.tex",    # 多页推导 -> PDF
    # 长图：**单页超长**，转成一张竖长 PNG（群友说「要图片」时用，比 PDF 好读）
    "longimg": BASE / "template_longimg.tex",
}

# 风格 2：题目/定理用彩色框（tcolorbox），**解答不框**、保持白底黑字。
# 只在 doc 模式有意义；card 模式没有「题目/解答」的区分。
TEMPLATES_STYLE2 = {
    "doc": BASE / "template_doc_style2.tex",
}


def pick_template(mode: str, style: str = ""):
    """按 (mode, style) 选模板。style 不认识就退回默认。"""
    if str(style).strip() == "2" and mode in TEMPLATES_STYLE2:
        return TEMPLATES_STYLE2[mode]
    return TEMPLATES[mode]

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

    # ⚠️ 渲染前自动配平环境（2026-10-12）：
    #    模型常忘写 \end{itemize} 之类，以前会让整篇渲染失败 →
    #    群友被要求「再发一次」。**这种低级错误不该让群友买单**，能自动救就自动救。
    body, notes = balance_environments(body)
    if notes:
        print(f"[配平] {'；'.join(notes)}", file=sys.stderr)

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



# ══════════ 渲染前自动配平 LaTeX 环境（车主 2026-10-12 定）══════════
# 真实事故：模型写了个 \begin{itemize} 忘了闭合 →
#   LaTeX 报 "\begin{itemize} ... ended by \end{document}" → 整篇渲染失败 →
#   群友被要求「再发一次」。**这种低级错误不该让群友买单。**
# 策略：能自动救就自动救（补 end / 删多余的 end），救不了再交给
#   brain.fix_latex 让模型修，最后才走诚实告知。
_BEGIN_RE = re.compile(r"\\begin\s*\{([^}]+)\}")
_END_RE = re.compile(r"\\end\s*\{([^}]+)\}")


def balance_environments(body: str) -> tuple[str, list[str]]:
    """配平 \\begin/\\end。返回 (修好的正文, 修了什么的说明列表)。

    规则：
      1. 没闭合的 \\begin{X} → 在末尾按**逆序**补上对应的 \\end{X}
      2. 多余的 \\end{X}（前面没有对应的 \\begin）→ 删掉
    只在真的不平衡时才动，平衡的原样返回（绝不画蛇添足）。
    """
    notes: list[str] = []
    # 先删多余的 \end
    stack: list[str] = []
    drop_spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\\(begin|end)\s*\{([^}]+)\}", body):
        kind, name = m.group(1), m.group(2)
        if kind == "begin":
            stack.append(name)
        else:
            if name in stack:
                # 从栈顶往下找第一个匹配的（容忍交叉）
                i = len(stack) - 1 - stack[::-1].index(name)
                stack.pop(i)
            else:
                drop_spans.append((m.start(), m.end()))
                notes.append(f"删掉多余的 \\end{{{name}}}")
    if drop_spans:
        for s, e in reversed(drop_spans):
            body = body[:s] + body[e:]
    # 再补没闭合的
    if stack:
        tail = "".join(f"\n\\end{{{name}}}" for name in reversed(stack))
        body = body.rstrip() + tail + "\n"
        notes.append("补上没闭合的 " + "、".join(f"\\begin{{{x}}}" for x in stack))
    return body, notes

def compile_tex(mode: str, body: str, out_dir: Path, name: str, keep_tex: bool,
                style: str = "") -> tuple[Path, str]:
    """编译出 PDF，返回 (pdf 路径, 编译日志)。"""
    template = pick_template(mode, style)
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


LONGIMG_DPI = 150          # 长图分辨率：屏幕阅读够用，又不至于几十 MB


def rasterize_long(pdf: Path, out_dir: Path, name: str,
                   dpi: int = LONGIMG_DPI) -> Path:
    """长图：把 PDF **所有页竖着拼成一张 PNG**，再**裁掉末尾空白**。

    两个关键设计：
      ① 模板把内容排在**一张 2500mm 的超长纸**上（正常情况只有 1 页），
         所以多数时候"拼接"其实是空操作 —— 没有分页缝隙，看起来是一整张。
      ② 内容短的时候底下会留大片空白，**必须裁掉**，否则发出去是一张
         上半部分有字、下半部分全白的怪图。

    内容实在超过 2500mm 时会溢出到第 2 页，那时候拼接就能救回来。
    """
    gs = shutil.which("gs")
    if not gs:
        raise RenderError("找不到 gs（Ghostscript），无法转图片")
    work = out_dir / f"_{name}_long"
    work.mkdir(parents=True, exist_ok=True)
    try:
        try:
            _run([gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
                  "-sDEVICE=png16m", f"-r{dpi}",
                  "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                  f"-sOutputFile={work}/p-%03d.png", str(pdf)],
                 cwd=work, timeout=COMPILE_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RenderError("长图转图片超时")

        pages = sorted(work.glob("p-*.png"))
        if not pages:
            raise RenderError("长图转图片没有产出")
        try:
            from PIL import Image
        except ImportError:
            # 没有 Pillow 就退回单页（不会拼接/裁剪，但至少有图）
            import shutil as _sh
            out = out_dir / f"{name}.png"
            _sh.copy2(pages[0], out)
            return out

        imgs = [Image.open(p).convert("RGB") for p in pages]
        if len(imgs) > 1:
            w = max(i.width for i in imgs)
            total_h = sum(i.height for i in imgs)
            canvas = Image.new("RGB", (w, total_h), "white")
            y = 0
            for i in imgs:
                canvas.paste(i, (0, y))
                y += i.height
            img = canvas
        else:
            img = imgs[0]

        # ▼ 裁掉底部空白（长图最关键的一步）
        img = _crop_bottom(img)
        out = out_dir / f"{name}.png"
        img.save(out, "PNG", optimize=True)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _crop_bottom(img, pad: int = 24, tol: int = 247):
    """裁掉底部（以及右侧）的空白。空白的定义是"这一行几乎全是白"。

    用整行最小值判断比逐像素快得多：只要一行里最暗的像素都 > tol，就算空白行。
    """
    try:
        from PIL import Image
        w, h = img.size
        gray = img.convert("L")
        px = gray.load()
        step = max(1, w // 400)          # 抽样，够判断了
        last = h
        for y in range(h - 1, -1, -1):
            dark = False
            for x in range(0, w, step):
                if px[x, y] <= tol:
                    dark = True
                    break
            if dark:
                last = y
                break
        new_h = min(h, last + 1 + pad)
        if new_h < h:
            img = img.crop((0, 0, w, new_h))
        return img
    except Exception:
        return img


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
    ap.add_argument("--mode", choices=["card", "doc", "longimg"], default="card",
                    help="card=紧凑卡片出图片，doc=多页排版的 PDF")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--body", help="直接传正文")
    src.add_argument("--body-file", help="从文件读正文")
    ap.add_argument("--out", default=str(BASE.parent / "out"), help="输出目录")
    ap.add_argument("--name", default="answer", help="输出文件名（不含后缀）")
    ap.add_argument("--style", default="",
                    help="排版风格：留空=默认（全白底黑字）；2=题目/定理彩色框")
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
            pdf, _ = compile_tex(args.mode, body, out_dir, args.name, args.keep_tex, args.style)
            png = None
            if args.mode == "card":
                png = rasterize(pdf, out_dir, args.name, args.dpi)
            elif args.mode == "longimg":
                png = rasterize_long(pdf, out_dir, args.name)
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
