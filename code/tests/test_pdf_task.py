"""PDF 解题任务全链路打桩测试 —— 离线，绝不发真实群。

按项目纪律打桩 onebot.call（所有发送动作只进捕获列表）；
brain.decide 也打桩（不花 API token）；下载用本地微型 PDF 模拟。
跑法：python3 tests/test_pdf_task.py
"""
from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER))

try:
    from tests.test_tablecheck import T
except ImportError:
    from test_tablecheck import T  # noqa: E402

# 本机没装 requests：打桩（导入期只需模块存在）
if "requests" not in sys.modules:
    try:
        import requests  # noqa: F401
    except ImportError:
        _fake = types.ModuleType("requests")
        class _RE(Exception):
            pass
        _fake.RequestException = _RE
        _fake.post = lambda *a, **k: (_ for _ in ()).throw(_RE("offline stub"))
        _fake.get = lambda *a, **k: (_ for _ in ()).throw(_RE("offline stub"))
        sys.modules["requests"] = _fake

import brain    # noqa: E402
import os as _os
import tempfile as _tempfile
# ⚠️ 必须在 import config 之前：把状态目录指到临时目录，
# 否则测试会写坏线上真实的 state/（2026-10-12 真实事故：游标被覆盖 → 凌晨骚扰好友）
_os.environ.setdefault("QQBOT_STATE_DIR", _tempfile.mkdtemp(prefix="qqbot-test-state-"))

import config   # noqa: E402
import humanize  # noqa: E402
import main     # noqa: E402
import onebot   # noqa: E402
import latexrender  # noqa: E402

import pymupdf  # noqa: E402  （pdf2md 依赖已装在本地 venv？没有则跳过）
try:
    import pymupdf4llm  # noqa: F401
    HAVE_PDF = True
except ImportError:
    HAVE_PDF = False

BOT_QQ = <BOT_QQ>
GROUP = 999999            # 假群号：所有发送都进桩，绝无真实消息
TINY_PDF = TESTS / "_tiny_test.pdf"

SENT: list[tuple] = []     # (action, params)
CALLS: dict = {"decide": [], "download": 0}


# ---------------- 桩 ----------------

def fake_call(action: str, timeout: int = 30, **params):
    SENT.append((action, params))
    if action == "get_msg":
        fid = params.get("message_id")
        return {"status": "ok", "retcode": 0, "data": FAKE_QUOTED.get(fid, {})}
    if action.startswith("send_") or action.startswith("upload_"):
        return {"status": "ok", "retcode": 0, "data": {"message_id": 1}}
    return {"status": "ok", "retcode": 0, "data": {}}


def fake_download(url, dest, timeout=90, max_bytes=0):
    CALLS["download"] += 1
    import shutil
    shutil.copy(TINY_PDF, dest)


def fake_decide(context, latest, mentioned=False, pdf_requested=False, *args, **kw):
    CALLS["decide"].append({"context": context, "latest": latest,
                            "mentioned": mentioned, "pdf": pdf_requested,
                            "max_tokens": kw.get("max_tokens")})
    return {"action": "reply",
            "parts": [{"kind": "math", "mode": "doc", "latex": "解：$x=1$",
                       "caption": "", "filename": "解题过程"}],
            "closing": "做完了，你看看对不对",
            "reason": "stub", "provider": "stub"}


FAKE_QUOTED = {
    111: {"message_id": 111, "message": [
        {"type": "file", "data": {"file": "测试题.pdf", "file_id": "/abc",
                                  "file_size": "1234", "url": "http://stub/x.pdf"}}]},
    222: {"message_id": 222, "message": [
        {"type": "file", "data": {"file": "笔记.txt", "file_id": "/def",
                                  "file_size": "10", "url": "http://stub/y.txt"}}]},
}

MARK_STR = "解题过程.pdf"


def make_tiny_pdf() -> None:
    # 用英文造测试 PDF：pymupdf 内置 Helvetica 写不了中文（会静默变点），
    # 中文提取效果已在服务器用真实群文件验证过，这里只验链路
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Problem 1: Let f(x) = x^2 - 4x + 3. Find the minimum.", fontsize=14)
    page2 = doc.new_page()
    page2.insert_text((72, 100), "Problem 2: Prove that 1 + 1 = 2.", fontsize=14)
    doc.save(TINY_PDF)
    doc.close()


def patch_env() -> None:
    config.BOT_QQ = BOT_QQ
    config.PDF_TASK_ENABLED = True
    config.PDF_TASK_COOLDOWN = 999.0          # 测试里手动清冷却字典
    humanize.reading_delay = lambda t: 0
    humanize.sleep_typing = lambda t: 0
    humanize.gap_between_parts = lambda: 0
    humanize.should_drop = lambda *a, **k: False
    time.sleep = lambda s: 0
    onebot.call = fake_call
    onebot.download_file = fake_download
    brain.decide = fake_decide
    import latexrender
    latexrender.render = lambda body, mode='card', style='': Path('/tmp/_stub_render.pdf')


def msg_of(reply_id, text_parts):
    segs = [{"type": "reply", "data": {"id": reply_id}},
            {"type": "at", "data": {"qq": str(BOT_QQ)}}]
    if text_parts:
        segs.append({"type": "text", "data": {"text": text_parts}})
    return segs


def reset() -> None:
    SENT.clear()
    CALLS["decide"].clear()
    CALLS["download"] = 0
    main._pdf_task_last.clear()
    import shutil
    cache = config.OUT_DIR / "pdfmd_cache"
    if cache.exists():
        shutil.rmtree(cache)     # 缓存在磁盘上，跨用例必须清干净


def run_handle(msg_segs):
    state = main.State()
    state.seeded.add(GROUP)
    msg = {"message_id": 555, "time": time.time(), "message": msg_segs,
           "sender": {"user_id": 777, "nickname": "测试群友"}}
    main.handle_message(GROUP, msg, [], 0, state)


def test_full_chain():
    print("== 引用 PDF + @ + 写要求 → 下载→转MD→解题→发PDF+收尾 ==")
    reset()
    run_handle(msg_of(111, "每页只解决第一题"))
    T.ok(CALLS["download"] == 1, "下载了引用的 PDF")
    T.ok(len(CALLS["decide"]) == 1, "调了一次解题")
    d = CALLS["decide"][0]
    T.ok(d["pdf"] is False, "不强制 PDF（模型按体量选 文字/图片/PDF）")
    # 2026-10-12：8000 → 12000（10 道题这类大活会被 8000 截断，截断比写短更糟）
    # 2026-10-12：12000 → 16000（要求「详细/严谨」时正文更长；正文上限已到 20000）
    T.ok(d["max_tokens"] == 16000, "保留大输出预算防截断")
    T.ok("输出形式" in d["context"], "上下文带了输出形式指引")
    T.ok("明确说了要 PDF" in d["context"], "带了「明确要 PDF 则必发 PDF」的优先级规则")
    T.ok("必须输出" in brain.PDF_HINT and "只有这条消息里根本没有具体问题" in brain.PDF_HINT,
         "主流程 PDF_HINT：出口只留给纯聊格式的消息，带任务+要PDF 必发 doc")
    T.ok("Find the minimum" in d["context"] and "Prove that 1 + 1 = 2" in d["context"],
         "PDF 全文（含第 2 页）进了模型输入")
    T.ok("每页只解决第一题" in d["latest"], "群友的要求原样传给模型")
    T.ok("测试题.pdf" in d["context"], "文件名进了上下文")
    actions = [a for a, _ in SENT]
    T.ok(any("upload_group_file" == a or (a == "send_group_msg" and MARK_STR in str(SENT))
             for a in actions), f"渲染出的 PDF 发回了群里（actions={actions}）")
    T.ok(any(a == "send_group_msg" and "做完了" in str(p)
             for a, p in SENT), "收尾 @ 群友")


def test_no_text_default_request():
    print("== 只引用不写字 → 默认=阅读总结（车主定的，不像解题机器）==")
    reset()
    run_handle(msg_of(111, ""))
    T.ok(len(CALLS["decide"]) == 1, "空文字也触发任务")
    T.ok("阅读总结" in CALLS["decide"][0]["latest"], "默认走阅读总结")
    T.ok("不要照抄原文" in CALLS["decide"][0]["latest"], "总结了报告写法约束")


def test_read_intent_vs_task():
    print("== 「阅读一下」→ 总结模式；具体任务 → 解题模式 ==")
    reset()
    run_handle(msg_of(111, "阅读一下"))
    T.ok("阅读总结" in CALLS["decide"][0]["latest"], "「阅读一下」归总结模式")
    reset()
    run_handle(msg_of(111, "每页只解决第一题"))
    latest = CALLS["decide"][0]["latest"]
    T.ok("每页只解决第一题" in latest and "阅读总结" not in latest,
         f"具体任务原样传给模型（latest={latest[:50]}）")
    reset()
    run_handle(msg_of(111, "总结一下第三节的证明思路"))
    T.ok("总结一下第三节的证明思路" in CALLS["decide"][0]["latest"],
         "带具体对象的「总结」不算空泛阅读，走任务模式")


def test_cooldown():
    print("== 冷却期内第二条引用 → 不下载不解题 ==")
    reset()
    run_handle(msg_of(111, "第一题"))
    run_handle(msg_of(111, "第二题"))
    T.ok(CALLS["download"] == 1 and len(CALLS["decide"]) == 1,
         "冷却刹车生效（下载和解题都只有 1 次）")


def test_non_pdf_quote():
    print("== 引用的不是 PDF（txt）→ 不触发 PDF 任务 ==")
    reset()
    run_handle(msg_of(222, "看看这个"))
    T.ok(CALLS["download"] == 0, "没下载")
    T.ok(all(not d["pdf"] for d in CALLS["decide"]),
         "没走 PDF 管线（落回普通消息流程）")


def test_scan_like_pdf():
    print("== 转不出文字的 PDF（模拟扫描件）→ 通知群友，不烧 API ==")
    reset()
    import pdf2md
    orig = pdf2md.pdf_to_md
    pdf2md.pdf_to_md = lambda *a, **k: ("", {"pages": 3, "page_count": 3,
                                             "chars": 0, "fallback_pages": 3,
                                             "avg_cov": 0.0, "truncated": False})
    try:
        run_handle(msg_of(111, "解题"))
    finally:
        pdf2md.pdf_to_md = orig
    T.ok(len(CALLS["decide"]) == 0, "没调解题 API（token 没白花）")
    T.ok(any(a == "send_group_msg" and "扫描件" in str(p) for a, p in SENT),
         "给群友发了说明")


def test_text_only_reply_no_closing():
    print("== 简短回答纯打字 → 不渲染文件也不叠收尾（像正常人说话）==")
    reset()
    orig = brain.decide
    brain.decide = lambda *a, **kw: {
        "action": "reply",
        "parts": [{"kind": "text", "text": "能啊，你把 PDF 发群里引用我就行"}],
        "closing": "", "reason": "stub", "provider": "stub"}
    try:
        run_handle(msg_of(111, "你能不能阅读PDF"))
    finally:
        brain.decide = orig
    actions = [a for a, _ in SENT]
    T.ok(not any("upload_group_file" == a for a in actions), "没渲染文件")
    T.ok(not any(a == "send_group_at" for a in actions), "没叠收尾 @")
    T.ok(any(a == "send_group_msg" and "能啊" in str(p) for a, p in SENT),
         "回答直接打字发出")


def test_coerce_pdf_respects_text_only():
    print("== _coerce_pdf：纯文字回答不再被硬掰成 PDF（车主反馈的病根）==")
    parts = [{"kind": "text", "text": "能啊，你发过来我看看"}]
    out = brain._coerce_pdf(list(parts))
    T.ok(out == parts, "纯文字放行")
    parts2 = [{"kind": "text", "text": "解答如下"},
              {"kind": "math", "mode": "card", "latex": "x=1",
               "caption": "", "filename": ""}]
    out2 = brain._coerce_pdf([dict(p) for p in parts2])
    T.ok(len(out2) == 1 and out2[0]["mode"] == "doc",
         "有实质公式内容时仍合并成一个 doc（文字并入）")


def test_quoted_request_suffix_stripped():
    print("== 要求里的（引用内容：…）占位被剥掉 ==")
    reset()
    run_handle(msg_of(111, "只做第二题"))
    latest = CALLS["decide"][0]["latest"]
    T.ok("引用内容" not in latest, f"要求干净（latest={latest[:60]}）")


def test_cache_hit():
    print("== 半小时内追问同一个 PDF → 命中缓存，不重新下载 ==")
    reset()
    run_handle(msg_of(111, "每页只解决第一题"))
    cache_dir = config.OUT_DIR / "pdfmd_cache"
    T.ok(any(f.suffix == ".md" for f in cache_dir.glob("*")), "MD 已写入缓存文件夹")
    main._pdf_task_last.clear()          # 手动过冷却，模拟半小时后追问
    run_handle(msg_of(111, "再讲讲第二题"))
    T.ok(CALLS["download"] == 1, "第二次没下载（命中缓存）")
    T.ok(len(CALLS["decide"]) == 2, "第二次照常解题")
    T.ok("Find the minimum" in CALLS["decide"][1]["context"], "缓存的 MD 内容完整进模型")


def test_cache_ttl_expiry_and_cleanup():
    print("== 超过半小时 → 缓存过期：重新下载，且顺手删掉过期文件 ==")
    reset()
    run_handle(msg_of(111, "第一题"))
    cache_dir = config.OUT_DIR / "pdfmd_cache"
    # 把缓存文件时间戳回拨到 TTL 之前
    stale = cache_dir / "stale_entry.md"
    stale.write_text("旧的", encoding="utf-8")
    old = time.time() - config.PDF_TASK_CACHE_TTL - 60
    for f in list(cache_dir.glob("*")):
        os.utime(f, (old, old))
    main._pdf_task_last.clear()
    run_handle(msg_of(111, "第二题"))
    T.ok(CALLS["download"] == 2, "缓存过期后重新下载转换")
    T.ok(not stale.exists(), "过期的孤儿缓存文件被顺手清掉")
    fresh = [f for f in cache_dir.glob("*.json")]
    T.ok(fresh and time.time() - fresh[0].stat().st_mtime < 60, "新缓存时间戳是新的")


if __name__ == "__main__":
    if not HAVE_PDF:
        sys.exit("需要 pymupdf4llm（本地 venv 里跑本测试）")
    make_tiny_pdf()
    patch_env()
    for fn in [test_full_chain, test_no_text_default_request,
               test_read_intent_vs_task, test_cooldown,
               test_non_pdf_quote, test_scan_like_pdf,
               test_quoted_request_suffix_stripped,
               test_cache_hit, test_cache_ttl_expiry_and_cleanup,
               test_text_only_reply_no_closing,
               test_coerce_pdf_respects_text_only]:
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
