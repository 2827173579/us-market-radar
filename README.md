# 🐮 美股量化雷达 · US Market Radar

> 单用户美股全周期量化看盘 / 告警 / AI 预测体系：70 只核心标的实时轮询、富途风格专业终端、
> 5 年 AI 多赛道波段预测、微信 + Discord 双通道预警、GitHub Pages 在线看板与云端 24/7 自动轮询。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Online-brightgreen.svg)](https://2827173579.github.io/us-market-radar/)

> ⚠️ 本项目为个人量化研究与学习项目，**不构成任何投资建议**。详见文末 [Disclaimer](#-disclaimer免责声明)。

---

## ✨ 项目简介

本项目是一套完整的「美股量化雷达」工具链，覆盖 **行情抓取 → 指标计算 → 静态看板渲染 → 告警推送 →
云端自动同步** 全流程，适合个人研究、复盘与自动化盯盘：

- **70 只标的实时轮询**：16 只 ETF + 54 只核心科技/蓝筹/高波动个股，支持 Finnhub 实时报价与 yfinance 兜底；
- **美股夜盘 24h 期货代理**：夜盘不用白天现货充数，防御背离改由 `NQ=F` / `ZB=F` / `GC=F` 实时折算；
- **破坑分级**：空头宣泄禁接刀，和超跌右侧博反抽分开，不会把飞刀当成买点；
- **五档差异化止损**：止损画在 VWAP 或 5 分钟企稳低点下面，宽基、行业 ETF、杠杆、厚盘口、薄盘口各用各的距离；偏离失效位过远就等回踩 VWAP，止损跟着新挂单价往下算，不会倒挂；
- **富途风格专业终端**：TradingView 暗色图表、分时至月 K 无缝切换、五维状态持久记忆、机构期权攻防墙；
- **AI 5 年波段预测雷达**：LightGBM 机器学习三阈值打标、赛道归因、样本外校准胜率、Top 10 行动建议；
- **黄金坑 / 风控看板**：斐波那契回撤低吸防守带、V3.2 暴跌熔断与防接飞刀军规、动态仓位计算；
- **微信 + Discord 双通道**：PushPlus 微信卡片实时推送，Discord 小豆泥量化哨兵（五梯队巡检，覆盖主雷达 70 只；另保留 TQQQ / BTC-USD / SCHD）；
- **云端全自动**：GitHub Actions 24/7 轮询 + 自动 commit/push + GitHub Pages 部署，手机随时看盘；
- **PWA 移动端**：iPhone/Android 可「添加到主屏幕」，获得接近原生 App 的全屏体验。

---

## 📊 双看板说明

### 1️⃣ 富途专业全周期看盘终端

- 入口：`futu_pro_terminal.html`（根路径 `index.html`）
- 在线访问：[https://2827173579.github.io/us-market-radar/](https://2827173579.github.io/us-market-radar/)
- 核心能力：
  - 70 只标的 TradingView 暗色 K 线，分时 / 5 分钟 / 15 分钟 / 60 分钟 / 日 / 周 / 月 K 无缝切换；
  - 五维状态（标的选择 / 周期 / 副图指标 / 分组 / 视图）持久记忆；
  - 机构期权异动大单墙（Call/Put 成交量、金额、PCR）；
  - 自选 ⭐ 标记、持仓成本与止盈/止损价位管理、日内买点提示（5m VWAP / RSI / 量比共振）；
  - PWA 支持（见下方「手机端」章节）。

### 2️⃣ AI 美股 5 年多赛道波段预测雷达

- 入口：`predict_radar_5y.html`
- 在线访问：[https://2827173579.github.io/us-market-radar/predict_radar_5y.html](https://2827173579.github.io/us-market-radar/predict_radar_5y.html)
- 核心能力：
  - LightGBM 三阈值打标（止盈 / 持有 / 止损概率）+ 23 维量价/宏观特征；
  - 赛道归因、样本外校准胜率与盈亏比、Top 10 多空行动建议；
  - 5 年历史仿真、摩擦滑点校准与穿透式账务审计（见 `backtest_report_5y.html`）。

### 其他看板

| 看板 | 文件 | 说明 |
|---|---|---|
| 宏观大盘司令塔 | `macro_radar.html` | 16 只宏观/避险/对冲 ETF 水温、市场天气晴雨表 |
| 核心个股碎股雷达 | `stocks_radar.html` | 黄金坑斐波那契低吸带、碎股仓位计算 |
| 5 年回测战报 | `backtest_report_5y.html` | 2021-2026 全周期双轨制策略收益曲线与审计 |

---

## 🚀 本地一键启动指南

### 环境要求

- **Python ≥ 3.10**（推荐 3.11 / 3.12 / 3.13）
- 无需数据库 / Node.js（前端为单文件静态 HTML）
- 依赖：`pip install -r requirements.txt`（可选 `pyarrow` 用于 parquet 缓存读写）

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

# 3) 单次全量扫描并生成看板
python integrated_market_radar.py --once
```

启动后打开本地看盘地址：`http://127.0.0.1:8765`（`--once` 会拉起常驻看盘 API）。

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
3. 独立窗口全屏运行，附带底部 5 栏导航（自选 / 图表 / 战术 / 期权 / 持仓）。

---

## 🔑 环境变量（`.env`）

`.env` 与 `integrated_market_radar.py` 同目录，已被 `.gitignore` 排除，**请勿提交真实 Key**。
完整模板见 [`.env.example`](.env.example)。

| 变量 | 必需性 | 说明 |
|---|---|---|
| `FINNHUB_API_KEY` | 强烈建议 | Finnhub 实时报价（免费注册即可） |
| `PUSHPLUS_TOKEN` | 可选 | PushPlus 微信推送；不填则跳过微信 |
| `PUSHPLUS_CHANNEL` | 可选 | PushPlus 渠道（默认 `clawbot`） |
| `DISCORD_WEBHOOK_URL` | 可选 | Discord 哨兵主频道 |
| `DISCORD_CLAW_WEBHOOK_URL` | 可选 | 微信告警镜像频道（兼容 `DISCORD_ALERT_WEBHOOK_URL`） |
| `DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL` | 可选 | 防御因子背离专属频道 |
| `GH_TOKEN` | 云端/VPS 自动同步必需 | 有本仓库 `contents:write` 权限的 GitHub PAT |

---

## ☁️ Fork 后开启 GitHub Pages 与 Actions 24/7 自动轮询

> 目标：让 Fork 出来的仓库也能像上游一样 **云端 24/5 自动抓取 → 自动 commit/push → GitHub Pages 自动更新**，
> 本地电脑完全不开机也能在手机上看最新看板。

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
| `GH_TOKEN` | 可选（推荐） | 有本仓库 contents:write 权限的 PAT；用于让 push 触发 Pages 重新部署（GitHub 内置 `GITHUB_TOKEN` 的 push 通常不会触发新的 workflow） |
| `DISCORD_WEBHOOK_URL` 等 | 可选 | Discord 推送频道 |

> 如启用 `GH_TOKEN`，请把 `.github/workflows/market_radar.yml` 主步骤的 env 中
> `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` 改为 `GH_TOKEN: ${{ secrets.GH_TOKEN }}`（或两者并存，代码优先读取 `GH_TOKEN`）。

### Step 4 · 启用并手动触发一次

1. 进入 **Actions** 标签，若提示 workflow 被禁用，点击 **I understand my workflows, go ahead and enable them**；
2. 左侧选择 **US Market Radar 24-7 Engine** → **Run workflow**；
3. `duration_minutes` 建议填 `10`、`interval_seconds` 填 `120`，点击运行，确认第一轮成功 push；
4. 之后按 workflow 内置的 6 个 cron 时段自动运行（每次常驻 240 分钟，60~120 秒一轮）。

### Step 5 · 注意事项

- **不要同时运行本地守护进程与云端 workflow**：workflow 内置了「15 分钟本地优先避让」（若 15 分钟内有非 actions 提交则 schedule 跳过），但仍建议同一时间只保留一个写入源；
- 若日后迁移到自建 VPS：请参考 [docs/vps_deploy/README.md](docs/vps_deploy/README.md)，并**先停本地守护进程、禁用云端雷达 workflow**（保留 `pages-build-deployment`）；
- Actions 免费额度有限（私有仓库计费；公开仓库免费），请按需调整运行时长与轮询间隔。

---

## 📈 V3.1 / V3.2 双轨制策略与历史回测

本项目内置的量化体系（回测明细详见 `backtest_report_5y.html` 与历史策略报告）：

#### 轨道一：Track 1 (Pure Stock Dynamic Risk-Managed / 纯个股动态风控轨)

- 54 只高 Alpha 科技个股与赛道龙头，1.5% 账户风险平价动态头寸，最多同时持仓 4 只；
- EMA10/EMA20 移动止盈 + -6.8% 结构性硬止损；历史捕获 ASML +30%、AMD +37%、AVGO +22% 等大波段。

#### 轨道二：Track 2 (Flagship QQQ Base Enhancement / QQQ 核心底仓增强旗舰轨)

- QQQ 作为流动性停泊底座：QQQ > SMA200 时空闲现金全额停泊 QQQ；破位则清仓避险；
- 卫星个股黄金坑触发时自动卖出等额 QQQ 释放流动性，获利了结后回流底仓。

**回测口径**：2021-2026 共 1,194 个交易日，初始本金 $100,000，Firstrade 零佣金 + **1.5 bps** 流动性滑点校准。
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
├── integrated_market_radar.py   # 主守护进程：抓取→计算→生成 HTML→告警→自动 git 同步
├── discord_sentinel.py          # Discord 小豆泥量化哨兵（五梯队，覆盖主雷达 70 只）
├── ml_predictor_5y.py           # AI 预测与特征工程（LightGBM 可选依赖）
├── create_futu_terminal.py      # 富途风格终端 HTML 生成器
├── index.html                   # 富途终端（GitHub Pages 根路径）
├── macro_radar.html             # 宏观 ETF 水温看板
├── stocks_radar.html            # 个股黄金坑 / 风控看板
├── predict_radar_5y.html        # AI 5 年预测看板
├── backtest_report_5y.html      # 5 年回测战报
├── data_cache/                  # 预测快照与 5 年面板 parquet
├── sounds/                      # 告警音效
├── docs/vps_deploy/             # VPS 部署备用脚本（当前未启用）
├── 1~5 号 bat / vbs             # Windows 一键脚本
├── test_*.py                    # 测试与对抗性验证套件
├── .env.example                 # 环境变量模板（唯一入库的环境配置）
├── user_portfolio.example.json  # 持仓配置模板（复制为 user_portfolio.json 使用）
└── AGENTS.md                    # 面向 AI 编程工具的项目导航与协作规范
```

---

## 🧪 测试

```bash
# Discord 哨兵 + 工程约束测试（14 项，当前 14/14 PASS）
python -m pytest test_discord_sentinel.py -q

# V3.2 熔断场景
python test_v32_circuit_breaker.py

# 5 年回测端到端（需要本地 .backtest_5y_cache.pkl 缓存）
python test_e2e_v31.py
```

---

## 🔒 安全与隐私

- `.env` 永不入库；`.env.example` 仅含占位符；
- 个人持仓请写入本地 `user_portfolio.json`（参考 `user_portfolio.example.json`），开源/分享前请自行脱敏；
- 运行日志、临时缓存、`.radar_daemon.log` 等均已加入 `.gitignore`；
- 若 Key 曾泄露：立即到对应平台吊销并轮换，必要时使用 `git filter-repo` 清理历史。

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

> ⚠️ 不要在 JSON 顶层添加 `_comment` 之类非股票代码的键：程序会把它们当成标的处理。说明信息请写在
> 每个标的的 `note` 字段中（如上例），或直接参考本 README。

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
