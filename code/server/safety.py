"""safety.py —— 周末平安值守（车主 2026-10-11 定的车主私人的定时功能）

功能（车主原话的需求形态）：
  - 开关只由车主本人说了算：私信（或群里 @）机器人「开启安全保镖」才开，说「关闭」才关
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

_ARM_RE = re.compile(r"开启(?:安全保镖|安全保镖|安全值守|平安值守)")
_DISARM_RE = re.compile(r"关闭(?:安全保镖|安全保镖|安全值守|平安值守)")
_STATUS_RE = re.compile(r"(?:安全保镖|安全保镖|安全值守|平安值守)状态")

# 车主 2026-10-11 定的警报原话（一个字不改地发）
# 双保险开启：第一次「开启」只是询问，窗口内再说一次才真正开启
# （车主 2026-10-11 定：这个功能不用随便开启，防止误触发；10 分钟内有效）
_CONFIRM_WINDOW = 600.0

_ALERT_MSG = (
    "车主现在可能遭遇了危险。请联系一下他，如果他一个小时内没有回复，"
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
    return _state


def _save() -> None:
    if _state is None:
        return
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(
        json.dumps(_state, ensure_ascii=False, indent=1), encoding="utf-8")


def owner_qq() -> int:
    return int(config.SAFETY_OWNER_QQ or 0)


def contact_qq() -> int:
    return int(config.SAFETY_CONTACT_QQ or 0)


def feature_live() -> bool:
    """功能是否已配置到能工作（车主 QQ 必须有；联系人可以后配）。"""
    return bool(config.SAFETY_CHECK_ENABLED and owner_qq())


def _dm(text: str) -> bool:
    """私信车主。机器人与车主必须是 QQ 好友，否则发不出去（记日志）。"""
    try:
        onebot.send_private_text(owner_qq(), text)
        return True
    except Exception as exc:
        log(f"私信车主失败（确认机器人与车主 {owner_qq()} 是否好友）：{exc}", "WARN")
        return False


def log(msg: str, level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] 安全值守: {msg}"
    print(line, flush=True)


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

    # ---- 开启（双保险：先反问，再确认）----
    if wants_arm or wants_confirm:
        if st.get("armed"):
            _dm("周末平安值守已经在进行中，不用再开。要关请发「关闭安全保镖」。")
            return True
        if wants_confirm or (pending_fresh and wants_arm):
            st["armed"] = True
            st["armed_ts"] = now
            st["pending_arm_ts"] = 0
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
                "规则：周六、周日每天 21:00 前私信我说句话就行（说什么都可以）；"
                f"哪天到点一条消息都没有，我会在群里 @{contact_qq() or '（联系人未配置）'} "
                "发出约定的警报。\n"
                "周一我会自动解除，下周要重新开启。中途想关，随时私信发「关闭安全保镖」。")
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
            _dm("当前未开启。要开启请发「开启安全保镖」（会再跟你确认一次）。")
        return True

    return False


# ---------------- 私信轮询（报平安 + 私信命令）----------------

def _poll_private() -> None:
    """拉车主的私信历史：处理 开启/关闭/状态 命令和「平安」报平安。

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
        if _ARM_RE.search(text) or _DISARM_RE.search(text) or _STATUS_RE.search(text):
            handle_owner_command(text, owner_qq(), via_group=False)
            continue
        # 任何私信消息 = 生命迹象 = 当天报平安（车主 2026-10-11 定：
        # 「我不一定报平安，发一些其他的消息，你自己判断」—— 能发消息就是平安）
        if st.get("armed"):
            day = _today(ts)
            first_today = day not in st["pings"]
            st["pings"][day] = ts
            if first_today and day == _today():
                _dm(f"收到你的消息，{day} 平安已记录 ✓")
            log(f"{day} 报平安（任意消息判定）")


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


def _tick(group_id: int, now: float) -> None:
    st = _load()
    today = _today(now)

    # 周一~周五：自动解除上周的值守（防陈旧状态误触发）
    if st.get("armed") and _weekday(now) < 5 and _today(st["armed_ts"]) != today:
        st["armed"] = False
        _save()
        log("周一自动解除上周值守")
        _dm("上周的周末平安值守已自动解除。需要时发「开启安全保镖」重新开启。")

    # 私信轮询：命令 + 报平安（一直开着，命令通道不能断）
    _poll_private()
    st = _load()

    # 周五傍晚私信提醒（如果开启了但还没在本周五私信过）
    if st.get("armed") and _weekday(now) == 4 \
            and _hour(now) >= config.SAFETY_FRIDAY_DM_HOUR \
            and st.get("friday_dm") != today:
        st["friday_dm"] = today
        _save()
        _dm("本周六、日平安值守进行中：每天 21:00 前私信我说句话就行，"
            "到点一条都没有我会在群里 @紧急联系人。今天报过了吗？")

    # 周末 21:00 警报：没报平安 → 群里 @ 联系人（每天最多一次，宕机恢复会补发）
    if st.get("armed") and _weekday(now) >= 5 \
            and _hour(now) >= config.SAFETY_ALERT_HOUR \
            and today not in st["pings"] and today not in st["alerts"]:
        st["alerts"][today] = now
        _save()
        cq = contact_qq()
        if cq:
            # 主通道：私信联系人（车主 2026-10-11 定：不再群里 @）。
            # 兜底：私信发不出去（被删好友等）才降级为群里 @ —— 宁可露脸，不可漏报。
            try:
                onebot.send_private_text(cq, _ALERT_MSG)
                log(f"⚠ 已发出今日（{today}）安全警报 → 私信联系人 {cq}")
            except Exception as exc:
                log(f"⚠ 私信联系人 {cq} 失败（{exc!r}），降级为群里 @", "ERROR")
                try:
                    onebot.send_group_at(group_id, cq, _ALERT_MSG)
                    log(f"⚠ 已降级：群里 @ {cq} 发出警报")
                except Exception as exc2:
                    log(f"⚠ 群里 @ 也失败：{exc2!r} —— 今日警报未送达！", "ERROR")
        else:
            log(f"⚠ {today} 未报平安，但紧急联系人未配置（SAFETY_CONTACT_QQ），"
                "警报无法发出！", "ERROR")
