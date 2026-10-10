"""配置加载：从 .env 读取，不依赖第三方库。

.env 权限应为 600，属主 qqbot —— 里面有 DeepSeek key 和 OneBot token。
"""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        # 已存在的环境变量优先，方便临时覆盖调试
        os.environ.setdefault(key.strip(), val.strip())


_load_env(BASE / ".env")


def _ints(name: str, default: str = "") -> list[int]:
    raw = os.environ.get(name, default).replace("，", ",")
    out = []
    for piece in raw.split(","):
        piece = piece.strip()
        if piece.isdigit():
            out.append(int(piece))
    return out


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# ---- OneBot / NapCat ----
ONEBOT_API = os.environ.get("ONEBOT_API", "http://127.0.0.1:3000").rstrip("/")
ONEBOT_TOKEN = os.environ.get("ONEBOT_TOKEN", "")

# ---- DeepSeek ----
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# ---- 机器人身份 ----
BOT_QQ = int(os.environ.get("BOT_QQ", "0") or 0)
ALLOWED_GROUPS = _ints("ALLOWED_GROUPS")

# ---- 行为节奏 ----
POLL_INTERVAL = _float("POLL_INTERVAL", 2.0)      # 轮询间隔（秒）
CONTEXT_SIZE = int(os.environ.get("CONTEXT_SIZE", "10") or 10)   # 送进模型的最近消息条数
MIN_REPLY_INTERVAL = _float("MIN_REPLY_INTERVAL", 40.0)          # 同群两次回复的最小间隔
MAX_PARTS = int(os.environ.get("MAX_PARTS", "3") or 3)           # 一条回复最多拆成几段

# 触发概率
PROB_AT = 1.0            # 被 @ 必回
PROB_QUESTION = 0.85     # 问号结尾
PROB_MATH_WORD = 0.45    # 出现数学关键词
PROB_IDLE = 0.03         # 纯闲聊插嘴（调低，121 人群别刷屏）

# 被 @ 时不受同群冷却限制（否则点名会被冷却吞掉）。
# 默认 0 = 永不因冷却丢掉一次点名 —— 「@ 必回」是无条件承诺。
# 万一将来有人刷屏式 @，可以把这个数字调大（单位秒）来限流。
MENTION_MIN_INTERVAL = _float("MENTION_MIN_INTERVAL", 0.0)

# 拟人化
TYPING_BASE = _float("TYPING_BASE", 1.2)      # 基础"打字"时间
TYPING_PER_CHAR = _float("TYPING_PER_CHAR", 0.055)
TYPING_MAX = _float("TYPING_MAX", 12.0)       # 单条最多等这么久
GAP_BETWEEN_PARTS = (0.8, 2.6)                # 多段之间的停顿区间
DROP_PROB = _float("DROP_PROB", 0.08)         # 非 @ 消息的"看了不回"概率

# ---- 冷场救场（群里太久没人说话时主动发个表情包）----
COLD_SILENCE_MIN = _float("COLD_SILENCE_MIN", 60.0)        # 冷场多少分钟后才考虑
COLD_STICKER_PROB = _float("COLD_STICKER_PROB", 0.25)      # 满足条件后每次检查的触发概率
COLD_STICKER_COOLDOWN = _float("COLD_STICKER_COOLDOWN", 3 * 3600)  # 两次之间至少隔多久
# 想彻底关掉自动发表情包：把 COLD_STICKER_PROB 设成 0

# ---- 额度监控（估算，非账单）----
# 实测校准（2026-10-10 阿里云试用额度页读到的真实数字）：
#   ECS 实例 + 系统盘 实际扣费 = 0.254 元/小时
#   公网流量（中国内地）另有 20 GiB 免费额度，超出才按量计费
SPEND_HOUR_RATE = _float("SPEND_HOUR_RATE", 0.254)            # 元/小时（实例 + 系统盘）
SPEND_TRAFFIC_RATE = _float("SPEND_TRAFFIC_RATE", 0.8)        # 元/GB（超出免费额度后）
SPEND_FREE_TRAFFIC_GB = _float("SPEND_FREE_TRAFFIC_GB", 20.0) # 公网流量免费额度（GB）
SPEND_TOTAL_QUOTA = _float("SPEND_TOTAL_QUOTA", 300.0)        # 免费额度总量（元）
SPEND_QUOTA_EXPIRE = os.environ.get("SPEND_QUOTA_EXPIRE", "2027-01-10")  # 额度有效期
SPEND_ALERT_AT = _float("SPEND_ALERT_AT", 200.0)              # 累计到多少元就提醒
SPEND_ALERT_QQ = int(os.environ.get("SPEND_ALERT_QQ", "0") or 0)   # 额度提醒发到哪个 QQ（在 .env 里设）

# ---- DeepSeek 余额监控（另一个钱包，别搞混）----
# 阿里云那笔钱只保证服务器开机；DeepSeek 余额耗尽会让机器人「默默哑掉」
DEEPSEEK_ALERT_AT = _float("DEEPSEEK_ALERT_AT", 10.0)   # 余额低于多少元就提醒

# ---- 什么时候允许机器人开口（车主 2026-10-10 明确要求）----
# 规则：**只有被 @ 了才回**；@ 别人（muse、凛音同学）的一律不参与。
# 而且这些名字出现在纯文本里（没带 @ 段）时，也一律不插嘴 ——
#   出过事故：muse 说了句「凛音别上来就甩PDF啊…」，
#   机器人把"凛音"当空气，又看到"PDF"就冒出来发了一份无关文档。
IGNORE_NAMES = [
    n.strip()
    for n in os.environ.get("IGNORE_NAMES", "凛音,凛音同学,muse").replace("，", ",").split(",")
    if n.strip()
]

# 没被点名时是否允许主动搭话（闲聊插嘴）。
# 默认关：车主明确要求"要艾特你才要回"。
# 想恢复"像 muse 那样偶尔冒泡"，把它设成 1，并调 rampup.py 里各阶段的 idle。
IDLE_CHAT_ENABLED = os.environ.get("IDLE_CHAT_ENABLED", "0").strip() == "1"

# **只有被 @ 才回复**（车主 2026-10-10 定的规矩，默认开）。
# 开着时：没被点名的消息一律不回 —— 问句、数学关键词、闲聊规则全部不生效。
# 也就是说它现在是个纯粹的"点名答疑"机器人。
# 想让它重新变成会主动参与群聊的，把这个设成 0。
MENTION_ONLY = os.environ.get("MENTION_ONLY", "1").strip() != "0"

# 机器人的名字别名（兜底用）。正常情况不用管这里 ——
# 程序会自动从 NapCat 抓当前昵称和群名片，自动派生出"别人可能怎么叫它"。
# 这个列表只在抓取失败时兜底。逗号分隔。
BOT_ALIASES = [
    n.strip()
    for n in os.environ.get("BOT_ALIASES", "阶导").replace("，", ",").split(",")
    if n.strip()
]
# 两条 API 线路同时失效时，多久重复告警一次（这种状态要反复提醒，不能只说一次）
API_DOWN_ALERT_INTERVAL = _float("API_DOWN_ALERT_INTERVAL", 6 * 3600)


# ---- API 线路（主 + 备用，自动切换）----
# 为什么要有备用：主线路余额耗尽 / 被限流 / 抽风时，机器人不会哑掉，
# 会自己换到备用线路上继续答。两条线路的钱是分开的，互相兜底。
def _build_providers() -> list[dict]:
    """按优先级组装 API 线路，前面的挂了自动往后换。

    2026-10 的实际情况（写在注释里，方便以后维护）：
        1. 主线路   官方 key A  10-13 到期
        2. 备用1    官方 key B  10-17 到期
        3. 备用2    官方 key C
        4. 备用3    官方 key D  10-17 到期
        5. 备用4    中转站      会员免费用，但慢（永远放最后）
    这个顺序让「额度过期」变成逐级降级，而不是突然哑掉。
    加新线路就在下面接着 add()，编号顺延，中转站永远留在最后一条。
    """
    out = []

    def add(name: str, key, base, model, note: str = "") -> None:
        key = (key or "").strip()
        if not key:
            return
        out.append({
            "name": name,
            "base_url": (base or "").strip().rstrip("/"),
            "key": key,
            "model": (model or "").strip(),
            "note": (note or "").strip(),
        })

    add("主线路",
        DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
        os.environ.get("DEEPSEEK_NOTE", ""))
    add("备用1",
        os.environ.get("FALLBACK_API_KEY"),
        os.environ.get("FALLBACK_BASE_URL", "https://api.deepseek.com"),
        os.environ.get("FALLBACK_MODEL", "deepseek-chat"),
        os.environ.get("FALLBACK_NOTE", ""))
    add("备用2",
        os.environ.get("FALLBACK2_API_KEY"),
        os.environ.get("FALLBACK2_BASE_URL", "https://api.deepseek.com"),
        os.environ.get("FALLBACK2_MODEL", "deepseek-chat"),
        os.environ.get("FALLBACK2_NOTE", ""))
    add("备用3",
        os.environ.get("FALLBACK3_API_KEY"),
        os.environ.get("FALLBACK3_BASE_URL", "https://api.deepseek.com"),
        os.environ.get("FALLBACK3_MODEL", "deepseek-chat"),
        os.environ.get("FALLBACK3_NOTE", ""))
    add("备用4",
        os.environ.get("FALLBACK4_API_KEY"),
        os.environ.get("FALLBACK4_BASE_URL", "https://api.bingheai.org/v1"),
        os.environ.get("FALLBACK4_MODEL", "deepseek-4.1-flash"),
        os.environ.get("FALLBACK4_NOTE", ""))
    return out


PROVIDERS = _build_providers()

# ---- 识图参数 ----
# API 额度现在很充足（四条线路），所以识图给足质量：
#   长边 1536 能让密密麻麻的题目截图/手写笔记看得更清楚，代价是 token 翻倍。
#   哪天额度紧了，把 VISION_MAX_LONG_SIDE 调回 1024 就能省三成。
VISION_MAX_IMAGES = int(os.environ.get("VISION_MAX_IMAGES", "5") or 5)
VISION_MAX_LONG_SIDE = int(os.environ.get("VISION_MAX_LONG_SIDE", "1536") or 1536)

# ---- 路径 ----
STATE_DIR = BASE / "state"
OUT_DIR = BASE / "out"
LATEX_DIR = BASE / "latex"
LOG_FILE = BASE / "bot.log"

for _d in (STATE_DIR, OUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
