#!/usr/bin/env bash
# 备份机器人全套状态。建议 crontab 每 6 小时跑一次：
#   0 */6 * * * /home/qqbot/qq-bot/backup.sh >> /home/qqbot/qq-bot/backup.log 2>&1
#
# 为什么必须自动备份：NapCat/QQ 的登录态和机器人代码都在磁盘上，
# 一旦环境被重置（重装系统、实例释放），没有备份就得从零重来。

set -euo pipefail

BACKUP_ROOT=${BACKUP_ROOT:-/root/qqbot-backups}
KEEP=${KEEP:-8}
STAMP=$(date '+%Y%m%d-%H%M%S')
DEST="$BACKUP_ROOT/$STAMP"

mkdir -p "$DEST"

echo "[$(date '+%F %T')] 开始备份到 $DEST"

# 1. 机器人代码 + 配置 + 状态（含 .env，权限 600）
if [ -d /home/qqbot/qq-bot ]; then
    tar -czf "$DEST/qq-bot-code.tar.gz" \
        -C /home/qqbot/qq-bot \
        --exclude=venv --exclude=out --exclude='*.log' \
        . 2>/dev/null || true
fi

# 2. NapCat 的 OneBot 配置
if [ -d /home/qqbot/Napcat/napcat/config ]; then
    tar -czf "$DEST/napcat-config.tar.gz" -C /home/qqbot/Napcat/napcat config 2>/dev/null || true
fi

# 3. QQ 登录态（体积大，但是 quick login 的关键）
if [ -d /root/.config/QQ ]; then
    tar -czf "$DEST/QQ-login-state.tar.gz" -C /root/.config QQ 2>/dev/null || true
fi

# 4. 系统级配置（systemd 单元、安全加固）
tar -czf "$DEST/system-config.tar.gz" \
    /etc/systemd/system/qqbot.service \
    /etc/systemd/system/napcat.service \
    /etc/systemd/system/napcat.service.d \
    /etc/ssh/sshd_config.d/00-hardening.conf \
    /etc/fail2ban/jail.local \
    /home/qqbot/napcat_token 2>/dev/null || true

chmod 600 "$DEST"/*.tar.gz 2>/dev/null || true

echo "[$(date '+%F %T')] 备份完成："
du -sh "$DEST"/*.tar.gz 2>/dev/null | sed 's/^/    /'

# 只保留最近 N 份
COUNT=$(ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | wc -l)
if [ "$COUNT" -gt "$KEEP" ]; then
    ls -1dt "$BACKUP_ROOT"/*/ | tail -n +$((KEEP + 1)) | while read -r old; do
        echo "    清理旧备份 $old"
        rm -rf -- "$old"
    done
fi
