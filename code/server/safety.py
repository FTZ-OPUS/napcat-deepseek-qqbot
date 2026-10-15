"""safety.py —— 周末平安值守（车主 2026-10-11 定的个人安全功能）

功能（车主原话的需求形态）：
  - 开关只由车主本人说了算：私信（或群里 @）机器人「开启<SENSITIVE_A>」才开，说「关闭」才关
  - 开启当天（约定周五）机器人私信车主，把私信通道打开、讲清规则
  - 周六、周日每天车主私信**随便说句话**就算报平安（有生命迹象即可，不必说特定词）
  - 哪天到 21:00 一条消息都没有 → 机器人私信紧急联系人发车主约定的警报原文
    （私信失败才降级为群里 @ —— 宁可露脸，不可漏报）
  - 周一自动解除，下周要重新开启（防陈旧状态几周后突然误触发）

设计红线（保命功能，两条都要保）：
  1. 不可误报：状态落盘防重启丢；只有车主本人的 QQ 能开启/报平安；
     非车主发相关词机器人完全装聋（不回应、不记日志关键词、不暴露功能存在）；
     周一自动解除；每条警报每天只发一次。
  2. 不可漏报：过了 21:00 没报平安，下一轮 tick 立刻补发（机器人宕机恢复后也会补）；
     状态随时落盘，重启不丢；群消息和私信两个通道都能触发命令。

隐私：报平安走私信；所有回复只发私信；唯一公开痕迹是真正的 21:00 警报。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import config
import onebot

# ---------------- 可注入时间（测试用）----------------
_NOW = time.time
_LOCALTIME = time.localtime


def _today(ts: float | None = None) -> str:
    lt = _LOCALTIME(ts if ts is not None else _NOW())
    return f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}"


def _weekday(ts: float | None = None) -> int:
    return _LOCALTIME(ts if ts is not None else _NOW()).tm_wday   # 0=周一 ... 6=周日


def _hour(ts: float | None = None) -> int:
    return _LOCALTIME(ts if ts is not None else _NOW()).tm_hour


_STATE_PATH = Path(config.STATE_DIR) / "safety_check.json"
_state: dict | None = None
_last_tick = 0.0

_ARM_RE = re.compile(r"开启(?:<SENSITIVE_A>|安全保镖|安全值守|平安值守)")
_DISARM_RE = re.compile(r"关闭(?:<SENSITIVE_A>|安全保镖|安全值守|平安值守)")
_STATUS_RE = re.compile(r"(?:<SENSITIVE_A>|安全保镖|安全值守|平安值守)状态")
# 周五问了"要不要开启"之后，车主回这些话也算数（车主 2026-10-12 定）
_YES_RE = re.compile(r"^(?:是|是的|对|好|好的|要|可以|需要|开启吧|开吧|yes|y|ok)$", re.I)
_NO_RE = re.compile(r"^(?:否|不|不用|不要|不了|先不|no|n)$", re.I)

# 车主 2026-10-11 定的警报原话（一个字不改地发）
# 双保险开启：第一次「开启」只是询问，窗口内再说一次才真正开启
# （车主 2026-10-11 定：这个功能不用随便开启，防止误触发；10 分钟内有效）
_CONFIRM_WINDOW = 600.0

_ALERT_MSG = (
    "<OWNER_NAME>现在可能遭遇了危险。请联系一下他，如果他一个小时内没有回复，"
    "请你自行决定是否要采取一些措施。"
)


def _load() -> dict:
    global _state
    if _state is None:
        try:
            _state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _state = {}
        _state.setdefault("armed", False)
        _state.setdefault("armed_ts", 0)
        _state.setdefault("pings", {})
        _state.setdefault("alerts", {})
        _state.setdefault("friday_dm", "")
        _state.setdefault("pending_arm_ts", 0)
        _state.setdefault("last_priv_ts", 0)
        # 报平安的两重校验：待应答的挑战 + 周五"要不要开启"的询问时间
        _state.setdefault("challenge", {})
        _state.setdefault("ask_ts", 0)
    return _state


def _save() -> None:
    if _state is None:
        return
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(
        json.dumps(_state, ensure_ascii=False, indent=1), encoding="utf-8")


def is_command(text: str) -> bool:
    """这条消息是不是本功能的命令（开启/关闭/状态/周五询问的"是/否"）？

    给 pmchat 用的避让接口：车主本人的私信现在也归私信问答了，
    但「开启安全保镖」这种命令**不能**被当成聊天问题回答 —— 命令归 safety.py 管。

    ⚠️ 周五机器人问「要不要开启」之后的「是」「否」也必须算命令 ——
    否则它会被 pmchat 当闲聊答掉，**开启指令就丢了**（写测试时发现的）。
    """
    t = text or ""
    if _ARM_RE.search(t) or _DISARM_RE.search(t) or _STATUS_RE.search(t):
        return True
    try:
        st = _load()
        if st.get("ask_ts") and (_YES_RE.fullmatch(t.strip())
                                 or _NO_RE.fullmatch(t.strip())):
            return True
    except Exception:
        pass
    return False


def owner_qq() -> int:
    return int(config.SAFETY_OWNER_QQ or 0)


def contact_qq() -> int:
    return int(config.SAFETY_CONTACT_QQ or 0)


def feature_live() -> bool:
    """功能是否已配置到能工作（车主 QQ 必须有；联系人可以后配）。"""
    return bool(config.SAFETY_CHECK_ENABLED and owner_qq())


# 所有发给车主的安全类消息都带这个标记（车主 2026-10-12 定）：
# 「这样我就知道这个消息很重要的，一定要回你」—— 和普通闲聊私信区分开。
_SAFE_MARK = "（安全）"


def _dm(text: str) -> bool:
    """私信车主。机器人与车主必须是 QQ 好友，否则发不出去（记日志）。

    ⚠️ 自动在末尾加「（安全）」标记 —— 车主靠它区分"必须回的安全消息"
    和"可以不急着回的闲聊"。**不要去掉。**
    """
    try:
        onebot.send_private_text(owner_qq(), (text or "").rstrip() + _SAFE_MARK)
        return True
    except Exception as exc:
        log(f"私信车主失败（确认机器人与车主 {owner_qq()} 是否好友）：{exc}", "WARN")
        return False


def log(msg: str, level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] 安全值守: {msg}"
    print(line, flush=True)


# ---------------- 报平安的两重校验（车主 2026-10-12 定）----------------
#
# 为什么需要：旧设计里"能发消息"就算平安。但**手机可能被人拿走**
# （被胁迫、被抢），那样发出来的"平安"是假的 —— 报警就永远不会响。
# 所以现在要验两道，只有车主本人才答得上来：
#     ① 数字暗号（config.SAFETY_CODE）
#     ② 只有你们俩知道的秘密问答（config.SAFETY_SECRET_Q / _A）
# 两道都过 = 真平安；都不对 = 判定危险，直接给紧急联系人发警报。


def verify_configured() -> bool:
    """暗号和秘密问答都配了才算"校验可用"。没配就不许开启值守 ——
    宁可明说没保护，也不能让车主以为自己被保护着。"""
    return bool(config.SAFETY_CODE and config.SAFETY_SECRET_A)


def _code_ok(text: str) -> bool:
    """数字暗号。允许夹在句子里（"暗号 <SENSITIVE_C>"），但不允许和别的数字粘连。"""
    c = config.SAFETY_CODE
    if not c:
        return False
    return bool(re.search(r"(?<!\d)" + re.escape(c) + r"(?!\d)", text or ""))


def _secret_ok(text: str) -> bool:
    """秘密问答的答案。"""
    a = config.SAFETY_SECRET_A
    return bool(a) and a in (text or "")


def _ask_code(day: str, ts: float) -> None:
    """开始校验：问数字暗号。"""
    st = _load()
    st["challenge"] = {"day": day, "stage": "code", "asked_ts": ts}
    _save()
    _dm("你真的平安吗？对一下我们的数字暗号，直接回那串数字就行。")
    log(f"{day} 开始校验：已问数字暗号")


def _ask_secret(day: str, ts: float) -> None:
    """数字不对 → 换秘密问答（第二道保险）。"""
    st = _load()
    st["challenge"] = {"day": day, "stage": "secret", "asked_ts": ts}
    _save()
    q = config.SAFETY_SECRET_Q or "我们之间的那个秘密"
    _dm(f"数字不对。那对个只有我们知道的事：{q}")
    log(f"{day} 数字暗号没通过 → 已问秘密问答")


def _confirm_ping(day: str, ts: float) -> None:
    """两重校验通过 → 记为当天平安。"""
    st = _load()
    first = day not in st["pings"]
    st["pings"][day] = ts
    st["challenge"] = {}
    _save()
    if first:
        _dm(f"{day} 平安已记录 ✓")
    log(f"{day} 报平安（两重校验通过）")


def _handle_owner_message(text: str, ts: float, group_id: int) -> None:
    """车主一条私信的统一入口：命令 → 校验应答 → 触发校验。

    顺序很重要：
      1. 命令（开启/关闭/状态）优先，且**命令本身不算平安**（拿手机的人也会发命令）
      2. 正在校验中就当作应答
      3. 今天还没确认过 → 这是当天第一次开口，开始校验
    """
    st = _load()
    day = _today(ts)

    # 1) 命令（含周五询问的"是/否"——is_command 已经把它们算进来了）
    if is_command(text):
        handle_owner_command(text, owner_qq(), via_group=False)
        return

    if not st.get("armed"):
        return                       # 没值守 → 正常聊天，归 pmchat
    if _weekday(ts) < 5:
        return                       # 工作日不校验

    # 2) 正在校验中 → 当作应答
    ch = st.get("challenge") or {}
    if ch.get("day") == day and ch.get("stage"):
        if ch["stage"] == "code":
            if _code_ok(text):
                _confirm_ping(day, ts)
            else:
                _ask_secret(day, ts)
        else:                        # secret
            if _secret_ok(text):
                _confirm_ping(day, ts)
            else:
                st["challenge"] = {}
                _save()
                log(f"⚠ {day} 两重校验都没通过 —— 判定危险，发警报", "ERROR")
                _send_alert(group_id, day, ts, "两重校验都没通过（可能手机被他人控制）")
        return

    # 3) 今天已经确认过 → 不再打扰
    if day in st["pings"]:
        return

    # 4) 当天第一次开口 → 开始校验
    _ask_code(day, ts)


# ---------------- 命令（只认车主本人）----------------

def handle_owner_command(text: str, sender_qq: int, via_group: bool) -> bool:
    """车主本人的安全保镖命令。处理了返回 True（调用方吞掉消息不走闲聊）。

    非车主的消息：一律返回 False 且不做任何事（完全装聋，不暴露功能存在）。
    回复一律走私信，绝不在群里回（群里 @ 开启也只在私信里确认）。
    流程：开启 → 机器人反问 → 再说一次「开启」或发「确认」 → 双保险生效。
    关闭随时可用，无需双保险。
    """
    if not feature_live() or int(sender_qq) != owner_qq():
        return False
    st = _load()
    now = _NOW()
    stripped = text.strip()

    # 车主能发命令本身就说明人没事 —— 命令消息同样记为当天报平安
    if st.get("armed"):
        day = _today(now)
        st["pings"][day] = st["pings"].get(day) or now

    pending = float(st.get("pending_arm_ts") or 0)
    pending_fresh = bool(st.get("pending_arm_ts")) and 0 <= now - pending <= _CONFIRM_WINDOW

    # ---- 取消待确认的开启 ----
    if re.fullmatch(r"取消(?:开启)?", stripped) and pending_fresh:
        st["pending_arm_ts"] = 0
        _save()
        _dm("已取消，本次不开启。")
        return True

    wants_arm = bool(_ARM_RE.search(text))
    wants_confirm = bool(re.fullmatch(r"确认(?:开启)?", stripped))
    wants_yes = bool(_YES_RE.fullmatch(stripped))
    # 周五机器人问了「要不要开启」，在有效期内回「是」就算数
    ask_ts = float(st.get("ask_ts") or 0)
    ask_fresh = bool(ask_ts) and 0 <= now - ask_ts <= config.SAFETY_ASK_WINDOW

    # 周五问了之后回「否」
    if wants_yes is False and _NO_RE.fullmatch(stripped) and ask_fresh and not st.get("armed"):
        st["ask_ts"] = 0
        _save()
        _dm("好，本周不开启。想开随时发「开启安全保镖」。")
        return True

    # ---- 开启 ----
    if wants_arm or wants_confirm or (wants_yes and ask_fresh):
        if st.get("armed"):
            _dm("周末平安值守已经在进行中，不用再开。要关请发「关闭安全保镖」。")
            return True
        # ⚠️ 没配暗号/秘密问答就不许开 —— 宁可明说没保护，
        #    也不能让车主以为自己被保护着（这是保命功能，不能有假的安心感）
        if not verify_configured():
            _dm("现在还不能开启：报平安的暗号或秘密问答还没配置好。"
                "请先把 SAFETY_CODE 和 SAFETY_SECRET_A 配到 .env 里。")
            log("拒绝开启：暗号/秘密问答未配置", "WARN")
            return True
        if wants_confirm or (wants_yes and ask_fresh) or (pending_fresh and wants_arm):
            st["armed"] = True
            st["armed_ts"] = now
            st["pending_arm_ts"] = 0
            st["ask_ts"] = 0
            st["challenge"] = {}
            today = _today(now)
            st["pings"] = {k: v for k, v in st["pings"].items() if k >= today}
            st["alerts"] = {k: v for k, v in st["alerts"].items() if k >= today}
            # ⚠️ 开启当天的双保险消息【不】算报平安 —— 车主原话：
            # "开启以后，你没收到我报的平安，那一天就要发"。开启日也要签到，
            # 否则中午开启、下午失联，当晚就没有保护了。
            st["last_priv_ts"] = max(float(st.get("last_priv_ts") or 0), now)
            if _weekday(now) == 4:              # 周五开启 = 私信通道本次已打开
                st["friday_dm"] = today
            _save()
            _dm("✅ 已经成功开启周末平安值守。\n"
                "规则：周六、周日你**当天第一次私信我**时，我会问你一句"
                "「你真的平安吗」并要你对数字暗号；对了才算平安。\n"
                f"两重校验都过不了、或者问了没人答，我会给紧急联系人"
                f"（{contact_qq() or '未配置'}）发警报。\n"
                "周一自动解除，下周要重新开启。中途想关，随时私信发「关闭安全保镖」。")
            log("已开启（双保险确认完成）")
            return True
        # 第一步：反问确认
        st["pending_arm_ts"] = now
        _save()
        _dm("⚠️ 你要开启周末平安值守？开启后：周六、周日每天 21:00 前私信我说句话"
            f"（说什么都行）；哪天到点一条消息都没有，我会在群里 "
            f"@{contact_qq() or '（联系人未配置）'} 发出约定的警报。\n"
            "确认开启请**再说一次「开启」**或发「确认」（10 分钟内有效），取消请发「取消」。")
        log("开启第一次请求，等待双保险确认")
        return True

    # ---- 关闭（随时可用，车主一句话就关）----
    if _DISARM_RE.search(text):
        was = bool(st.get("armed"))
        st["armed"] = False
        st["pending_arm_ts"] = 0
        st["ask_ts"] = 0
        st["challenge"] = {}
        _save()
        _dm("✅ 已成功关闭周末平安值守。" if was
            else "周末平安值守本来就未开启，已保持关闭。")
        log("已关闭（车主命令）")
        return True

    # ---- 状态查询 ----
    if _STATUS_RE.search(text):
        if st.get("armed"):
            since = time.strftime("%m-%d %H:%M", time.localtime(st["armed_ts"]))
            today_ping = "已报 ✓" if _today(now) in st["pings"] else "今天还没报"
            _dm(f"安全保镖值守中（自 {since}）。今天：{today_ping}。"
                f"截止时间每天 {config.SAFETY_ALERT_HOUR}:00（私信我说句话就算）。")
        else:
            _dm("当前未开启。要开启请发「开启<SENSITIVE_A>」（会再跟你确认一次）。")
        return True

    return False


# ---------------- 私信轮询（报平安 + 私信命令）----------------

def _poll_private(group_id: int = 0) -> None:
    """拉车主的私信历史：命令 + 报平安的两重校验。

    ⚠️ 必须按发送者过滤：好友历史里混着**机器人自己发的私信**，
    机器人的确认语里就有「平安」两个字——不过滤的话，机器人会把自己的
    确认消息当成车主的报平安，真出事时警报永远不响（保命级 bug）。
    """
    st = _load()
    try:
        msgs = onebot.get_friend_msg_history(owner_qq(), count=15)
    except Exception as exc:
        log(f"拉私信历史失败：{exc}", "WARN")
        return
    if not msgs:
        return
    # 只留车主本人发的（排除机器人自己、排除任何第三人）
    mine = [m for m in msgs
            if int((m.get("sender") or {}).get("user_id") or 0) == owner_qq()
            and int((m.get("sender") or {}).get("user_id") or 0) != int(config.BOT_QQ or 0)]
    if not mine:
        return
    last = float(st.get("last_priv_ts") or 0)
    fresh = [m for m in mine if float(m.get("time", 0)) > last]
    if not fresh:
        return
    st["last_priv_ts"] = max(float(m.get("time", 0)) for m in fresh)

    for m in sorted(fresh, key=lambda x: float(x.get("time", 0))):
        ts = float(m.get("time", 0))
        text = onebot.message_text(m.get("message"))
        # ⚠️ 统一交给 _handle_owner_message：
        #    命令 / 校验应答 / 触发校验 三种情况都在里面分流。
        #    旧版这里「任何消息 = 平安」——**已废弃**：
        #    手机被人拿走时发出来的消息也是"消息"，那样报警永远不会响。
        _handle_owner_message(text, ts, group_id)


# ---------------- 周期 tick（主循环每分钟调一次）----------------

def maybe_tick(group_id: int) -> None:
    """主循环挂载点。内部自带 60 秒节流，任何异常都不许打断主循环。"""
    global _last_tick
    if not feature_live():
        return
    now = _NOW()
    if now - _last_tick < 55:
        return
    _last_tick = now
    try:
        _tick(group_id, now)
    except Exception as exc:
        log(f"tick 异常（不影响主循环）：{exc!r}", "WARN")


def _send_alert(group_id: int, day: str, now: float, why: str) -> None:
    """给紧急联系人发警报。私信为主通道，群内 @ 作兜底。**每天最多一次。**

    why 只进日志，不进消息 —— 提醒联系人的原文一个字不改。
    """
    st = _load()
    if day in st["alerts"]:
        return
    st["alerts"][day] = now
    _save()
    cq = contact_qq()
    if not cq:
        log(f"⚠ {day} 需要发警报（{why}），但紧急联系人未配置，警报无法发出！", "ERROR")
        return
    try:
        onebot.send_private_text(cq, _ALERT_MSG)
        log(f"⚠ 已发出今日（{day}）安全警报 → 私信联系人 {cq}｜原因：{why}")
    except Exception as exc:
        log(f"⚠ 私信联系人 {cq} 失败（{exc!r}），降级为群里 @", "ERROR")
        try:
            onebot.send_group_at(group_id, cq, _ALERT_MSG)
            log(f"⚠ 已降级：群里 @ {cq} 发出警报｜原因：{why}")
        except Exception as exc2:
            log(f"⚠ 群里 @ 也失败：{exc2!r} —— 今日警报未送达！", "ERROR")


def _tick(group_id: int, now: float) -> None:
    st = _load()
    today = _today(now)

    # 周一~周五：自动解除上周的值守（防陈旧状态误触发）
    if st.get("armed") and _weekday(now) < 5 and _today(st["armed_ts"]) != today:
        st["armed"] = False
        _save()
        log("周一自动解除上周值守")
        _dm("上周的周末平安值守已自动解除。要开就等我周五问你，或直接发「开启安全保镖」。")

    # 私信轮询：命令 + 报平安（一直开着，命令通道不能断）
    _poll_private(group_id)
    st = _load()

    # ---- 校验超时：问了暗号却没人答 → 判定危险（车主 2026-10-12 定：等 15 分钟）----
    # 这是"手机被别人拿走"最可能的形态：对方答不出暗号，只能装死。
    ch = st.get("challenge") or {}
    if ch.get("stage"):
        waited = now - float(ch.get("asked_ts") or 0)
        if waited > config.SAFETY_VERIFY_TIMEOUT:
            d = ch.get("day") or today
            st["challenge"] = {}
            _save()
            log(f"⚠ {d} 问了暗号 {int(waited)} 秒无人应答（超过 "
                f"{int(config.SAFETY_VERIFY_TIMEOUT)} 秒）—— 判定危险", "ERROR")
            _send_alert(group_id, d, now, f"问了暗号 {int(waited)} 秒没人应答")
            st = _load()

    # ---- 周五：主动问车主"本周要不要开启"（车主 2026-10-12 定的新流程）----
    if _weekday(now) == 4 and _hour(now) >= config.SAFETY_FRIDAY_ASK_HOUR \
            and st.get("friday_dm") != today:
        st["friday_dm"] = today
        if st.get("armed"):
            st["ask_ts"] = 0
            _save()
            _dm("本周六、日平安值守进行中：每天你第一次私信我时我会对一次暗号，记得回。")
            log("周五提醒（已开启）")
        elif verify_configured():
            st["ask_ts"] = now
            _save()
            _dm("本周要开启周末平安值守吗？回复「是」我就开启；回「否」就跳过。")
            log("周五询问是否开启")
        else:
            _save()
            log("周五到点了，但暗号/秘密问答没配置 —— 跳过询问（开了也没有效保护）", "WARN")

    # ---- 周末 21:00 警报：到点没有任何（校验通过的）报平安 ----
    # ⚠️ 但如果此刻**正在校验中**（车主刚发过消息、暗号还没对完），绝不报 ——
    #    人明明在，只是还没对完暗号。真正的风险由「校验超时」那条兜（15 分钟）。
    #    （写测试时发现的误报：车主 21:02 发消息 → 21:00 判定立刻误报）
    _ch_pending = bool((st.get("challenge") or {}).get("stage"))
    if st.get("armed") and not _ch_pending and _weekday(now) >= 5 \
            and _hour(now) >= config.SAFETY_ALERT_HOUR \
            and today not in st["pings"] and today not in st["alerts"]:
        _send_alert(group_id, today, now, "到点没有任何报平安")
        st = _load()

    # 解除值守时把待校验状态也清掉（防陈旧挑战过了几天突然报警）
    if not st.get("armed") and st.get("challenge"):
        st["challenge"] = {}
        _save()
