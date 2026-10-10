#!/usr/bin/env python3
"""额度监控 —— 估算这台机器的累计消费，超阈值就 QQ 私聊提醒你。

⚠️ 重要：这是**估算**，不是账单。它靠两个单价乘用量算出来：
       实例运行小时数 × 每小时单价  +  累计出站流量 × 每 GB 单价
   真实数字以阿里云费用中心为准。这个脚本的价值是「趋势预警」，
   让你不会在完全不知情的情况下把额度烧光。

**权威兜底仍然要设阿里云自带的「可用额度预警」**（免费，短信 + 邮件）：
   控制台 → 费用中心 → 可用额度预警 → 设一个阈值（比如余额低于 ¥100 就通知）
两者配合才最稳：一个准，一个能在你手机上调。

单价可以在 .env 里覆盖（默认值是估算的，你可以按实际账单校准）：
   SPEND_HOUR_RATE=0.20      元/小时（实例 + 系统盘）
   SPEND_TRAFFIC_RATE=0.8    元/GB
   SPEND_ALERT_AT=200        估算累计到多少元就提醒
   SPEND_ALERT_QQ=           提醒发到哪个 QQ（默认你的主号）

单独跑一次看现状（不发提醒）：
   sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python /home/qqbot/qq-bot/spend.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import config

STATE_FILE = config.STATE_DIR / "spend.json"
GB = 1024 ** 3


def default_iface() -> str:
    """从路由表找出默认网卡。"""
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 2 and fields[1] == "00000000":
                return fields[0]
    except OSError:
        pass
    return "eth0"


def tx_bytes(iface: str) -> int:
    """网卡累计发出的字节数。"""
    try:
        for line in Path("/proc/net/dev").read_text().splitlines():
            line = line.strip()
            if line.startswith(iface + ":"):
                return int(line.split(":")[1].split()[8])
    except (OSError, IndexError, ValueError):
        pass
    return 0


def load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def instance_start_time() -> float:
    """用系统启动时间当计费起点。

    新开的按量实例，启动时间基本等于创建时间，所以这个估算够用。
    （如果实例被停止过再开机，估算会偏小——真实数字以账单为准。）
    """
    try:
        import subprocess
        out = subprocess.run(["uptime", "-s"], capture_output=True, text=True, timeout=5)
        stamp = out.stdout.strip()
        if out.returncode == 0 and stamp:
            return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    return time.time()


def save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def build_alert(total: float, hours: float, gb: float, inst: float, traf: float,
                empty_date: str) -> str:
    quota = config.SPEND_TOTAL_QUOTA or 1.0
    return (
        f"【额度提醒】-e阶导- 机器人\n"
        f"\n"
        f"估算已用 ¥{total:.0f} / ¥{config.SPEND_TOTAL_QUOTA:.0f}"
        f"（{total / quota * 100:.0f}%）\n"
        f"已过提醒线 ¥{config.SPEND_ALERT_AT:.0f}\n"
        f"\n"
        f"  运行 {hours:.0f} 小时 × ¥{config.SPEND_HOUR_RATE}/h ≈ ¥{inst:.0f}\n"
        f"  出站流量 {gb:.2f} GB"
        f"（免费额度 {config.SPEND_FREE_TRAFFIC_GB:.0f} GB 内不计费）\n"
        f"\n"
        f"按当前速率，额度大约 {empty_date} 用尽。\n"
        f"额度有效期到 {config.SPEND_QUOTA_EXPIRE}。\n"
        f"\n"
        f"注意：这是本机估算，不是账单，准确数字看阿里云费用中心。\n"
        f"该充值还是该停机，你决定。"
    )


def main() -> int:
    now = time.time()
    state = load()

    if "since" not in state:
        state["since"] = instance_start_time()   # 用开机时间当计费起点
    iface = state.get("iface") or default_iface()
    state["iface"] = iface

    current_tx = tx_bytes(iface)
    last_tx = state.get("last_tx")

    if last_tx is None:
        delta = 0
    elif current_tx < last_tx:
        delta = current_tx          # 计数器归零 = 重启过
    else:
        delta = current_tx - last_tx

    state["cum_bytes"] = int(state.get("cum_bytes", 0)) + delta
    state["last_tx"] = current_tx

    hours = (now - state["since"]) / 3600.0
    gb = state["cum_bytes"] / GB

    inst_cost = hours * config.SPEND_HOUR_RATE
    # 公网流量有免费额度，超出部分才算钱
    billable_gb = max(0.0, gb - config.SPEND_FREE_TRAFFIC_GB)
    traf_cost = billable_gb * config.SPEND_TRAFFIC_RATE
    total = inst_cost + traf_cost

    # 预测：主要成本就是「机器开着」，所以按小时单价外推
    rate = config.SPEND_HOUR_RATE
    if rate > 0:
        h_to_alert = max(0.0, (config.SPEND_ALERT_AT - total) / rate)
        h_to_empty = max(0.0, (config.SPEND_TOTAL_QUOTA - total) / rate)
        alert_date = time.strftime("%Y-%m-%d %H:%M", time.localtime(now + h_to_alert * 3600))
        empty_date = time.strftime("%Y-%m-%d %H:%M", time.localtime(now + h_to_empty * 3600))
        empty_days = h_to_empty / 24
    else:
        alert_date = empty_date = "无法预测"
        empty_days = 0

    state.update({
        "last_check": int(now),
        "est_total": round(total, 2),
        "est_hours": round(hours, 1),
        "est_gb": round(gb, 3),
        "est_billable_gb": round(billable_gb, 3),
    })

    quota = config.SPEND_TOTAL_QUOTA or 1.0
    print(f"[额度估算] 已用 ¥{total:.2f} / ¥{config.SPEND_TOTAL_QUOTA:.0f}"
          f"（剩 {100 - total / quota * 100:.2f}%）")
    print(f"           运行 {hours:.1f}h × ¥{config.SPEND_HOUR_RATE}/h = ¥{inst_cost:.2f}")
    if billable_gb > 0:
        print(f"           流量 {gb:.3f}GB（超出免费额度 {billable_gb:.3f}GB）= ¥{traf_cost:.2f}")
    else:
        print(f"           流量 {gb:.3f}GB（免费额度 {config.SPEND_FREE_TRAFFIC_GB:.0f}GB 内，不计费）")
    if rate > 0:
        days_to_alert = max(0.0, (config.SPEND_ALERT_AT - total) / rate) / 24
        print(f"           按当前速率：约 {days_to_alert:.1f} 天后到"
              f" ¥{config.SPEND_ALERT_AT:.0f} 提醒线（{alert_date}）")
        print(f"                       约 {empty_days:.1f} 天后额度用尽（{empty_date}）")
    else:
        print("           未配置小时单价，无法预测")
    print(f"           额度有效期至 {config.SPEND_QUOTA_EXPIRE}")

    # 到线了就私聊提醒（只提醒一次，避免刷屏）
    if total >= config.SPEND_ALERT_AT and not state.get("alerted"):
        save(state)
        if config.SPEND_ALERT_QQ:
            try:
                import onebot
                text = build_alert(total, hours, gb, inst_cost, traf_cost, empty_date)
                onebot.send_private_text(config.SPEND_ALERT_QQ, text)
                state["alerted"] = True
                state["alerted_at"] = int(now)
                print(f"  -> 已私聊提醒 {config.SPEND_ALERT_QQ}")
            except Exception as exc:
                print(f"  -> 提醒发送失败：{exc}", file=sys.stderr)
        else:
            print("  -> 没配 SPEND_ALERT_QQ，跳过提醒", file=sys.stderr)

    save(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
