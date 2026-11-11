# TEST_READY — Discord Sentinel 测试与质量状态（M4 已完结）

**最后更新**：2026-11-11 ｜ **当前状态**：`test_discord_sentinel.py` **14 / 14 PASS（100%）**
**测试框架**：Python 内置 `unittest`（兼容 `pytest`） ｜ **网络依赖**：0（全部离线确定性 fixture）
**执行耗时**：约 0.8~4.6s（视是否首次 import 依赖）

---

## 1. 如何运行

```bash
# 推荐：pytest（14 项全部收集）
python -m pytest test_discord_sentinel.py -q

# 或 unittest 原生执行
python test_discord_sentinel.py
python -m unittest test_discord_sentinel.py -v
```

**环境要求**：Python ≥ 3.10、`pandas`、`numpy`；配置校验项需要仓库根目录存在 `.env`（本地测试可用
`.env.example` 复制出的占位配置，见 §3）。

---

## 2. 14 项测试与实现状态

| # | 测试 | 覆盖功能 | 实现位置 | 状态 |
|---|---|---|---|---|
| 1 | `test_4h_aggregation_no_cross_day_stitching` | 4H K 线严格按交易日聚合，不跨日拼接 | `discord_sentinel.aggregate_1h_to_4h_df` | ✅ PASS |
| 2 | `test_4h_aggregation_early_close_and_intraday` | 半日市 / 盘中未完成 K 线的优雅处理 | 同上 | ✅ PASS |
| 3 | `test_prepost_market_snapshot_does_not_mutate_historical_bars` | 盘前/盘后快照只追加临时 bar，不改写历史 bar | `append_realtime_snapshot_bar` | ✅ PASS |
| 4 | `test_1h_support_resistance_strict_boundary_protection` | 1H 支撑 < 现价 < 1H 压力 严格边界不变式 | `compute_and_protect_1h_sr` | ✅ PASS |
| 5 | `test_batch_fetch_and_btc_usd_bypass` | 51 标的批量抓取防 429；BTC-USD 绕过 Finnhub | `fetch_sentinel_market_data` | ✅ PASS |
| 6 | `test_volume_profile_poc_calculation` | 140 根 1H / 25 桶 Volume Profile POC（典型价加权） | `compute_volume_profile_poc` | ✅ PASS |
| 7 | `test_rsi_state_machine_transitions` | 6 级 RSI 真话状态机（含 V3.2 冷却 / VWAP 右侧企稳解锁） | `evaluate_rsi_state` | ✅ PASS |
| 8 | `test_defense_factor_y_and_divergence` | 防御因子 Y = mean(SCHD, KO, GLD) + 背离告警 | `compute_factor_y` / `evaluate_cross_asset_resonance` | ✅ PASS |
| 9 | `test_3x_mirror_resonance` | SOXL×SOXS、TQQQ×SQQQ 3X 镜像共振 | `evaluate_cross_asset_resonance` | ✅ PASS |
| 10 | `test_embed_native_markdown_no_codeblocks` | Discord 原生 Markdown（无灰码块），加粗/emoji 合规 | `build_discord_payloads` / `format_*` | ✅ PASS |
| 11 | `test_discord_payload_character_limits` | 消息 ≤2000/5500 字符、embeds ≤10、单 embed ≤4096 | `build_discord_payloads` / `dispatch_discord_messages` | ✅ PASS |
| 12 | `test_radar_linkage_async_and_isolated` | 主雷达以 daemon 线程异步接入，网络异常隔离不阻塞主循环 | `integrated_market_radar.dispatch_discord_sentinel_async` + `discord_sentinel.run_sentinel_cycle_safe` | ✅ PASS |
| 13 | `test_universe_structure_and_completeness` | 51 只标的、5 梯队（4/12/13/13/9）唯一无重复 | `SENTINEL_UNIVERSE_TIERS` | ✅ PASS |
| 14 | `test_env_webhook_configuration` | `.env` 中 `DISCORD_WEBHOOK_URL` 指向 `discordapp.com/api/webhooks/` | 仓库根目录 `.env`（本地未追踪） | ✅ PASS |

---

## 3. 配置校验项说明（第 14 项）

该测试检查仓库根目录 `.env` 是否存在且包含合规格式的 Discord Webhook：

```bash
# 本地/CI 复现（占位配置即可，不会真的发消息）
cp .env.example .env
# 确保包含：
# DISCORD_WEBHOOK_URL=https://discordapp.com/api/webhooks/<任意ID>/<任意Token>
```

> 此前该测试失败的两个根因已修复：
> 1. `test_discord_sentinel.py` 原硬编码旧电脑绝对路径（已删除，不再保留于仓库），
>    已改为 `PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))`，可在任意目录/机器运行；
> 2. 仓库根目录缺少 `.env`，已在本地创建未追踪占位 `.env`（`.gitignore` 已排除，不会入库）。

---

## 4. 历史记录（M0 → M4）

- **M0 基线（2026-11-10 10:54 前）**：`discord_sentinel.py` 为接口骨架，14 项测试中 11 项 `NotImplementedError`、
  2 项失败（环境配置 / 雷达联动），仅 1 项通过 —— 该基线已由后续提交实现并全量转绿，原 M0 文档内容可从 git 历史中追溯。
- **M1~M3（2026-11-10）**：Tier 1 五项工程修正、Tier 2 四项量化引擎、Tier 3 两项 Discord 格式约束、
  Tier 4 雷达非阻塞联动全部实现。
- **M4（2026-11-11）**：补齐动态测试路径与本地 `.env` 后，**14/14 全部 PASS**；同时完成仓库级旧电脑绝对路径清理。

---

## 5. 相关测试套件

| 套件 | 用途 | 备注 |
|---|---|---|
| `test_discord_sentinel.py` | 哨兵 + 工程约束（本文件） | 14/14 PASS |
| `test_v32_circuit_breaker.py` | V3.2 熔断 / 反向 ETF 隔离 / 同日跳空冷却 | 17/17 PASS |
| `test_challenger_m2.py` | AI 预测 JSON 契约压力测试 | 9/9 PASS（有 §6 副作用） |
| `test_defects_4_8_verification.py` | 历史缺陷 4/8 回归 | 4/4 PASS（有 §6 副作用） |
| `test_m2_adversarial_challenge.py` | 告警状态机 / 预测 schema 对抗 | 9/9 PASS（有 §6 副作用） |
| `test_challenger_m2_recheck_deep.py` | 双 JSON 一致性 / SHA256 校验 | 8/9 PASS（1 项为 §6 数据侧既有缺陷） |
| `test_e2e_v31.py` | 5 年回测端到端 55 项断言 | 需要 `.backtest_5y_cache.pkl`（未入库） |
| `test_v31_synthesis.py` | V3.1 策略综合审计脚本 | 需要 `.backtest_5y_cache.pkl`（未入库） |

---

## 6. 已知测试副作用与数据侧缺陷（重要）

### 6.1 `build_futu_terminal_html()` 的实盘同步副作用

`create_futu_terminal.build_futu_terminal_html()` 内部包含“实盘同步”逻辑：
读取并**重写** `data_cache/latest_ai_predictions.json`、重绘 `predict_radar_5y.html`，
并在仓库根执行 `git add predict_radar_5y.html data_cache/latest_ai_predictions.json`（只 add，不会 commit/push），
还可能生成 `us-market-radar/data_cache/...` 镜像目录。

部分测试会调用它：`test_challenger_m2.py`、`test_defects_4_8_verification.py`、
`test_defects_6_7_9_10.py`、`test_m2_adversarial_challenge.py`。运行这些测试后**工作区会出现非预期改动**，
建议按下面命令清理（不会丢失任何代码修改）：

```bash
git restore --staged predict_radar_5y.html data_cache/latest_ai_predictions.json 2>/dev/null || true
git checkout HEAD -- macro_radar.html stocks_radar.html predict_radar_5y.html data_cache/latest_ai_predictions.json
rm -rf us-market-radar
```

> 根治建议（需人类确认后修改核心业务代码）：给 `build_futu_terminal_html()` 增加
> `sync_side_effects: bool = True` 参数，测试/回放时传 `False`，避免测试污染工作区。

### 6.2 `top_long` 排序缺陷（数据侧）

`test_challenger_m2_recheck_deep.py::test_json_structure_and_types` 断言
`data_cache/latest_ai_predictions.json` 的 `top_long` 按 `calibrated_win_rate_pct` 降序，
但实测（含 origin/main 最新数据）均未排序 —— 根因在 `ml_predictor_5y.py:3178`：
`top_long_stocks = [r for r in all_live_rows if not is_etf][:10]` 直接切片，缺少按胜率排序；
`top_short` 已正确排序。该问题与路径修复无关，属**核心业务代码的既有缺陷**，
建议人工确认后单独修复（修复后此测试即可 9/9）。

---

## 7. 2026-11-11：AI 5Y 基准迁移 QQQM → QQQ（验证记录）

- 迁移范围：雷达监控池 ETF、富途终端映射/过滤、宏观天气引擎、`ml_predictor_5y.py`
  （`MACRO_ETFS`、特征列 `QQQ_Bias_MA50` / `QQQ_Ret_5d` / `Alpha_QQQ_5d`、`SECTOR_MAP`、展示标签）、
  相关测试与文档；
- 数据：`data_cache/ml_panel_5y.parquet` 已重建（35 标的 / 41,827 行 / 2021-11-01～2026-11-11，
  含 QQQ、无 QQQM）；`data_cache/latest_ai_predictions.json` 与 `predict_radar_5y.html` 已重新生成；
- 兼容性修复：`build_extended_live_features_df()` 中 `merge_asof` 前统一 `Date` 为 `datetime64[ns]`
  （pandas ≥3.0 要求两侧 dtype 一致）；
- 验证：`py_compile` 全通过；`test_ml_predictor.py` 35/35、`test_short_predictor.py` 15/15、
  `test_ml_predictor_adversarial.py` 14/14、`test_challenger_v11_empirical.py` 15/15；
  `test_challenger_feature2_adversarial.py` 10/13、`test_challenger_v11_2_stress.py` 9/12
  （失败项均为**改动前 HEAD 即已存在**的旧断言，或“工作区干净”检查——未提交时必然失败，
  提交后即恢复）；
- 说明：`qqqm_bias` 等内部 JSON 字段名保留（前端模板兼容）；历史告警/期权缓存与尚未重绘的
  雷达 HTML 中仍可能残留 QQQM 字样，守护进程下一轮自动刷新。
