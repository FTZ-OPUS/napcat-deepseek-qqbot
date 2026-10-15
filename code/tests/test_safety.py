"""safety.py 状态机离线测试 —— 保命功能，全场景覆盖，绝不发真实消息。

时间全部注入（monkeypatch safety._NOW/_LOCALTIME），onebot 全打桩。
跑法：用本地 venv 或服务器 venv：python3 tests/test_safety.py
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

# requests 桩（本机没装）
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

import os as _os
import tempfile as _tempfile
# ⚠️ 必须在 import config 之前：把状态目录指到临时目录，
# 否则测试会写坏线上真实的 state/（2026-10-12 真实事故：游标被覆盖 → 凌晨骚扰好友）
_os.environ.setdefault("QQBOT_STATE_DIR", _tempfile.mkdtemp(prefix="qqbot-test-state-"))

import config   # noqa: E402
import onebot   # noqa: E402
import safety   # noqa: E402

OWNER, CONTACT, GROUP = 10000, 20000, 999999
SENT: list[tuple] = []
FAKE_PRIV = {"msgs": []}


def fake_call(action, timeout=30, **params):
    SENT.append((action, params))
    return {"status": "ok", "retcode": 0, "data": {"messages": FAKE_PRIV["msgs"]}}


def set_clock(y, m, d, h, mi=0, wday=None):
    """注入固定时间，返回该时刻的时间戳。"""
    fixed = time.mktime((y, m, d, h, mi, 0, 0, 0, -1))
    safety._NOW = lambda: fixed
    safety._LOCALTIME = lambda ts=None: time.localtime(ts if ts is not None else fixed)
    return fixed


def priv(msg_text, ts, sender=OWNER):
    FAKE_PRIV["msgs"] = [{"time": ts, "sender": {"user_id": sender}, "message": [
        {"type": "text", "data": {"text": msg_text}}]}]


def reset():
    global FAKE_PRIV
    SENT.clear()
    FAKE_PRIV = {"msgs": []}
    onebot.call = fake_call
    config.SAFETY_CHECK_ENABLED = True
    config.SAFETY_OWNER_QQ = OWNER
    config.SAFETY_CONTACT_QQ = CONTACT
    # 报平安的两重校验（车主 2026-10-12）：测试用固定值
    config.SAFETY_CODE = "<SENSITIVE_C>"
    config.SAFETY_SECRET_Q = "你最喜欢的角色是谁？"
    config.SAFETY_SECRET_A = "<SENSITIVE_B>"
    config.SAFETY_VERIFY_TIMEOUT = 900
    config.SAFETY_FRIDAY_ASK_HOUR = 20
    config.SAFETY_ASK_WINDOW = 86400
    safety._state = None
    sf = Path(config.STATE_DIR) / "safety_check.json"
    if sf.exists():
        sf.unlink()
    safety._last_tick = 0.0


def tick():
    safety._last_tick = 0.0
    safety.maybe_tick(GROUP)


def _say(text, y, m, d, h, mi, s=0):
    """在指定时刻发一条车主私信，然后跑一次 tick。返回该时刻时间戳。"""
    ts = set_clock(y, m, d, h, mi, s)
    priv(text, ts)
    tick()
    return ts


def do_arm():
    """双保险开启：第一次「开启」→ 反问；第二次「开启」→ 真正开启。"""
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    s1 = safety._load()["armed"]
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    s2 = safety._load()["armed"]
    return s1, s2


def group_sends():
    return [(a, p) for a, p in SENT
            if a == "send_group_msg" and p.get("group_id") == GROUP]


def dms():
    return [(a, p) for a, p in SENT if a == "send_private_msg"]


def dms_to(user):
    return [(a, p) for a, p in SENT
            if a == "send_private_msg" and p.get("user_id") == user]


def group_at():
    """群里 @ 联系人的消息。

    ⚠️ 旧写法是 `"@20000" in str(p)` —— **永远匹配不到**：真实的 at 是
    消息段 `{"type":"at","data":{"qq":"20000"}}`，参数串里根本没有字面量 "@20000"。
    这个帮手坏了很久没人发现，因为它对应的用例一直**没被注册进运行清单**（死代码）。
    DSH 2026-10-12 把它注册上、顺手修好了帮手。
    """
    return [(a, p) for a, p in SENT
            if a == "send_group_msg"
            and any(isinstance(s, dict) and s.get("type") == "at"
                    and str((s.get("data") or {}).get("qq")) == str(CONTACT)
                    for s in (p.get("message") or []))]


# ---------------- 测试 ----------------

def test_double_confirm_arm():
    print("== 双保险开启：第一次反问，第二次才开；群里无痕迹 ==")
    reset()
    set_clock(2026, 10, 16, 21, 0)          # 周五
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    st = safety._load()
    T.ok(st["armed"] is False and st["pending_arm_ts"] > 0, "第一次：未开启、进入待确认")
    T.ok(any("再说一次" in str(p) for _, p in dms()), "私信反问确认")
    T.ok(group_sends() == [], "群里没有任何痕迹")
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    st = safety._load()
    T.ok(st["armed"] is True and st["pending_arm_ts"] == 0, "第二次：真正开启")
    T.ok(any("已经成功开启" in str(p) for _, p in dms()), "明确回复「已经成功开启」")
    T.ok(st["friday_dm"] == "2026-10-16", "周五私信标记")


def test_confirm_word_and_expire_and_cancel():
    print("== 「确认」可代替第二次开启；超时重问；「取消」作废 ==")
    reset()
    set_clock(2026, 10, 16, 21, 0)
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    set_clock(2026, 10, 16, 21, 3)
    safety.handle_owner_command("确认", OWNER, via_group=True)
    T.ok(safety._load()["armed"] is True, "「确认」完成双保险")
    # 超时：11 分钟后再说开启 → 重新反问
    reset()
    set_clock(2026, 10, 16, 21, 0)
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    set_clock(2026, 10, 16, 21, 11)
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    T.ok(safety._load()["armed"] is False, "超过 10 分钟窗口：第二次不直接生效")
    T.ok(any("再说一次" in str(p) for _, p in dms()[-1:]), "重新反问确认")
    # 取消
    reset()
    set_clock(2026, 10, 16, 21, 0)
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    safety.handle_owner_command("取消", OWNER, via_group=True)
    st = safety._load()
    T.ok(st["pending_arm_ts"] == 0 and st["armed"] is False, "取消清空待确认")


def test_double_arm_when_already_armed():
    print("== 已开启时再说开启 → 提示进行中，不重置 ==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    safety.handle_owner_command("开启<SENSITIVE_A>", OWNER, via_group=True)
    T.ok(safety._load()["armed"] is True, "状态保持")
    T.ok(any("已经在进行中" in str(p) for _, p in dms()), "提示不重复开")


def test_non_owner_deaf():
    print("== 非车主发开启命令 → 完全装聋 ==")
    reset()
    set_clock(2026, 10, 16, 21, 0)
    T.ok(safety.handle_owner_command("开启<SENSITIVE_A>", 999, via_group=True) is False,
         "非车主返回 False")
    T.ok(safety._load()["armed"] is False and dms() == [], "没开启、没私信、没暴露")


def test_saturday_alert_when_no_ping():
    print("== 周六没报平安 → 21:00 群里 @联系人发警报（车主原话，每天一次）==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    set_clock(2026, 10, 17, 21, 0, 30)
    tick()
    cd = dms_to(CONTACT)
    T.ok(len(cd) == 1, "警报私信发给联系人")
    T.ok("<OWNER_NAME>现在可能遭遇了危险" in str(cd), "警报为车主约定的原话")
    T.ok(group_sends() == [], "正常路径不在群里发")
    set_clock(2026, 10, 17, 21, 30, 30)
    tick()
    T.ok(len(dms_to(CONTACT)) == 1, "同一天不重复发")


def test_first_message_triggers_code_challenge():
    print("== 当天第一条私信 → 先对暗号，不再直接算平安（2026-10-12 新规）==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    _say("我还在忙", 2026, 10, 17, 14, 0)
    st = safety._load()
    T.ok(not st["pings"].get("2026-10-17"), "还没验证 → 不算平安")
    T.ok(st["challenge"].get("stage") == "code", "进入数字暗号校验")
    T.ok(any("数字暗号" in str(p) for _, p in dms_to(OWNER)), "私信问了数字暗号")
    T.ok(dms_to(CONTACT) == [], "这时候还不能报警")


def test_code_ok_confirms_ping():
    print("== 回对数字暗号 → 记录平安，当天不再打扰 ==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    _say("在", 2026, 10, 17, 14, 0)
    _say("<SENSITIVE_C>", 2026, 10, 17, 14, 1)
    st = safety._load()
    T.ok(st["pings"].get("2026-10-17"), "平安已记录")
    T.ok(not st["challenge"], "校验状态已清")
    T.ok(any("平安已记录" in str(p) for _, p in dms_to(OWNER)), "回了确认")
    n = len(dms_to(OWNER))
    _say("再聊两句", 2026, 10, 17, 14, 5)
    T.ok(not safety._load()["challenge"], "当天已确认过 → 不再重复要求对暗号")
    T.ok(len(dms_to(OWNER)) == n, "也不再刷屏")


def test_wrong_code_then_secret_ok():
    print("== 暗号错 → 升级到秘密问答；答对仍算平安（第二道保险）==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    _say("在", 2026, 10, 17, 14, 0)
    _say("123456", 2026, 10, 17, 14, 1)
    st = safety._load()
    T.ok(st["challenge"].get("stage") == "secret", "升级到秘密问答")
    T.ok(any("最喜欢的角色" in str(p) for _, p in dms_to(OWNER)), "问了秘密问题")
    T.ok(dms_to(CONTACT) == [], "错一次还不报警")
    _say("<SENSITIVE_B>", 2026, 10, 17, 14, 2)
    T.ok(safety._load()["pings"].get("2026-10-17"), "答对秘密 → 平安记录")
    T.ok(dms_to(CONTACT) == [], "全程没误报")


def test_both_wrong_alerts():
    print("== 两重都错 → 立刻给紧急联系人发警报（判定手机被他人控制）==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    _say("在", 2026, 10, 17, 14, 0)
    _say("123456", 2026, 10, 17, 14, 1)
    _say("火影", 2026, 10, 17, 14, 2)
    cd = dms_to(CONTACT)
    T.ok(len(cd) == 1, "警报已私信联系人")
    T.ok("现在可能遭遇了危险" in str(cd), "用车主约定的警报原话")
    T.ok(not safety._load()["pings"].get("2026-10-17"), "没算平安")


def test_no_answer_alerts_after_timeout():
    print("== 问了暗号 15 分钟没人答 → 判定危险发警报（车主定的 15 分钟）==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    _say("在", 2026, 10, 17, 14, 0)
    T.ok(dms_to(CONTACT) == [], "刚问完不报警")
    set_clock(2026, 10, 17, 14, 10)
    tick()
    T.ok(dms_to(CONTACT) == [], "10 分钟还不报（可能只是没看到）")
    set_clock(2026, 10, 17, 14, 16)
    tick()
    T.ok(len(dms_to(CONTACT)) == 1, "超过 15 分钟 → 报警")
    set_clock(2026, 10, 17, 14, 40)
    tick()
    T.ok(len(dms_to(CONTACT)) == 1, "同一天不重复报")


def test_safe_marker_on_every_message():
    print("== 所有发给车主的安全类消息都带「（安全）」标记（2026-10-12 车主定）==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    _say("在", 2026, 10, 17, 14, 0)
    _say("123456", 2026, 10, 17, 14, 1)
    _say("<SENSITIVE_B>", 2026, 10, 17, 14, 2)
    me = dms_to(OWNER)
    T.ok(me, "有发给车主的消息")
    T.ok(all("（安全）" in str(p) for _, p in me), f"每条都带标记（共 {len(me)} 条）")


def test_friday_ask_then_yes():
    print("== 周五主动问「要不要开启」→ 回「是」才开启（2026-10-12 新流程）==")
    reset()
    set_clock(2026, 10, 16, 20, 0)          # 周五 20:00
    tick()
    T.ok(not safety._load()["armed"], "问了还没开")
    T.ok(any("要开启" in str(p) for _, p in dms_to(OWNER)), "主动问了要不要开启")
    _say("是", 2026, 10, 16, 20, 5)
    T.ok(safety._load()["armed"], "回「是」后开启")


def test_friday_ask_then_no():
    print("== 周五回「否」→ 不开，且下周不会残留待确认 ==")
    reset()
    set_clock(2026, 10, 16, 20, 0)
    tick()
    _say("否", 2026, 10, 16, 20, 5)
    T.ok(not safety._load()["armed"], "没开启")
    T.ok(safety._load()["ask_ts"] == 0, "待确认已清（回「是」也不会误开）")
    _say("是", 2026, 10, 16, 20, 30)
    T.ok(not safety._load()["armed"], "问了之后回「是」不会意外开启")


def test_no_code_refuses_arm():
    print("== 没配暗号/秘密问答 → 拒绝开启（不给假的安心感）==")
    reset()
    config.SAFETY_CODE = ""
    set_clock(2026, 10, 16, 21, 0)
    do_arm()
    T.ok(not safety._load()["armed"], "拒绝开启")
    T.ok(any("不能开启" in str(p) for _, p in dms_to(OWNER)), "明确说明了原因")


def test_sunday_separate_from_saturday():
    print("== 周六报了、周日没报 → 周日照发警报 ==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    _say("平安", 2026, 10, 17, 20, 0)
    _say("<SENSITIVE_C>", 2026, 10, 17, 20, 1)      # 周六正常验证通过
    T.ok(safety._load()["pings"].get("2026-10-17"), "周六已确认平安")
    set_clock(2026, 10, 18, 21, 5, 30)
    tick()
    T.ok(len(dms_to(CONTACT)) == 1, "周日照发警报（私信，两天独立计算）")


def test_bot_own_dm_not_counted():
    print("== 机器人自己的确认私信绝不算报平安（保命级过滤）==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    set_clock(2026, 10, 17, 21, 5, 30)
    priv("任意消息", time.time(), sender=config.BOT_QQ)
    tick()
    T.ok(safety._load()["pings"] == {}, "机器人自己的消息被过滤")
    T.ok(len(dms_to(CONTACT)) == 1, "真没报平安 → 警报照发（不因机器人自己的话漏报）")


def test_monday_auto_disarm():
    print("== 周一自动解除，下周不重新开启就不工作 ==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    set_clock(2026, 10, 19, 10, 0, 0)
    tick()
    T.ok(safety._load()["armed"] is False, "周一自动解除")
    T.ok(any("自动解除" in str(p) for _, p in dms()), "私信告知已解除")
    set_clock(2026, 10, 24, 21, 30, 30)
    tick()
    T.ok(group_sends() == [] and dms_to(CONTACT) == [], "陈旧状态不会在几周后误触发")


def test_disarm_command():
    print("== 周六中途私信关闭 → 明确回复已关闭，当天不再发警报 ==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    set_clock(2026, 10, 17, 18, 0, 30)
    T.ok(safety.handle_owner_command("关闭<SENSITIVE_A>", OWNER, via_group=False) is True,
         "私信通道随时可关")
    T.ok(any("已成功关闭" in str(p) for _, p in dms()), "明确回复「已成功关闭」")
    set_clock(2026, 10, 17, 21, 30, 30)
    tick()
    T.ok(dms_to(CONTACT) == [], "关闭后不发警报")


def test_contact_unset_no_group_send():
    print("== 联系人未配置 → 不发群消息，只记 ERROR 日志 ==")
    reset()
    config.SAFETY_CONTACT_QQ = 0
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    set_clock(2026, 10, 17, 21, 30, 30)
    tick()
    T.ok(dms_to(CONTACT) == [] and group_at() == [], "没配置联系人就不发任何人")
    T.ok(safety._load()["alerts"].get("2026-10-17") is not None, "当天标记已处理")


def test_late_ping_beats_alert_same_tick():
    print("== 21:00 后才发的消息，同一 tick 内先记账后判警 → 不误报 ==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    ts21 = set_clock(2026, 10, 17, 21, 2, 30)
    priv("刚看到消息", ts21)
    tick()
    T.ok(dms_to(CONTACT) == [], "私信在警报判定前处理，不误报")


def test_dm_fail_falls_back_to_group():
    print("== 私信联系人失败 → 降级群里 @（宁可露脸，不可漏报）==")
    reset()
    set_clock(2026, 10, 17, 12, 0)
    do_arm()
    set_clock(2026, 10, 17, 21, 30, 30)
    orig = onebot.send_private_text
    onebot.send_private_text = lambda uid, text: (
        (_ for _ in ()).throw(RuntimeError("not friend")) if uid == CONTACT
        else orig(uid, text))
    tick()
    onebot.send_private_text = orig
    T.ok(len(group_at()) == 1, "降级为群里 @ 联系人")
    T.ok(any("<OWNER_NAME>现在可能遭遇了危险" in str(p) for a, p in group_at()),
         "降级后文案不变")


def test_feature_inert_without_owner_qq():
    print("== SAFETY_OWNER_QQ 没配 → 整个功能彻底休眠 ==")
    reset()
    config.SAFETY_OWNER_QQ = 0
    set_clock(2026, 10, 17, 21, 30, 30)
    tick()
    T.ok(True, "tick 不炸")
    T.ok(safety._load().get("armed") in (False, None), "状态不被创建/变更")


if __name__ == "__main__":
    for fn in [test_double_confirm_arm, test_confirm_word_and_expire_and_cancel,
               test_double_arm_when_already_armed, test_non_owner_deaf,
               test_saturday_alert_when_no_ping,
               test_first_message_triggers_code_challenge, test_code_ok_confirms_ping,
               test_wrong_code_then_secret_ok, test_both_wrong_alerts,
               test_no_answer_alerts_after_timeout, test_safe_marker_on_every_message,
               test_friday_ask_then_yes, test_friday_ask_then_no, test_no_code_refuses_arm,
               test_sunday_separate_from_saturday, test_bot_own_dm_not_counted,
               test_monday_auto_disarm, test_disarm_command,
               test_contact_unset_no_group_send, test_late_ping_beats_alert_same_tick,
               test_dm_fail_falls_back_to_group,
               test_feature_inert_without_owner_qq]:
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
