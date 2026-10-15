#!/bin/bash
# 查看所有线路的健康状态（车主 2026-10-15）
cd /home/qqbot/qq-bot || exit 1
exec sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python - <<'PY'
import sys, time; sys.path.insert(0,'/home/qqbot/qq-bot')
import config, linehealth as lh
now = time.time()
print("  ── 线路健康（按答题时的实际优先级）──")
for i, p in enumerate(lh.order(list(config.PROVIDERS)), 1):
    fam = lh.family(p)
    down = lh.is_down(p, now)
    tag = "⛔ 熔断中" if down else "✅ 可用"
    print(f"    {i}. {p['name']:8} {p['model']:28} {fam:10} {tag}")
print()
print("  ── 点名线 ──")
for p in config.NAMED_PROVIDERS:
    print(f"    {p['name']:12} {p['model']:24} {lh.family(p)}"
          + ("  ⛔ 熔断中" if lh.is_down(p, now) else "  ✅ 可用"))
print()
print(f"  ── 熔断状态: {lh.status()} ──")
_kp = lh.key_problems()
if _kp:
    print("  ── ⚠️ 密钥级问题（额度/限流/权限，不影响其它线路）──")
    for x in _kp:
        print(f"     {x}")
else:
    print("  ── ✅ 无密钥级问题（没有欠费/限流/权限异常）──")
print(f"  ── 识图 API: {config.VISION_API_MODEL}（{lh.family({'model':config.VISION_API_MODEL,'base_url':config.VISION_API_BASE})}）──")
PY
