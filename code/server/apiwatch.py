#!/usr/bin/env python3
"""DeepSeek 余额监控 —— 服务器和 API 是两个钱包，都得盯。

为什么需要这个：
    机器要同时依赖「阿里云服务器开着」和「DeepSeek 有余额」，缺一个都不干活。
    但这两笔钱是分开充的。阿里云那 ¥300 免费额度只保证机器在跑，
    DeepSeek 那边余额耗尽的话，表现是**机器人默默不回话了**——
    没有报错弹给你，你只会觉得它哑巴了。所以单独监控。

查余额用的是 DeepSeek 官方接口：
    GET https://api.deepseek.com/user/balance
    需要带上你自己的 API key（就在 .env 里）

低于阈值就 QQ 私聊提醒你。充钱之后余额回升，会自动重新武装，下次再低还能再提醒。

单独跑一次看余额：
    sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python /home/qqbot/qq-bot/apiwatch.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

import config

STATE_FILE = config.STATE_DIR / "apiwatch.json"


def load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def fetch_balance() -> tuple[float | None, str]:
    """返回 (人民币余额, 说明)。查不到时余额为 None。"""
    if not config.DEEPSEEK_API_KEY:
        return None, "没配 DEEPSEEK_API_KEY"
    try:
        resp = requests.get(
            f"{config.DEEPSEEK_BASE_URL}/user/balance",
            headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
            timeout=20,
        )
    except requests.RequestException as exc:
        return None, f"网络错误: {exc}"

    if resp.status_code == 401:
        return None, "API key 无效或已失效（401）"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text[:160]}"

    try:
        data = resp.json()
    except ValueError:
        return None, "返回值不是 JSON"

    if not data.get("is_available", True):
        return 0.0, "账号余额不足（接口明确返回不可用）"

    infos = data.get("balance_infos") or []
    if not infos:
        return None, "接口没返回余额明细"

    # 优先人民币
    pick = next((i for i in infos if str(i.get("currency", "")).upper() == "CNY"), infos[0])
    try:
        return float(pick.get("total_balance", 0)), str(pick.get("currency", ""))
    except (TypeError, ValueError):
        return None, "余额字段无法解析"


def check_relay() -> tuple[bool, str]:
    """探备用线路还活着没。用 /models 接口，不消耗 token。

    中转站的余额接口返回的是假数据（hard_limit_usd 恒定 1e8），
    没法像官方那样读真余额。所以退一步：只判断它「还能不能用」。
    """
    if len(config.PROVIDERS) < 2:
        return True, "未配置备用线路"
    # 最后一条固定是兜底那条（中转站），探它最有意义
    provider = config.PROVIDERS[-1]
    try:
        resp = requests.get(
            f"{provider['base_url']}/models",
            headers={"Authorization": f"Bearer {provider['key']}"},
            timeout=20,
        )
    except requests.RequestException as exc:
        return False, f"连不上（{exc}）"
    if resp.status_code == 200:
        return True, "正常"
    return False, f"HTTP {resp.status_code}"


def build_api_down_alert(primary_note: str, relay_note: str) -> str:
    return (
        f"🚨 【机器人已经答不了话了】\n"
        f"\n"
        f"两条 API 线路同时失效，现在群里 @ 它不会有任何反应。\n"
        f"\n"
        f"  主线路：{primary_note}\n"
        f"  备用线路：{relay_note}\n"
        f"\n"
        f"请尽快处理（给官方充值，或去中转站看余额）。\n"
        f"\n"
        f"注意：这条消息能发出来，说明 QQ 那边还是好的——\n"
        f"只有「大脑」停了，「嘴」还在。"
    )


def build_alert(balance: float, burn_per_day: float | None = None) -> str:
    if burn_per_day:
        days = balance / burn_per_day if burn_per_day > 0 else 0
        rate_line = f"\n实测消耗约 ¥{burn_per_day:.2f}/天，按此速度还能撑约 {days:.1f} 天。\n"
    else:
        rate_line = ""
    return (
        f"【API 余额提醒】-e阶导- 机器人\n"
        f"\n"
        f"DeepSeek 余额只剩 ¥{balance:.2f}（提醒线 ¥{config.DEEPSEEK_ALERT_AT:.0f}）\n"
        f"{rate_line}"
        f"\n"
        f"余额耗尽后，机器人的表现是「突然不回话了」——\n"
        f"不会弹错误给你，只会安静地哑掉。所以提前告诉你。\n"
        f"\n"
        f"去 platform.deepseek.com 充值，充完这个提醒会自动恢复。\n"
        f"\n"
        f"（顺带提醒：阿里云那 ¥300 是另一笔钱，只管服务器开机，\n"
        f"  加起来机器才能干活。）"
    )


def main() -> int:
    now = time.time()
    state = load()
    balance, note = fetch_balance()

    if balance is None:
        print(f"[API余额] 主线路查询失败：{note}")
        state["last_error"] = note
        state["last_check"] = int(now)
        # 注意：这里**不能 return** —— 主线路不可用，正是最需要检查备用线路的时候
    else:
        print(f"[API余额] DeepSeek 剩余 ¥{balance:.2f}（提醒线 ¥{config.DEEPSEEK_ALERT_AT:.0f}）")

        state["last_check"] = int(now)
        state["balance"] = round(balance, 2)
        state.pop("last_error", None)

        # 记录余额历史，用来算消耗速率（只留 48 小时）
        history = [h for h in (state.get("history") or []) if h[0] >= now - 48 * 3600]
        history.append([int(now), round(balance, 2)])
        state["history"] = history

        burn_per_day = None
        if len(history) >= 2:
            span_h = (now - history[0][0]) / 3600.0
            used = history[0][1] - balance
            if span_h >= 0.5 and used > 0:
                burn_per_day = used / span_h * 24
                state["burn_per_day"] = round(burn_per_day, 3)

        if burn_per_day:
            days_left = balance / burn_per_day if burn_per_day > 0 else 999
            print(f"           实测消耗 ¥{burn_per_day:.2f}/天  →  按此速度还能撑 {days_left:.1f} 天")
        else:
            print("           消耗速率：观察中（需要至少半小时的数据）")

    # ---- 备用线路探活 ----
    relay_ok, relay_note = check_relay()
    primary_ok = balance is not None and balance > 0
    print(f"[线路状态] 主线路 {'可用' if primary_ok else '不可用'}"
          f"（余额 {'¥%.2f' % balance if balance is not None else '查询失败'}）"
          f"  |  备用线路 {relay_note}")

    # 两条都废了 = 机器人已经是个哑巴，必须告警。这种状态要能反复提醒
    if not primary_ok and not relay_ok:
        last_down_alert = state.get("down_alerted_at", 0)
        if now - last_down_alert >= config.API_DOWN_ALERT_INTERVAL:
            save(state)
            if config.SPEND_ALERT_QQ:
                try:
                    import onebot
                    primary_note = note if balance is None else "余额已归零"
                    onebot.send_private_text(
                        config.SPEND_ALERT_QQ, build_api_down_alert(primary_note, relay_note)
                    )
                    state["down_alerted_at"] = int(now)
                    print(f"  -> 🚨 两条线路全废，已私聊告警 {config.SPEND_ALERT_QQ}")
                except Exception as exc:
                    print(f"  -> 告警发送失败：{exc}", file=sys.stderr)
            # 立刻落盘，别等到函数末尾 —— 万一后面抛异常，节流标记就丢了
            save(state)
        else:
            left = (config.API_DOWN_ALERT_INTERVAL - (now - last_down_alert)) / 3600
            print(f"           两条线路仍不可用，{left:.1f} 小时后再次告警")

    # 主线路查不到余额时 balance 是 None，不能直接比大小（曾经在这里崩过）
    low = balance is not None and balance < config.DEEPSEEK_ALERT_AT

    # 充值回升到阈值的 1.5 倍以上，就把提醒重新武装
    if balance is not None and not low and balance > config.DEEPSEEK_ALERT_AT * 1.5:
        if state.pop("alerted", None):
            print("  -> 余额已回升，提醒重新武装")

    if low and not state.get("alerted"):
        save(state)
        if config.SPEND_ALERT_QQ:
            try:
                import onebot
                onebot.send_private_text(
                    config.SPEND_ALERT_QQ, build_alert(balance, burn_per_day)
                )
                state["alerted"] = True
                state["alerted_at"] = int(now)
                print(f"  -> 已私聊提醒 {config.SPEND_ALERT_QQ}")
            except Exception as exc:
                print(f"  -> 提醒发送失败：{exc}", file=sys.stderr)
            save(state)
        else:
            print("  -> 没配 SPEND_ALERT_QQ，跳过提醒", file=sys.stderr)

    save(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
