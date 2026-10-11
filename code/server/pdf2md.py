#!/usr/bin/env python3
"""pdf2md.py —— PDF → Markdown 转换器（pymupdf4llm 内核 + 覆盖率自检兜底）

两个用法：
  1. 命令行：python3 pdf2md.py 输入.pdf [--pages 0-23] [-o out.md]
  2. 被机器人 import：pdf_to_md(path, ...) → (markdown, 统计)

为「群友 PDF 转 MD、再交给 LLM API 处理」的场景做的轻量工具。
纯本地转换，不消耗任何 LLM API token。

核心设计 —— 逐页覆盖率自检：
  pymupdf4llm 的版面引擎遇到「公式框 / 复杂图形」时会把整块区域当图片
  处理，文字直接蒸发（真实踩坑：数学讲义里装在彩色框里的公式全丢）。
  所以每一页都做核对：先用 page.get_text() 拿该页全部文字作底本，
  再测转换出的 MD 覆盖了多少；覆盖率不足的页自动回退为纯文本输出，
  **宁可没有排版，不能没有内容**。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

try:
    import pymupdf
    import pymupdf4llm
except ImportError:
    sys.exit("缺依赖：先在 venv 里 pip install pymupdf4llm")

# 覆盖率比对这个字符集（中英文的实质内容；空白/标点/MD 符号不参与）
_KEEP_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]")
_MARK_RE = re.compile(r"</?mark>")


def _keep(s: str) -> str:
    return _KEEP_RE.sub("", s)


def _coverage(md_text: str, plain_text: str) -> float:
    """MD 对纯文本底本的内容覆盖率（0~1）。

    用原文字符 2-gram 步进采样在 MD 里找：抗排版重排/加粗切分，
    又能稳稳抓住整段蒸发的情况。底本为空（空白页）按 1.0 算。
    """
    b = _keep(plain_text)
    if len(b) < 2:
        return 1.0
    a = _keep(_MARK_RE.sub("", md_text))
    grams = [b[i:i + 2] for i in range(0, len(b) - 1, 3)]
    hits = sum(1 for g in grams if g in a)
    return hits / len(grams)


def _clean(md: str) -> str:
    """轻清理：去 <mark> 高亮标记、压多余空行。不动实质内容。"""
    md = _MARK_RE.sub("", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def convert_page(doc: pymupdf.Document, pno: int, threshold: float):
    """转换单页 → (markdown, 实际路径说明, 覆盖率)。"""
    page = doc[pno]
    plain = page.get_text("text")
    try:
        chunks = pymupdf4llm.to_markdown(
            doc, pages=[pno], page_chunks=True, show_progress=False)
        md = chunks[0].get("text", "") if chunks else ""
    except Exception as exc:                      # 引擎对个别页炸了也得给内容
        md = ""
        sys.stderr.write(f"[warn] 第 {pno} 页版面引擎异常：{exc!r}\n")
    md = _clean(md)
    cov = _coverage(md, plain)
    if cov >= threshold:
        return md, "md", cov
    # 覆盖率不足 → 纯文本兜底：内容完整优先于排版好看
    fallback = plain.strip() or "（本页无可提取文本）"
    return (
        f"<!-- 第 {pno + 1} 页版式特殊（覆盖率 {cov:.0%}），"
        "以下为纯文本兜底以保证内容完整 -->\n\n" + fallback,
        "text", cov,
    )


def parse_pages(spec: str, page_count: int) -> list[int] | None:
    """'0-23' / '0,3,7-9' → 0 基页码列表；越界自动裁掉。None = 全部。"""
    if not spec:
        return None
    pages: list[int] = []
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            a, b = piece.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(piece)
        pages.extend(p for p in range(lo, hi + 1) if 0 <= p < page_count)
    return sorted(set(pages)) or None


def pdf_to_md(path: str | Path, pages: list[int] | None = None,
              threshold: float = 0.85, max_chars: int | None = None,
              max_pages: int | None = None) -> tuple[str, dict]:
    """给机器人/其他程序调用的转换入口。

    返回 (markdown, stats)。stats: pages(转的页数)/page_count(总页数)/
    chars/fallback_pages/avg_cov/truncated(是否截断)。
    max_chars 给了就超长截断、max_pages 给了就只转前 N 页
    （都是保护下游 API 的 prompt 预算和主循环耗时）。
    """
    doc = pymupdf.open(path)
    page_list = pages if pages is not None else list(range(doc.page_count))
    if max_pages is not None:
        page_list = page_list[:max_pages]

    parts: list[str] = []
    n_fallback = 0
    covs: list[float] = []
    for pno in page_list:
        md, engine, cov = convert_page(doc, pno, threshold)
        parts.append(md)
        covs.append(cov)
        if engine == "text":
            n_fallback += 1
    out = "\n\n".join(parts) + "\n"

    truncated = False
    if max_chars is not None and len(out) > max_chars:
        out = out[:max_chars]
        truncated = True

    stats = {
        "pages": len(page_list),
        "page_count": doc.page_count,
        "chars": len(out),
        "fallback_pages": n_fallback,
        "avg_cov": sum(covs) / len(covs) if covs else 1.0,
        "truncated": truncated,
    }
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF → Markdown（内容完整性优先）")
    ap.add_argument("input", help="输入 PDF 路径")
    ap.add_argument("-o", "--out", help="输出 .md 路径（默认同名 .md）")
    ap.add_argument("--pages", default="", help='页码，0 基："0-23" 或 "0,3,7-9"')
    ap.add_argument("--stdout", action="store_true", help="输出到终端不写文件")
    ap.add_argument("--threshold", type=float, default=0.85,
                    help="兜底阈值，MD 覆盖率低于它就回退纯文本（默认 0.85）")
    args = ap.parse_args()

    src = Path(args.input).expanduser()
    if not src.exists():
        sys.exit(f"输入不存在：{src}")

    t0 = time.time()
    out_md, stats = pdf_to_md(src, pages=parse_pages(args.pages, 10 ** 6),
                              threshold=args.threshold)
    dt = time.time() - t0

    n_chars = stats["chars"]
    lo_est, hi_est = int(n_chars * 0.6 / 1000), int(n_chars * 0.8 / 1000)

    if args.stdout:
        sys.stdout.write(out_md)
    else:
        out = Path(args.out).expanduser() if args.out else src.with_suffix(".md")
        out.write_text(out_md, encoding="utf-8")
        print(f"[OK] {src.name} → {out.name}")
        print(f"     页数 {stats['pages']}/{stats['page_count']}｜耗时 {dt:.1f}s｜"
              f"平均覆盖率 {stats['avg_cov']:.0%}｜纯文本兜底 {stats['fallback_pages']} 页")
        print(f"     {n_chars} 字符｜粗估 {lo_est}~{hi_est}k tokens（喂 API 前掂量）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
