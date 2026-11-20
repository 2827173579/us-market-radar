# 🐮 美股量化雷达 · US Market Radar

> 单用户美股全周期量化看盘 / 告警 / AI 预测体系：**70 只核心标的（14 ETF + 56 个股/加密资产）** 实时轮询、富途风格专业终端、
> **5 年 AI 多赛道波段预测与 15 日时空周期推演**、微信 + Discord 双通道预警、**PC（主）+ 安卓平板（备）双活热备去重**、GitHub Pages 在线看板与 24/7 自动同步。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Online-brightgreen.svg)](https://2827173579.github.io/us-market-radar/)

> ⚠️ 本项目为个人量化研究与学习项目，**不构成任何投资建议**。详见文末 [Disclaimer](#-disclaimer免责声明)。

---

## 🌐 在线实盘看板直达（GitHub Pages 实时同步）

| 看板名称 | 在线直达链接 | 本地文件 | 核心定位与功能亮点 |
|---|---|---|---|
| **🐮 富途专业看盘主终端** | **[点击打开主终端](https://2827173579.github.io/us-market-radar/)** | `futu_pro_terminal.html` / `index.html` | 70 标的 TradingView 暗色全周期 K 线、5m 量价买点、期权异动攻防墙、实盘预警抽屉、手机端快捷跨页导航 |
| **⚡ AI 5 年多赛道预测雷达** | **[点击打开 AI 预测](https://2827173579.github.io/us-market-radar/predict_radar_5y.html)** | `predict_radar_5y.html` | LightGBM 多空双向校准胜率、V2.2 15 日时空周期推演（KNN-5 历史相似 + 零漂移 FFT 谐波）、`<0.1s` 实时价格锚定 |
| **🎯 核心个股实战雷达** | **[点击打开个股雷达](https://2827173579.github.io/us-market-radar/stocks_radar.html)** | `stocks_radar.html` | 56 只核心标的黄金坑斐波那契防守带、**纯百分比动态风险平价仓位**、V3.2 暴跌熔断防接刀矩阵 |
| **🏛️ 宏观大盘司令塔** | **[点击打开宏观司令塔](https://2827173579.github.io/us-market-radar/macro_radar.html)** | `macro_radar.html` | 14 只宏观/行业/避险/反向对冲 ETF 水温、市场气象晴雨表、防御因子背离实时监控 |
| **📈 5 年历史回测对账战报** | **[点击打开 5Y 回测](https://2827173579.github.io/us-market-radar/backtest_report_5y.html)** | `backtest_report_5y.html` | 2021–2026（1,194 交易日）双轨制策略净值曲线、1.5 bps 滑点校准与机构复式记账平账审计 |

---

## ✨ 项目简介与核心特性

本项目是一套完整的「美股量化雷达」工具链，覆盖 **行情抓取 → 指标计算 → 静态看板渲染 → 告警推送 → 跨端去重与云端自动同步** 全流程，适合个人研究、复盘与自动化盯盘：

- **70 只标的实时轮询**：**14 只宏观/行业/对冲 ETF + 56 只核心科技/蓝筹/数字资产标的**，支持 Finnhub 秒级实时报价与 yfinance 免费接口双擎联动；
- **🪙 数字资产（BTC）专区 & 7×24 小时自然日连续模式**：
  - 左侧分类栏内置「🪙 数字资产 (BTC)」专属 Tab（覆盖 `BTC-USD`、`MSTR`、`COIN`）；
  - `BTC-USD` 100% 走 Yahoo 免费接口绕过 Finnhub 频控，TradingView 自动映射 `COINBASE:BTCUSD`；
  - 在 `predict_radar_5y.html` 中为 `BTC-USD` 独立启用 **7×24 小时自然日连续模式**（保留周六、周日完整自然日 K 线，未来 `T+1 ~ T+15` 推演横轴按连续自然日递增，不跳过周末；其余 69 只美股/ETF 严格遵循美股交易日历）；
- **⚡ AI 5 年波段预测雷达 & 盘中毫秒级实时同步**：
  - LightGBM 机器学习双向三阈值打标（做多 Top 10 + 做空 Top 10）、7 大赛道先验校准胜率、4H 共振与 S1/S2/R1/R2 筹码点位；
  - **V2.2 15 日时空周期推演**：结合 KNN-5 历史相似形态匹配与零漂移 EMA20 去趋势傅里叶谐波（FFT）分解，自动标定未来 15 日周期阻力窗与回踩窗；
  - **盘中每分钟轻量实时同步（`sync_live_prices_to_predict_html`）**：每轮行情刷新后以 `<0.1s` 极速对齐全部 70 只标的的右上角最新现价、图上“今日节点”与 15 日推演起点（保持 15 日涨跌幅波形比例不变），并在美股 16:05 ET 收盘或 `BTC-USD` UTC 00:00 日切时自动触发全量重算；
- **美股夜盘 24h 期货代理**：夜盘不用白天现货充数，防御背离改由 `NQ=F` / `ZB=F` / `GC=F` 实时折算；
- **破坑分级与五档差异化止损**：空头宣泄禁接刀与超跌右侧博反抽严格分离；止损画在 VWAP 或 5 分钟企稳低点下方，宽基、行业 ETF、杠杆、厚盘口、薄盘口各用各的距离；偏离失效位过远自动提示等回踩 VWAP，杜绝止损倒挂；
- **黄金坑 / 纯百分比风控看板**：60 日斐波那契回撤低吸防守带、V3.2 暴跌熔断与防接飞刀军规、`stocks_radar.html` 纯百分比动态风险平价仓位（`15%~25%` 分批低吸 / `10%~15%` 顺势等回踩 / `0%` 严禁接刀）；
- **微信 + Discord 双通道 & PC/平板双活热备去重**：
  - PushPlus 微信卡片实时推送 + Discord 小豆泥量化哨兵（五大梯队巡检，严格覆盖主雷达全量 70 只标的）；
  - **PC（主）+ 安卓平板 Termux（备）360 秒心跳避让（`shared_alert_state.py`）**：PC 在线值班时平板自动 Standby 静默待命；PC 休眠/离线超 6 分钟后平板自动无缝接管实盘告警；跨端共享纯文本去重账本 `data_cache/shared_alert_dedupe.json`（零重复推送、零 Webhook/密钥入库）；
- **全平台丝滑交互（Desktop & Mobile PWA）**：
  - 桌面端实盘预警通知中心与左侧赛道分类栏支持 **鼠标左键按住拖拽（Click & Drag）+ 滚轮横向滚动 + `< >` 微调箭头** 三合一交互；
  - 手机端（`<= 768px`）顶部固定展示紧凑 4 宫格跨页导航栏（`⚡ AI预测` / `🎯 个股雷达` / `🏛️ 司令塔` / `📈 5Y回测`）及底部 5 栏 App 级导航，各子看板顶部一键返回 `🐮 看盘主终端`；iPhone/Android 可「添加到主屏幕」获得原生级 PWA 体验。

---

## 📊 核心看板详解

### 1️⃣ 富途专业全周期看盘终端（`futu_pro_terminal.html` / `index.html`）

- 在线访问：[https://2827173579.github.io/us-market-radar/](https://2827173579.github.io/us-market-radar/)
- 核心能力：
  - 70 只标的 TradingView 暗色 K 线，分时 / 5 分钟 / 15 分钟 / 60 分钟 / 日 / 周 / 月 K 无缝切换；
  - 五维状态（标的选择 / 周期 / 副图指标 / 分组 / 视图）本地 `localStorage` 持久记忆；
  - 左侧赛道与信号分类（含 `⚡ AI高胜率`、`🩸 AI做空榜`、`🟢 黄金坑`、`🪙 数字资产 (BTC)` 等），支持鼠标拖拽与滚轮横向滑动；
  - 机构期权异动大单墙（Call/Put 成交量、权利金金额、PCR、ATM 隐含波动率预期波动带）；
  - 右上角「🔔 实盘预警通知中心」抽屉（含 7 类音效试听、分类拖拽筛选、点击卡片一键联动主图）；
  - 手机端顶部跨页快捷导航栏 + 底部 5 栏切换（行情 / K线 / 决策 / 期权 / 持仓）。

### 2️⃣ AI 美股 5 年多赛道波段预测雷达（`predict_radar_5y.html`）

- 在线访问：[https://2827173579.github.io/us-market-radar/predict_radar_5y.html](https://2827173579.github.io/us-market-radar/predict_radar_5y.html)
- 核心能力：
  - LightGBM 双向三阈值打标（做多止盈/止损 + 做空下杀/反抽）+ 23 维量价与宏观截面特征；
  - 70 标的 15 日时空周期推演交互式 Canvas 图表（支持聚焦 15 日 / 全景 35 日切换、鼠标拖拽平移与滚轮缩放）；
  - `BTC-USD` 专属 7×24 自然日连续推演模式 + 全量 70 标的每分钟现价/今日节点/推演起点实时同步；
  - 5 年历史仿真、摩擦滑点校准与穿透式账务审计（见 `backtest_report_5y.html`）。

---

## 🚀 本地一键启动指南

### 环境要求

- **Python ≥ 3.10**（推荐 3.11 / 3.12 / 3.13）
- 无需数据库（前端为单文件零依赖静态 HTML，内置响应式与 Canvas 渲染）
- 依赖：`pip install -r requirements.txt`（含 `pyarrow` 用于 Parquet 5 年行情面板高速读写）

### 三步启动

```bash
# 1) 克隆代码
git clone https://github.com/2827173579/us-market-radar.git
cd us-market-radar

# 2) 安装依赖 + 配置环境变量
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 按需填入 Finnhub / PushPlus / Discord / GH_TOKEN

# 3) 单次全量扫描并生成四大 HTML 看板
python integrated_market_radar.py --once
```

常驻极速轮询模式（每 60 秒自动抓取、同步 HTML 并推送 GitHub Pages）：

```bash
python -u integrated_market_radar.py --loop-daemon --interval-seconds 60 --duration-minutes 0
```

启动后本地看盘地址：`http://127.0.0.1:8765`。

### Windows 一键脚本（推荐新手）

| 脚本 | 功能 |
|---|---|
| `1-新电脑一键环境配置.bat` | 检测 Python、自动安装依赖（含国内镜像加速） |
| `2-启动雷达控制面板.bat` | 打开可视化控制中心（启动/停止后台、看盘、发测试消息） |
| `3-一键后台静默启动.vbs` | 后台静默常驻轮询（适合长期开机盯盘） |
| `4-一键停止雷达.bat` | 安全停止所有后台雷达进程 |
| `5-设置开机自启.bat` | 写入启动目录，开机自动后台运行 |

### 手机端 PWA

1. **iPhone / iPad（Safari）**：打开本地/在线看板 → 点击「分享」→ 选择「**添加到主屏幕**」→ 添加；
2. **Android（Chrome / Edge）**：打开看板 → 右上角菜单 → 「**安装应用**」或「添加到主屏幕」；
3. 独立窗口全屏运行，顶部配备跨页导航栏（`AI预测 / 个股雷达 / 司令塔 / 5Y回测`），底部配备 5 栏导航（`行情 / K线 / 决策 / 期权 / 持仓`）。

---

## 🔑 环境变量（`.env`）

`.env` 与 `integrated_market_radar.py` 同目录，已被 `.gitignore` 严格排除，**请勿提交真实 Key 或 Webhook**。
完整模板见 [`.env.example`](.env.example)。

| 变量 | 必需性 | 说明 |
|---|---|---|
| `FINNHUB_API_KEY` | 强烈建议 | Finnhub 实时报价（免费注册即可；`BTC-USD` 自动走 Yahoo 免费接口不占额度） |
| `PUSHPLUS_TOKEN` | 可选 | PushPlus 微信推送；不填则跳过微信 |
| `PUSHPLUS_CHANNEL` | 可选 | PushPlus 渠道（默认 `clawbot`） |
| `DISCORD_WEBHOOK_URL` | 可选 | Discord 哨兵主频道 |
| `DISCORD_CLAW_WEBHOOK_URL` | 可选 | 微信告警镜像频道（兼容 `DISCORD_ALERT_WEBHOOK_URL`） |
| `DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL` | 可选 | 防御因子背离专属频道 |
| `GH_TOKEN` | 本地自动 push 需要；Actions 上用于刷新 Pages | 有**当前仓库** `contents:write` 的 PAT。Actions 没配这个 secret 时，仍会用默认 `GITHUB_TOKEN` 把看板 push 到本仓库 `main`，但「Deploy from a branch」不会因此重新构建 Pages |

---

## ☁️ Fork 后开启 GitHub Pages 与 Actions 24/7 自动轮询

> 目标：让 Fork 出来的仓库也能像上游一样 **云端自动抓取 → 自动 commit/push → GitHub Pages 自动更新**，
> 本地电脑不开机也能在手机上看最新看板。

### Step 1 · Fork 仓库

点击 GitHub 右上角 **Fork**，得到 `你的用户名/us-market-radar`。

### Step 2 · 开启 GitHub Pages

1. 进入 Fork 仓库 → **Settings** → **Pages**；
2. Source 选择 **Deploy from a branch**；
3. Branch 选择 **main** / 目录选 **/(root)** → **Save**；
4. 约 1 分钟后访问 `https://你的用户名.github.io/us-market-radar/` 验证。

> 仓库根目录已含 `.nojekyll`，避免 Jekyll 干扰静态 HTML。

### Step 3 · 配置 Actions Secrets

进入 **Settings → Secrets and variables → Actions → New repository secret**，按需添加：

| Secret | 必需 | 说明 |
|---|---|---|
| `FINNHUB_API_KEY` | ✅ | Finnhub 免费 API Key |
| `PUSHPLUS_TOKEN` | 可选 | 微信推送 Token |
| `GH_TOKEN` | 不需要 | Actions 使用自带的 `GITHUB_TOKEN` 推送本仓库，并调用官方 Pages 接口发布。不要再配 PAT。本地电脑自己跑守护进程时才需要 |
| `DISCORD_WEBHOOK_URL` 等 | 可选 | Discord 推送频道 |

### Step 4 · 启用并手动触发一次

1. 进入 **Actions** 标签，若提示 workflow 被禁用，点击 **I understand my workflows, go ahead and enable them**；
2. 左侧选择 **US Market Radar 24-7 Engine** → **Run workflow**；
3. `duration_minutes` 默认 `0`：只跑一轮并发布 Pages。需要像定时任务那样长驻时再改成 `240`（`interval_seconds` 仅在持续运行时生效），确认第一轮成功 push；
4. 之后按 workflow 内置的 cron 时段自动运行。

### Step 5 · 多端协同与避让机制

- **PC（主）+ 安卓平板 Termux（备）自动避让**：通过 `shared_alert_state.py` 维护 360 秒 PC 心跳锁，PC 在线时安卓端（`--android`）自动进入 Standby 仅同步看板不发重复告警；PC 离线超 360 秒后安卓端自动接管告警推送；
- **云端 Actions 互斥**：只有**本仓库**最近 15 分钟内出现非 Actions 提交时，定时任务才会避让。Fork 时继承的上游提交，以及之后从上游同步下来的同一批 commit，不会被当成「本地客户端正在跑」。云端只 `commit` / `pull` / `push` 当前仓库（`github.repository`），不会把 `origin` 改到上游，也不会在 Actions 里 rebase 上游。
- **Pages**：维持「Deploy from a branch」时，每一轮成功 push 之后用 `GITHUB_TOKEN` 调用官方 `POST /repos/{当前仓库}/pages/builds`，不需要 PAT。若以后把 Source 改成 GitHub Actions，同一次 workflow 会改用 `actions/upload-pages-artifact` 和 `actions/deploy-pages`。定时任务走的是同一条发布路径，不是只发一次。

---

## 📈 V3.1 / V3.2 双轨制策略与历史回测

本项目内置的量化体系（回测明细详见 `backtest_report_5y.html` 与历史策略报告）：

#### 轨道一：Track 1 (Pure Stock Dynamic Risk-Managed / 纯个股动态风控轨)

- 56 只高 Alpha 科技个股与赛道龙头，1.5% 账户风险平价动态头寸（单股上限 25%），最多同时持仓 4 只；
- EMA10/EMA20 移动止盈 + -6.8% 结构性硬止损；历史捕获 ASML +30%、AMD +37%、AVGO +22% 等大波段。

#### 轨道二：Track 2 (Flagship QQQ Base Enhancement / QQQ 核心底仓增强旗舰轨)

- QQQ 作为流动性停泊底座：QQQ > SMA200 时空闲现金全额停泊 QQQ；破位则清仓避险；
- 卫星个股黄金坑触发时自动卖出等额 QQQ 释放流动性，获利了结后回流底仓。

**回测口径**：2021-2026 共 1,194 个交易日，初始本金 \$100,000，Firstrade 零佣金 + **1.5 bps** 流动性滑点校准。
双轨制 5 年回测对比、资金盈亏比（Dollar PF）与最大回撤详见 `backtest_report_5y.html`。

#### V3.2 暴跌熔断与防接飞刀军规

- 动态 ATR 自适应熔断闸（winsorize 至 1.2%~5.0%）；
- 断头铡刀日内跳水 / 盘后跳空保护；
- 3 日暴跌宣泄期冷却锁（`prev_crash_3d`）与次日企稳解锁条件；
- 反向损耗 ETF（SQQQ / SOXS / VIXY）物理隔离，严禁作为多头抄底标的。

---

## 📁 目录结构（核心）

```
us-market-radar/
├── integrated_market_radar.py   # 主守护进程：抓取→计算→生成 HTML→轻量同步预测现价→告警→自动 git 同步
├── ml_predictor_5y.py           # AI 5年多赛道预测、V2.2 15日时空周期推演与 BTC-USD 7x24 自然日引擎
├── create_futu_terminal.py      # 富途风格全周期看盘终端 HTML 生成器
├── discord_sentinel.py          # Discord 小豆泥量化哨兵（五大梯队，严格对齐主雷达 70 只标的）
├── shared_alert_state.py        # PC(主)/安卓平板(备) 360s 心跳避让与跨设备纯文本告警去重账本
├── index.html                   # 富途看盘主终端（GitHub Pages 根路径）
├── futu_pro_terminal.html       # 富途看盘主终端
├── predict_radar_5y.html        # AI 5 年预测与 15 日时空周期推演看板
├── stocks_radar.html            # 核心个股黄金坑与纯百分比仓位雷达
├── macro_radar.html             # 宏观大盘司令塔（14 只 ETF 水温看板）
├── backtest_report_5y.html      # 5 年历史回测对账战报
├── data_cache/                  # AI 预测快照 JSON、跨端去重账本与 5 年面板 Parquet 缓存
├── sounds/                      # 7 类实盘量化预警音效
├── docs/vps_deploy/             # VPS / Termux 部署辅助脚本与说明
├── 1~5 号 bat / vbs             # Windows 一键环境配置、控制面板与静默自启脚本
├── test_*.py                    # 单元测试与法医级对抗性验证套件（100+ 项断言测试）
├── .env.example                 # 环境变量安全模板（唯一入库的环境配置说明）
├── user_portfolio.example.json  # 持仓配置模板（本地复制为 user_portfolio.json 使用）
└── AGENTS.md                    # 面向 AI 编程工具的项目架构与协作规范
```

---

## 🧪 自动化测试套件

```bash
# 1) 前端 DOM 完整性、拖拽交互、手机跨页导航与 BTC-USD 7x24 自然日推演测试
python -m unittest test_challenger_m3.py

# 2) 时空周期推演 21 核心标的法医级对抗审计
python -m unittest test_adversarial_challenger_r2.py

# 3) Discord 哨兵、跨端去重与零密钥泄露门禁测试
python -m unittest test_discord_sentinel.py

# 4) V3.2 暴跌熔断与防接飞刀测试
python -m unittest test_v32_circuit_breaker.py

# 5) 5 年 AI 预测管道、因果特征与三阈值打标全量测试
python -m unittest test_ml_predictor.py
```

---

## 🔒 安全与隐私

- `.env` 永不入库；`.env.example` 仅含安全占位符，全仓 tracked 文件真实 Webhook 数量严格为 **0**；
- 跨端去重账本 `data_cache/shared_alert_dedupe.json` 仅存储纯文本去重键（`ET日期:代码:信号类型`），绝不写入任何 Token 或 URL；
- 个人持仓请写入本地 `user_portfolio.json`（参考 `user_portfolio.example.json`），已被 `.gitignore` 自动排除；
- 运行日志、临时缓存、`.radar_daemon.log` 等均已加入 `.gitignore`。

### 持仓配置模板（`user_portfolio.example.json`）

复制为 `user_portfolio.json`（同目录，已被 `.gitignore` 排除）后按需修改：

| 字段 | 含义 |
|---|---|
| `shares` | 实际持股数（`0` 表示仅观察、不计持仓盈亏） |
| `cost` | 持仓成本价（美元/股） |
| `base_val` | 防守/止损价（程序兼容 `stop_val` 字段名） |
| `target` | 目标价 / 规划止盈价 |
| `trim1` / `trim1_shares` | 第一止盈价 / 第一止盈减仓股数 |
| `trim2` | 第二止盈价 |
| `note` / `name` / `cat` | 看板显示用备注 / 名称 / 赛道分类 |

> ⚠️ 不要在 JSON 顶层添加 `_comment` 之类非股票代码的键：程序会把它们当成标的处理。说明信息请写在每个标的的 `note` 字段中。

---

## 📄 License

本项目基于 [MIT License](LICENSE) 开源。

---

## ⚠️ Disclaimer（免责声明）

> **本项目仅用于个人量化学习、研究与技术交流，不构成任何投资建议、买卖要约或收益承诺。**
>
> - 所有行情数据来自第三方（Finnhub / Yahoo Finance 等），可能存在延迟、缺失或错误，作者不对数据准确性作任何担保；
> - 所有信号、指标、AI 预测与回测结果均为历史统计与模型输出，**历史表现不代表未来收益**，实盘交易存在本金损失风险；
> - 使用者应自行独立判断并承担全部交易风险；因使用本项目导致的任何直接或间接损失，作者不承担任何责任；
> - 请遵守所在地区法律法规及所使用券商/数据源的条款；本项目不提供任何形式的投资顾问服务；
> - 部分模块（微信 / Discord 推送、GitHub Actions、云服务）可能涉及第三方平台费用或条款限制，请自行确认并遵守。

**English**: This project is for personal quantitative research and educational purposes only and does **not**
constitute investment advice. Market data is provided by third parties and may be delayed or inaccurate. Past
performance does not guarantee future results. Use at your own risk; the author assumes no liability for any
losses arising from the use of this software. You are responsible for complying with your broker's and data
providers' terms of service.
