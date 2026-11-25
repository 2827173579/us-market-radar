# 币安 77 只美股/ETF 做空通道只读盘点报告

> 生成时间：`2026-11-25 17:22:02`（纯只读探测，零下单、零私钥签名、零改动线上交易逻辑与生产账本）

## 0. 核心结论总览

- **币安 TradFi USDT 永续总上架**：`245` 个合约（严格过滤 `contractType=TRADIFI_PERPETUAL` / `underlyingSubType=TradFi`，已剔除加密货币同名碰撞：`DIAUSDT`、`STXUSDT`）。
- **77 只监控池 TradFi 永续直接覆盖**：**`65 / 77`（`84.4%`）**，中位实时盘口点差 **`2.2 bps`**（范围 `0.14 ~ 24.55 bps`），支持 1x~低倍无日度复利损耗的精准单标的做空。
- **币安美股清单（共 `7960` 只）反向 ETF 覆盖**：主流板块/宽基/美债反向 ETF **`14 / 14`** 全覆盖；核心个股反向 ETF **`22`** 只（覆盖 `14` 只高弹性核心个股，如 `NVDA/TSLA/MSTR/COIN/AMD/PLTR/META/AMZN/MSFT/AAPL/GOOGL/TSM/MU/AVGO`）。
- **分层架构统计（合计 77 只）**：**T1 TradFi 永续直空 `63` 只**（其中 `14` 只同时具备个股反向 ETF 备选） | **T2 专项反向 ETF / 同源永续 `3` 只**（`GLD->XAUUSDT`、`TLT->TBF/TBT/TMV`、`DIA->SDOW`） | **T3 板块反向 ETF 兜底映射 `8` 只**（`AXP, FLY, FN, FPS, LUNR, NASA, PEP, STX`） | **反向/对冲本体 `3` 只**（`SOXS, SQQQ, VIXY`）。
- **防同名碰撞护栏**：FAPI 中存在与美股代码同名的加密货币永续合约：`STXUSDT`（分类：`Infrastructure/Crypto`）、`DIAUSDT`（分类：`Infrastructure/Crypto`），实盘对接时**严禁**仅按 `baseAsset` 模糊匹配，必须强校验 `contractType == 'TRADIFI_PERPETUAL'`。

## 1. TradFi 美股 USDT 本位永续合约覆盖盘点（65/77）

| 标的 | 名称 | 分类 | 永续合约 | Bid | Ask | Mid/Mark | 实时点差 | 当前资金费率(8h) |
| :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| `SPY` | 标普500 | 大盘基准 | `SPYUSDT` | 712.95 | 712.96 | 712.96 | 0.1 bps | +0.0005% |
| `QQQ` | 纳指100ETF | 大盘基准 | `QQQUSDT` | 626.84 | 626.85 | 626.85 | 0.2 bps | +0.0048% |
| `MU` | 美光科技 | 存储芯片 | `MUUSDT` | 603.54 | 603.55 | 603.54 | 0.2 bps | +0.0119% |
| `META` | Meta | 社交AI | `METAUSDT` | 579.77 | 579.78 | 579.77 | 0.2 bps | +0.0089% |
| `MSFT` | 微软 | 云与软件 | `MSFTUSDT` | 490.04 | 490.05 | 490.05 | 0.2 bps | +0.0000% |
| `V` | Visa | 金融科技 | `VUSDT` | 417.50 | 417.51 | 417.50 | 0.2 bps | +0.0000% |
| `TSLA` | 特斯拉 | 物理AI | `TSLAUSDT` | 378.29 | 378.30 | 378.30 | 0.3 bps | +0.0000% |
| `AAPL` | 苹果 | 终端硬件 | `AAPLUSDT` | 352.55 | 352.56 | 352.56 | 0.3 bps | +0.0000% |
| `AVGO` | 博通 | 通信芯片 | `AVGOUSDT` | 293.48 | 293.49 | 293.49 | 0.3 bps | +0.0013% |
| `GOOGL` | Alphabet | 生态平台 | `GOOGLUSDT` | 274.31 | 274.32 | 274.31 | 0.4 bps | +0.0000% |
| `AMZN` | 亚马逊 | 云与电商 | `AMZNUSDT` | 249.23 | 249.24 | 249.24 | 0.4 bps | +0.0013% |
| `AMD` | AMD | 芯片算力 | `AMDUSDT` | 396.03 | 396.05 | 396.04 | 0.5 bps | +0.0000% |
| `XOM` | 埃克森美孚 | 传统能源 | `XOMUSDT` | 157.75 | 157.76 | 157.75 | 0.6 bps | -0.1861% |
| `PLTR` | Palantir | 企业AI | `PLTRUSDT` | 156.02 | 156.03 | 156.03 | 0.6 bps | +0.0000% |
| `NVDA` | NVIDIA | 芯片算力 | `NVDAUSDT` | 149.74 | 149.75 | 149.75 | 0.7 bps | +0.0000% |
| `SPCX` | SpaceX | 太空商业 | `SPCXUSDT` | 123.39 | 123.40 | 123.40 | 0.8 bps | +0.0000% |
| `LITE` | Lumentum | 光通信 | `LITEUSDT` | 469.14 | 469.18 | 469.16 | 0.9 bps | +0.0000% |
| `SKHY` | SK Hynix | 存储芯片 | `SKHYUSDT` | 108.03 | 108.04 | 108.03 | 0.9 bps | +0.0000% |
| `ORCL` | 甲骨文 | 云与软件 | `ORCLUSDT` | 106.19 | 106.20 | 106.19 | 0.9 bps | +0.0012% |
| `COIN` | Coinbase | 数字资产 | `COINUSDT` | 100.88 | 100.89 | 100.88 | 1.0 bps | +0.0000% |
| `KO` | 可口可乐 | 价值蓝筹 | `KOUSDT` | 98.61 | 98.62 | 98.62 | 1.0 bps | +0.0000% |
| `TSM` | 台积电 | 晶圆代工 | `TSMUSDT` | 342.19 | 342.23 | 342.21 | 1.2 bps | +0.0000% |
| `QCOM` | 高通 | 通信芯片 | `QCOMUSDT` | 160.14 | 160.16 | 160.15 | 1.2 bps | +0.0074% |
| `ANT` | Anthropic (IPO) | 云与软件 | `ANTUSDT` | 79.48 | 79.49 | 79.48 | 1.3 bps | +0.0000% |
| `SNDK` | 闪迪 | 闪存存储 | `SNDKUSDT` | 74.82 | 74.83 | 74.82 | 1.3 bps | +0.0000% |
| `SOXS` | 3倍做空半导体 | 反向对冲 | `SOXSUSDT` | 67.90 | 67.91 | 67.91 | 1.5 bps | -0.0461% |
| `WDC` | 西部数据 | 数据存储 | `WDCUSDT` | 265.04 | 265.08 | 265.06 | 1.5 bps | +0.0000% |
| `MRVL` | Marvell | 通信芯片 | `MRVLUSDT` | 126.41 | 126.43 | 126.42 | 1.6 bps | +0.0018% |
| `INTC` | Intel | 芯片算力 | `INTCUSDT` | 53.16 | 53.17 | 53.16 | 1.9 bps | +0.0019% |
| `SQQQ` | 3倍做空纳指 | 反向对冲 | `SQQQUSDT` | 51.55 | 51.56 | 51.55 | 1.9 bps | -0.0235% |
| `KORU` | 3倍做多韩国 | 正向杠杆 | `KORUUSDT` | 5.09 | 5.09 | 5.09 | 2.0 bps | +0.0506% |
| `MCD` | 麦当劳 | 价值蓝筹 | `MCDUSDT` | 282.56 | 282.62 | 282.59 | 2.1 bps | -0.0663% |
| `LLY` | 礼来 | 生物医药 | `LLYUSDT` | 1424.53 | 1424.84 | 1424.68 | 2.2 bps | +0.0000% |
| `NOW` | ServiceNow | 云与软件 | `NOWUSDT` | 89.81 | 89.83 | 89.82 | 2.2 bps | +0.0000% |
| `RKLB` | Rocket Lab | 太空商业 | `RKLBUSDT` | 44.69 | 44.70 | 44.70 | 2.2 bps | +0.0071% |
| `WMT` | 沃尔玛 | 传统消费 | `WMTUSDT` | 133.05 | 133.08 | 133.06 | 2.3 bps | -0.0140% |
| `NBIS` | Nebius | 算力租赁 | `NBISUSDT` | 82.78 | 82.80 | 82.79 | 2.4 bps | +0.0082% |
| `CRWV` | CoreWeave | 算力租赁 | `CRWVUSDT` | 40.74 | 40.75 | 40.75 | 2.5 bps | +0.0000% |
| `COHR` | Coherent | 光通信 | `COHRUSDT` | 192.66 | 192.71 | 192.69 | 2.6 bps | +0.0000% |
| `MSTR` | 微策略 | 数字资产 | `MSTRUSDT` | 76.08 | 76.10 | 76.09 | 2.6 bps | +0.0000% |
| `IWM` | 罗素2000 | 小盘弹性 | `IWMUSDT` | 265.72 | 265.79 | 265.75 | 2.6 bps | +0.0000% |
| `AAOI` | AOI | 光通信 | `AAOIUSDT` | 36.71 | 36.72 | 36.72 | 2.7 bps | +0.0000% |
| `NKE` | 耐克 | 可选消费 | `NKEUSDT` | 35.66 | 35.67 | 35.66 | 2.8 bps | -0.0030% |
| `GLW` | 康宁 | 光通信 | `GLWUSDT` | 98.58 | 98.61 | 98.59 | 3.0 bps | +0.0000% |
| `DELL` | 戴尔科技 | 服务器ODM | `DELLUSDT` | 294.87 | 294.96 | 294.91 | 3.1 bps | +0.0000% |
| `AXTI` | AXTI | 光通信 | `AXTIUSDT` | 30.39 | 30.40 | 30.39 | 3.3 bps | +0.0000% |
| `BE` | Bloom Energy | 能源电力 | `BEUSDT` | 149.82 | 149.87 | 149.84 | 3.3 bps | +0.0014% |
| `SOXL` | 3倍做多半导体 | 正向杠杆 | `SOXLUSDT` | 29.04 | 29.05 | 29.05 | 3.4 bps | +0.0322% |
| `MRNA` | Moderna | 生物医药 | `MRNAUSDT` | 223.07 | 223.16 | 223.12 | 4.0 bps | +0.0000% |
| `ASTS` | AST SpaceMobile | 太空商业 | `ASTSUSDT` | 22.85 | 22.86 | 22.86 | 4.4 bps | +0.0000% |
| `AMAT` | 应用材料 | 制造设备 | `AMATUSDT` | 309.98 | 310.13 | 310.06 | 4.8 bps | +0.0173% |
| `IGV` | 软件科技ETF | 云与软件 | `IGVUSDT` | 92.54 | 92.59 | 92.56 | 5.4 bps | +0.0000% |
| `SOXX` | 费城半导体ETF | 半导体 | `SOXXUSDT` | 124.17 | 124.24 | 124.20 | 5.6 bps | +0.0095% |
| `CRDO` | Credo | 高速互联 | `CRDOUSDT` | 101.76 | 101.82 | 101.79 | 5.9 bps | +0.0000% |
| `SMH` | 半导体ETF | 半导体 | `SMHUSDT` | 413.00 | 413.27 | 413.13 | 6.5 bps | +0.0000% |
| `CRWD` | CrowdStrike | AI安全 | `CRWDUSDT` | 203.78 | 203.92 | 203.85 | 6.9 bps | +0.0000% |
| `ASML` | 阿斯麦 | 制造设备 | `ASMLUSDT` | 1295.25 | 1296.21 | 1295.73 | 7.4 bps | +0.0000% |
| `JPM` | 摩根大通 | 传统金融 | `JPMUSDT` | 316.40 | 316.65 | 316.52 | 7.9 bps | +0.0000% |
| `CAT` | 卡特彼勒 | 传统工业 | `CATUSDT` | 529.56 | 530.03 | 529.79 | 8.9 bps | +0.0000% |
| `HPE` | 慧与科技 | 服务器ODM | `HPEUSDT` | 39.05 | 39.09 | 39.07 | 10.2 bps | +0.0000% |
| `VST` | Vistra Energy | 能源电力 | `VSTUSDT` | 117.97 | 118.10 | 118.03 | 11.0 bps | +0.0000% |
| `VRT` | Vertiv | 液冷散热 | `VRTUSDT` | 167.38 | 167.61 | 167.50 | 13.7 bps | -0.0373% |
| `OKLO` | Oklo | 核能电力 | `OKLOUSDT` | 18.67 | 18.70 | 18.69 | 16.1 bps | +0.0000% |
| `APLD` | Applied Digital | 算力租赁 | `APLDUSDT` | 11.70 | 11.72 | 11.71 | 17.1 bps | +0.0000% |
| `CEG` | 星座能源 | 核能电力 | `CEGUSDT` | 240.02 | 240.61 | 240.31 | 24.6 bps | +0.0000% |

**未上线 TradFi 永续的 12 只监控标的**：`AXP, DIA, FLY, FN, FPS, GLD, LUNR, NASA, PEP, STX, TLT, VIXY`。
（补充：黄金现货 TradFi 永续 `XAUUSDT` 已上线，Bid `3536.52` / Ask `3536.53` / 点差 `0.0 bps` / 资金费率 `+0.0031%`，可直接作为 `GLD` 的 1x 做空通道。）

## 2. 板块 / 宽基 / 美债反向 ETF 盘口盘点（14/14 已上架）

| 反向 ETF | 追踪方向/底层 | 倍数 | 盘前盘后 | 夜盘 | Bid | Ask | Mid | 实时点差 | 说明 |
| :--- | :--- | :---: | :---: | :---: | ---: | ---: | ---: | ---: | :--- |
| `SOXS` | 半导体 (ICE Semi) | -3x | ✅ | ✅ | 67.93 | 67.95 | 67.94 | 2.9 bps | 半导体三倍反向，费半/AI芯片/光通信核心做空工具 |
| `SQQQ` | 纳斯达克100 (QQQ) | -3x | ✅ | ✅ | 51.43 | 51.44 | 51.44 | 1.9 bps | 纳指三倍反向，科技成长核心做空工具 |
| `QID` | 纳斯达克100 (QQQ) | -2x | ✅ | ✅ | 70.30 | 70.34 | 70.32 | 5.7 bps | 纳指两倍反向，损耗低于 SQQQ |
| `PSQ` | 纳斯达克100 (QQQ) | -1x | ✅ | ✅ | 29.07 | 29.08 | 29.07 | 3.4 bps | 纳指一倍反向，适合波段对冲 |
| `SPXU` | 标普500 (SPY) | -3x | ✅ | ✅ | 41.70 | 41.79 | 41.75 | 21.6 bps | 标普三倍反向，大盘系统性下跌工具 |
| `SPXS` | 标普500 (SPY) | -3x | ✅ | ✅ | 29.63 | 29.64 | 29.63 | 3.4 bps | 标普三倍反向 (Direxion) |
| `SDS` | 标普500 (SPY) | -2x | ✅ | ✅ | 62.00 | 62.15 | 62.08 | 24.2 bps | 标普两倍反向 |
| `SH` | 标普500 (SPY) | -1x | ✅ | ✅ | 34.77 | 34.79 | 34.78 | 5.8 bps | 标普一倍反向，低损耗大盘对冲 |
| `SDOW` | 道琼斯30 (DIA) | -3x | ✅ | ✅ | 27.13 | 27.33 | 27.23 | 73.4 bps | 道指三倍反向，适配 DIA/蓝筹防御板块 |
| `TZA` | 罗素2000 (IWM) | -3x | ✅ | ✅ | 52.76 | 52.77 | 52.77 | 1.9 bps | 小盘股三倍反向，适配高 Beta 中小盘 |
| `RWM` | 罗素2000 (IWM) | -1x | ✅ | ✅ | 30.03 | 30.03 | 30.03 | 0.0 bps | 小盘股一倍反向 |
| `TBF` | 20Y+美债 (TLT) | -1x | ✅ | ✅ | 22.78 | 29.00 | 25.89 | 2402.5 bps | 长端美债一倍反向，利率上行对冲 |
| `TBT` | 20Y+美债 (TLT) | -2x | ✅ | ✅ | 37.10 | 38.76 | 37.93 | 437.6 bps | 长端美债两倍反向 |
| `TMV` | 20Y+美债 (TLT) | -3x | ✅ | ✅ | 42.66 | 42.67 | 42.66 | 2.3 bps | 长端美债三倍反向 |

## 3. 核心高弹性个股反向 ETF 盘口盘点（22 只已上架，覆盖 14 只个股）

| 正股 | 个股反向 ETF | 倍数 | 发行方/说明 | 盘前盘后 | 夜盘 | Bid | Ask | Mid | 实时点差 | 同标的 TradFi 永续点差 |
| :--- | :--- | :---: | :--- | :---: | :---: | ---: | ---: | ---: | ---: | ---: |
| `NVDA` | `NVDQ` | -2x | T-Rex 2X Inverse NVDA | ✅ | ✅ | 10.39 | 10.45 | 10.42 | 57.6 bps | 0.7 bps |
| `NVDA` | `NVD` | -2x | GraniteShares 2x Short NVDA | ✅ | ✅ | 4.66 | 4.69 | 4.68 | 64.2 bps | 0.7 bps |
| `NVDA` | `NVDS` | -1.5x | Tradr 1.5X Short NVDA | ✅ | ✅ | 23.42 | 23.76 | 23.59 | 144.1 bps | 0.7 bps |
| `NVDA` | `NVDD` | -1x | Direxion Daily NVDA Bear 1X | ✅ | ✅ | 37.70 | 37.96 | 37.83 | 68.7 bps | 0.7 bps |
| `TSLA` | `TSLQ` | -2x | Tradr 2X Short TSLA | ✅ | ✅ | 13.96 | 13.98 | 13.97 | 14.3 bps | 0.3 bps |
| `TSLA` | `TSLZ` | -2x | T-Rex 2X Inverse TSLA | ✅ | ✅ | 9.05 | 9.09 | 9.07 | 44.1 bps | 0.3 bps |
| `TSLA` | `TSDD` | -2x | GraniteShares 2x Short TSLA | ✅ | ✅ | 5.99 | 6.08 | 6.04 | 149.1 bps | 0.3 bps |
| `TSLA` | `TSLS` | -1x | Direxion Daily TSLA Bear 1X | ✅ | ✅ | 50.52 | 51.10 | 50.81 | 114.2 bps | 0.3 bps |
| `MSTR` | `MSTZ` | -2x | T-Rex 2X Inverse MSTR | ✅ | ✅ | 8.26 | 8.28 | 8.27 | 24.2 bps | 2.6 bps |
| `MSTR` | `SMST` | -2x | Defiance 2x Short MSTR | ✅ | ✅ | 38.99 | 39.36 | 39.17 | 94.4 bps | 2.6 bps |
| `COIN` | `CONI` | -2x | GraniteShares 2x Short COIN | ✅ | ✅ | 65.18 | 66.27 | 65.72 | 165.8 bps | 1.0 bps |
| `AMD` | `AMDD` | -1x | Direxion Daily AMD Bear 1X | ✅ | ✅ | 29.32 | 29.65 | 29.48 | 111.9 bps | 0.5 bps |
| `PLTR` | `PLTD` | -1x | Direxion Daily PLTR Bear 1X | ✅ | ✅ | 5.34 | 5.37 | 5.36 | 56.0 bps | 0.6 bps |
| `PLTR` | `PLTZ` | -2x | Defiance 2x Short PLTR | ✅ | ✅ | 9.22 | 9.36 | 9.29 | 150.7 bps | 0.6 bps |
| `META` | `METD` | -1x | Direxion Daily META Bear 1X | ✅ | ✅ | 15.62 | 15.67 | 15.64 | 32.0 bps | 0.2 bps |
| `AMZN` | `AMZD` | -1x | Direxion Daily AMZN Bear 1X | ✅ | ✅ | 8.55 | 8.57 | 8.56 | 23.4 bps | 0.4 bps |
| `MSFT` | `MSFD` | -1x | Direxion Daily MSFT Bear 1X | ✅ | ✅ | 10.31 | 10.38 | 10.35 | 67.7 bps | 0.2 bps |
| `AAPL` | `AAPD` | -1x | Direxion Daily AAPL Bear 1X | ✅ | ✅ | 9.71 | 9.76 | 9.73 | 51.4 bps | 0.3 bps |
| `GOOGL` | `GGLS` | -1x | Direxion Daily GOOGL Bear 1X | ✅ | ✅ | 70.71 | 71.63 | 71.17 | 129.3 bps | 0.4 bps |
| `TSM` | `TSMZ` | -1x | Direxion Daily TSM Bear 1X | ✅ | ✅ | 8.98 | 9.01 | 9.00 | 33.4 bps | 1.2 bps |
| `MU` | `MUD` | -1x | Direxion Daily MU Bear 1X | ✅ | ✅ | 13.71 | 13.80 | 13.76 | 65.4 bps | 0.2 bps |
| `AVGO` | `AVS` | -1x | Direxion Daily AVGO Bear 1X | ✅ | ✅ | 9.20 | 9.22 | 9.21 | 21.7 bps | 0.3 bps |

## 4. 77 标的币安做空通道分层对照表

| # | 标的 | 名称 | 分类 | 分层归属 | 推荐主做空通道 | 备选/板块兜底通道 | 备注 |
| ---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `AAOI` | AOI | 光通信 | T1: 1x TradFi 永续直空 | `AAOIUSDT` (2.7 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 2 | `AAPL` | 苹果 | 终端硬件 | T1: 1x TradFi 永续直空 | `AAPLUSDT` (0.3 bps · 费率 +0.0000%) | `AAPD`(-1x, 51.4 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 3 | `AMAT` | 应用材料 | 制造设备 | T1: 1x TradFi 永续直空 | `AMATUSDT` (4.8 bps · 费率 +0.0173%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 4 | `AMD` | AMD | 芯片算力 | T1: 1x TradFi 永续直空 | `AMDUSDT` (0.5 bps · 费率 +0.0000%) | `AMDD`(-1x, 111.9 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 5 | `AMZN` | 亚马逊 | 云与电商 | T1: 1x TradFi 永续直空 | `AMZNUSDT` (0.4 bps · 费率 +0.0013%) | `AMZD`(-1x, 23.4 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 6 | `ANT` | Anthropic (IPO) | 云与软件 | T1: 1x TradFi 永续直空 | `ANTUSDT` (1.3 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 7 | `APLD` | Applied Digital | 算力租赁 | T1: 1x TradFi 永续直空 | `APLDUSDT` (17.1 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 8 | `ASML` | 阿斯麦 | 制造设备 | T1: 1x TradFi 永续直空 | `ASMLUSDT` (7.4 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 9 | `ASTS` | AST SpaceMobile | 太空商业 | T1: 1x TradFi 永续直空 | `ASTSUSDT` (4.4 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 10 | `AVGO` | 博通 | 通信芯片 | T1: 1x TradFi 永续直空 | `AVGOUSDT` (0.3 bps · 费率 +0.0013%) | `AVS`(-1x, 21.7 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 11 | `AXP` | 美国运通 | 金融蓝筹 | T3: 板块反向 ETF 映射 | SH (-1x) / SPXU (-3x) / SDOW (-3x) | `SQQQ` / `SOXS` | 属于「金融蓝筹」，无直连永续，映射标普/道指反向 ETF |
| 12 | `AXTI` | AXTI | 光通信 | T1: 1x TradFi 永续直空 | `AXTIUSDT` (3.3 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 13 | `BE` | Bloom Energy | 能源电力 | T1: 1x TradFi 永续直空 | `BEUSDT` (3.3 bps · 费率 +0.0014%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 14 | `CAT` | 卡特彼勒 | 传统工业 | T1: 1x TradFi 永续直空 | `CATUSDT` (8.9 bps · 费率 +0.0000%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 15 | `CEG` | 星座能源 | 核能电力 | T1: 1x TradFi 永续直空 | `CEGUSDT` (24.6 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 16 | `COHR` | Coherent | 光通信 | T1: 1x TradFi 永续直空 | `COHRUSDT` (2.6 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 17 | `COIN` | Coinbase | 数字资产 | T1: 1x TradFi 永续直空 | `COINUSDT` (1.0 bps · 费率 +0.0000%) | `CONI`(-2x, 165.8 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 18 | `CRDO` | Credo | 高速互联 | T1: 1x TradFi 永续直空 | `CRDOUSDT` (5.9 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 19 | `CRWD` | CrowdStrike | AI安全 | T1: 1x TradFi 永续直空 | `CRWDUSDT` (6.9 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 20 | `CRWV` | CoreWeave | 算力租赁 | T1: 1x TradFi 永续直空 | `CRWVUSDT` (2.5 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 21 | `DELL` | 戴尔科技 | 服务器ODM | T1: 1x TradFi 永续直空 | `DELLUSDT` (3.1 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 22 | `DIA` | 道琼斯蓝筹 | 大盘基准 | T2: 专项反向 ETF | SDOW (-3x) / SH (-1x) / SPXU (-3x) | `SH`/`SPXU` | 道指专项反向 ETF（注意已排除加密货币 DIAUSDT） |
| 23 | `FLY` | Firefly Aerospace | 太空商业 | T3: 板块反向 ETF 映射 | SQQQ (-3x) / TZA (-3x) | `SQQQ` / `SOXS` | 属于「太空商业」，无直连永续，映射纳指/罗素小盘反向 ETF |
| 24 | `FN` | Fabrinet | 光通信 | T3: 板块反向 ETF 映射 | SOXS (-3x) / SQQQ (-3x) | `SQQQ` / `SOXS` | 属于「光通信」，无直连永续，统一映射半导体/纳指反向 ETF |
| 25 | `FPS` | Forgent Power | 能源电力 | T3: 板块反向 ETF 映射 | SH (-1x) / SPXU (-3x) / SDOW (-3x) | `SQQQ` / `SOXS` | 属于「能源电力」，无直连永续，映射标普/道指反向 ETF |
| 26 | `GLD` | 黄金现货ETF | 大宗商品 | T2: 同源商品永续替代 | `XAUUSDT` (0.0 bps · 费率 +0.0031%) | 暂无币安反向黄金 ETF | 黄金现货同源 TradFi 永续直接做空 |
| 27 | `GLW` | 康宁 | 光通信 | T1: 1x TradFi 永续直空 | `GLWUSDT` (3.0 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 28 | `GOOGL` | Alphabet | 生态平台 | T1: 1x TradFi 永续直空 | `GOOGLUSDT` (0.4 bps · 费率 +0.0000%) | `GGLS`(-1x, 129.3 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 29 | `HPE` | 慧与科技 | 服务器ODM | T1: 1x TradFi 永续直空 | `HPEUSDT` (10.2 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 30 | `IGV` | 软件科技ETF | 云与软件 | T1: 1x TradFi 永续直空 | `IGVUSDT` (5.4 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 31 | `INTC` | Intel | 芯片算力 | T1: 1x TradFi 永续直空 | `INTCUSDT` (1.9 bps · 费率 +0.0019%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 32 | `IWM` | 罗素2000 | 小盘弹性 | T1: 1x TradFi 永续直空 | `IWMUSDT` (2.6 bps · 费率 +0.0000%) | RWM (-1x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 33 | `JPM` | 摩根大通 | 传统金融 | T1: 1x TradFi 永续直空 | `JPMUSDT` (7.9 bps · 费率 +0.0000%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 34 | `KO` | 可口可乐 | 价值蓝筹 | T1: 1x TradFi 永续直空 | `KOUSDT` (1.0 bps · 费率 +0.0000%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 35 | `KORU` | 3倍做多韩国 | 正向杠杆 | T1: 1x TradFi 永续直空 | `KORUUSDT` (2.0 bps · 费率 +0.0506%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 36 | `LITE` | Lumentum | 光通信 | T1: 1x TradFi 永续直空 | `LITEUSDT` (0.9 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 37 | `LLY` | 礼来 | 生物医药 | T1: 1x TradFi 永续直空 | `LLYUSDT` (2.2 bps · 费率 +0.0000%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 38 | `LUNR` | Intuitive Machines | 太空商业 | T3: 板块反向 ETF 映射 | SQQQ (-3x) / TZA (-3x) | `SQQQ` / `SOXS` | 属于「太空商业」，无直连永续，映射纳指/罗素小盘反向 ETF |
| 39 | `MCD` | 麦当劳 | 价值蓝筹 | T1: 1x TradFi 永续直空 | `MCDUSDT` (2.1 bps · 费率 -0.0663%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 40 | `META` | Meta | 社交AI | T1: 1x TradFi 永续直空 | `METAUSDT` (0.2 bps · 费率 +0.0089%) | `METD`(-1x, 32.0 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 41 | `MRNA` | Moderna | 生物医药 | T1: 1x TradFi 永续直空 | `MRNAUSDT` (4.0 bps · 费率 +0.0000%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 42 | `MRVL` | Marvell | 通信芯片 | T1: 1x TradFi 永续直空 | `MRVLUSDT` (1.6 bps · 费率 +0.0018%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 43 | `MSFT` | 微软 | 云与软件 | T1: 1x TradFi 永续直空 | `MSFTUSDT` (0.2 bps · 费率 +0.0000%) | `MSFD`(-1x, 67.7 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 44 | `MSTR` | 微策略 | 数字资产 | T1: 1x TradFi 永续直空 | `MSTRUSDT` (2.6 bps · 费率 +0.0000%) | `MSTZ`(-2x, 24.2 bps), `SMST`(-2x, 94.4 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 45 | `MU` | 美光科技 | 存储芯片 | T1: 1x TradFi 永续直空 | `MUUSDT` (0.2 bps · 费率 +0.0119%) | `MUD`(-1x, 65.4 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 46 | `NASA` | NASA太空主题ETF | 太空商业 | T3: 板块反向 ETF 映射 | SQQQ (-3x) / TZA (-3x) | `SQQQ` / `SOXS` | 属于「太空商业」，无直连永续，映射纳指/罗素小盘反向 ETF |
| 47 | `NBIS` | Nebius | 算力租赁 | T1: 1x TradFi 永续直空 | `NBISUSDT` (2.4 bps · 费率 +0.0082%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 48 | `NKE` | 耐克 | 可选消费 | T1: 1x TradFi 永续直空 | `NKEUSDT` (2.8 bps · 费率 -0.0030%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 49 | `NOW` | ServiceNow | 云与软件 | T1: 1x TradFi 永续直空 | `NOWUSDT` (2.2 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 50 | `NVDA` | NVIDIA | 芯片算力 | T1: 1x TradFi 永续直空 | `NVDAUSDT` (0.7 bps · 费率 +0.0000%) | `NVDQ`(-2x, 57.6 bps), `NVD`(-2x, 64.2 bps), `NVDD`(-1x, 68.7 bps), `NVDS`(-1.5x, 144.1 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 51 | `OKLO` | Oklo | 核能电力 | T1: 1x TradFi 永续直空 | `OKLOUSDT` (16.1 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 52 | `ORCL` | 甲骨文 | 云与软件 | T1: 1x TradFi 永续直空 | `ORCLUSDT` (0.9 bps · 费率 +0.0012%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 53 | `PEP` | 百事可乐 | 价值蓝筹 | T3: 板块反向 ETF 映射 | SH (-1x) / SPXU (-3x) / SDOW (-3x) | `SQQQ` / `SOXS` | 属于「价值蓝筹」，无直连永续，映射标普/道指反向 ETF |
| 54 | `PLTR` | Palantir | 企业AI | T1: 1x TradFi 永续直空 | `PLTRUSDT` (0.6 bps · 费率 +0.0000%) | `PLTD`(-1x, 56.0 bps), `PLTZ`(-2x, 150.7 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 55 | `QCOM` | 高通 | 通信芯片 | T1: 1x TradFi 永续直空 | `QCOMUSDT` (1.2 bps · 费率 +0.0074%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 56 | `QQQ` | 纳指100ETF | 大盘基准 | T1: 1x TradFi 永续直空 | `QQQUSDT` (0.2 bps · 费率 +0.0048%) | PSQ (-1x) / QID (-2x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 57 | `RKLB` | Rocket Lab | 太空商业 | T1: 1x TradFi 永续直空 | `RKLBUSDT` (2.2 bps · 费率 +0.0071%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 58 | `SKHY` | SK Hynix | 存储芯片 | T1: 1x TradFi 永续直空 | `SKHYUSDT` (0.9 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 59 | `SMH` | 半导体ETF | 半导体 | T1: 1x TradFi 永续直空 | `SMHUSDT` (6.5 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 60 | `SNDK` | 闪迪 | 闪存存储 | T1: 1x TradFi 永续直空 | `SNDKUSDT` (1.3 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 61 | `SOXL` | 3倍做多半导体 | 正向杠杆 | T1: 1x TradFi 永续直空 | `SOXLUSDT` (3.4 bps · 费率 +0.0322%) | SOXS (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 62 | `SOXS` | 3倍做空半导体 | 反向对冲 | 反向/对冲本体 | `SOXSUSDT` (可双向) | 做多本标的即看空基准 | 本身为反向 ETF / 恐慌对冲工具 |
| 63 | `SOXX` | 费城半导体ETF | 半导体 | T1: 1x TradFi 永续直空 | `SOXXUSDT` (5.6 bps · 费率 +0.0095%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 64 | `SPCX` | SpaceX | 太空商业 | T1: 1x TradFi 永续直空 | `SPCXUSDT` (0.8 bps · 费率 +0.0000%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 65 | `SPY` | 标普500 | 大盘基准 | T1: 1x TradFi 永续直空 | `SPYUSDT` (0.1 bps · 费率 +0.0005%) | SH (-1x) / SDS (-2x) / SPXU (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 66 | `SQQQ` | 3倍做空纳指 | 反向对冲 | 反向/对冲本体 | `SQQQUSDT` (可双向) | 做多本标的即看空基准 | 本身为反向 ETF / 恐慌对冲工具 |
| 67 | `STX` | 希捷科技 | 数据存储 | T3: 板块反向 ETF 映射 | SOXS (-3x) / SQQQ (-3x) | `SQQQ` / `SOXS` | 属于「数据存储」，无直连永续，统一映射半导体/纳指反向 ETF |
| 68 | `TLT` | 20年+美债 | 利率债券 | T2: 专项反向 ETF | TBF (-1x) / TBT (-2x) / TMV (-3x) | - | 20Y+ 长端美债专项反向 ETF |
| 69 | `TSLA` | 特斯拉 | 物理AI | T1: 1x TradFi 永续直空 | `TSLAUSDT` (0.3 bps · 费率 +0.0000%) | `TSLQ`(-2x, 14.3 bps), `TSLZ`(-2x, 44.1 bps), `TSLS`(-1x, 114.2 bps), `TSDD`(-2x, 149.1 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 70 | `TSM` | 台积电 | 晶圆代工 | T1: 1x TradFi 永续直空 | `TSMUSDT` (1.2 bps · 费率 +0.0000%) | `TSMZ`(-1x, 33.4 bps) | 永续 1x 直空 + 个股反向 ETF 双通道 |
| 71 | `V` | Visa | 金融科技 | T1: 1x TradFi 永续直空 | `VUSDT` (0.2 bps · 费率 +0.0000%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 72 | `VIXY` | 短期恐慌指数 | 黑天鹅对冲 | 反向/对冲本体 | 现货多头即做空大盘 | 做多本标的即看空基准 | 本身为反向 ETF / 恐慌对冲工具 |
| 73 | `VRT` | Vertiv | 液冷散热 | T1: 1x TradFi 永续直空 | `VRTUSDT` (13.7 bps · 费率 -0.0373%) | SQQQ (-3x) / TZA (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 74 | `VST` | Vistra Energy | 能源电力 | T1: 1x TradFi 永续直空 | `VSTUSDT` (11.0 bps · 费率 +0.0000%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 75 | `WDC` | 西部数据 | 数据存储 | T1: 1x TradFi 永续直空 | `WDCUSDT` (1.5 bps · 费率 +0.0000%) | SOXS (-3x) / SQQQ (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 76 | `WMT` | 沃尔玛 | 传统消费 | T1: 1x TradFi 永续直空 | `WMTUSDT` (2.3 bps · 费率 -0.0140%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
| 77 | `XOM` | 埃克森美孚 | 传统能源 | T1: 1x TradFi 永续直空 | `XOMUSDT` (0.6 bps · 费率 -0.1861%) | SH (-1x) / SPXU (-3x) / SDOW (-3x) | 优先 1x TradFi 永续直空，无杠杆损耗 |
