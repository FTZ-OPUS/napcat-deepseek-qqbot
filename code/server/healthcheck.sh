#!/usr/bin/env bash
# 健康检查 + 流量护栏。建议 crontab 每 5 分钟跑一次：
#   */5 * * * * /home/qqbot/qq-bot/healthcheck.sh >> /home/qqbot/qq-bot/health.log 2>&1
#
# 流量护栏的意义：这台机器是「按使用流量」计费，约 ¥0.8/GB，且没有上限。
# 万一程序失控或被刷，一天能烧掉几百块。这里做个本地刹车。

set -uo pipefail

BOT_DIR=/home/qqbot/qq-bot
STATE="$BOT_DIR/state/health.json"
IFACE=$(ip route | awk '/^default/{print $5; exit}')
IFACE=${IFACE:-eth0}

WARN_GB=${WARN_GB:-5}      # 单日出站超过这个数就告警
STOP_GB=${STOP_GB:-20}     # 超过这个数就停机器人（想只告警不刹车，把 STOP_GB 调很大）
TRAFFIC_ACTION=${TRAFFIC_ACTION:-warn}   # warn | stop

ts() { date '+%Y-%m-%d %H:%M:%S'; }
say() { echo "$(ts) $*"; }

TX_BYTES=$(cat "/sys/class/net/$IFACE/statistics/tx_bytes" 2>/dev/null || echo 0)
TODAY=$(date '+%Y-%m-%d')

# 读上一次的累计值（用 python 解析，避免依赖 jq）
read -r PREV_DAY PREV_GB PREV_TX PREV_LAST < <(
  python3 - "$STATE" <<'PY' 2>/dev/null || echo "none 0 0 0"
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
if p.exists():
    d = json.loads(p.read_text())
    print(d.get("day","none"), d.get("total_gb",0), d.get("last_tx",0), d.get("last_ts",0))
else:
    print("none 0 0 0")
PY
)

if [ "$PREV_DAY" != "$TODAY" ]; then
    PREV_GB=0
    PREV_TX=$TX_BYTES      # 跨天就重新起算，避免把昨天的算进来
fi

DELTA=$(( TX_BYTES - PREV_TX ))
[ "$DELTA" -lt 0 ] && DELTA=0
TOTAL_BYTES=$(( $(python3 -c "print(int(float('$PREV_GB')*1073741824))") + DELTA ))
TOTAL_GB=$(python3 -c "print(round($TOTAL_BYTES/1073741824, 3))")

problems=()

# ---- 1. NapCat 进程 ----
if pgrep -f '/opt/QQ/qq' >/dev/null 2>&1; then
    :
else
    problems+=("NapCat 进程不存在")
fi

# ---- 2. OneBot 接口 + 登录态 ----
TOKEN=$(cat /home/qqbot/napcat_token 2>/dev/null || echo "")
LOGIN=$(curl -s -X POST http://127.0.0.1:3000/get_login_info \
        -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        -d '{}' --max-time 8 2>/dev/null || echo "")
case "$LOGIN" in
    *'"user_id"'*) : ;;
    *) problems+=("OneBot 接口不通或未登录") ;;
esac

# ---- 3. 机器人主循环 ----
if systemctl is-active --quiet qqbot.service; then
    :
else
    problems+=("qqbot.service 未运行")
    say "尝试拉起 qqbot.service"
    systemctl start qqbot.service 2>/dev/null
fi

# ---- 4. 磁盘 ----
DISK_PCT=$(df / | awk 'NR==2{gsub("%","",$5); print $5}')
[ "${DISK_PCT:-0}" -ge 85 ] && problems+=("磁盘已用 ${DISK_PCT}%")

# ---- 5. 流量护栏 ----
if python3 -c "import sys; sys.exit(0 if $TOTAL_GB >= $STOP_GB else 1)"; then
    problems+=("今日出站流量 ${TOTAL_GB}GB 已超刹车线 ${STOP_GB}GB")
    say "!! 流量超限，按 TRAFFIC_ACTION=$TRAFFIC_ACTION 处理"
    if [ "$TRAFFIC_ACTION" = "stop" ]; then
        systemctl stop qqbot.service 2>/dev/null
        say "!! 已停止 qqbot.service。确认无异常后执行： systemctl start qqbot"
    fi
elif python3 -c "import sys; sys.exit(0 if $TOTAL_GB >= $WARN_GB else 1)"; then
    problems+=("今日出站流量 ${TOTAL_GB}GB 已超告警线 ${WARN_GB}GB")
fi

# ---- 落盘状态 ----
python3 - "$STATE" "$TODAY" "$TOTAL_GB" "$TX_BYTES" <<'PY'
import json, sys, time, pathlib
p = pathlib.Path(sys.argv[1])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({
    "day": sys.argv[2],
    "total_gb": float(sys.argv[3]),
    "last_tx": int(sys.argv[4]),
    "last_ts": int(time.time()),
}, ensure_ascii=False))
PY

# ---- 6. 额度估算（超线会 QQ 私聊提醒你）----
# 注意：DeepSeek / 中转站的余额**故意不在这里监控**——
# 车主自己手机上每天会看，不需要机器人重复提醒。
# apiwatch.py 仍然保留在磁盘上，需要排查时手动跑一次即可：
#   sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python /home/qqbot/qq-bot/apiwatch.py
if [ -x /home/qqbot/qq-bot/venv/bin/python ]; then
    sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python /home/qqbot/qq-bot/spend.py 2>&1 | sed 's/^/  /'
fi

if [ ${#problems[@]} -eq 0 ]; then
    say "OK | 流量 ${TOTAL_GB}GB | 磁盘 ${DISK_PCT}%"
else
    for p in "${problems[@]}"; do say "异常: $p"; done
fi
