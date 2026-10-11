# 06 · 接手续开发记录（ZCode，2026-10-10 交接后）

> **这份文档是什么**：DSH 把项目交接给 ZCode（接手的 AI）之后，ZCode 继续开发的完整记录——
> 做了什么、为什么做、踩了什么坑、怎么解决的、改了哪些文件、加了哪些开关。
> **本文件夹自包含**：接手时的世界见《07-交接文档-脱敏版》（红线/车主规矩/服务器配置全在里面），
> 本文只记增量；换服务器重建 = 07 §8（基础）+ 本文 §7（增量）。
> ⚠️ 全套文档中的 IP / QQ 号 / 群号 / key 均为占位符，可直接公开。

---

## 0. 接手后做了什么（30 秒速览）

| # | 工作 | 交付物 | 状态 |
|---|---|---|---|
| 1 | **单纯形表算术自检**：模型画的表先用精确分数验算，错了代码修/模型重算/兜底标注 | `server/tablecheck.py` + 29 项测试 | ✅ 已上线 |
| 2 | **PDF→MD 转换器**：本地转换零 API 消耗，带逐页覆盖率自检兜底 | `server/pdf2md.py`（CLI + 可导入） | ✅ 已上线 |
| 3 | **PDF 解题任务**：群友引用群里的 PDF + @机器人 → 转 MD → 阅读总结或解题（输出形式按体量自选）| `main.py` 新分支 + `onebot.py` 两个方法 | ✅ 已上线 |
| 4 | **数学方法论技能文档**：车主讲义 101 页精读提炼成 21 张方法卡 | 服务器 `docs/数学巧解方法论·技能文档.md` | ✅ 已上线 |
| 5 | **NapCat 崩溃事故修复**（2026-10-11）：WebUI API 通道 + 扫码重登 | 本文 §5 | ✅ 已恢复 |

**接手到现在的时间线**（给下一个 AI 一个体感）：
- 10-10 晚：交接、连服务器确认状态 → 当晚完成表格自检并部署（29 项测试）
- 10-11 凌晨：pdf2md 转换器 + PDF 解题任务上线（36 项测试）；车主讲义提炼成方法论文档
- 10-11 上午：按群里真实使用反馈修三轮行为（阅读总结模式 / 输出形式分级 / 明确要 PDF 优先级）
- 10-11 中午：NapCat 内部崩溃事故，扫码重登修复（~28 分钟停机）
- 10-11 下午：渲染烟测三形态全过，全部功能确认正常

---

## 0.5 给下一个 AI：10 分钟上手路径

1. 读本文 §0（做了什么）→ 读《07-交接文档-脱敏版》§4 §5（**红线和车主规矩，一条都不能违反**）
2. 代码全在 `code/server/`（与服务器 `/home/qqbot/qq-bot/` 同步）；测试在 `code/tests/`，**全离线**，改完先跑：
   ```bash
   # 服务器上（或本地 venv）
   sudo -u qqbot PYTHONPATH=/home/qqbot/qq-bot /home/qqbot/qq-bot/venv/bin/python test_tablecheck.py    # 29 项
   sudo -u qqbot PYTHONPATH=/home/qqbot/qq-bot /home/qqbot/qq-bot/venv/bin/python test_integration.py  # 27 项
   sudo -u qqbot PYTHONPATH=/home/qqbot/qq-bot /home/qqbot/qq-bot/venv/bin/python test_pdf_task.py      # 36 项
   ```
3. 部署：scp 改动文件到服务器 `/tmp/qqbot-deploy/`（deploy.sh 常驻该目录）→ `bash deploy.sh` → `systemctl restart qqbot`
4. **重启后必做两个核对**：
   - `systemctl show qqbot -p ActiveEnterTimestamp` 确认真的重启了（ssh 命令链会静默断，真实教训）
   - 重启**前**先 `tail bot.log` 确认没有进行中的回复（重启会打断正在生成的回答）
5. 出问题：本文 §7 故障树 → 03 手册的「排查手法」章

---

## 1. 工作一：单纯形表算术自检（tablecheck.py）

### 1.1 为什么做（DSH 留下的头号待办）

模型画的单纯形表**格式漂亮但数字是编的**——真实案例：σ 行用了冷门的 z−c 约定（数值还自洽），却据此宣布了错误的入基变量；更早还有无可行解硬凑最优解。评卷照着表给分，数字错 = 整个回答报废。DSH 在《02-能力迭代史》里把「解析 → 验算 → 纠正」方案写好了但没来得及做。

### 1.2 设计（比 DSH 原方案多走的几步）

- **精确分数**：所有数字转 `fractions.Fraction`，不float。`\frac{39}{80}`、`0.4875`、`-43/80` 全部精确解析
- **σ 约定投票**：国内教材主流 `σ = c−z`，但模型有时写 `z−c`（数值自洽）。验算器对两种约定投票——**基变量列的 σ 必为 0，这在两种约定下都成立，是铁锚点**。按表内占优约定验数值；纯约定漂移且入基选择自洽时不报错（防噪音）
- **跨表验算不认任何约定**：相邻两表之间**穷举所有合法主元 (行,列)**，只要存在一组主元能从表 k 算出表 k+1 就算通过。编一个数字都会被抓，且完全绕开 max/min、c−z/z−c 的约定地狱
- **防误伤红线**：运输问题/指派问题的表（无 σ 行）跳过；两阶段法阶段切换、跳步（基变量一次变 >1 个）跳过跨表验算
- **三层纠正回路**：①数据行可信 → 代码直接重算 σ 行回填（不花 API 钱）；②数据行错 → 把「第几行第几列应该是多少」的清单回传给模型重算一轮（brain.py 新增 `revise_table()`，temperature 0.2）；③还错 → 放行原文并附「表我校了两遍还是有点心虚，你们也帮着盯一眼」的标注（人设：算错了就认，不假装可靠）

### 1.3 踩过的坑（都是写测试时被自己的断言抓出来的）

| 坑 | 症状 | 修法 |
|---|---|---|
| **booktabs 命令全文删除导致偏移错位** | 修复 σ 行时回填位置错乱 | 行边界在**原始文本**上定位，横线命令只在单元格解析时逐行抹掉（表格里绝不能先全文 replace 再记偏移） |
| **表头变量名行被误判成数据行** | 整表报废 | 数据行硬标准 = 有变量名 + **至少 3 个数字格**；表头变量行零数字格，靠这个区分 |
| **θ 列后面的 ← 箭头把行宽顶爆** | 布局对不上号整表拒验 | 宽度按「最后一个数字格的位置」数，不按总格数 |
| **数据行被当成 c_j 行** | c_j 变成鬼值，σ 全部冤判 | c_j 候选行绝不能含变量名格、不能是 σ 行 |
| **`0.334 ≈ 1/3` 被放行** | 编数检测漏报 | 等值比较做成「截断/舍入感知」：小数必须是真值的直接截断，或与真值差严格小于半个末位；两个小数之间严格比对 |
| **提示词里的反斜杠** | 静默损坏 | 沿用 DSH 的纪律：**提示词里反斜杠必须双写**，TABLE_FIX_PROMPT 已纳入启动自检清单 |

### 1.4 测试与靶子

`tests/test_tablecheck.py`，29 项断言。**回归靶子是真实坏样本**：DSH 交接前留在服务器 `out/table_test.pdf` 里的模型产出（σ=z−c 且入基选错），复刻成 `BROKEN_SAMPLE` 常量。另含内置精确分数单纯形求解器当 ground truth，覆盖退化（θ=0）、多重最优（非基 σ=0）、跳步/阶段切换三类边界。

---

## 2. 工作二：PDF → Markdown 转换器（pdf2md.py）

### 2.1 为什么做

群友有「把 PDF 转成 MD 再交给 API 处理」的需求。选型对比：微软 markitdown 对 PDF 只做纯文本提取结构全丢；marker/docling 要拉 PyTorch（几十上百 MB），2 核 4G 服务器碰不得。**选 pymupdf4llm**：专为喂 LLM 设计（标题/加粗/列表/表格都转正经 Markdown），只带 pymupdf 一个依赖（~50MB wheel），pip 阿里云镜像直装。

### 2.2 最大的坑：公式框整块蒸发（值得单独记住）

用车主的数学讲义（101 页，公式全装在彩色框里）做测试时发现：**新版 pymupdf4llm 默认走新的 Layout 引擎，它把「彩色框 + 里面的公式」识别成 `formula` 类型的盒子，按图片处理**——`write_images=False` 时文字直接蒸发，24 页里 18 页中招。

排查过程（都试过、都不行，记下来防止重复走）：
- `graphics=False` ❌ —— 参数名就是错的，被 `**kwargs` 静默吞掉
- `ignore_graphics=True` ❌ —— 图形聚类关了，公式照样丢
- `use_layout(False)` 切回老 RAG 引擎 ❌ —— 更糟，正文只剩标题
- `table=False` / `margins=0` ❌

**最终方案（覆盖率自检兜底）**：不跟引擎缠斗。每一页先用 `page.get_text()` 拿全部文字当底本，再用 2-gram 步进采样测 MD 覆盖了多少；覆盖率 < 0.85 的页自动回退为纯文本输出，并加 `<!-- 第 N 页版式特殊 -->` 注释。原则：**宁可没有排版，不能没有内容**——下游是 LLM，内容完整比格式漂亮值钱。正常页照样走 MD 路径（目录页 94% 覆盖），数学密集页自动兜底。

### 2.3 其他小坑

- **pymupdf 内置 Helvetica 写不了中文**：`insert_text` 写中文静默变点（不是报错）——造测试 PDF 要用英文，或指定 CJK 字体。真实中文 PDF 的提取用群里真实文件验证过，没问题
- **国内网络 pip 默认源超时**：本机和服务器统一走阿里云镜像
- **CLI 与 API 是两套**：`python -m pymupdf4llm` 是批量模式不支持页码；用 Python API `to_markdown(page_chunks=True)` 才能做逐页覆盖率

---

## 3. 工作三：PDF 解题任务（车主定的「第三条路」）

### 3.1 最终行为定义（车主三轮反馈打磨后的定稿，**以此为准**）

**触发**：群友把 PDF 发进群 → 长按引用该文件消息 → @机器人（要求文字可空）。

**模式判定**（`main.py` 的 `_PDF_READ_ONLY_RE` + 空 request）：
- 要求为空，或只是「阅读一下 / 看看 / 读一下 / 总结一下」这类空泛短语 → **阅读总结模式**：写一份《阅读总结》报告——讲什么、结构主线、核心结论、精彩点，像人写的读书报告，大半页到一页，不照抄原文不复述推导，文件名「阅读总结」
- 带具体任务的（「每页只解决第一题」）→ **解题模式**：要求原样传给模型

**输出形式（按回答体量自选，车主定的「不要啥都发 PDF」）**：
- 几句话能说清 → 纯 text 片段直接打字（**不叠收尾 @**，像正常人说话）
- 要展示公式但一页以内 → `card`（渲染成一张图片发群里）
- 完整解答确实超过一页 → `doc`（渲染成 PDF）
- 阅读总结类 → `doc`，文件名「阅读总结」
- **唯一例外（优先级最高）**：要求里**明确说了要 PDF/要文件** → 必须发 `doc`，短也发——体量规则让位

**主流程同步修的病根**：`brain._coerce_pdf` 曾把模型的纯文字回答硬掰成 PDF（真实事故：有人问「你能不能阅读 pdf」被回了一份文档）。现在纯文字回答放行——模型用了 PDF_HINT 出口的判断要尊重。`decide()` 新增 `max_tokens` 参数：PDF 任务不强制 pdf_requested 但保留 8000 大预算防截断。persona 文件名指引加了「阅读总结」条目；PDF_HINT 的出口收窄为「**只有消息里根本没有具体任务、纯聊 PDF 格式时**才允许不输出 PDF」。

### 3.2 链路关键点（都是实测出来的）

- **群文件消息自带下载直链**：`get_group_msg_history` 里的 file 段含 `file_id` 和 `url`（QQ 网盘中转直链，无需鉴权），连 `get_group_file_url` 都不用调
- **引用回查**：QQ 的 reply 段只带 message_id，用 `get_msg` 回查原消息确认是 `.pdf`（其他文件不触发）；文件消息有 message_id，所以可以被引用
- **复用现有管线**：解题走 `brain.decide` —— 人格、JSON 输出协议、七级 API 降级、LaTeX 渲染、单纯形表验算全部复用
- **失败体面**：扫描件（图片型）转不出文字时 @ 群友说明，不烧 token

### 3.3 防刷与缓存（车主明确要求的）

| 机制 | 配置 | 说明 |
|---|---|---|
| 任务冷却 | `PDF_TASK_COOLDOWN=120` 秒/群 | 解题那次调用输入 ≈ 全文 token，必须有刹车 |
| 字符上限 | `PDF_TASK_MAX_CHARS=30000` | 超长截断并在给模型的上下文里注明 |
| 页数上限 | `PDF_TASK_MAX_PAGES=60` | 超长只转前 N 页（主循环是串行的，防阻塞） |
| 下载护栏 | `PDF_TASK_MAX_BYTES=25MB` | 流式下载边下边数，超了拒收 |
| **MD 缓存** | `PDF_TASK_CACHE_TTL=1800`（半小时） | 车主定的：转好的 MD 存 `out/pdfmd_cache/<sha1(群:file_id)[:16]>.md+.json`，**半小时内群友追问同一个 PDF 直接命中，跳过下载转换**；每次任务开头顺手清过期缓存（healthcheck 的 7 天规则只兜底） |

注意：缓存键含 file_id——群友**重新上传**同一份 PDF 会得到新 file_id，重新下载转换属正常。

### 3.4 测试纪律

`tests/test_pdf_task.py`，36 项断言，全部离线打桩（`onebot.call` 只进捕获列表、`brain.decide` 打桩、下载用本地微型 PDF 模拟）——**全程没在真实群发过测试消息**，链路预演用的是群里已有的真实文件（下载+转换手动跑通）。上线后靠日志留痕：真实触发时日志有「PDF 任务」开头的完整链路。

---

## 4. 工作四：数学方法论技能文档

车主提供了自己的讲义（《无限未来4》系列，101 页 24 专题），要求**灵活学方法论不照搬题**。已完整精读并提炼成《数学巧解方法论·技能文档》：21 张方法卡（复数法证几何/切线法/加权均值凑齐次/排序不等式正反用/秩夹逼/同时对角化/复分析定理链/二次插值估上界……每张 = 内核 + 流程 + 适用信号 + 易错点）+ 7 条元方法论 + 陷阱清单 + 专题对照表。

存放：服务器 `docs/数学巧解方法论·技能文档.md`（属主 qqbot，644）。**注意：这份文档没有注入机器人提示词**——persona 每个字都随每次调用发给 API，整篇注入 = 每次聊天变相烧 token。文档躺在 docs/ 里随时可被代码读取，是否接入提示词由车主决定。

---

## 5. 事故案例：NapCat 内部崩溃（2026-10-11，停机 28 分钟）

**这是接手以来最严重的一次故障，完整记录给后来人。**

### 5.1 时间线

| 时刻 | 事件 |
|---|---|
| 11:24:22 | QQ 内部 zygote 进程崩溃重启（`ps` 可见新 zygote 的启动时间正好是这一秒） |
| 11:24:21 | 机器人日志开始刷 `get_group_msg_history ... Connection refused`——OneBot HTTP（3000 端口）蒸发 |
| 11:24~11:40 | 排查：QQ 主进程还活着（僵尸壳）、`onebot11.json` 完好、3000 无监听、只剩 WebUI 的 6099 在听 |
| 11:40 前后 | 逆向 NapCat WebUI API（见 5.2），发现 `GetQuickLoginList` 还能列出账号 → 触发快速登录 |
| 11:45 左右 | **快速登录被腾讯拒**：「你的用户身份已失效，为保证账号安全，请你重新登录」——崩溃同时把会话判了失效 |
| 11:46~11:51 | 拿新二维码，SSH 隧道把 WebUI 映射到车主本机浏览器，**车主用机器人 QQ 的手机扫码** |
| 11:51:57 | 3000 端口恢复；11:52 主循环自动接上；11:53 真实 @ 消息回复成功——**满血复活** |

### 5.2 关键知识：NapCat WebUI API（事故宝典）

- WebUI 端口 6099，token 在 `napcat/config/webui.json`
- **登录不是发原 token**：`POST /api/auth/login` 载荷 `{"hash": SHA256(token + ".napcat")}`，返回 `data.Credential` 当 Bearer 用
- `POST /api/QQLogin/GetQuickLoginList` → 存储的快速登录账号；`POST /api/QQLogin/SetQuickLogin`（崩溃后腾讯会拒）
- `POST /api/QQLogin/GetQQLoginQrcode` → 拿登录二维码链接
- `POST /api/OB11Config/GetConfig|SetConfig`（载荷 `{"config": "<JSON字符串>"}`）→ **在线重载 OneBot 网络层**（要求登录态存活——本次因登录失效没用上，但它是「不杀进程改网络」的正道）
- 前端路由规律：`fetch("/api" + path)`，路由是 PascalCase 两段式（`/Process/Restart`、`/WebUIConfig/GetConfig`）

### 5.3 教训

1. **崩溃把「HTTP 服务」和「登录会话」一起带走**——不要以为进程活着就只是端口问题；快速登录被拒时直接走扫码，别浪费时间
2. **扫码重登不需要 kill 进程**：现有 WebUI 里拿新二维码即可，OneBot 配置（`onebot11_<uin>.json`）在登录成功后自动加载——红线三件事（kill 进程/改配置/起第二实例）一件都不用犯
3. 诊断顺序：`ps` 看主进程 → `ss -tlnp` 看端口 → WebUI API 探登录态 → 快速登录 → 扫码
4. 修复验证三件套：`ss :3000`、机器人 token 调 `get_login_info`、`tail bot.log` 看主循环恢复

---

## 6. 交接后改动的全部文件

| 文件 | 变更 | 说明 |
|---|---|---|
| `server/tablecheck.py` | **新增** | 单纯形表解析/验算/修复（纯 stdlib，无新依赖） |
| `server/pdf2md.py` | **新增** | PDF→MD（CLI + `pdf_to_md()` 可导入），依赖 pymupdf4llm |
| `server/main.py` | 修改 | PDF 任务分支（`find_quoted_pdf` / `handle_pdf_task` / `_pdf_cache_cleanup` / 阅读总结判定）；表格自检抽成 `_run_tablecheck()` 供两条路径共用 |
| `server/brain.py` | 修改 | `TABLE_FIX_PROMPT` + `revise_table()`；`decide()` 加 `max_tokens` 参数；`_coerce_pdf` 纯文字放行；`PDF_HINT` 出口收窄 |
| `server/persona.py` | 修改 | 文件名指引加「阅读总结」 |
| `server/config.py` | 修改 | `TABLECHECK_*` 与 `PDF_TASK_*` 两组开关 |
| `server/onebot.py` | 修改 | `get_msg()`（引用回查）、`download_file()`（流式 + 大小护栏） |
| `server/requirements.txt` | 修改 | + `pymupdf4llm>=0.0.27` |
| `server/.env.example` | 修改 | + 新开关模板 |
| `tests/`（3 个文件） | **新增** | 29 + 27 + 36 项断言，全离线 |
| 服务器 `docs/数学巧解方法论·技能文档.md` | **新增** | 讲义方法论提炼 |

**配置开关速查**（都写进了 `.env.example`，全部有默认值，不配也能跑）：

```ini
TABLECHECK_ENABLED=1        # 单纯形表算术自检
TABLECHECK_MAX_REVISE=1     # 表错了让模型重算几轮
TABLECHECK_ANNOTATE=1       # 修不好时放行原文+说实话标注
PDF_TASK_ENABLED=1          # 群友引用 PDF + @ 解题任务
PDF_TASK_COOLDOWN=120       # 同群两次任务最小间隔（秒）
PDF_TASK_MAX_CHARS=30000    # 喂给模型的 MD 字符上限（超长截断）
PDF_TASK_MAX_PAGES=60       # 最多转换多少页
PDF_TASK_CACHE_TTL=1800     # MD 缓存半小时（车主定的）
```

---

## 7. 换服务器重建清单（增量部分）

主流程照 **07 交接文档 §1 §8**（连服务器/装环境）和 **05-服务器配置详单**（精确配置与复核命令）走，这里只列**交接后新增的增量步骤**：

```bash
# ── 1. 代码到位后，venv 依赖会多一个（deploy.sh 会自动装）────────
/home/qqbot/qq-bot/venv/bin/pip install -i \
  https://mirrors.aliyun.com/pypi/simple/ -r /home/qqbot/qq-bot/requirements.txt

# ── 2. 确认新文件在位（deploy.sh 的通配符会带上它们）────────────
ls /home/qqbot/qq-bot/tablecheck.py /home/qqbot/qq-bot/pdf2md.py

# ── 3. 方法论技能文档放回 docs/（属主和权限照 latex-format.md）──
install -o qqbot -g qqbot -m 644 数学巧解方法论·技能文档.md \
  /home/qqbot/qq-bot/docs/

# ── 4. .env 里补新开关（可选，都有默认值）──────────────────────
#    参考 code/server/.env.example 末尾两组

# ── 5. 三套离线测试全绿（不发真实群）──────────────────────────
#    tests/ 在 code/tests/，scp 上去后：
sudo -u qqbot PYTHONPATH=/home/qqbot/qq-bot /home/qqbot/qq-bot/venv/bin/python test_tablecheck.py    # 29
sudo -u qqbot PYTHONPATH=/home/qqbot/qq-bot /home/qqbot/qq-bot/venv/bin/python test_integration.py  # 27
sudo -u qqbot PYTHONPATH=/home/qqbot/qq-bot /home/qqbot/qq-bot/venv/bin/python test_pdf_task.py      # 36

# ── 6. PDF 能力单测（不依赖 QQ）───────────────────────────────
sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python \
  /home/qqbot/qq-bot/pdf2md.py 任意文字型.pdf --pages 0-2
```

**验收标志**：`systemctl is-active qqbot` 为 active；日志出现「启动 | bot_qq=…」且「当前登录」有 QQ 号；发一条 @ 消息能回；渲染烟测三形态出图（card PNG / doc PDF / 风格 2）。

---

## 8. 排查指引（交接后新增功能的故障树）

| 症状 | 先看 |
|---|---|
| **机器人不回话 + 日志刷 Connection refused** | **NapCat 内部崩溃**（2026-10-11 真实案例）→ 本文 §5 完整处理流程；快速登录被拒就直接扫码，别浪费时间 |
| 表格验算误报/漏报 | `tests/test_tablecheck.py` 的 BROKEN_SAMPLE 回归；σ 约定投票逻辑在 `_detect_sigma` |
| PDF 任务没触发 | 日志搜「PDF 任务」；确认群友是**引用文件消息**（不是发完就 @）；`.pdf` 后缀大小写不敏感 |
| PDF 转出来是空的 | 多半是扫描件——本工具不做 OCR（诚实告知群友）；文字型 PDF 却为空 → 看「覆盖率」日志，阈值在 `--threshold` |
| 转换慢 | 正常：服务器 2 核约 0.4s/页；60 页上限就是为这个设的 |
| 缓存没命中 | file_id 变了（群友重新上传过）；或超过半小时；日志会写「命中 X 分钟前的缓存」或直接走下载路径 |
| 重启后回答重复了一条 | 重启恰好卡在状态保存间隙，一条已读标记丢了——偶发，无害 |

---

## 9. 文档导航（这个问题去查哪份）

| 你要做什么 | 去哪 |
|---|---|
| 从零复刻整套系统（买服务器→装环境→上线） | 01 实战全记录 + 05 配置详单 |
| 遇到没见过的 bug、要「怎么查」的方法论 | 03 踩坑与排查手册（「排查手法」章最值钱） |
| 改机器人说话风格 / 输出协议 / 提示词 | 04 提示词与人格设计 + `code/server/persona.py` |
| 了解能力怎么一步步演进（DSH 时期） | 02 能力迭代史 |
| **接手时的世界：红线、车主规矩、服务器概况** | **07 交接文档（脱敏版）** |
| **接手后的功能、坑、事故、增量重建** | **本文（06）** |
| 换新服务器快速搭起来 | 07 §8 + 本文 §7 |

---

*整理：ZCode（接手 AI）。DSH 的原始五份文档（01~05）见本目录；《07-交接文档-脱敏版》为 DSH 交接文档的公开化改写。*
