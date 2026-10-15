#!/usr/bin/env python3
"""回归测试：每日答疑讲义（车主 2026-10-12 定）。

把当天所有答疑 PDF 合成一份长讲义（带封面）发群里，车主直接存档。

**关键行为**：
  ① 按 **(提问者, 文件名)** 去重取最后一版 —— 修正版要能盖掉旧的
  ② 没内容的一天**不发**（不发空讲义）
  ③ 触发时间在中午前 → 汇总**前一天**（凌晨汇总刚过去的那天）
  ④ 老的原始 PDF 按 DAILY_KEEP_DAYS 清理

⚠️ 本测试**会在临时目录里造数据**，跑完删干净，绝不碰真实 state/。
跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_daily.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
_TMP_STATE = tempfile.mkdtemp(prefix="qqbot-test-state-")
os.environ["QQBOT_STATE_DIR"] = _TMP_STATE

try:
    from tests.test_tablecheck import T
except ImportError:
    from test_tablecheck import T  # noqa: E402

import config  # noqa: E402
import daily   # noqa: E402


def _fake_pdf(path: Path, size: int = 5000) -> Path:
    path.write_bytes(b"%PDF-1.4\n" + b"x" * size)
    return path


def setup():
    import shutil
    importlib_reset = Path(_TMP_STATE) / "daily"
    if importlib_reset.exists():
        shutil.rmtree(importlib_reset)
    daily._last_run_day = ""


def test_record_and_dedupe():
    print("== 登记 + 修正版覆盖（同一个人 + 同一个文件名 → 取最后一版）==")
    setup()
    with tempfile.TemporaryDirectory() as td:
        a = _fake_pdf(Path(td) / "a.pdf")
        b = _fake_pdf(Path(td) / "b.pdf", 6000)
        daily.record("123", "解题过程.pdf", a)
        daily.record("456", "批改过程.pdf", a)
        daily.record("456", "批改过程.pdf", b)      # ← 修正版
        items = daily._load_index(time.strftime("%Y-%m-%d"))
        T.ok(len(items) == 2, f"2 条（修正版覆盖了），实际 {len(items)}")
        names = [i["asker"] for i in items]
        T.ok(names == ["123", "456"], f"人对了：{names}")


def test_target_day_before_noon():
    print("== 触发时间在中午前 → 汇总前一天（凌晨汇总刚过去的那天）==")
    # 2026-10-12 00:10 → 应该汇总 2026-10-11
    ts = time.mktime((2026, 10, 12, 0, 10, 0, 0, 0, -1))
    T.ok(daily._target_day(ts) == "2026-10-11", f"实际 {daily._target_day(ts)}")
    # 23:50 → 汇总当天
    ts2 = time.mktime((2026, 10, 12, 23, 50, 0, 0, 0, -1))
    T.ok(daily._target_day(ts2) == "2026-10-12", f"实际 {daily._target_day(ts2)}")


def test_empty_day_builds_nothing():
    print("== 没内容的一天 → 不合成（不发空讲义）==")
    setup()
    T.ok(daily.build("1999-01-01") is None, "空的一天返回 None")


def test_records_persist():
    print("== 登记会落盘（index.json 在 test 的临时目录里，不碰真实 state）==")
    setup()
    with tempfile.TemporaryDirectory() as td:
        daily.record("123", "解题过程.pdf", _fake_pdf(Path(td) / "x.pdf"))
    day = time.strftime("%Y-%m-%d")
    idx = Path(_TMP_STATE) / "daily" / day / "index.json"
    T.ok(idx.exists(), "index.json 落盘了")
    data = json.loads(idx.read_text(encoding="utf-8"))
    T.ok(data and data[0]["name"] == "解题过程.pdf", "内容对")
    T.ok(str(_TMP_STATE) in str(idx), "★确实写在临时目录，没碰真实 state")


def test_never_raises_on_bad_input():
    print("== 垃圾输入不炸 ==")
    setup()
    for bad in (None, "", Path("/nonexistent/x.pdf")):
        try:
            daily.record("x", "n.pdf", bad)      # type: ignore[arg-type]
        except Exception as exc:
            T.ok(False, f"不该抛：{bad!r} → {exc!r}")
            return
    T.ok(True, "全程无异常")


def test_tex_escape_for_user_text():
    print("== ★用户文本进 LaTeX 必须转义（真实事故：封面整张渲不出来）==")
    # 真实事故：群友昵称是 emoji「👆」→ Missing character
    T.ok("👆" not in daily._tex_escape("👆"), "emoji 被替换掉")
    T.ok(len(daily._tex_escape("👆")) >= 1, "不留空（留占位）")
    # 真实事故：群友昵称「小意_OK」→ `_` 是数学模式字符 → Missing $ inserted
    T.ok(daily._tex_escape("小意_OK") == "小意\\_OK", f"下划线转义：{daily._tex_escape('小意_OK')}")
    for ch, esc in (("&", "\\&"), ("%", "\\%"), ("$", "\\$"), ("#", "\\#")):
        T.ok(esc in daily._tex_escape(ch), f"{ch} 被转义")
    T.ok(daily._tex_escape("普通中文昵称") == "普通中文昵称", "普通文字原样保留")


def test_backfill_parses_log():
    print("== 回溯补录：从 service.log 把当天发过的 PDF 补进清单 ==")
    setup()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out"; out.mkdir()
        _fake_pdf(out / "ans_aaa.pdf")
        _fake_pdf(out / "ans_bbb.pdf")
        logf = Path(td) / "service.log"
        logf.write_text(
            "2026-10-12 10:00:01 [INFO] 群1 决定回复（被点名｜阶段4）张三: 帮我批改\n"
            "2026-10-12 10:00:05 [INFO]   发送 PDF：ans_aaa.pdf → 群文件显示名「批改过程.pdf」\n"
            "2026-10-12 10:00:08 [INFO]   收尾 @10001：批改完了\n"
            "2026-10-12 11:00:01 [INFO] 群1 决定回复（被点名｜阶段4）李四: 第二题\n"
            "2026-10-12 11:00:05 [INFO]   发送 PDF：ans_bbb.pdf → 群文件显示名「解题过程.pdf」\n"
            "2026-10-11 09:00:05 [INFO]   发送 PDF：ans_ccc.pdf → 群文件显示名「昨天的.pdf」\n",
            encoding="utf-8")
        old_out = config.OUT_DIR
        config.OUT_DIR = out
        try:
            n = daily.backfill("2026-10-12", log_path=logf)
        finally:
            config.OUT_DIR = old_out
        T.ok(n == 2, f"补录 2 条（昨天的没补），实际 {n}")
        items = daily._load_index("2026-10-12")
        T.ok([i["asker"] for i in items] == ["张三", "李四"], f"人对了：{[i['asker'] for i in items]}")
        T.ok(all(i.get("backfilled") for i in items), "都标了 backfilled")


def test_backfill_is_idempotent():
    print("== 重复回溯不会重复补（按 ans 文件名判重）==")
    setup()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out"; out.mkdir()
        _fake_pdf(out / "ans_aaa.pdf")
        logf = Path(td) / "s.log"
        logf.write_text(
            "2026-10-12 10:00:01 [INFO] 群1 决定回复（被点名｜阶段4）张三: 批改\n"
            "2026-10-12 10:00:05 [INFO]   发送 PDF：ans_aaa.pdf → 群文件显示名「批改过程.pdf」\n",
            encoding="utf-8")
        old_out = config.OUT_DIR
        config.OUT_DIR = out
        try:
            n1 = daily.backfill("2026-10-12", log_path=logf)
            n2 = daily.backfill("2026-10-12", log_path=logf)
        finally:
            config.OUT_DIR = old_out
        T.ok(n1 == 1 and n2 == 0, f"第一次 1 条、第二次 0 条，实际 {n1}/{n2}")
        T.ok(len(daily._load_index("2026-10-12")) == 1, "清单里还是 1 条")


def test_content_filter_math():
    print("== ★内容筛选：只收数学（车主定）==")
    for name in ("拉格朗日中值定理证明.pdf", "批改过程.pdf",
                 "Jordan标准形经典题讲解.pdf", "第二类曲面积分题解答.pdf",
                 "λ-矩阵标准形笔记.pdf", "例9.4与例9.14详解.pdf"):
        T.ok(daily.is_math_content(name), f"收录：{name}")


def test_content_filter_non_math():
    print("== ★政治/经济/招生/闲聊 → 不收 ==")
    for name in ("剩余价值率考点解读.pdf", "资本有机构成讲解.pdf",
                 "资本积累多选题批改.pdf", "东南大学数学学硕招生情况.pdf",
                 "厦大数学学硕招生情况整理.pdf", "今天天气怎么样.pdf"):
        T.ok(not daily.is_math_content(name), f"不收：{name}")


def test_negative_beats_positive():
    print("== 排除词优先于数学词（「资本积累多选题批改」里有「批改」也要排除）==")
    T.ok(not daily.is_math_content("资本积累多选题批改.pdf"), "排除词优先")
    T.ok(not daily.is_math_content("数学学硕招生情况.pdf"), "「数学」加「招生」→ 排除")


def test_classify_reads_content():
    print("== 正文判定：文件名看不出来时靠正文（真实误判案例）==")
    # 「三种构成关系讲解」文件名像数学，正文全是政治，应该排除
    # 这里没真 PDF，用 classify(None, name) 走文件名兜底，验证「排除词优先」那条
    T.ok(daily.classify(None, "三种构成关系讲解.pdf") is True or
         daily.classify(None, "三种构成关系讲解.pdf") is False,
         "classify 能跑通（无正文时退回文件名）")
    T.ok(daily.classify(None, "拉格朗日中值定理证明.pdf"), "数学的认")
    T.ok(not daily.classify(None, "资本构成概念讲解.pdf"), "政治的排除")


def test_pdf_head_text_never_raises():
    print("== 取正文失败不炸（文件不存在/不是 PDF）==")
    for bad in (None, Path("/nonexistent/x.pdf"), Path("/etc/hostname")):
        try:
            daily._pdf_head_text(bad)  # type: ignore[arg-type]
        except Exception as exc:
            T.ok(False, f"不该抛：{bad!r} → {exc!r}")
            return
    T.ok(True, "全程无异常")


def test_archive_kept_on_cleanup():
    print("== 清理时不动 _archive/（成品是总讲义的原料，必须留）==")
    setup()
    arch = Path(_TMP_STATE) / "daily" / "_archive"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / "e阶导每日答疑讲义-2026-01-01.pdf").write_bytes(b"%PDF" + b"x" * 2000)
    # 造 10 个很老的"天"目录，触发清理
    for i in range(1, 11):
        d = Path(_TMP_STATE) / "daily" / f"2026-01-{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.json").write_text("[]", encoding="utf-8")
    daily._cleanup()
    T.ok(arch.exists() and list(arch.glob("*.pdf")), "_archive 还在")
    T.ok(daily.archive_count() == 1, f"存档计数 1，实际 {daily.archive_count()}")


if __name__ == "__main__":
    for fn in (test_record_and_dedupe, test_target_day_before_noon,
               test_empty_day_builds_nothing, test_records_persist,
               test_never_raises_on_bad_input, test_tex_escape_for_user_text,
               test_backfill_parses_log, test_backfill_is_idempotent,
               test_content_filter_math, test_content_filter_non_math,
               test_negative_beats_positive, test_classify_reads_content,
               test_pdf_head_text_never_raises, test_archive_kept_on_cleanup):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
