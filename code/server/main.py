#!/usr/bin/env python3
"""-e阶导- 主循环。

设计：
  - 轮询 get_group_msg_history（每 2 秒，取最近 30 条）
  - message_id 不单调，所以用「已见集合」去重，绝不用大小比较
  - 首次启动把历史消息全部标记已读，避免回复陈年旧账
  - 回复前先判断触发条件 → 冷却检查 → "看了不回" → 反应延迟 → 问模型 → 分段发送
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
import traceback
from pathlib import Path

import brain
import config
import humanize
import latexrender
import media
import onebot
import persona
import rampup
import vision

# 出现这些词，说明大概率是数学问题
MATH_WORDS = [
    "积分", "极限", "导数", "微分", "矩阵", "行列式", "级数", "收敛", "证明", "求导",
    "方程", "函数", "概率", "期望", "向量", "特征值", "傅里叶", "泰勒", "洛必达",
    "定理", "不等式", "组合", "数列", "几何", "三角", "对数", "复数", "拓扑",
    "群论", "数论", "素数", "Manim", "manim", "公式", "推导",
]

# 这些说法配合「被点名」才算「要求 PDF」
PDF_HINTS = ("pdf", "发文件", "要文档", "导出文档", "排版文件")

# 被点名但模型仍不肯接话时的兜底 —— 保证「@ 必回」这个承诺不落空
MENTION_FALLBACK = ["在的，你说", "嗯？我在", "怎么了？", "在，说吧"]

# 发了 PDF 但模型没给收尾话时的兜底（保证一定会 @ 提问者说一声）
CLOSING_FALLBACK = ["过程发你了，你看看", "弄好了，有问题再喊我", "发你了"]


def wants_pdf(text: str, mentioned: bool = False) -> bool:
    """判断这条消息是不是在「跟机器人要一份 PDF」。

    ⚠️ 必须同时满足两个条件：① 点名了机器人 ② 内容提到 pdf 之类。
    只满足②绝对不能触发 —— 出过一次事故：群里有人聊天时说
    「凛音别上来就甩PDF啊」，机器人把这句话当成"要 PDF"，
    在没有题目的情况下硬出了一份内容完全对不上的文档，非常尴尬。

    PDF 是一种交付形式，只有「对着机器人提出」才算要求。
    没被点名时，顶多是在讨论 PDF 这个格式而已。
    """
    if not mentioned:
        return False
    lowered = text.lower()
    return any(h in lowered for h in PDF_HINTS)

STATE_FILE = config.STATE_DIR / "cursor.json"
MAX_SEEN = 5000

# ---- 机器人自己的名字（动态识别，别写死）----
# 车主改过好几次名（-e阶导- → e阶导 …），写死字符串的话改一次名它就不认识自己了。
# 所以启动时和每隔半小时都从 NapCat 抓一次当前昵称/群名片，自动派生写法。
BOT_NAMES: set[str] = set(config.BOT_ALIASES)
# 当前「主名字」（群名片优先），用来替换人格提示词里的昵称占位符 ——
# 不然模型会拿写死的旧名字自我介绍。
BOT_DISPLAY_NAME: str = persona.DEFAULT_BOT_NAME
_names_refreshed_at = 0.0
NAMES_REFRESH_INTERVAL = 1800


def _name_variants(raw: str) -> set[str]:
    """从一个名字派生「别人可能怎么叫它」的各种写法。

    "-e阶导-"  →  {"-e阶导-", "e阶导", "阶导"}
    "e阶导"    →  {"e阶导", "阶导"}
    """
    out: set[str] = set()
    name = (raw or "").strip()
    if not name:
        return out
    out.add(name)
    # 去掉首尾的装饰符号
    core = re.sub(r"^[\s\-_~·.。、|]+|[\s\-_~·.。、|]+$", "", name)
    if core:
        out.add(core)
    # 再去掉开头的拉丁字母/数字："e阶导" → "阶导"
    stripped = re.sub(r"^[A-Za-z0-9]+", "", core).strip()
    if stripped:
        out.add(stripped)
    # 抽最后一段连续汉字 —— 名字里常混着字母和装饰，
    # 但群友实际叫的时候往往只叫中文那部分。
    # "e的e阶导" → "阶导"；"小e阶导" → "阶导"
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", core):
        out.add(run)
    return {x for x in out if len(x) >= 2}


def refresh_bot_names(force: bool = False) -> None:
    """从 NapCat 抓机器人当前的名字，更新「被点名的写法」集合 + 主名字。"""
    global BOT_NAMES, BOT_DISPLAY_NAME, _names_refreshed_at
    now = time.time()
    if not force and now - _names_refreshed_at < NAMES_REFRESH_INTERVAL:
        return
    _names_refreshed_at = now

    names: set[str] = set(config.BOT_ALIASES)
    # 群名片是群友实际看到并照着叫的名字，优先级最高
    display = ""
    for gid in config.ALLOWED_GROUPS:
        try:
            data = onebot.call("get_group_member_info",
                               group_id=gid, user_id=config.BOT_QQ).get("data") or {}
            card = str(data.get("card") or "").strip()
            if card and not display:
                display = card
            names |= _name_variants(card)
            names |= _name_variants(str(data.get("nickname") or ""))
        except Exception as exc:
            log(f"抓群名片失败（群 {gid}）：{exc}", "WARN")
    try:
        names |= _name_variants(str(onebot.get_login_info().get("nickname") or ""))
    except Exception as exc:
        log(f"抓昵称失败（不影响兜底）：{exc}", "WARN")

    if display and display != BOT_DISPLAY_NAME:
        BOT_DISPLAY_NAME = display
    if names and names != BOT_NAMES:
        BOT_NAMES = names
        log(f"名字识别集：{sorted(names)}｜自我介绍用「{BOT_DISPLAY_NAME}」")


def is_name_called(text: str) -> bool:
    """文本里有没有叫机器人的名字（不带 @ 段的那种叫法）。"""
    return any(n in text for n in BOT_NAMES)


# ---------------------------------------------------------------- 日志

def log(msg: str, level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {msg}"
    print(line, flush=True)
    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 状态

class State:
    def __init__(self) -> None:
        raw = {}
        if STATE_FILE.exists():
            try:
                raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                log("状态文件损坏，重新开始", "WARN")
        self.seen_order: list = list(raw.get("seen", []))
        self.seen: set = set(self.seen_order)
        self.seeded: set = set(raw.get("seeded", []))
        self.last_reply: dict = {int(k): v for k, v in (raw.get("last_reply") or {}).items()}
        self.last_cold_sticker: float = float(raw.get("last_cold_sticker", 0) or 0)

    def mark(self, message_id) -> None:
        if message_id in self.seen:
            return
        self.seen.add(message_id)
        self.seen_order.append(message_id)

    def save(self) -> None:
        if len(self.seen_order) > MAX_SEEN:
            drop = self.seen_order[:-MAX_SEEN]
            self.seen_order = self.seen_order[-MAX_SEEN:]
            self.seen.difference_update(drop)
        payload = {
            "seen": self.seen_order,
            "seeded": sorted(self.seeded),
            "last_reply": {str(k): v for k, v in self.last_reply.items()},
            "last_cold_sticker": self.last_cold_sticker,
        }
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)


# ---------------------------------------------------------------- 触发判断

def is_question(text: str) -> bool:
    if text.rstrip().endswith(("?", "？")):
        return True
    return any(h in text for h in ("吗？", "呢？", "怎么", "为什么", "如何", "请问", "求"))


def addressed_to_others(message, text: str) -> bool:
    """这条消息是不是在跟别人说话？

    两种情况都算：
      1. @ 了别人（@ 段里的 QQ 不是机器人自己）
      2. 纯文本里点了别的机器人/群友的名（比如"凛音别上来就甩PDF啊"）

    第 2 条是事故教训：那句话里"凛音"是纯文本不是 @ 段，
    结果机器人既没被点名、又看到"PDF"就冒出来发文档了。
    """
    bot = str(config.BOT_QQ)
    for qq in onebot.at_targets(message):
        # @全体成员 不算"跟别人说话"
        if qq not in (bot, "all", "0"):
            return True
    for name in config.IGNORE_NAMES:
        if name and name in text:
            return True
    return False


def is_image_only(text: str, has_image: bool) -> bool:
    """这条消息是不是「一张裸图」—— 只有图，一个字都没打。

    车主明确要求：裸图一律不主动接，只有被 @ 才回。
    （带字的图不算裸图，那是一条正常聊天消息，可以走闲聊插嘴。）
    """
    if not has_image:
        return False
    cleaned = text.replace("[图片]", "").replace("[表情]", "").strip()
    return not cleaned


def should_reply(text: str, mentioned: bool, params: dict,
                 image_only: bool = False, to_others: bool = False) -> tuple[bool, str]:
    """判断这条消息要不要接。

    ⚠️ 车主定的规矩（别改）：
        **只有被 @ 了才回**；@ 别人、或者点名别的机器人的消息，一律不参与。
        "被 @ + 让解题 / 要 PDF"是最典型的场景，但也包括 @ 之后的正常对话。
        没被点名时一律不主动搭话（闲聊插嘴默认关闭，见 config.IDLE_CHAT_ENABLED）。

    关于图片：图片不是触发条件。只有「引用图 + @ 机器人」才需要回，
    图片在被 @ 之后作为回答的素材（见 vision.extract 的回溯逻辑）。
    """
    if mentioned:
        return True, "被点名"
    # 车主规矩：只有被 @ 才回。没点名的一律不接 —— 管它是不是问句、有没有数学词。
    if config.MENTION_ONLY:
        return False, ""
    # 在跟别人说话 —— 不管内容是啥都别插嘴
    if to_others:
        return False, ""
    if image_only:
        return False, ""
    # 注意：wants_pdf 要求「被点名」才成立，而上面被点名已经返回了，
    # 所以这里不需要再判断 PDF —— 没被点名时说 PDF 只是聊天内容。
    if is_question(text) and random.random() < params["question"]:
        return True, "问句"
    if any(w in text for w in MATH_WORDS) and random.random() < params["math"]:
        return True, "数学相关"
    # 凌晨 idle 会被置 0，等于完全不主动搭话
    if params["idle"] > 0 and random.random() < params["idle"]:
        return True, "闲聊插嘴"
    return False, ""


def sender_name(msg: dict) -> str:
    sender = msg.get("sender") or {}
    return sender.get("card") or sender.get("nickname") or str(sender.get("user_id", "?"))


def build_context(history: list[dict], upto_index: int) -> str:
    start = max(0, upto_index - config.CONTEXT_SIZE)
    lines = []
    for m in history[start:upto_index]:
        text = onebot.message_text(m.get("message"))
        if text:
            lines.append(f"{sender_name(m)}: {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 发送

def send_parts(group_id: int, parts: list[dict]) -> None:
    sent_any = False
    for idx, part in enumerate(parts[: config.MAX_PARTS]):
        if idx > 0:
            time.sleep(humanize.gap_between_parts())

        if part["kind"] == "text":
            for j, chunk in enumerate(humanize.split_long_text(part["text"])):
                if j > 0:
                    time.sleep(humanize.gap_between_parts())
                delay = humanize.sleep_typing(chunk)
                onebot.send_group_text(group_id, chunk)
                sent_any = True
                log(f"  发送文字 {len(chunk)} 字（打字 {delay:.1f}s）")

        elif part["kind"] == "math":
            caption = part.get("caption", "")
            try:
                path = latexrender.render(part["latex"], part["mode"])
            except Exception as exc:
                log(f"  渲染失败：{exc}", "ERROR")
                continue

            if caption:
                humanize.sleep_typing(caption)
                onebot.send_group_text(group_id, caption)

            if part["mode"] == "card":
                onebot.send_group_image(group_id, path)
                log(f"  发送公式图片：{path.name}")
            else:
                # PDF 显示名由模型按任务类型起（批改过程 / 解题过程 / 推导过程…）
                fname = (part.get("filename") or "").strip() or "解答过程"
                if not fname.lower().endswith(".pdf"):
                    fname += ".pdf"
                onebot.send_group_file(group_id, path, name=fname)
                log(f"  发送 PDF：{path.name} → 群文件显示名「{fname}」")
            sent_any = True

        elif part["kind"] in ("sticker", "video"):
            image_mode = part["kind"] == "sticker"
            path = media.pick("image" if image_mode else "video")
            if not path:
                log(f"  素材库是空的（{part['kind']}），跳过", "WARN")
                continue
            # 发之前稍停一下，像人在相册里挑一张
            time.sleep(random.uniform(0.8, 2.2))
            if image_mode:
                onebot.send_group_image(group_id, path)
                log(f"  发送表情包：{path.name}")
            else:
                onebot.send_group_video(group_id, path)
                log(f"  发送视频：{path.name}")
            sent_any = True

    if not sent_any:
        log("  所有片段都没发成功", "WARN")


def maybe_cold_sticker(group_id: int, history: list[dict], state: State) -> None:
    """群里冷场太久时，偶尔丢个表情包活跃一下。

    只在「已经融入」（idle > 0）、非凌晨静默、且距上次冷场表情包超过冷却时间时才考虑。
    默认阈值保守：冷场 60 分钟以上、每 3 小时最多一次。
    """
    if config.COLD_STICKER_PROB <= 0:
        return
    params = rampup.effective()
    if params["quiet"] or params["idle"] <= 0:
        return
    if not history:
        return

    last_ts = max(int(m.get("time", 0)) for m in history)
    silence_min = (time.time() - last_ts) / 60.0
    if silence_min < config.COLD_SILENCE_MIN:
        return

    now = time.time()
    if now - state.last_cold_sticker < config.COLD_STICKER_COOLDOWN:
        return
    if random.random() > config.COLD_STICKER_PROB:
        return

    path = media.pick("image")
    if not path:
        return

    state.last_cold_sticker = now
    log(f"群{group_id} 冷场 {silence_min:.0f} 分钟，发个表情包活跃一下：{path.name}")
    time.sleep(random.uniform(1.0, 3.0))
    onebot.send_group_image(group_id, path)


# ---------------------------------------------------------------- 单条消息处理

def handle_message(group_id: int, msg: dict, history: list[dict], index: int, state: State) -> None:
    message = msg.get("message")
    text = onebot.message_text(message)

    # 取图（包含 reply 引用的那条消息里的图）—— 这是「能看图」的关键一步
    quoted_text, image_urls = vision.extract(message)
    if quoted_text:
        text = f"{text}（引用内容：{quoted_text}）".strip()

    if not text and not image_urls:
        return

    ats = onebot.at_targets(message)
    mentioned = str(config.BOT_QQ) in ats or is_name_called(text)
    # 在跟别人说话（@了别人 / 点名了别的机器人）—— 一律不参与
    to_others = addressed_to_others(message, text)
    # 必须先算出 mentioned，PDF 请求只有「对着机器人提」才算数
    pdf_requested = wants_pdf(text, mentioned)
    params = rampup.effective()

    ok, reason = should_reply(text, mentioned, params,
                              image_only=is_image_only(text, bool(image_urls)),
                              to_others=to_others)
    if not ok:
        return

    now = time.time()
    elapsed = now - state.last_reply.get(group_id, 0)
    # 被 @ 时只受极小的防爆走间隔约束，不会被同群冷却吞掉
    floor = config.MENTION_MIN_INTERVAL if mentioned else params["interval"]
    if elapsed < floor:
        log(f"群{group_id} 冷却中（还剩 {floor - elapsed:.0f}s），跳过：{text[:30]}")
        return

    # 人家明确要文件、或者带了图，不能装没看见
    if humanize.should_drop(mentioned or pdf_requested or bool(image_urls), params["drop"]):
        log(f"群{group_id} 装没看见（{reason}）：{text[:30]}")
        state.last_reply[group_id] = now
        return

    log(f"群{group_id} 决定回复（{reason}{' + PDF' if pdf_requested else ''}"
        f"{' + %d张图' % len(image_urls) if image_urls else ''}｜{params['phase']}）"
        f"{sender_name(msg)}: {text[:40]}")

    context = build_context(history, index)
    time.sleep(humanize.reading_delay(text))

    result = brain.decide(context, f"{sender_name(msg)}: {text}", mentioned,
                          pdf_requested, params["style"], image_urls=image_urls,
                          bot_name=BOT_DISPLAY_NAME)
    state.last_reply[group_id] = time.time()

    # 主线路挂了会自动换备用，这里留个痕迹方便排查
    provider = result.get("provider")
    if provider and config.PROVIDERS and provider != config.PROVIDERS[0]["name"]:
        log(f"  ⚠ 主线路不可用，本次由「{provider}」作答")

    if result["action"] != "reply" or not result["parts"]:
        # 「@ 必回」是硬承诺：模型不接，就自己接一句，绝不冷场
        if mentioned:
            line = random.choice(MENTION_FALLBACK)
            log(f"  被点名但模型没接话，发兜底回应：{line}")
            humanize.sleep_typing(line)
            onebot.send_group_text(group_id, line)
        else:
            log(f"  模型选择不接话：{result.get('reason','')[:80]}")
        return

    send_parts(group_id, result["parts"])

    # 发完 PDF 后，一定要 @ 提问者说一声。
    # 模型没给收尾话就用兜底 —— 出了文件却没人被 @，对方根本不知道是做给他的。
    sent_pdf = any(p["kind"] == "math" and p.get("mode") == "doc" for p in result["parts"])
    closing = (result.get("closing") or "").strip()
    if sent_pdf and not closing:
        closing = random.choice(CLOSING_FALLBACK)
    asker = (msg.get("sender") or {}).get("user_id")
    if closing and asker:
        time.sleep(random.uniform(0.8, 2.0))
        humanize.sleep_typing(closing)
        try:
            onebot.send_group_at(group_id, asker, closing)
            log(f"  收尾 @{asker}：{closing[:40]}")
        except Exception as exc:
            log(f"  收尾消息发送失败：{exc}", "WARN")


# ---------------------------------------------------------------- 主循环

def loop_group(group_id: int, state: State) -> None:
    messages = onebot.get_group_msg_history(group_id, count=30)
    if not messages:
        return
    history = sorted(messages, key=lambda m: m.get("time", 0))

    if group_id not in state.seeded:
        for m in history:
            state.mark(m.get("message_id"))
        state.seeded.add(group_id)
        state.save()
        log(f"群 {group_id} 首次运行：已把 {len(history)} 条历史标记为已读，不回旧消息")
        return

    for index, msg in enumerate(history):
        mid = msg.get("message_id")
        if mid in state.seen:
            continue
        state.mark(mid)

        if str((msg.get("sender") or {}).get("user_id")) == str(config.BOT_QQ):
            continue
        if time.time() - int(msg.get("time", 0)) > 300:
            continue

        try:
            handle_message(group_id, msg, history, index, state)
        except Exception as exc:
            log(f"处理消息出错：{exc}\n{traceback.format_exc()[:600]}", "ERROR")

    try:
        maybe_cold_sticker(group_id, history, state)
    except Exception as exc:
        log(f"冷场表情包判断出错：{exc}", "WARN")

    state.save()


def main() -> int:
    log("=" * 64)
    log(f"启动 | bot_qq={config.BOT_QQ} | 群={config.ALLOWED_GROUPS} | 轮询={config.POLL_INTERVAL}s")

    try:
        info = onebot.get_login_info()
        log(f"当前登录：{info.get('user_id')} 「{info.get('nickname')}」")
    except Exception as exc:
        log(f"拿不到登录信息，NapCat 可能没起来：{exc}", "ERROR")
        return 1

    # 抓一次当前名字（昵称 + 各群名片），派生"被点名的写法"
    refresh_bot_names(force=True)

    state = State()
    failures = 0
    last_phase = None

    while True:
        # 名字可能被改（改昵称/群名片），定期重新抓一次，不用重启
        try:
            refresh_bot_names()
        except Exception:
            pass

        # 融入阶段变了就记一笔，方便事后对日志
        try:
            params = rampup.effective()
            if params["phase"] != last_phase:
                note = "  [凌晨静默，不主动搭话]" if params["quiet"] else ""
                log(f"行为阶段：{params['phase']}（已过 {params['elapsed_h']}h）"
                    f"｜插嘴 {params['idle']}｜数学词 {params['math']}｜问句 {params['question']}"
                    f"｜间隔 {params['interval']}s{note}")
                last_phase = params["phase"]
        except Exception as exc:
            log(f"读取融入阶段失败：{exc}", "WARN")

        try:
            for group_id in config.ALLOWED_GROUPS:
                loop_group(group_id, state)
            failures = 0
        except KeyboardInterrupt:
            log("收到中断，退出")
            state.save()
            return 0
        except Exception as exc:
            failures += 1
            log(f"主循环异常（第 {failures} 次）：{exc}", "ERROR")
            if failures == 5:
                log("连续失败 5 次，等待 30 秒再继续", "WARN")
                time.sleep(30)
            time.sleep(min(config.POLL_INTERVAL * failures, 15))
            continue

        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
