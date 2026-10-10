import sys, requests
sys.path.insert(0, '/home/qqbot/qq-bot')
import config

print("=" * 74)
print("  API 线路清单（按调用优先级排序，前面的挂了自动往后换）")
print("=" * 74)
print()

total = 0.0
for i, p in enumerate(config.PROVIDERS, 1):
    print(f"  {i}. 【{p['name']}】")
    print(f"       地址   : {p['base_url']}")
    print(f"       模型   : {p['model']}")
    print(f"       备注   : {p['note'] or '（无）'}")
    key = p['key']
    print(f"       令牌   : {key[:10]}...{key[-4:]}  （{len(key)} 字符）")

    if "deepseek.com" in p["base_url"]:
        try:
            r = requests.get("https://api.deepseek.com/user/balance",
                             headers={"Authorization": f"Bearer {key}"}, timeout=20)
            if r.status_code == 200:
                d = r.json()
                b = d["balance_infos"][0]
                bal = float(b["total_balance"])
                total += bal
                print(f"       余额   : ¥{bal:.2f}   （赠送 ¥{b['granted_balance']} / 充值 ¥{b['topped_up_balance']}）")
                print(f"       可用   : {d.get('is_available')}")
            else:
                print(f"       余额   : 查询失败 HTTP {r.status_code}")
        except Exception as e:
            print(f"       余额   : 查询异常 {e}")
    else:
        # 中转站：探活即可，余额查不到
        try:
            r = requests.get(f"{p['base_url']}/models",
                             headers={"Authorization": f"Bearer {key}"}, timeout=20)
            n = len((r.json().get("data") or [])) if r.status_code == 200 else 0
            print(f"       状态   : {'✅ 可用，支持 ' + str(n) + ' 个模型' if r.status_code == 200 else '❌ HTTP ' + str(r.status_code)}")
            print(f"       余额   : 查不到（New API 中转站返回假数据，要登它后台看）")
        except Exception as e:
            print(f"       状态   : ❌ 异常 {e}")
    print()

print("=" * 74)
# 数量写死过一次（写着"三把"，后来加到四把就错位了），改成数出来
n_official = sum(1 for p in config.PROVIDERS if "deepseek.com" in p["base_url"])
n_relay = len(config.PROVIDERS) - n_official
print(f"  官方 key 余额合计：¥{total:.2f}   （{n_official} 把官方 + {n_relay} 条中转站兜底）")
print(f"  中转站的余额查不到，要登它自己的后台看")
print("=" * 74)
