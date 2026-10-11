#!/usr/bin/env python3
"""生成「API 线路清单」，格式为方便截图的等宽文本。

用法：
    sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python /home/qqbot/qq-bot/show_api_lines.py

⚠️ 额度/备注是**按接口地址判定**的，不是按「备用N」的编号 ——
   编号会随增删线路变动，按编号硬编码过一次就错位了（真实踩过）。
"""
import sys
sys.path.insert(0, '/home/qqbot/qq-bot')
import config

try:
    import requests
except ImportError:
    requests = None

# 按接口地址特征判定；官方 DeepSeek 的余额实时读
BY_HOST = {
    "xiaomimimo.com":  ("¥10",          "40 天后到期，多模态"),
    "ark.cn-beijing":  ("50 万 token",  "推理模型，已关思考"),
    "bingheai.org":    ("会员免费",      "中转站，慢，最后兜底"),
}
NOTE_OFFICIAL = {"主线路": "10-13 到期", "备用1": "10-17 到期", "备用2": ""}

quota, note, official_total, n_official = {}, {}, 0.0, 0
for p in config.PROVIDERS:
    name, host = p["name"], p["base_url"]
    hit = next((v for k, v in BY_HOST.items() if k in host), None)
    if hit:
        quota[name], note[name] = hit
        continue
    # 官方 DeepSeek
    n_official += 1
    note[name] = NOTE_OFFICIAL.get(name, "")
    got = False
    if requests:
        try:
            r = requests.get("https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {p['key']}"}, timeout=15).json()
            b = float(r["balance_infos"][0]["total_balance"])
            quota[name] = f"¥{b:.2f}"; official_total += b; got = True
        except Exception:
            pass
    if not got:
        quota[name] = "¥?"

def w(s):                       # 显示宽度：中文算 2 格
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)

def pad(s, n):
    return s + " " * max(0, n - w(s))

COL = [9, 29, 13, 16]
rows = [pad("线路", COL[0]) + pad("模型", COL[1]) + pad("额度", COL[2]) + "备注"]
for p in config.PROVIDERS:
    rows.append(pad(p["name"], COL[0]) + pad(p["model"], COL[1])
                + pad(quota.get(p["name"], "?"), COL[2]) + note.get(p["name"], ""))

W = max(w(r) for r in rows) + 4
bar = "═" * W
n = len(config.PROVIDERS)
out = [bar, f"  QQ 数学答疑机器人 · API 线路（{n} 级自动降级）", bar, ""]
out += ["  " + r for r in rows]
out += ["  " + "─" * (W - 4), "",
        f"  DeepSeek 官方 {n_official} 把合计   ¥{official_total:.2f}",
        "  第三方兜底           小米 ¥10 ／ 方舟 50 万 token ／ 中转站 免费", "",
        "  前一条失效 → 自动降级到下一条，群友完全无感",
        "  能力：被 @ 必回 · 识图批改 · 画表 · 画函数图 · 出 PDF · 闲聊 · 风格2排版",
        bar]
print("\n".join(out))
