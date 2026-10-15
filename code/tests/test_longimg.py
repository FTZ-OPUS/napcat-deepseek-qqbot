#!/usr/bin/env python3
"""回归测试：长图模式 longimg（车主 2026-10-12 定）。

**背景**：别的机器人（凛音同学）能发」竖长图" —— 题目+解答排在一张图里，
群里直接往下翻、不用下载文件。车主问能不能仿，就做了这个。

**三个曾经踩过的坑（都在这测着）**：
  ① `_VALID_MODES` 忘了加 longimg → 模型明明返回了 longimg，
     却在归一化时被**悄悄改成 card**，长图根本出不来（排查花了半天）
  ② 主路径默认只给 3000 输出预算 → 长图内容被截断 → JSON 不完整 → 只回兜底话术
  ③ 光用提示词强制不可靠 → 模型会当耳旁风，只回一行文字（必须校验+重试）

跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_longimg.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
sys.path.insert(0, str(HERE.parent / "latex"))
os.environ.setdefault("QQBOT_STATE_DIR", tempfile.mkdtemp(prefix="qqbot-test-state-"))

try:
    from tests.test_tablecheck import T
except ImportError:
    from test_tablecheck import T  # noqa: E402

try:
    import requests  # noqa: F401
except ImportError:  # pragma: no cover
    import types as _t
    _r = _t.ModuleType("requests")

    class _E(Exception):
        pass

    _r.RequestException = _E
    _r.get = lambda *a, **k: (_ for _ in ()).throw(_E("stub"))
    _r.post = lambda *a, **k: (_ for _ in ()).throw(_E("stub"))
    sys.modules["requests"] = _r

import brain     # noqa: E402
import persona   # noqa: E402

# main 会拉起 pdf2md（需要 pymupdf4llm，本地开发机可能没装）→ 拉不起就跳过相关断言
try:
    import main  # noqa: E402
    HAVE_MAIN = True
except (Exception, SystemExit) as _exc:  # noqa: pragma: no cover
    # ⚠️ pdf2md 缺依赖时会直接 sys.exit() —— SystemExit 不是 Exception 的子类，
    #    只写 except Exception 抓不住（真实踩坑）。
    print(f"  ⏭ 无法导入 main（{_exc}），涉及 main 的断言将跳过")
    main = None
    HAVE_MAIN = False

def _src(name: str) -> Path:
    """源码目录：**本地**是 qq-bot/server/xxx.py，**服务器**是平铺的 qq-bot/xxx.py。"""
    for c in (HERE.parent / "server" / name, HERE.parent / name):
        if c.exists():
            return c
    return HERE.parent / "server" / name


LATEX = HERE.parent / "latex" if (HERE.parent / "latex").exists() else HERE.parent / "latex"


def test_valid_modes_includes_longimg():
    print("== ★坑①：_VALID_MODES 必须含 longimg（忘了会被悄悄改成 card）==")
    T.ok("longimg" in brain._VALID_MODES, f"_VALID_MODES = {brain._VALID_MODES}")
    T.ok("card" in brain._VALID_MODES and "doc" in brain._VALID_MODES, "老的 mode 还在")


def test_template_exists_and_registered():
    print("== 长图模板存在且已注册 ==")
    tpl = LATEX / "template_longimg.tex"
    T.ok(tpl.exists(), "template_longimg.tex 在")
    txt = tpl.read_text(encoding="utf-8")
    T.ok("paperheight=2500mm" in txt.replace(" ", ""), "是超长单页（2500mm）")
    T.ok("pagestyle{empty}" in txt, "没页码页眉（裁图后才好看）")
    T.ok("titlesec" in txt, "用了 titlesec 做彩色分区条")
    T.ok("pgfplots" in txt and "tikz" in txt, "能画配图")
    import render as R
    T.ok("longimg" in R.TEMPLATES, "render.TEMPLATES 注册了 longimg")


def test_rasterize_long_has_crop():
    print("== ★裁掉末尾空白是长图的关键（否则下半张全白）==")
    import render as R
    T.ok(hasattr(R, "rasterize_long"), "有 rasterize_long")
    T.ok(hasattr(R, "_crop_bottom"), "有 _crop_bottom")
    # 造一张」上面有字、下面全白」的图，验证裁掉
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("    ⏭ 没 Pillow，跳过")
        return
    im = Image.new("RGB", (400, 800), "white")
    ImageDraw.Draw(im).rectangle([20, 20, 380, 200], fill="black")
    out = R._crop_bottom(im, pad=10)
    T.ok(out.height < 300, f"从 800 裁到 {out.height}（只留内容）")
    T.ok(out.width == 400, "宽度不变")


def test_wants_long_image_detection():
    print("== 群友要图片的判定（只认「要图片当输出」，不是看到「图片」就触发）==")
    if not HAVE_MAIN:
        print("    ⏭ 跳过（本地没有 main 的依赖）")
        return
    for t in ("帮我用图片发出来", "能发个长图吗", "别发PDF，发图片",
              "截图给我看看", "做成图吧"):
        T.ok(main.wants_long_image(t), f"触发：{t}")
    for t in ("这道题怎么做", "这张图片里的题怎么解", "求极限", "谢谢"):
        T.ok(not main.wants_long_image(t), f"不触发：{t}")


def test_persona_mentions_longimg():
    print("== persona 要告诉模型有 longimg 这个模式 ==")
    p = persona.PERSONA
    T.ok("longimg" in p, "persona 提到 longimg")
    T.ok("竖长图" in p, "说明了它是竖长图")
    T.ok("tikz" in p or "pgfplots" in p, "鼓励配图（tikz/pgfplots）")


def test_force_mode_budget():
    print("== ★坑②：强制模式必须给足输出预算（否则被截断）==")
    src = _src("brain.py").read_text(encoding="utf-8")
    T.ok("if force_mode and max_tokens < 12000" in src,
         "有 force_mode 的预算保护")


def test_force_mode_validated_in_main():
    print("== ★坑③：强制 mode 必须校验+重试（模型会当耳旁风）==")
    if not HAVE_MAIN:
        print("    ⏭ 跳过（本地没有 main 的依赖）")
        return
    src = _src("main.py").read_text(encoding="utf-8")
    T.ok("重试" in src and "没按要求出" in src, "有校验+重试逻辑")
    T.ok("_force_mode" in src, "用了 force_mode")


def test_send_parts_treats_longimg_as_image():
    print("== longimg 要走「发图片」分支，不是「发文件」==")
    if not HAVE_MAIN:
        print("    ⏭ 跳过（本地没有 main 的依赖）")
        return
    src = _src("main.py").read_text(encoding="utf-8")
    T.ok('part["mode"] in ("card", "longimg")' in src,
         "send_parts 把 longimg 当图片发")


def test_default_is_pdf_not_longimg():
    print("== ★默认必须是 PDF（实测 PDF 渲染更快），长图只在要图片/需要配图时用 ==")
    p = persona.PERSONA
    T.ok("一律默认用" in p and "doc" in p and "渲染更快" in p, "persona 说明了默认 PDF 且给了速度理由")
    T.ok("只有群友明确点名要图片时" in p, "只有点名要图片才切长图")
    T.ok("需要画图就在 PDF 里画" in p, "需要配图时在 PDF 里画（不是切长图）")


def test_all_pdf_templates_have_tikz():
    print("== ★PDF 模板必须能画图（TikZ/pgfplots）—— 车主定：需要图就在 PDF 里画 ==")
    for name in ("template_card.tex", "template_doc.tex",
                 "template_doc_style2.tex", "template_longimg.tex"):
        f = LATEX / name
        T.ok(f.exists(), f"{name} 存在")
        txt = f.read_text(encoding="utf-8")
        T.ok("usepackage{tikz}" in txt, f"{name} 加载 tikz")
        T.ok("pgfplots" in txt, f"{name} 加载 pgfplots")
        T.ok("compat=1.18" in txt, f"{name} 设了 compat（版本不对 axis 会报错）")


def test_explicit_image_request_phrase():
    print("== ★车主指定的标准说法「本题解答需要图片」必须触发 ==")
    if not HAVE_MAIN:
        print("    ⏭ 跳过（本地没有 main 的依赖）")
        return
    for t in ("本题解答需要图片", "这题解答需要图片，发我", "解答需要图片"):
        T.ok(main.wants_long_image(t), f"触发：{t}")
    # 只是"需要画图"不触发 —— 那种情况应该在 PDF 里画
    for t in ("这题要画函数图像", "要画个图", "这题有图吗"):
        T.ok(not main.wants_long_image(t), f"不触发（该在 PDF 里画）：{t}")


def test_image_working_line_exists():
    print("== 发长图前要先回一句「图片正在渲染」==")
    if not HAVE_MAIN:
        print("    ⏭ 跳过")
        return
    T.ok(bool(main.IMG_WORKING_LINES), "有提示语表")
    T.ok(any("渲染" in x for x in main.IMG_WORKING_LINES), "含「渲染」字样")


if __name__ == "__main__":
    for fn in (test_valid_modes_includes_longimg, test_template_exists_and_registered,
               test_rasterize_long_has_crop, test_wants_long_image_detection,
               test_persona_mentions_longimg, test_force_mode_budget,
               test_force_mode_validated_in_main,
               test_send_parts_treats_longimg_as_image,
               test_default_is_pdf_not_longimg, test_all_pdf_templates_have_tikz,
               test_explicit_image_request_phrase, test_image_working_line_exists):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
