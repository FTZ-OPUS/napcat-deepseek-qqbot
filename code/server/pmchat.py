"""pmchat.py —— 私信问答（白名单制，2026-10-11 车主要求）

功能：白名单里的好友私信机器人 → 走 brain.decide 正常问答 → 私信回复。
上线动机：车主要给朋友介绍机器人（"私信一句话就行"），而主循环原本
只轮询群消息——不补这块，介绍出去的服务就是空话。

设计：
  - 白名单 `PM_CHAT_USERS`（逗号分隔）：不在名单里的私信一概不处理
  - 安全保镖通道互不干扰：跳过 SAFETY_OWNER_QQ（那是 safety.py 的专用通道）
  - 每个用户独立水位（state/pmchat.json），只处理增量消息
  - 回复复用主流程能力：文字/图片(card)/PDF(doc)、表格算术自检、
    机器人自己的消息按发送者过滤（绝不自问自答）
  - 任何异常都不许打断主循环
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pdf2md
import brain
import config
import humanize
import latexrender
import media
import onebot
import persona
import rampup
import safety
import tablecheck
import uuid

_NOW = time.time
_POLL_INTERVAL = 30.0          # 私信轮询节流（秒）

# ⚠️ 时效护栏（2026-10-12 事故后加的，**不要删**）
# 只回"刚刚"发的消息（默认 10 分钟）。游标一旦丢失（测试污染 / 换机器 /
# 文件损坏），所有历史消息都会被当成新消息重答一遍。
# 真实发生过：凌晨 3 点把两个好友的历史私信全回了一遍，车主被骚扰到。
_MAX_AGE = 600.0
_last_run = 0.0
_state: dict | None = None

_STATE_PATH = Path(config.STATE_DIR) / "pmchat.json"


def log(msg: str, level: str = "INFO") -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] 私信问答: {msg}"
    print(line, flush=True)


def _users() -> list[int]:
    """白名单。

    ⚠️ 2026-10-12 变更：**车主本人现在也在私信问答范围内**（车主定的 ——
    他是最该能私聊的人）。之前这里把 SAFETY_OWNER_QQ 排除掉，导致车主私信
    完全没人理。

    和安全保镖不会打架：
      - 两边用**各自独立的时间游标**，不是互相消费
      - safety 只在**值守开启时**、且**每天只回一次**「平安已记录」
      - 命令消息由 `safety.is_command()` 避让（见下面的 _SKIP 判断），
        不会把「开启安全保镖」当成聊天问题回答
      - main.py 里两个 tick 各自 try/except，一边炸不影响另一边
    """
    return [u for u in config.PM_CHAT_USERS if u]


def _load() -> dict:
    global _state
    if _state is None:
        try:
            _state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _state = {}
        _state.setdefault("last_ts", {})
    return _state


def _save() -> None:
    if _state is None:
        return
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(
        json.dumps(_state, ensure_ascii=False), encoding="utf-8")


def _send_parts(user_id: int, parts: list[dict]) -> bool:
    """私信版的分段发送。返回是否至少成功发出一条。"""
    sent_any = False
    for part in parts[: config.MAX_PARTS]:
        try:
            if part.get("kind") == "text":
                for chunk in humanize.split_long_text(part["text"]):
                    humanize.sleep_typing(chunk)
                    onebot.send_private_text(user_id, chunk)
                    sent_any = True
            elif part.get("kind") in ("sticker", "video"):
                path = media.pick("image" if part["kind"] == "sticker" else "video")
                if not path:
                    log(f"素材库空（{part['kind']}），跳过", "WARN")
                    continue
                if part["kind"] == "sticker":
                    onebot.send_private_image(user_id, path)
                else:
                    onebot.send_private_video(user_id, path)
                sent_any = True
            elif part.get("kind") == "math":
                path = latexrender.render(part.get("latex", ""),
                                          part.get("mode", "card"),
                                          style=config.LATEX_DEFAULT_STYLE)
                if part.get("caption"):
                    humanize.sleep_typing(part["caption"])
                    onebot.send_private_text(user_id, part["caption"])
                if part.get("mode") == "card":
                    onebot.send_private_image(user_id, path)
                else:
                    fname = (part.get("filename") or "").strip() or "解答过程"
                    if not fname.lower().endswith(".pdf"):
                        fname += ".pdf"
                    onebot.send_private_file(user_id, path, name=fname)
                sent_any = True
        except Exception as exc:
            log(f"片段发送失败（{user_id}）：{exc}", "WARN")
    return sent_any


_FORM_HINT = (
    "\n\n【输出形式，按回答的体量自己选】几句话能说清 → 只用 text 片段；"
    "要排版/要完整成文 → mode 用 \"doc\"（我会渲染成 PDF 发回）；"
    "阅读总结类文件名用「阅读总结」。")


def _handle_private_pdf(user_id: int, name: str, file_seg: dict,
                        instr: str) -> None:
    """白名单好友私信发 PDF：下载 → 转 MD（本地零 token）→ 按指令作答。

    指令取文件消息前最近的一条文字；没有就默认写阅读总结。
    输出形式交模型按体量选，但保留大预算防截断。
    """
    data = file_seg.get("data") or {}
    fname = str(data.get("file") or data.get("file_name") or "文件.pdf")
    file_id = str(data.get("file_id") or "")
    url = str(data.get("url") or "")
    log(f"{user_id} 私信发来 PDF《{fname}》｜指令：{instr[:40] or '（默认阅读总结）'}")

    def fail(why: str) -> None:
        try:
            onebot.send_private_text(user_id, f"{why}，你看看文件对不对，或者过会儿再发一次")
        except Exception as exc:
            log(f"失败通知也发不出去：{exc}", "WARN")

    dest = Path(config.STATE_DIR) / f"pm_pdf_{uuid.uuid4().hex[:8]}.pdf"
    try:
        if url:
            onebot.download_file(url, dest, max_bytes=25 * 1024 * 1024)
        elif file_id:
            info = onebot.get_file(file_id)
            local = Path(str(info.get("file") or ""))
            if not local.exists():
                raise RuntimeError("拿不到文件内容")
            local.replace(dest)
        else:
            raise RuntimeError("消息里没有下载地址")
        md, stats = pdf2md.pdf_to_md(dest, max_chars=30000, max_pages=60)
    except Exception as exc:
        fail(f"文件处理失败（{str(exc)[:60]}）")
        return
    finally:
        dest.unlink(missing_ok=True)

    if stats["chars"] < 40:
        fail("这份 PDF 转不出文字，多半是扫描件/图片型的")
        return

    cut = "，超长已截断" if stats["truncated"] else ""
    log(f"转 MD 完成：{stats['pages']} 页｜{stats['chars']} 字符{cut}")
    if not instr:
        instr = "请通读这份资料，写一份阅读总结：讲了什么、核心内容与结论、值得注意的点。"
    context = (f"【对方私发来的 PDF《{fname}》（共 {stats['page_count']} 页，"
               f"本次处理 {stats['pages']} 页，已转 Markdown{cut}）】\n{md}" + _FORM_HINT)
    result = brain.decide(context, f"{name}: {instr}", mentioned=True,
                          style_hint="私聊，正常聊",
                          bot_name=persona.DEFAULT_BOT_NAME, max_tokens=8000)
    if result.get("action") != "reply" or not result.get("parts"):
        fail("我没处理出来")
        return
    try:
        if config.TABLECHECK_ENABLED:
            result["parts"], extra = tablecheck.enforce(
                result["parts"], revise=brain.revise_table, log=log)
            if extra:
                texts = [p2 for p2 in result["parts"] if p2.get("kind") == "text"]
                if texts:
                    texts[0]["text"] = (texts[0]["text"] + " " + extra)[:120]
        sent = _send_parts(user_id, result["parts"])
        log(f"PDF 任务已回复 {user_id}（{'成功' if sent else '全部片段失败'}）")
    except Exception as exc:
        log(f"回复 {user_id} 失败：{exc!r}", "ERROR")


def _answer(user_id: int, name: str, text: str) -> None:
    """私信问答主链路：decide → 表格自检 → 私信发送。"""
    result = brain.decide(
        f"（这是一段与你的私聊，对方是 {name}。私聊没有群背景，"
        f"回答保持自然、简短即可。）",
        f"{name}: {text}",
        mentioned=True,                    # 对方主动私聊 = 明确在跟机器人说话
        style_hint="私聊，正常聊",
        bot_name=persona.DEFAULT_BOT_NAME,
        max_tokens=4000,
    )
    if result.get("action") != "reply" or not result.get("parts"):
        log(f"{user_id} 的私信模型选择不接话")
        return
    try:
        note = ""
        if config.TABLECHECK_ENABLED:
            result["parts"], note = tablecheck.enforce(
                result["parts"], revise=brain.revise_table, log=log)
            if note:
                texts = [p for p in result["parts"] if p.get("kind") == "text"]
                if texts:
                    texts[0]["text"] = (texts[0]["text"] + " " + note)[:120]
        sent = _send_parts(user_id, result["parts"])
        log(f"已回复 {user_id}（{'成功' if sent else '全部片段失败'}）")
    except Exception as exc:
        log(f"回复 {user_id} 失败：{exc!r}", "ERROR")


def maybe_tick() -> None:
    """主循环挂载点。内部 30 秒节流；白名单为空直接返回。"""
    global _last_run
    if not config.PM_CHAT_ENABLED:
        return
    users = _users()
    if not users:
        return
    now = _NOW()
    if now - _last_run < _POLL_INTERVAL:
        return
    _last_run = now
    try:
        st = _load()
        for uid in users:
            last = float(st["last_ts"].get(str(uid), 0))
            try:
                msgs = onebot.get_friend_msg_history(uid, count=8)
            except Exception as exc:
                # 最常见原因：还不是好友（拉不到会话）
                log(f"拉 {uid} 私信历史失败（是好友吗？）：{exc}", "WARN")
                continue
            # 只处理对方本人发的消息（排除机器人自己）
            mine = [m for m in msgs
                    if int((m.get("sender") or {}).get("user_id") or 0) == int(uid)]
            fresh = [m for m in mine if float(m.get("time", 0)) > last]
            if not fresh:
                continue
            st["last_ts"][str(uid)] = max(float(m.get("time", 0)) for m in fresh)
            # 避让安全保镖的命令：「开启安全保镖」这种不是聊天问题，归 safety.py 管。
            # （车主本人现在也在白名单里，所以这道避让是必须的）
            fresh = [m for m in fresh
                     if not safety.is_command(onebot.message_text(m.get("message")))]
            # ⚠️ 时效护栏：只回"刚刚"的消息。游标丢过的话，历史消息会全部涌进来。
            cutoff = now - _MAX_AGE
            stale = [m for m in fresh if float(m.get("time", 0)) < cutoff]
            if stale:
                log(f"{uid} 有 {len(stale)} 条超过 {int(_MAX_AGE)} 秒的旧消息，"
                    f"只推进游标不回（游标可能丢过）", "WARN")
            fresh = [m for m in fresh if float(m.get("time", 0)) >= cutoff]
            if not fresh:
                _save()
                continue
            # ⚠️ 凌晨静默：不发私信（车主 2026-10-12 定："更不要凌晨主动给他们发消息"）
            # 复用群聊同一套静默判定，两边口径一致。
            if rampup.in_quiet_hours(now):
                log(f"凌晨静默：跳过 {uid} 的 {len(fresh)} 条，不主动发私信")
                _save()
                continue
            _save()
            for idx, m in enumerate(sorted(fresh, key=lambda x: float(x.get("time", 0)))):
                segs = m.get("message") if isinstance(m.get("message"), list) else []
                file_seg = next((x for x in segs
                                 if isinstance(x, dict) and x.get("type") == "file"), None)
                if file_seg and str((file_seg.get("data") or {}).get("file", "")).lower().endswith(".pdf"):
                    # 私信发 PDF：查找文件前最近的一条文字消息当指令
                    name = (m.get("sender") or {}).get("nickname") or str(uid)
                    instr = ""
                    for prev in reversed(fresh[:idx]):
                        psegs = prev.get("message") if isinstance(prev.get("message"), list) else []
                        ptext = (onebot.message_text(psegs) or "").strip()
                        if ptext and not ptext.startswith("[文件"):
                            instr = ptext
                            break
                    _handle_private_pdf(uid, name, file_seg, instr)
                    continue
                text = (onebot.message_text(m.get("message")) or "").strip()
                if not text:
                    continue
                name = (m.get("sender") or {}).get("nickname") or str(uid)
                _answer(uid, name, text)
    except Exception as exc:
        log(f"tick 异常（不影响主循环）：{exc!r}", "WARN")
