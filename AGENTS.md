# AGENTS.md — us-market-radar 项目导航与 AI 代理工作规范

> 面向外部 AI 编程工具（Claude Code / Codex / Cursor / Gemini CLI 等）的项目说明。
> 最后更新：2026-11-11 ｜ 维护者：2827173579 ｜ 许可证：MIT（见 `LICENSE`）
>
> **English summary**: A single-user US market quant radar: Python daemon (`integrated_market_radar.py`)
> polls 70 tickers every 60–120 s, renders static HTML dashboards, pushes WeChat/Discord alerts, and
> auto-commits/pushes to GitHub so GitHub Pages stays fresh. `discord_sentinel.py` sends a 51-ticker
> 5-tier Discord report. Do **not** kill running daemons or `git commit/push` autonomously; never commit
> `.env` or real keys.

---

## 0. ⛔ 绝对红线（所有 AI 代理必须先读）

1. **禁止自动 `git commit` / `git push`**：本仓库有实盘守护进程自动提交，任何手动提交必须先由人类确认。
2. **禁止 kill / 重启正在运行的守护进程**：尤其 `integrated_market_radar.py --loop-daemon` 与旧电脑/云端的轮询进程。
3. **禁止提交 `.env`、真实 API Key、Token、Webhook URL**：`.env` 已被 `.gitignore` 排除；只允许维护脱敏的 `.env.example`。
4. **禁止把带个人信息的运行时数据直接开源**：`user_portfolio.json` 等见 §7。
5. 修改核心业务代码前先说明方案；默认只改「任务指定文件」，不顺手重构无关模块。
6. 任何新增文件先保存到本地工作区，由人类审查后再决定是否提交。

---

## 1. 项目是什么

单用户的美股全周期量化看盘 + 告警 + AI 预测体系（不是面向多用户的 SaaS）：

| 能力 | 实现 |
|---|---|
| 实时行情轮询（70 只标的：16 ETF + 54 个股） | `integrated_market_radar.py`（Finnhub / yfinance） |
| 三大静态看板（宏观 / 个股 / 富途专业终端） | `macro_radar.html`、`stocks_radar.html`、`futu_pro_terminal.html` / `index.html` |
| AI 5 年波段预测看板（LightGBM 三阈值打标） | `ml_predictor_5y.py` → `predict_radar_5y.html` |
| 微信推送（PushPlus）+ Discord 镜像 | `integrated_market_radar.py` 内的 `send_pushplus_wechat_alert` / `send_discord_*` |
| Discord 五梯队量化哨兵（51 标的） | `discord_sentinel.py`（由雷达以 daemon 线程调用） |
| Windows 一键控制中心（tkinter） | `TradeRadar_ControlPanel.pyw` + `1~5 号 bat/vbs` |
| 云端 24/5 托管 | GitHub Actions `.github/workflows/market_radar.yml` + GitHub Pages |
| 历史回测 / 策略研究 | `v31_strategy.py`、`backtest_5y_engine.py`、`ml_predictor_5y.py`（本地研究见 `daily_research/`，未入仓） |

### 仓库地图（核心文件）

```
integrated_market_radar.py   主守护进程（约 4,900 行）：抓取→计算→生成 HTML→推送→自动 git 同步
discord_sentinel.py          小豆泥量化哨兵（约 1,400 行）：51 标的五梯队 / 6 级状态机 / Discord Embed
ml_predictor_5y.py           AI 预测与特征工程（约 5,800 行）；lightgbm/sklearn 为可选依赖
create_futu_terminal.py      富途风格终端 HTML 生成器
create_desktop_shortcuts.py  在桌面生成看板快捷方式（动态定位当前项目目录）
TradeRadar_ControlPanel.pyw  Windows 可视化控制中心（启动/停止后台、发测试消息）
futu_pro_terminal.html       生成的终端看板（同时复制为 index.html 供 GitHub Pages 根路径访问）
macro_radar.html             宏观 ETF 水温看板（生成物）
stocks_radar.html            个股黄金坑/风控看板（生成物）
predict_radar_5y.html        AI 5 年预测看板（生成物）
backtest_report_5y.html      5 年回测战报（生成物）
data_cache/                  预测快照 + 5 年面板 parquet（daemon/脚本产物）
test_*.py                    测试与对抗性验证套件
1~5-*.bat / *.vbs            Windows 一键脚本（环境配置/控制面板/静默启动/停止/开机自启）
docs/vps_deploy/             VPS 部署备用脚本（setup_server.sh / 一键更新云服务器.bat / README）
README.md                    开源门面（项目介绍 / 双看板 / 本地启动 / Fork 云端部署 / Disclaimer）
LICENSE                      MIT 许可证
TEST_READY.md                当前测试与质量状态（M4：14/14 PASS）
AGENTS.md                    本文件（AI 代理导航）
.env.example                 环境变量模板（唯一允许入库的环境配置）
user_portfolio.example.json  持仓配置模板（复制为 user_portfolio.json 使用）
```

---

## 2. 运行环境

- **Python ≥ 3.10**（GitHub Actions 用 3.10；本地测试用 3.11~3.13 均可）
- 必需依赖（`requirements.txt`）：`requests / pandas / numpy / yfinance / plotly`
- 可选依赖：`pyarrow`（读写 `data_cache/*.parquet`）、`lightgbm` + `scikit-learn`（`ml_predictor_5y.py` 训练/回测；daemon 非必需，import 时有 try/except 兜底）、`python-dotenv`（可选，未安装时有内置 .env 解析）
- 无需 Node.js / 数据库；HTML 为单文件静态看板
- Windows 脚本依赖：cmd、PowerShell、Windows 10+ 自带 `tar`；VPS 更新脚本依赖 OpenSSH 客户端

---

## 3. 环境变量（`.env`）

`.env` 与 `integrated_market_radar.py` 同目录，已被 `.gitignore` 忽略。完整模板见 `.env.example`。

| 变量 | 必需性 | 说明 |
|---|---|---|
| `FINNHUB_API_KEY` | 强烈建议 | Finnhub 实时报价，免费额度够用 |
| `PUSHPLUS_TOKEN` | 可选 | PushPlus 微信推送；不填则跳过微信 |
| `PUSHPLUS_CHANNEL` | 可选 | PushPlus 渠道（默认 `clawbot`） |
| `DISCORD_WEBHOOK_URL` | 可选 | Discord 哨兵主频道；不填则跳过哨兵 |
| `DISCORD_CLAW_WEBHOOK_URL` | 可选 | 微信告警镜像频道（兼容 `DISCORD_ALERT_WEBHOOK_URL`） |
| `DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL` | 可选 | 防御因子背离专属频道 |
| `GH_TOKEN` | 自动同步必需 | 有本仓库 `contents:write` 权限的 PAT；Actions 环境用内置 `GITHUB_TOKEN` |
| `TZ` | 可选 | 时区（默认 Asia/Seoul；VPS systemd 注入） |

> 代码加载顺序：脚本目录下 `.env`（`load_env_file()`）→ 系统环境变量。**环境变量优先**。

---

## 4. 常用命令

### 本地 Windows

```bat
:: 1) 安装依赖
1-新电脑一键环境配置.bat
:: 2) 打开控制中心（启动/停止后台、看盘、发测试消息）
2-启动雷达控制面板.bat
:: 3) 后台静默启动 24/7 轮询（VBS，无窗口）
3-一键后台静默启动.vbs
:: 4) 安全停止后台轮询
4-一键停止雷达.bat
:: 5) 设置开机自启
5-设置开机自启.bat
```

### 命令行（跨平台）

```bash
# 单次全量扫描并生成三份看板（不会启动常驻服务）
python integrated_market_radar.py --once

# 24/7 无上限长轮询（60~120 秒/轮；0 = 无限时长）
python integrated_market_radar.py --loop-daemon --duration-minutes 0 --interval-seconds 120

# 微信推送连通性测试
python integrated_market_radar.py --test-wechat

# Discord 哨兵单次播报（离线可跑测试；真实播报需 DISCORD_WEBHOOK_URL）
python discord_sentinel.py --once

# 移动端/局域网看盘 API（端口 8765；loop-daemon 也会内置启动）
python integrated_market_radar.py --star-server-daemon
```

### 测试

```bash
python -m pytest test_discord_sentinel.py -q   # 14 项哨兵/工程约束测试（当前 14/14 PASS）
python test_v32_circuit_breaker.py             # V3.2 熔断场景（17/17 PASS）
python test_e2e_v31.py                         # 5 年回测端到端 55 项断言（需 .backtest_5y_cache.pkl）
```

---

## 5. 部署现状（2026-11-11）

### 5.1 当前生产

- **实际 pusher 是本地旧电脑的守护进程**（提交作者 `a18669358277@gmail.com`，每轮约 90~105 秒，提交信息 `⚡ [实盘同步] 第 N 轮极速刷新`）。
- GitHub Actions 的 `US Market Radar 24-7 Engine`（workflow id `398857789`）仍为 **active**，但受 workflow 内「本地优先检测」影响：**若最近 15 分钟有非 actions 提交，schedule 触发会被跳过**（run 约 24 秒完成，conclusion=success 但主任务被 skip）。当前 active/queued run = 0。
- `pages-build-deployment` 由每次 push 触发，负责 GitHub Pages 部署，**必须保留**。
- **2026-11-12 迁移推送后**：旧电脑守护进程在拉取 QQQM→QQQ 迁移提交后停止推送（最后提交：第 277 轮
  00:11）。需在旧电脑上**重启雷达守护进程**（如 `4-一键停止雷达.bat` → `3-一键后台静默启动.vbs`，
  或重启电脑/控制中心）以加载新代码；重启后会自动恢复 24/7 实盘同步并持续推送 QQQ 看板。

### 5.2 VPS 迁移：已决定暂缓

- 2026-11-11 决定：**VPS 部署先搁置不用**，继续使用现有「本地守护进程 / GitHub Actions」方案；
- 备用脚本已收纳到 `docs/vps_deploy/`（`setup_server.sh`、`一键更新云服务器.bat`、`README.md`），未启用；
- 若未来重启迁移：**必须先停旧电脑守护进程 + 禁用 Actions 雷达 workflow**（保留 `pages-build-deployment`），再按 `docs/vps_deploy/README.md` 操作，避免双写冲突；
- 回滚：重新 Enable workflow / 重启旧电脑守护进程。

---

## 6. 测试与质量现状（2026-11-11）

- `test_discord_sentinel.py`：**14/14 PASS（100%）**，离线确定性，约 0.8~4.6s。
  - 历史 M0 基线（11 个 stub / 2 失败）已由 2026-11-10 的 M1~M3 提交全部实现；
  - 唯一遗留的配置校验问题已修复：`test_discord_sentinel.py` 第 55 行旧电脑硬编码路径改为
    `PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))`；本地 `.env` 已创建（未追踪、gitignore，占位配置）。
- `TEST_READY.md` 已更新为 **M4 完结状态**（14/14 PASS + 各套件现状 + 历史记录）。
- 其他回归：`test_v32_circuit_breaker.py` 17/17、`test_challenger_m2.py` 9/9、`test_defects_4_8_verification.py` 4/4、
  `test_m2_adversarial_challenge.py` 9/9、`test_defects_6_7_9_10.py` 8/8 均通过。
- **已修复（2026-11-12）**：`ml_predictor_5y.py` 的多头精选榜 `top_long` 原先直接切片未排序（第 3178 行），
  现已按 `calibrated_win_rate_pct` 严格降序排序并重新生成预测快照/看板；
  `test_challenger_m2_recheck_deep.py` **9/9 全部通过**。
- **⚠️ 测试副作用**：`test_challenger_m2.py` / `test_defects_4_8_verification.py` /
  `test_defects_6_7_9_10.py` / `test_m2_adversarial_challenge.py` 会调用
  `create_futu_terminal.build_futu_terminal_html()`，该函数内含“实盘同步”副作用：
  重写 `data_cache/latest_ai_predictions.json`、重绘 `predict_radar_5y.html`、执行
  `git add`（不 commit/push）并生成 `us-market-radar/` 镜像目录。跑完这些测试后请清理：
  `git checkout HEAD -- macro_radar.html stocks_radar.html predict_radar_5y.html data_cache/latest_ai_predictions.json`
  并删除 `us-market-radar/`；根治需给该函数加 `sync_side_effects=False` 开关（需人类确认改核心代码）。
- **2026-11-11 基准迁移（QQQM → QQQ）**：雷达监控池、富途终端、宏观天气引擎、AI 5Y 预测
  （`ml_predictor_5y.py` 的 `MACRO_ETFS` / 特征列 `QQQ_Bias_MA50` 等 / `SECTOR_MAP` / 预测快照与
  `predict_radar_5y.html`）已全部切换为 **QQQ**；5 年面板 `ml_panel_5y.parquet` 已重建（含 QQQ，
  无 QQQM，数据至 2026-11-11）。历史告警/期权缓存与未重绘的雷达 HTML 中可能残留旧 QQQM 字样，
  守护进程下一轮会自动重绘；`qqqm_bias` 等内部 JSON 键名保留以兼容前端。

---

## 7. 数据与产物（开源前必读）

### 7.1 生成物 / 缓存（通常随 daemon 每次提交更新）

- `index.html` / `futu_pro_terminal.html` / `macro_radar.html` / `stocks_radar.html` / `predict_radar_5y.html`
- `data_cache/latest_ai_predictions.json`、`data_cache/ml_panel_5y.parquet`、`data_cache/ml_extended_panel.parquet`
- `.radar_alert_history.json`、`.terminal_notifications.json`、`.options_cache.json`、`.earnings_cache.json`
- `.radar_last_sync`、`.radar_daemon.log`、`.discord_sentinel_state.json`（后三者已 gitignore）

### 7.2 ⚠️ 个人信息 / 敏感数据文件（开源前需要人类决策）

| 文件 | 风险 | 建议 |
|---|---|---|
| `user_portfolio.json` | 真实持仓（含股数与成本价） | 开源前清空/换成示例数据；**请保留本地副本**，不要直接删除影响实盘 |
| `.terminal_notifications.json` | 真实告警历史（价格/标的/时间） | 开源前清空为 `[]` |
| `.radar_alert_history.json` | 告警去重历史 | 开源前清空 |
| `.options_cache.json` / `.earnings_cache.json` | 期权/财报缓存数据 | 可保留（公开市场数据），确认无账户信息 |
| `target_watchlist.json` | 个人自选池 | 可按需替换为示例池 |

> 新增 `user_portfolio.example.json` 作为脱敏模板（含字段说明），建议 README 引导用户复制使用。

---

## 8. 开源脱敏清单（2026-11-11 已完成路径清理）

### 8.1 旧电脑绝对路径 —— ✅ 已全部修复并复扫 0 残留

- 修复范围：**17 个文件 / 34 处**（含 `check_radar_status.bat`、`create_desktop_shortcuts.py`、
  `ml_predictor_5y.py`、`start_radar_daemon_silent.vbs`、`v31_strategy.py` 及全部 `test_*.py` 测试脚本）；
- 统一改法：Python 用 `os.path.dirname(os.path.abspath(__file__))`；`.bat` 用 `%~dp0`；
  `.vbs` 用 `CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)`；
- `check_radar_status.bat` 中硬编码局域网 IP 已改为 `127.0.0.1:8765`，并给进程检测增加
  `python*.exe` 名称过滤（避免 PowerShell 自匹配误报）；
- 复扫结果：`git grep` 旧电脑绝对路径 / 旧 IDE scratch 目录名 / 硬编码局域网 IP 均为 **0 命中**。

### 8.2 密钥 / Token 扫描结论

- **tracked 文件中未发现真实 API Key / Token / Webhook URL**（`ghp_` 命中均为「检测 remote URL 中 token」的逻辑代码 / 测试断言字符串，不是真实凭证）。
- 唯一的环境配置模板 `.env.example` 为占位符，可安全开源。
- ⚠️ 本地存在 `intraday_scalper/.env`（含真实 Key；该目录为本地研究目录，已被 git 排除）——**打包/开源时务必排除 `intraday_scalper/`、`daily_research/`、`.env`**。
- 本地根目录 `.env` 为测试占位配置（未追踪，gitignore），不会入库。
- 若未来在仓库里发现真实 Key：立刻吊销+轮换，并在开源前用 `git filter-repo` 清理历史。

---

## 9. 给 AI 代理的协作约定

1. **先读再改**：改任何文件前先读其上下文与相关测试；不确定时先输出方案让人类确认。
2. **小步提交式工作，但不自动 commit**：把改动留在工作区，列出 `git status` 与 diff 摘要。
3. **不新增重量级依赖**；如需新增，先改 `requirements.txt` 并说明理由（人类审批）。
4. **文件编码**：Python/Markdown/HTML 用 UTF-8；`.bat` 保持项目现有风格（`chcp 65001` + UTF-8）；`.sh` 用 LF 换行（禁止 CRLF）。
5. **网络**：测试应尽量离线确定性；涉及 Finnhub/yfinance/Discord 的联网调用需在改动说明中标注。
6. **安全**：任何日志/报错都不允许打印 `.env` 值；示例 URL 用 `your_webhook_id/your_webhook_token` 占位。
7. **部署**：不得自行启动/停止生产进程；VPS 变更走 `docs/vps_deploy/setup_server.sh` / `docs/vps_deploy/一键更新云服务器.bat` / `update_server.sh` 并保留回滚路径。
8. **研究归档**：已终止策略（如「不接飞刀」三驱抄底模型）归档于本地 `daily_research/no_knife_archive/`，不进入开源仓库。

---

## 10. 已知 TODO / 待人类决策

- [x] 更新 `TEST_READY.md` 为 M4 真实状态（14/14 PASS）。
- [x] 修复 `test_discord_sentinel.py` 硬编码路径 + 本地 `.env`，恢复 14/14。
- [x] 清理 17 个文件中的旧电脑绝对路径与硬编码局域网 IP。
- [x] 新增 `README.md` / `LICENSE` / `user_portfolio.example.json` / `docs/vps_deploy/`，并归档 `AGENTS.md`。
- [ ] 开源前脱敏 §7.2 个人信息数据文件（`user_portfolio.json`、告警历史等）。
- [x] 修复 `ml_predictor_5y.py` 的 `top_long` 排序缺陷（2026-11-12，`test_challenger_m2_recheck_deep` 9/9）。
- [ ] 重启旧电脑雷达守护进程以加载 QQQ 迁移后的新代码并恢复 24/7 推送。
- [ ] 如重启 VPS 迁移：先停旧电脑守护 + 禁用 Actions 雷达 workflow（保留 Pages 部署）。
