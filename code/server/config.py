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
# 一条回复最多拆成几段。
# 2026-10-13 从 3 提到 4：政治题闲聊式回答要在**文字中间插图**
#（文字 → 图 → 文字 → 图），3 段放不下两张图。
MAX_PARTS = int(os.environ.get("MAX_PARTS", "4") or 4)

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

# ---- 闲聊强度（2026-10-10 车主要求：像 muse 一样高强度参与闲聊）----
# 每小时最多**主动**（非被点名）发言多少条。
#
# 为什么必须有这个硬刹车：概率参数调高之后，遇到群里刷屏可能连着回十几条 ——
# 概率模型没有"停"的概念，只有"接不接"。所以加一个绝对上限兜住。
#
# 被点名的回复**不计入**这个上限：那是群友明确要的，拒了就是产品故障。
# 参考：群里另一个机器人 muse 实测 9.4 条/小时（最活跃的人类 10.1 条/小时）。
MAX_CHAT_PER_HOUR = int(os.environ.get("MAX_CHAT_PER_HOUR", "10") or 10)

# ---- 排版风格 ----
# 群里有人说「风格2」时，PDF 换成彩色框那套（题目/定理用 tcolorbox 框住，
# **解答保持白底黑字不加框**）。不说就用默认风格（全白底黑字）。
# 车主 2026-10-10 定的。参考文档见服务器 /home/qqbot/qq-bot/docs/latex-format.md
STYLE2_HINTS = ["风格2", "风格 2", "风格二", "style2", "style 2"]

# ---- 表格算术自检（2026-10-10 交接后新增，详见 server/tablecheck.py）----
# 单纯形表「格式对但数字是编的」一直是最大短板。开着时：
#   1. 模型给的表先用精确分数验算（σ/z/θ 一致性 + 跨表换基穷举重算）
#   2. 数据行可信 → 代码直接重算 σ 行回填（不花 API 钱瞬时完成）
#   3. 数据行有错 → 把具体错误清单回传给模型重算一轮（多花一次调用）
#   4. 还错 → 放行原文并附一句「没验算过」的标注（不假装可靠）
# 跳过条件内置在模块里：运输问题/指派问题的表、两阶段切换自动跳过不误伤。
TABLECHECK_ENABLED = os.environ.get("TABLECHECK_ENABLED", "1").strip() == "1"
TABLECHECK_MAX_REVISE = int(os.environ.get("TABLECHECK_MAX_REVISE", "1") or 1)
TABLECHECK_ANNOTATE = os.environ.get("TABLECHECK_ANNOTATE", "1").strip() == "1"

# ---- PDF 解题任务（2026-10-11 车主定的第三条路）----
# 群友发 PDF 到群里 → 引用这个 PDF 并 @ 机器人写要求（如"每页只做第一题"）
# → 机器人把 PDF 转成 Markdown 自己读（pdf2md.py，本地转换零 API 消耗）
# → 按要求解题 → 渲染成 PDF 发回群里。
# 转换不花钱，花钱的是解题那一次 LLM 调用（输入 ≈ 全文 token），
# 所以有单独的防刷冷却和长度上限。
PDF_TASK_ENABLED = os.environ.get("PDF_TASK_ENABLED", "1").strip() == "1"
PDF_TASK_COOLDOWN = _float("PDF_TASK_COOLDOWN", 120.0)     # 同群两次任务的最小间隔
PDF_TASK_MAX_CHARS = int(os.environ.get("PDF_TASK_MAX_CHARS", "30000") or 30000)
PDF_TASK_MAX_BYTES = int(os.environ.get("PDF_TASK_MAX_BYTES", str(25 * 1024 * 1024)) or 26214400)
PDF_TASK_MAX_PAGES = int(os.environ.get("PDF_TASK_MAX_PAGES", "60") or 60)   # 超过只转前 N 页
# 转好的 MD 缓存多久（车主 2026-10-11 定的：半小时 —— 怕群友回头追问同一个 PDF，
# 命中缓存就跳过下载+转换；每次任务顺手清掉过期缓存）
PDF_TASK_CACHE_TTL = _float("PDF_TASK_CACHE_TTL", 1800.0)

# ---- 周末平安值守（车主 2026-10-11 定的个人安全功能，详见 server/safety.py）----
# 车主私信「开启<SENSITIVE_A>」才开启；周六周日每天 21:00 前私信发「平安」；
# 没报 → 群里 @ 紧急联系人发警报；周一自动解除。
# ⚠️ SAFETY_OWNER_QQ 不填 = 整个功能不生效；两个 QQ 都必须是机器人的好友。
SAFETY_CHECK_ENABLED = os.environ.get("SAFETY_CHECK_ENABLED", "1").strip() == "1"
SAFETY_OWNER_QQ = int(os.environ.get("SAFETY_OWNER_QQ", "0") or 0)
SAFETY_CONTACT_QQ = int(os.environ.get("SAFETY_CONTACT_QQ", "0") or 0)
SAFETY_ALERT_HOUR = int(os.environ.get("SAFETY_ALERT_HOUR", "21") or 21)

# ---- 报平安的暗号校验（车主 2026-10-12 定）----
# 为什么需要：光"能发消息"不等于"人是安全的" —— 手机可能被人拿走、被胁迫发消息。
# 所以报平安要走两重校验：① 数字暗号 ② 只有你们俩知道的秘密问答。
# 两重都过 = 真平安；都不对 → 判定为危险，直接给紧急联系人发警报。
#
# ⚠️ 这两个值**只能从 .env 读，绝不能在代码里写死** ——
#    一旦进了公开仓库，这套保护就形同虚设。
#    没配置时功能会拒绝开启（见 safety.py 的 arm 检查），不会假装有保护。
SAFETY_CODE = os.environ.get("SAFETY_CODE", "").strip()
SAFETY_SECRET_Q = os.environ.get("SAFETY_SECRET_Q", "").strip()
SAFETY_SECRET_A = os.environ.get("SAFETY_SECRET_A", "").strip()

# 问了暗号之后等多久没回就报警（秒）。默认 15 分钟。
SAFETY_VERIFY_TIMEOUT = float(os.environ.get("SAFETY_VERIFY_TIMEOUT", "900") or 900)

# 周五几点开始问"本周要不要开启"。默认 20 点。
SAFETY_FRIDAY_ASK_HOUR = int(os.environ.get("SAFETY_FRIDAY_ASK_HOUR", "20") or 20)

# "是"的有效期：周五问了之后，多久之内回"是"还算数（秒）。默认 24 小时。
SAFETY_ASK_WINDOW = float(os.environ.get("SAFETY_ASK_WINDOW", "86400") or 86400)
SAFETY_FRIDAY_DM_HOUR = int(os.environ.get("SAFETY_FRIDAY_DM_HOUR", "20") or 20)

# ---- LaTeX 默认排版风格（车主 2026-10-11 定：默认用风格 2 彩色框版式）----
# "2" = doc 渲染走 template_doc_style2.tex（题目/定理彩色框，解答不框）。
# card 图片没有框的区别，自动回退普通卡片模板。
LATEX_DEFAULT_STYLE = os.environ.get("LATEX_DEFAULT_STYLE", "2")

# ---- 私信问答（白名单制，2026-10-11 配合安全保镖的介绍上线）----
# 白名单里的好友私信机器人 → 正常问答回复。不在名单里的一概不处理。
# 安全保镖通道（SAFETY_OWNER_QQ）不在此列 —— 那条通道归 safety.py 专用。
PM_CHAT_ENABLED = os.environ.get("PM_CHAT_ENABLED", "1").strip() == "1"
PM_CHAT_USERS = [
    int(u) for u in os.environ.get("PM_CHAT_USERS", "").replace("，", ",").split(",")
    if u.strip().isdigit()
]

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

    2026-10-11 的实际情况（按到期日排序：快过期的先用掉）：
        1. 主线路   官方A  10-13 到期
        2. 备用1    官方B  10-17 到期
        3. 备用2    官方E  10-17 到期
        4. 备用3    官方C  无到期日
        5. 备用4    官方D  无到期日（新号池，2026-10-12 加）
        5. 备用4    小米 MiMo V2.6 Pro  11-10 到期（¥10）
        6. 备用5    小米 MiMo Flash     40 天期
        7. 备用6    火山方舟   50 万 token（第三方）
        8. 备用7    中转站     免费但慢（永远放最后）
    这个顺序让「额度过期」变成逐级降级，而不是突然哑掉。
    加新线路就在下面接着 add()，编号顺延，中转站永远留在最后一条。
    """
    out = []

    def add(name: str, key, base, model, note: str = "",
            extra: dict | None = None) -> None:
        key = (key or "").strip()
        if not key:
            return
        # 支持按名字临时停用某条线路（额度耗尽的线路留着只会白试一次，拉长回复时间）
        disabled = [x.strip() for x in os.environ.get("DISABLED_LINES", "").split(",") if x.strip()]
        if name in disabled:
            return
        out.append({
            "name": name,
            "base_url": (base or "").strip().rstrip("/"),
            "key": key,
            "model": (model or "").strip(),
            "note": (note or "").strip(),
            # 这条线路专属的额外请求参数，会并进 payload。
            # 用途：不同厂商有各自的开关（比如火山方舟的 thinking）
            "extra": extra or {},
        })

    add("主线路",
        DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
        os.environ.get("DEEPSEEK_NOTE", ""))
    add("备用1",
        os.environ.get("FALLBACK_API_KEY"),
        os.environ.get("FALLBACK_BASE_URL", "https://api.deepseek.com"),
        os.environ.get("FALLBACK_MODEL", "deepseek-chat"),
        os.environ.get("FALLBACK_NOTE", ""))
    # 备用2：官方E（10-17 到期，余额 ¥7.37）
    #
    # 为什么插在这个位置：**按到期日排序** ——
    #     官方A(10-13) → 官方B(10-17) → 官方E(10-17) → 官方C(无到期)
    # 快过期的额度先用掉，不过期的留到最后，避免"用不完就作废"。
    # ⚠️ 备用2（官方E）已于 2026-10-15 **撤下** —— 余额 -¥0.01（欠费），
    #    实测返回 HTTP 402。车主确认："那个备用2 估计没了，应该我也开发你用掉，
    #    你给它撤掉就行了。"
    #    **代码留着**（不删）—— 哪天充上钱，把下面这段注释去掉就恢复了。
    # add("备用2",
    #     os.environ.get("FALLBACK2_API_KEY"),
    #     os.environ.get("FALLBACK2_BASE_URL", "https://api.deepseek.com"),
    #     os.environ.get("FALLBACK2_MODEL", "deepseek-chat"),
    #     os.environ.get("FALLBACK2_NOTE", "官方E（10-17 到期）"))

    add("备用3",
        os.environ.get("FALLBACK3_API_KEY"),
        os.environ.get("FALLBACK3_BASE_URL", "https://api.deepseek.com"),
        os.environ.get("FALLBACK3_MODEL", "deepseek-chat"),
        os.environ.get("FALLBACK3_NOTE", "官方C（无到期日）"))
    # 备用3（官方D）已于 2026-10-10 撤掉 —— 车主拿它去开发了，
    # 机器人不再占用它的额度。要恢复就把上面那段 add() 加回来。

    # 备用4：官方D —— 新号池（车主 2026-10-12 提供，余额 ¥2.01）
    # 和前面三把一样是**官方线路**（速度同为 ~0.9s），但**无到期日** ——
    # 所以排在所有「会过期」的官方之后，让快过期的额度先用掉。
    add("备用4",
        os.environ.get("OFFICIAL2_API_KEY"),
        os.environ.get("OFFICIAL2_BASE_URL", "https://api.deepseek.com"),
        os.environ.get("OFFICIAL2_MODEL", "deepseek-chat"),
        os.environ.get("OFFICIAL2_NOTE", "官方D（新号池，无到期日）"))
    # 备用6：小米 MiMo（Xiaomi MiMo API 开放平台）
    #
    # 和火山方舟同为推理模型，但**处理方式相反** —— 实测数据：
    #     方舟：不识图也乱想，reasoning 吃掉 269~315，容易把 max_tokens 耗光
    #     小米：会自己判断该想多少，解一道积分只用了 7 个 reasoning token
    # 所以**这条不要关思考**，实测关掉反而更慢（13.5s vs 9.4s）且答案变长。
    # 保持默认（不传 thinking 参数）即可。
    #
    # 多模态支持好：开着思考时它能认出卡通角色的具体出处，
    # 关掉就只能给泛泛描述 —— 说明它的思考对质量有实际贡献。
    # （下面两条小米线共用这套规矩：不关思考、多模态好。）
    # 备用5：百度千帆（Baidu Qianfan）——车主 2026-10-12 加
    #
    # ⚠️ **必须关掉「思考」**，和火山方舟同一个坑（实测数据）：
    #     max_tokens=20 不关思考 → 20 个 token 全被 reasoning 吃掉，正文返回**空字符串**
    #     max_tokens=20 关思考   → reasoning=0，正文 1 个 token 就答完了
    # 试过但**无效**的写法（别再用）：enable_thinking=false、reasoning_effort=none
    # 只有 thinking={"type":"disabled"} 管用。
    #
    # 模型是 deepseek-v4.1-flash（上下文 144 万 token），识图实测可用。
    # 30 天后到期、¥20 代金券（比小米的 40 天早），所以按到期日排序插在两条小米**之前**。
    add("备用5",
        os.environ.get("FALLBACK4_API_KEY"),
        os.environ.get("FALLBACK4_BASE_URL", "https://qianfan.baidubce.com/v2"),
        os.environ.get("FALLBACK4_MODEL", "deepseek-v4.1-flash"),
        os.environ.get("FALLBACK4_NOTE", "百度千帆（30天后到期，¥20 代金券）"),
        extra={"thinking": {"type": "disabled"}})

    # 备用6：小米 MiMo V2.6 Pro（车主 2026-10-11 加：比 Flash 优先级高，
    # 先给大家用好模型；11-10 到期，¥10 额度）
    add("备用6",
        os.environ.get("FALLBACK5_API_KEY"),
        os.environ.get("FALLBACK5_BASE_URL", "https://api.xiaomimimo.com/v1"),
        os.environ.get("FALLBACK5_MODEL", "mimo-v2.6-pro"),
        os.environ.get("FALLBACK5_NOTE", "小米MiMo 2.6 Pro（11-10到期）"))
    # 备用7：小米 MiMo Flash（原来排备用4，Pro 插进来后顺延）
    add("备用7",
        os.environ.get("FALLBACK6_API_KEY"),
        os.environ.get("FALLBACK6_BASE_URL", "https://api.xiaomimimo.com/v1"),
        os.environ.get("FALLBACK6_MODEL", "mimo-v2.6-flash"),
        os.environ.get("FALLBACK6_NOTE", "小米MiMo 2.6 Flash（40天后到期）"))
    # 备用8：火山方舟（Volcengine Ark）
    #
    # ⚠️ **必须关掉「思考」，这不是可选优化，是能不能用的前提。**
    # 方舟的 deepseek-v4-1-flash 是**推理模型**：它先输出 reasoning 再输出正文，
    # 而 **reasoning 也算进 max_tokens**。实测：
    #     max_tokens=50  → reasoning 就吃掉 50，正文返回**空字符串**
    #     max_tokens=100 → reasoning=38，正文 11 字，正常
    # 空正文会让我们的 JSON 解析直接失败。关掉思考后 reasoning=0，
    # 正文稳定返回，而且快 2.4 倍（1.3s vs 3.1s），识图质量没差。
    add("备用8",
        os.environ.get("FALLBACK7_API_KEY"),
        os.environ.get("FALLBACK7_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        os.environ.get("FALLBACK7_MODEL", "deepseek-v4-1-flash-260910"),
        os.environ.get("FALLBACK7_NOTE", "火山方舟（推理模型/已关思考）"),
        extra={"thinking": {"type": "disabled"}})
    # 备用9：中转站（永远放最后一条）
    add("备用9",
        os.environ.get("FALLBACK8_API_KEY"),
        os.environ.get("FALLBACK8_BASE_URL", "https://api.bingheai.org/v1"),
        os.environ.get("FALLBACK8_MODEL", "deepseek-4.1-flash"),
        os.environ.get("FALLBACK8_NOTE", "中转站（慢，最后兜底）"))
    # ══════════ 点名专用线（车主 2026-10-14 定）══════════
    # 群友**点名**要某个模型时，直接路由给它；其余一律走 DeepSeek 保速度。
    #
    # ⚠️ jieyouai 这个站**按 key 分组** —— 实测（2026-10-14）：
    #      sk-19537e…（Claude 组）→ 23 个 claude 模型，调 claude-opus-5-5 OK
    #      sk-f815e7…（GPT 组）   → 10 个 gpt 模型，调 claude 报 404 not available for this group
    #    所以**两条线各用各的 key，不能混**（我第一次就混了，误判 Opus 挂了）。
    NAMED = []
    if False:  # 2026-10-14 车主实测降质，Opus/Claude 线路已停用
        k = (os.environ.get("OPUS_API_KEY") or "").strip()
        if k:
            NAMED.append({
                "name": "Opus5.5",
                "base_url": os.environ.get("OPUS_BASE_URL", "https://api.jieyouai.it.com/v1").rstrip("/"),
                "key": k,
                "model": os.environ.get("OPUS_MODEL", "claude-opus-5-5"),
                "note": "点名线：Opus 5.5（实测 5/5 数学题对，中位 9.55s）",
                # 触发词：只认这两个人名，大小写不敏感
                "hints": ("claude", "opus"),
            })
    if os.environ.get("GPT_ENABLED", "1").strip() == "1":
        k = (os.environ.get("GPT_API_KEY") or "").strip()
        if k:
            NAMED.append({
                "name": "GPT6.1SOL",
                "base_url": os.environ.get("GPT_BASE_URL", "https://api.jieyouai.it.com/v1").rstrip("/"),
                "key": k,
                "model": os.environ.get("GPT_MODEL", "gpt-6.1-sol"),
                "note": "点名线：GPT 6.1 SOL（实测矩阵求逆正确；偏慢 15~90s）",
                "hints": ("gpt", "sol", "6.1"),
            })
    # ⭐ 车主 2026-10-14 追加：ASTRA —— 更强的型号（gpt-6-astra）
    # 路由规则（车主原话）：
    #   「别人要说 GPT，你就默认是 6.1 Sol。
    #     如果说 ASTRA 这个词出现了，大小写都可以……那你就路由到那个。」
    # ⚠️ 命中顺序上 astra 必须**先于** gpt 判断（见 main.reply_policy），
    #    否则「用 astra 而不是 gpt」会被 gpt 抢先命中。
    if os.environ.get("GPT_ENABLED", "1").strip() == "1":
        _ka = (os.environ.get("GPT_API_KEY") or "").strip()
        if _ka:
            NAMED.append({
                "name": "GPT6ASTRA",
                "base_url": os.environ.get("GPT_BASE_URL", "https://api.jieyouai.it.com/v1").rstrip("/"),
                "key": _ka,
                "model": os.environ.get("ASTRA_MODEL", "gpt-6-astra"),
                "note": "点名线：GPT 6 ASTRA（更强型号，触发词 astra 不限大小写）",
                "hints": ("astra",),
            })

    # 备用10：jieyouai 中转站（车主 2026-10-14 给的新站）
    # ⚠️ 接入时**账户余额是 0** —— 实测（做了对照）：
    #      你给的 key   → HTTP 403 INSUFFICIENT_BALANCE（key 有效，只是没钱）
    #      故意写错的 key → HTTP 401 INVALID_API_KEY（无效 key 长这样）
    #    → **key 格式没问题，充值后就能用**，不用改代码，只要确认模型 id。
    #    ⚠️ 模型 id 还没法验证（余额不足时任何模型都返回 403，试不出哪个存在）。
    #       充值后我拿它试一圈，把 FALLBACK9_MODEL 改成该站真实支持的 id。
    #    想临时摘掉这条线：设 FALLBACK9_ENABLED=0
    if os.environ.get("FALLBACK9_ENABLED", "1").strip() == "1":
        add("备用10",
            os.environ.get("FALLBACK9_API_KEY"),
            os.environ.get("FALLBACK9_BASE_URL", "https://api.jieyouai.it.com/v1"),
            os.environ.get("FALLBACK9_MODEL", "deepseek-chat"),
            os.environ.get("FALLBACK9_NOTE", "jieyouai 中转站（待充值）"))
    return out, NAMED


PROVIDERS, NAMED_PROVIDERS = _build_providers()

# ══════════ 识图 API（**和答疑 API 分开**，车主 2026-10-13 定）══════════
# 为什么要分开：
#   群友反映「草稿纸上的手写题」识别难度大，别的机器人（用 Gemini）认得更准，
#   人都跑去那边批改草稿了。所以**手写稿先用 Gemini 把题抄准，再交给 DeepSeek 解答**。
#   正常图片/PDF（印刷题、截图）照旧走 DeepSeek —— 快得多（0.8s vs 8.9s）。
#
# ⚠️ 模型 id 是 `gemini-3.8-flash-high`（实测 `gemini-3.8-flash` 会 503 说不存在）。
# ⚠️ key 默认沿用中转站那把（实测能调 gemini）；车主另给的 key 若生效，填 VISION_API_KEY 覆盖。
VISION_API_BASE = os.environ.get("VISION_API_BASE", "https://api.bingheai.org/v1").rstrip("/")
VISION_API_MODEL = os.environ.get("VISION_API_MODEL", "gemini-3.8-flash-high")
VISION_API_KEY = os.environ.get("VISION_API_KEY", "").strip()
if not VISION_API_KEY:
    # 没单独配就借用中转站的 key（同平台、实测可用）
    for _p in PROVIDERS:
        if "binghe" in _p["base_url"].lower():
            VISION_API_KEY = _p["key"]
            break
# 手写识别总开关；关掉就所有图都走 DeepSeek
VISION_HANDWRITING = os.environ.get("VISION_HANDWRITING", "1").strip() == "1"

# ---- 每日政治知识点推荐（车主 2026-10-13 定）----
# 每天固定时间从考研政治考点库里挑一条没推过的，写成群聊口吻 + 配关系图发群里。
POLITICS_DAILY_ENABLED = os.environ.get("POLITICS_DAILY_ENABLED", "1").strip() == "1"
POLITICS_DAILY_HOUR = int(os.environ.get("POLITICS_DAILY_HOUR", "8") or 8)      # 默认早上 8 点
POLITICS_DAILY_MINUTE = int(os.environ.get("POLITICS_DAILY_MINUTE", "0") or 0)

# ══════════════ 运行模式开关（车主 2026-10-14 定）══════════════
# **模式一 = 完整模式**：原来的全部行为 —— @ 必回、/pdf 必接、主动闲聊、
#                        点名线（GPT）、识别复检、讲义/政治推送…
# **模式二 = 受限模式**：风控恢复期的临时档 —— 只有「@ + gpt」和「政治题」回，
#                        其余一律不发声
#
# ⚠️ 切模式**只改这一个值**，两种模式的代码都在，不会丢。
#    切换：bash /home/qqbot/qq-bot/mode.sh 1    或    mode.sh 2
BOT_MODE = int(os.environ.get("BOT_MODE", "2") or 2)

# ══════════════ 静默休息模式（车主 2026-10-14 定）══════════════
# **背景**：QQ 号 <BOT_QQ> 被腾讯冻结，申诉恢复。号在**观察期**，这时
#           **任何自动发送行为都是风险**，所以先彻底停一天。
#
# ⚠️ **怎么"停"很关键**：
#    ✅ 正确：QQ 挂着、机器人**零发送** → 看着就是个"在线但不说话的普通用户"，完全正常
#    ❌ 错误：登出 QQ → 反复登出/登录**本身就是风控信号**，反而更糟
#    所以这个开关只让机器人**闭嘴**，不动 QQ/NapCat。
#
# 开：REST_MODE=1  → 收到消息也不回，所有每日推送全停（但连接保持）
# 关：REST_MODE=0  → 恢复正常
REST_MODE = os.environ.get("REST_MODE", "0").strip() == "1"

# ══════════════ 风控收敛（车主 2026-10-14 定）══════════════
# **背景**：2026-10-14 凌晨 QQ 号 <BOT_QQ> 被腾讯**冻结**，申诉后恢复。
# 根因分析：10-13 一天发了 **104 份 PDF**，加上 211 次秒回 ——
#           QQ 风控最敏感的两件事（大量文件传输 + 高频自动回复）我们全占了。
# 结论：**根因是行为，不是号**。换新号只会死得更快（新号更敏感）。
#       所以继续用这个号，但把行为改掉。
#
# 这几项就是「收敛」开关，觉得太紧随时调大。
PDF_DAILY_LIMIT = int(os.environ.get("PDF_DAILY_LIMIT", "30") or 30)      # 每天最多发几份 PDF
SEND_HOURLY_LIMIT = int(os.environ.get("SEND_HOURLY_LIMIT", "15") or 15)  # 每小时最多发几条（含图片/文件）
SEND_GAP_MIN = float(os.environ.get("SEND_GAP_MIN", "1.2") or 1.2)        # 两条之间最小间隔（秒）
SEND_GAP_MAX = float(os.environ.get("SEND_GAP_MAX", "4.0") or 4.0)        # 最大间隔（随机，像真人）
THROTTLE_ENABLED = os.environ.get("THROTTLE_ENABLED", "1").strip() == "1"

# ---- 每日群使用分析报告（车主 2026-10-13 定）----
# 每晚定时生成一份：谁@得多、谁最满意、话题分布、token 费用、我的观察。
# 存到 state/reports/<日期>.md，车主随时要看就取。
REPORT_ENABLED = os.environ.get("REPORT_ENABLED", "1").strip() == "1"
REPORT_HOUR = int(os.environ.get("REPORT_HOUR", "23") or 23)
REPORT_MINUTE = int(os.environ.get("REPORT_MINUTE", "30") or 30)


# ---- 识图参数 ----
# API 额度现在很充足（四条线路），所以识图给足质量：
#   长边 1536 能让密密麻麻的题目截图/手写笔记看得更清楚，代价是 token 翻倍。
#   哪天额度紧了，把 VISION_MAX_LONG_SIDE 调回 1024 就能省三成。
VISION_MAX_IMAGES = int(os.environ.get("VISION_MAX_IMAGES", "5") or 5)
VISION_MAX_LONG_SIDE = int(os.environ.get("VISION_MAX_LONG_SIDE", "1536") or 1536)

# ---- 路径 ----
# ⚠️ 可被环境变量覆盖 —— **测试必须覆盖它**，否则会把线上真实状态写坏。
# 真实事故（2026-10-12）：测试用假 QQ 覆盖了线上 state/pmchat.json 的游标，
# 游标一丢，重启后 pmchat 把历史私信全重答了一遍（凌晨 3 点给两个好友发了消息）。
# ---- 每日答疑讲义（车主 2026-10-12 定）----
# 把当天所有答疑 PDF 合成一份长讲义，带封面，发群里；车主可以直接存到电脑。
# 凌晨 00:10 汇总**前一天**（群里 23:00 最活跃，23:50 会切掉尾巴；00:10 不在静默时段）。
DAILY_DIGEST_ENABLED = os.environ.get("DAILY_DIGEST_ENABLED", "1").strip() == "1"
DAILY_DIGEST_HOUR = int(os.environ.get("DAILY_DIGEST_HOUR", "0") or 0)
DAILY_DIGEST_MINUTE = int(os.environ.get("DAILY_DIGEST_MINUTE", "10") or 10)
# 讲义里单份 PDF 的份数上限（防某天异常多时合出一个几百页的怪物）
# 默认 60 —— 实测一天 49 条是常态，40 会截掉当天的一部分（讲义就不完整了）。
# 这个上限只是防"异常多的一天"合出几百页怪兽，正常日子不该碰到。
DAILY_DIGEST_MAX_PARTS = int(os.environ.get("DAILY_DIGEST_MAX_PARTS", "60") or 60)
# 原始 PDF 保留几天（车主定：先别删，3~5 天清一次）
DAILY_KEEP_DAYS = int(os.environ.get("DAILY_KEEP_DAYS", "5") or 5)
# 讲义超过这个大小（MB）就用 Ghostscript 压一道 —— 每天一份大文件，别让群友下载太慢
DAILY_DIGEST_COMPRESS_MB = float(os.environ.get("DAILY_DIGEST_COMPRESS_MB", "2.0") or 2.0)
# 成品讲义（_archive/）保留多少天；0 = 永久保留（车主以后要合成"总讲义"，默认别删）
DAILY_ARCHIVE_KEEP_DAYS = int(os.environ.get("DAILY_ARCHIVE_KEEP_DAYS", "0") or 0)

STATE_DIR = Path(os.environ.get("QQBOT_STATE_DIR") or (BASE / "state"))
OUT_DIR = Path(os.environ.get("QQBOT_OUT_DIR") or (BASE / "out"))
LATEX_DIR = BASE / "latex"
LOG_FILE = BASE / "bot.log"

for _d in (STATE_DIR, OUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ⚠️ 旧消息硬保险（车主 2026-10-15 定：「千万不要补回」）
#   超过这个秒数的消息一律不处理 —— 防止重启/游标丢失后追着回旧消息，
#   那会瞬间回到高强度模式，风控风险极大。
STALE_MSG_SECONDS = int(os.environ.get("STALE_MSG_SECONDS", "300") or 300)
