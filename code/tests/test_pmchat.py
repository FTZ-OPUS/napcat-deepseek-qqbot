"""pmchat 私信问答离线测试 —— 打桩，绝不发真实私信。

场景：白名单好友私信 → decide 被调 → 私信回复；
非白名单/机器人自己的消息 → 不处理；owner 的私信不归 pmchat（归 safety）。
跑法：python3 tests/test_pmchat.py
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))
try:
    from tests.test_tablecheck import T
except ImportError:
    from test_tablecheck import T  # noqa: E402

if "requests" not in sys.modules:
    try:
        import requests  # noqa: F401
    except ImportError:
        _fake = types.ModuleType("requests")
        class _RE(Exception):
            pass
        _fake.RequestException = _RE
        _fake.post = lambda *a, **k: (_ for _ in ()).throw(_RE("stub"))
        sys.modules["requests"] = _fake

import brain   # noqa: E402
import config  # noqa: E402
import humanize  # noqa: E402
import onebot  # noqa: E402
import pmchat  # noqa: E402

FRIEND, STRANGER, OWNER = 20000, 30000, 10001
SENT: list[tuple] = []
DECIDES: list[dict] = []


def fake_call(action, timeout=30, **params):
    SENT.append((action, params))
    return {"status": "ok", "retcode": 0, "data": {"messages": FAKE_MSGS}}


FAKE_MSGS: list[dict] = []


def reset():
    global FAKE_MSGS
    SENT.clear()
    DECIDES.clear()
    FAKE_MSGS = []
    onebot.call = fake_call
    config.PM_CHAT_ENABLED = True
    config.PM_CHAT_USERS = [FRIEND]
    config.SAFETY_OWNER_QQ = OWNER
    humanize.sleep_typing = lambda t: 0
    humanize.split_long_text = lambda t: [t]
    brain.decide = lambda ctx, latest, **kw: (
        DECIDES.append({"latest": latest, **kw}) or
        {"action": "reply",
         "parts": [{"kind": "text", "text": "你好，我是 e阶导"}],
         "reason": "stub", "provider": "stub"})
    pmchat._state = None
    sf = Path(config.STATE_DIR) / "pmchat.json"
    if sf.exists():
        sf.unlink()
    pmchat._last_run = 0.0


def friend_msg(text, ts):
    FAKE_MSGS.append({"time": ts, "sender": {"user_id": FRIEND, "nickname": "淼"},
                      "message": [{"type": "text", "data": {"text": text}}]})


def tick():
    pmchat._last_run = 0.0
    pmchat.maybe_tick()


def test_friend_pm_answered():
    print("== 白名单好友私信 → decide 被调 → 私信回复 ==")
    reset()
    friend_msg("在吗，帮我排版个东西", time.time() - 5)
    tick()
    T.ok(len(DECIDES) == 1, "调了一次 decide")
    T.ok("排版个东西" in DECIDES[0]["latest"], "私信内容传给模型")
    T.ok(DECIDES[0].get("max_tokens") == 4000, "私聊预算 4000")
    T.ok(any(a == "send_private_msg" and "e阶导" in str(p) for a, p in SENT),
         "私信回复发出")
    T.ok(not any(a == "send_group_msg" for a in SENT), "全程没有群消息")


def test_watermark_no_repeat():
    print("== 同一批消息不重复回复（水位）==")
    reset()
    ts = time.time() - 10
    friend_msg("第一条", ts)
    tick()
    n1 = len(DECIDES)
    tick()                                # 没有新消息
    T.ok(len(DECIDES) == n1, "无新消息不重复答")
    friend_msg("第二条", ts + 5)
    tick()
    T.ok(len(DECIDES) == n1 + 1, "新消息正常接续")


def test_non_friend_ignored():
    print("== 非白名单用户的私信不处理 ==")
    reset()
    FAKE_MSGS.append({"time": time.time() - 5,
                      "sender": {"user_id": STRANGER, "nickname": "路人"},
                      "message": [{"type": "text", "data": {"text": "在吗"}}]})
    tick()
    T.ok(len(DECIDES) == 0, "陌生人私信不理（防止被陌生人烧 token）")


def test_bot_own_and_owner_excluded():
    print("== 机器人自己的消息和车主的私信都不归 pmchat ==")
    reset()
    FAKE_MSGS.append({"time": time.time() - 5,
                      "sender": {"user_id": config.BOT_QQ or 10000, "nickname": "e阶导"},
                      "message": [{"type": "text", "data": {"text": "✅ 已成功开启"}}]})
    FAKE_MSGS.append({"time": time.time() - 4,
                      "sender": {"user_id": OWNER, "nickname": "车主"},
                      "message": [{"type": "text", "data": {"text": "开启安全保镖"}}]})
    tick()
    T.ok(len(DECIDES) == 0, "机器人和车主的私信都不走问答")


def test_friend_pdf_task():
    print("== 白名单好友私信发 PDF → 下载转MD → 按指令作答 → 私信发回 ==")
    reset()
    config.PM_CHAT_USERS = [FRIEND]
    tiny = Path(__file__).resolve().parent / "_tiny_test.pdf"
    import shutil, uuid as _uuid
    def fake_dl(url, dest, **kw):
        shutil.copy(tiny, dest)
    onebot.download_file = fake_dl
    import latexrender
    latexrender.render = lambda body, mode="card", style="": Path("/tmp/_stub.pdf")
    def fake_decide(context, latest, **kw):
        DECIDES.append({"context": context, "latest": latest, **kw})
        return {"action": "reply", "parts": [{"kind": "math", "mode": "doc",
                 "latex": "总结：OK", "caption": "", "filename": "阅读总结"}],
                "reason": "stub", "provider": "stub"}
    brain.decide = fake_decide
    ts = time.time() - 5
    FAKE_MSGS.append({"time": ts, "sender": {"user_id": FRIEND, "nickname": "淼"},
                      "message": [{"type": "text", "data": {"text": "帮我总结这份资料"}}]})
    FAKE_MSGS.append({"time": ts + 2, "sender": {"user_id": FRIEND, "nickname": "淼"},
                      "message": [{"type": "file",
                                   "data": {"file": "资料.pdf", "file_id": "/f1",
                                            "url": "http://stub/x.pdf"}}]})
    tick()
    T.ok(len(DECIDES) == 2, f"文字先答一条、PDF 再答一条（实际 {len(DECIDES)} 次）")
    T.ok("Find the minimum" in DECIDES[1]["context"], "PDF 全文进了第二次调用的输入")
    T.ok("帮我总结这份资料" in DECIDES[1]["latest"], "文件前的文字指令被带上")
    T.ok(any(a == "send_private_file" or (a == "send_private_msg" and "阅读总结" in str(p))
             for a, p in SENT), "处理结果私信发回")


def test_sticker_part_sent_privately():
    print("== 私聊回复带表情包 → 私信发出图片 ==")
    reset()
    brain.decide = lambda *a, **kw: {"action": "reply",
        "parts": [{"kind": "text", "text": "这个我还真说不好，哈哈哈"},
                  {"kind": "sticker"}],
        "reason": "stub", "provider": "stub"}
    import media
    media.pick = lambda kind="image": Path("/tmp/_stub_sticker.jpg")
    onebot.send_private_image = lambda uid, path: SENT.append(
        ("send_private_image", {"user_id": uid}))
    friend_msg("讲讲你不会的东西", time.time() - 5)
    tick()
    T.ok(any(a == "send_private_image" for a, _ in SENT), "表情包以私信图片发出")
    T.ok(any(a == "send_private_msg" and "说不好" in str(p) for a, p in SENT),
         "文字回复照发")


def test_never_raises():
    print("== 垃圾输入不炸主循环 ==")
    reset()
    FAKE_MSGS.append({"time": time.time() - 5, "sender": {"user_id": FRIEND},
                      "message": "纯字符串消息"})
    friend_msg("", time.time() - 3)
    def boom(*a, **k):
        raise RuntimeError("boom")
    brain.decide = boom
    tick()
    T.ok(True, "全程无异常抛出")


if __name__ == "__main__":
    for fn in [test_friend_pm_answered, test_watermark_no_repeat,
               test_non_friend_ignored, test_bot_own_and_owner_excluded,
               test_friend_pdf_task, test_sticker_part_sent_privately,
               test_never_raises]:
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
