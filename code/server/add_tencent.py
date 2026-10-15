#!/usr/bin/env python3
"""把腾讯云（大模型知识引擎 LKEAP）的 DeepSeek V4.1 Flash 接成一条新线路，
插入到「火山方舟（备用8）」**前面**（车主 2026-10-15 定）。

用法：
    python3 add_tencent.py <API_KEY> [模型名]
失败（key 没授权）时**不会改任何配置**，只打印原因。
"""
import sys, json, subprocess, pathlib, re
import requests

KEY = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
MODEL = (sys.argv[2] if len(sys.argv) > 2 else "deepseek-v4.1-flash").strip()
# ⚠️ 2026-10-15 查官方文档更正：LKEAP 已迁移到 TokenHub，
#    Token Plan 的正确端点是 /plan/v3（不是 /v1）
BASE = "https://api.lkeap.cloud.tencent.com/plan/v3"
ENVF = pathlib.Path("/home/qqbot/qq-bot/.env")

if not KEY.startswith("sk-"):
    print("❌ key 不像腾讯云的（应该 sk- 开头）"); sys.exit(1)

# ── 1. 先验证：不通过就一个字都不改 ──
print(f"① 验证 {BASE}  model={MODEL}")
for m in (MODEL, "deepseek-v3.2", "deepseek-v3.1-terminus",
          "deepseek-v3-0324", "deepseek-r1-0528", "deepseek-v3"):
    try:
        r = requests.post(BASE + "/chat/completions",
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json={"model": m, "max_tokens": 16,
                  "messages": [{"role": "user", "content": "只回两字：可用"}]}, timeout=45)
    except Exception as e:
        print(f"   🔴 {m:24} {str(e)[:50]}"); continue
    if r.status_code == 200:
        txt = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        print(f"   ✅ {m:24} 通了！返回 {txt[:16]!r}")
        MODEL = m
        break
    else:
        try:
            msg = (r.json().get("error") or {}).get("message", "")[:70]
        except Exception:
            msg = r.text[:70]
        print(f"   ❌ {m:24} HTTP {r.status_code}  {msg}")
else:
    print()
    print("❌ 所有模型名都不通 —— 配置**未改动**。")
    print("   请到腾讯云控制台检查：① 大模型知识引擎是否已开通 ② 这个 API KEY 是否已启用 ③ 资源包是否已绑定")
    sys.exit(1)

# ── 2. 备份 .env ──
bak = ENVF.with_suffix(f".env.bak.{__import__('time').strftime('%Y%m%d_%H%M%S')}")
bak.write_text(ENVF.read_text(encoding="utf-8"), encoding="utf-8")
print(f"② .env 已备份 → {bak.name}")

# ── 3. 追加腾讯云变量 ──
t = ENVF.read_text(encoding="utf-8")
def setkv(s, k, v):
    if re.search(rf"^{k}=", s, re.M):
        return re.sub(rf"^{k}=.*$", f"{k}={v}", s, flags=re.M)
    return s.rstrip("\n") + f"\n{k}={v}\n"
t = setkv(t, "TENCENT_API_KEY", KEY)
t = setkv(t, "TENCENT_BASE_URL", BASE)
t = setkv(t, "TENCENT_MODEL", MODEL)
t = setkv(t, "TENCENT_ENABLED", "1")
t = setkv(t, "TENCENT_NOTE", "腾讯云大模型知识引擎 DeepSeek V4.1 Flash（1 年有效期）")
ENVF.write_text(t, encoding="utf-8")
print(f"③ 已写入 TENCENT_* 变量（model={MODEL}）")
print("④ 下一步：在 config.py 的 _build_providers 里，把这条 add() 插到「备用8 火山方舟」之前")
print("   （告诉我一声我来改，或者直接说「接上」即可）")
