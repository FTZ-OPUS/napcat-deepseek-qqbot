#!/bin/bash
# 运行模式切换（车主 2026-10-14 定）
#   bash mode.sh 1   → 模式一：完整模式
#   bash mode.sh 2   → 模式二：受限模式
#   bash mode.sh     → 查看当前模式
ENVF=/home/qqbot/qq-bot/.env

show() {
  M=$(grep '^BOT_MODE=' "$ENVF" 2>/dev/null | cut -d= -f2)
  case "$M" in
    1) echo "当前模式: 1 —— 完整模式（@ 必回、/pdf 必接、主动闲聊、全部触发词）" ;;
    2|"") echo "当前模式: 2 —— 受限模式（只有 @+gpt 和 政治题回）" ;;
    *) echo "当前模式: $M（未知值）" ;;
  esac
}

case "$1" in
  1|2)
    if grep -q '^BOT_MODE=' "$ENVF"; then
      sed -i "s/^BOT_MODE=.*/BOT_MODE=$1/" "$ENVF"
    else
      echo "BOT_MODE=$1" >> "$ENVF"
    fi
    if [ "$1" = "1" ]; then
      echo "已切到【模式一 · 完整】—— 重启机器人中…"
    else
      echo "已切到【模式二 · 受限】—— 重启机器人中…"
    fi
    systemctl restart qqbot.service
    sleep 7
    echo "qqbot: $(systemctl is-active qqbot.service)"
    show
    ;;
  *)
    show
    echo
    echo "切换用法:"
    echo "  bash /home/qqbot/qq-bot/mode.sh 1    # 完整模式"
    echo "  bash /home/qqbot/qq-bot/mode.sh 2    # 受限模式"
    ;;
esac
