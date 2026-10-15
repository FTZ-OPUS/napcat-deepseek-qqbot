#!/bin/bash
# 取 NapCat 最新的登录二维码（打印时间 + 指纹 + 拷到 /tmp/qr_now.png）
# 用法：bash get_qr.sh
QR=/home/qqbot/Napcat/napcat/cache/qrcode.png
if [ ! -f "$QR" ]; then echo "🔴 没有二维码文件（账号可能已经登上了）"; exit 1; fi
echo "二维码生成时间: $(stat -c %y "$QR")"
echo "当前时间:       $(date '+%Y-%m-%d %H:%M:%S')"
echo "指纹: $(md5sum "$QR" | cut -d' ' -f1)"
cp "$QR" /tmp/qr_now.png && chmod 644 /tmp/qr_now.png
echo "已拷贝到 /tmp/qr_now.png"
# 顺便看一眼登录状态
curl -s -o /dev/null -w "OneBot(3000): HTTP %{http_code}\n" --max-time 4 http://127.0.0.1:3000/get_status 2>/dev/null || echo "OneBot(3000): 还没起来"
