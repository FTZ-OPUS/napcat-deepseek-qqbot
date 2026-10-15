#!/usr/bin/env bash
# 在服务器上部署/更新机器人代码。用法：sudo bash deploy.sh
#
# 注意：这里用通配符扫描而不是写死文件名列表 ——
# 之前写死过一次，结果新增 rampup.py 忘了加，服务起来就报 ModuleNotFoundError。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST=/home/qqbot/qq-bot

echo "==> 同步代码到 $DST"
mkdir -p "$DST"

copied=0
for f in "$SRC"/*.py "$SRC"/*.sh "$SRC"/.env.example "$SRC"/requirements.txt; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    # deploy.sh 自己不需要装过去
    [ "$base" = "deploy.sh" ] && continue
    install -o qqbot -g qqbot -m 644 "$f" "$DST/$base"
    copied=$((copied + 1))
done
echo "    同步了 $copied 个文件"

mkdir -p "$DST/latex" "$DST/out" "$DST/state" "$DST/docs"
# docs/（方法论文档、陷阱库、考研政治考点库等）也要同步过去 —— 运行时要读
# ⚠️ 必须**递归**：考研政治工具箱是 docs/考研政治答题工具箱/ 子目录，里面有 .db
if [ -d "$SRC/docs" ]; then
    ( cd "$SRC/docs" && find . -type f \( -name '*.md' -o -name '*.db' -o -name '*.json' \) -print0 ) \
    | while IFS= read -r -d '' rel; do
        sub="$(dirname "$rel")"
        [ "$sub" = "." ] || mkdir -p "$DST/docs/$sub"
        install -o qqbot -g qqbot -m 644 "$SRC/docs/$rel" "$DST/docs/$rel"
    done
    chown -R qqbot:qqbot "$DST/docs"
fi
for f in "$SRC"/latex/*.py "$SRC"/latex/*.tex; do
    [ -f "$f" ] || continue
    install -o qqbot -g qqbot -m 644 "$f" "$DST/latex/$(basename "$f")"
done
chmod 755 "$DST"/*.sh 2>/dev/null || true
chmod 755 "$DST"/latex/*.py 2>/dev/null || true

echo "==> 准备 Python 虚拟环境（用阿里云 PyPI 镜像，国内快）"
if [ ! -x "$DST/venv/bin/python" ]; then
    python3 -m venv "$DST/venv"
fi
"$DST/venv/bin/pip" install -q -i https://mirrors.aliyun.com/pypi/simple/ --upgrade pip
"$DST/venv/bin/pip" install -q -i https://mirrors.aliyun.com/pypi/simple/ -r "$SRC/requirements.txt"

# .env 只在不存在时创建，避免覆盖已有的 key
if [ ! -f "$DST/.env" ]; then
    install -o qqbot -g qqbot -m 600 "$SRC/.env.example" "$DST/.env"
    echo "!! 已从 .env.example 生成 $DST/.env，请填入真实 key"
else
    echo "==> .env 已存在，保留不动"
fi

chown -R qqbot:qqbot "$DST"
chmod 600 "$DST/.env"

echo "==> 安装 systemd 服务"
for unit in qqbot.service napcat.service; do
    [ -f "$SRC/$unit" ] && install -m 644 "$SRC/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload

echo "==> 完成。常用命令："
echo "    systemctl start|stop|restart|status qqbot"
echo "    tail -f $DST/bot.log"
