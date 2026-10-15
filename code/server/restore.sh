#!/bin/bash
# 一键恢复 e阶导 机器人（车主 2026-10-14 定）
# 用法：bash /home/qqbot/qq-bot/restore.sh
# 前提：手机端已经重新登录（账号是"活"的）
set -e
echo "① 启用并启动 NapCat（会生成登录二维码）"
systemctl enable napcat.service >/dev/null 2>&1
systemctl start napcat.service
sleep 12
echo "② 取二维码"
bash /home/qqbot/qq-bot/get_qr.sh || true
echo
echo "③ 扫完码后，确认 OneBot 通了，再执行："
echo "     systemctl start qqbot.service"
echo
echo "⚠️ 第一次恢复务必先确认 REST_MODE 的值："
grep '^REST_MODE=' /home/qqbot/qq-bot/.env || echo "     （REST_MODE 未设置，默认 0=正常）"
