"""逐步融入群聊的时间表。

为什么需要这个：一个刚进群的新号，如果上来就什么话都接、还主动搭话，
是最容易被看出是机器人的。而且参考你那份额外文档的教训——
「未信任设备 + 高频活动 = 加速风控」。

所以前几个小时先潜水观察，之后按时间表一点点放开。

要调时间表，改下面的 PHASES。想立刻跳到常态，把 PHASES 第一项改成
常态参数、并把 state/rampup.json 删掉重启即可。

查当前处于哪个阶段：
    sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python /home/qqbot/qq-bot/rampup.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import config

STATE_FILE = config.STATE_DIR / "rampup.json"

# (进入该阶段所需的「已经过小时数」, 该阶段的参数)
#   idle     纯闲聊主动插嘴的概率
#   math     消息里出现数学关键词时的回复概率
#   question 问句结尾时的回复概率
#   interval 同一个群里两次回复的最小间隔（秒）
#   drop     非点名消息「装没看见」的概率
#   style    给模型的行为提示（影响它说话的长短和主动性）
PHASES: list[tuple[float, dict]] = [
    (0, dict(
        idle=0.05, math=0.40, question=0.85, interval=75, drop=0.15,
        style="你已经在群里开始活跃起来了。可以自然接话，群友闲聊你也搭得上，"
              "不用非得聊数学。但别抢话、别连着刷屏，回复短一点，"
              "像群友随手打的一句，不是写答案。",
    )),
    (3, dict(
        idle=0.07, math=0.45, question=0.85, interval=60, drop=0.12,
        style="你已经融入这个群了，可以自然地接话，偶尔主动起个话头、开个玩笑。",
    )),
    (12, dict(
        idle=0.08, math=0.50, question=0.85, interval=45, drop=0.10,
        style="你是群里挺活跃的一员了，怎么自然怎么来，该贫就贫两句。",
    )),
    (24, dict(
        idle=0.09, math=0.50, question=0.85, interval=40, drop=0.08,
        style="你是群里的老熟人了，随意自然就好。",
    )),
]

# 凌晨这段时间不主动闲聊（被点名、要 PDF 照常回）
# 一个真实的人不会凌晨四点在群里主动搭话，这是最容易露馅的细节之一
QUIET_START, QUIET_END = 1, 7


def _load_start() -> float:
    """读起始时间；没有就现在开始计时，并落盘，重启不会重置进度。"""
    if STATE_FILE.exists():
        try:
            return float(json.loads(STATE_FILE.read_text(encoding="utf-8"))["start"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
    now = time.time()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"start": now}), encoding="utf-8")
    return now


_START = _load_start()


def start_time() -> float:
    return _START


def elapsed_hours(now: float | None = None) -> float:
    return ((now or time.time()) - _START) / 3600.0


def in_quiet_hours(now: float | None = None) -> bool:
    hour = time.localtime(now or time.time()).tm_hour
    return QUIET_START <= hour < QUIET_END


def effective(now: float | None = None) -> dict:
    """算出此刻该用的行为参数。"""
    now = now or time.time()
    hours = elapsed_hours(now)

    index = 0
    for i, (need, _) in enumerate(PHASES):
        if hours >= need:
            index = i

    params = dict(PHASES[index][1])
    params["phase"] = f"阶段{index + 1}"
    params["elapsed_h"] = round(hours, 2)
    params["quiet"] = in_quiet_hours(now)
    if params["quiet"]:
        params["idle"] = 0.0          # 凌晨只答疑，不主动搭话
    # 车主规定「要被 @ 才回」，所以默认把主动搭话关掉。
    # 想恢复，把 .env 里的 IDLE_CHAT_ENABLED 设成 1。
    if not config.IDLE_CHAT_ENABLED:
        params["idle"] = 0.0

    params["next_phase_h"] = PHASES[index + 1][0] if index + 1 < len(PHASES) else None
    return params


if __name__ == "__main__":
    now = time.time()
    p = effective(now)
    print("=" * 58)
    print(f"  起始时间 : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_START))}")
    print(f"  已经过   : {p['elapsed_h']} 小时")
    print(f"  当前阶段 : {p['phase']}")
    if p["next_phase_h"] is not None:
        left = max(0.0, p["next_phase_h"] - p["elapsed_h"])
        when = time.strftime("%H:%M", time.localtime(now + left * 3600))
        print(f"  下一阶段 : 再过 {left:.1f} 小时（约 {when}）进入")
    print(f"  凌晨静默 : {'是（现在只答疑，不主动搭话）' if p['quiet'] else '否'}")
    print("=" * 58)
    print(f"  闲聊主动插嘴概率 : {p['idle']}")
    print(f"  数学词回复概率   : {p['math']}")
    print(f"  问句回复概率     : {p['question']}")
    print(f"  同群最小间隔     : {p['interval']} 秒")
    print(f"  装没看见概率     : {p['drop']}")
    print(f"  行为提示         : {p['style']}")
    print("=" * 58)
