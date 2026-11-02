"""
AI Multi-Sector 5Y US Market Predictor (`ml_predictor_5y.py`)
Qlib-style Rolling Time-Series Cross-Sectional Panel Data System

Architecture & Design:
1. 35-Symbol Universe: 28 target stocks across 7 core sectors + 7 macro benchmark ETFs.
2. Long Panel Format [Date, Ticker, Sector]: Independent per-stock technical computation
   and left-join macro weather features; strictly prohibits wide-table global dropna.
3. 23 Strictly Causal Features: 15 stock technicals, 2 cross-asset alphas, 6 macro broadcasters.
4. Persistent Parquet Caching: Cached to data_cache/ml_panel_5y.parquet via pyarrow.
5. Triple Barrier Labeling Engine: NEXT_OPEN execution, dynamic ATR barriers, conservative stop priority.
"""

import os
import sys
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import time
import argparse
from typing import List, Dict, Tuple, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf

# ==============================================================================
# 1. 标的池与赛道定义 (35-Symbol Universe & Sector Mapping)
# ==============================================================================

SECTOR_MAP = {
    # 1. 半导体与设备 (Semiconductors & Equipment)
    'NVDA': 'Semiconductors',
    'TSM': 'Semiconductors',
    'AMD': 'Semiconductors',
    'AVGO': 'Semiconductors',
    'ASML': 'Semiconductors',
    'AMAT': 'Semiconductors',
    # 2. 存储芯片 (Memory & Storage)
    'MU': 'Memory',
    'WDC': 'Memory',
    'STX': 'Memory',
    # 3. 光通信与互联 (Optical & Interconnect)
    'COHR': 'Optical_Interconnect',
    'LITE': 'Optical_Interconnect',
    'CRDO': 'Optical_Interconnect',
    'MRVL': 'Optical_Interconnect',
    'AAOI': 'Optical_Interconnect',
    # 4. 数据中心能源电力 (Power & Clean Energy)
    'VST': 'Energy_Power',
    'CEG': 'Energy_Power',
    'BE': 'Energy_Power',
    # 5. 企业软件与 AI SaaS (Enterprise SaaS & Cloud)
    'PLTR': 'Enterprise_SaaS',
    'MSFT': 'Enterprise_SaaS',
    'NOW': 'Enterprise_SaaS',
    'ORCL': 'Enterprise_SaaS',
    # 6. Neo-Cloud 算力租赁 (Neo-Cloud & Compute)
    'NBIS': 'Neo_Cloud',
    'APLD': 'Neo_Cloud',
    'CRWV': 'Neo_Cloud',
    # 7. 加密币股代理 (Crypto Proxy)
    'MSTR': 'Crypto_Proxy',
    'COIN': 'Crypto_Proxy',
    'MARA': 'Crypto_Proxy',
    'IBIT': 'Crypto_Proxy',
}

SECTOR_CHINESE_MAP = {
    'Semiconductors': '半导体与设备',
    'Memory': '存储芯片',
    'Optical_Interconnect': '光通信与互联',
    'Energy_Power': '数据中心能源电力',
    'Enterprise_SaaS': '企业软件与 AI SaaS',
    'Neo_Cloud': 'Neo-Cloud 算力租赁',
    'Crypto_Proxy': '加密币股代理',
}

MACRO_ETFS = ['QQQM', 'SPY', 'SMH', 'IGV', 'TLT', 'VIXY', 'GLD']
TARGET_STOCKS = list(SECTOR_MAP.keys())
ALL_SYMBOLS = TARGET_STOCKS + MACRO_ETFS  # Exactly 35 symbols

# 23 维严格 T 日因果特征清单
STOCK_FEATURE_COLS = [
    'Bias_MA20',
    'Bias_MA50',
    'Bias_SMA200',
    'EMA10_20_Spread',
    'Fib_Pos_60',
    'RSI_14',
    'RSI_Slope_3d',
    'ATR_Pct',
    'Ret_1d',
    'Ret_3d',
    'Ret_5d',
    'Ret_10d',
    'Ret_20d',
    'Vol_Ratio_20',
    'Bar_Strength',
]

CROSS_ASSET_FEATURE_COLS = [
    'Alpha_QQQM_5d',
    'Alpha_SMH_20d',
]

MACRO_FEATURE_COLS = [
    'QQQM_Bias_MA50',
    'SMH_Bias_MA50',
    'TLT_Bias_MA50',
    'VIXY_Bias_MA20',
    'VIXY_Pos_60',
    'SMH_IGV_Ratio_Bias',
]

FEATURE_COLS = STOCK_FEATURE_COLS + CROSS_ASSET_FEATURE_COLS + MACRO_FEATURE_COLS
assert len(FEATURE_COLS) == 23, f"Expected exactly 23 features, got {len(FEATURE_COLS)}"


# ==============================================================================
# 2. 数据获取与 Parquet 持久化缓存 (Data Ingestion & Persistent Caching)
# ==============================================================================

def _resolve_cache_path(cache_file: str) -> str:
    """解析缓存文件绝对路径并确保所在目录存在"""
    if not os.path.isabs(cache_file):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cache_path = os.path.join(base_dir, cache_file)
    else:
        cache_path = cache_file
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    return cache_path


def _fetch_single_ticker(ticker: str, start_date: str = '2021-11-01', max_retries: int = 3) -> Optional[pd.DataFrame]:
    """通过 yfinance 拉取单只标的的历史日线数据，自动扁平化多级列名"""
    for attempt in range(max_retries):
        try:
            df = yf.download(ticker, start=start_date, auto_adjust=True, progress=False)
            if df is None or len(df) == 0:
                time.sleep(0.5)
                continue

            # 处理 yfinance MultiIndex 列名
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.reset_index()
            # 兼容 Datetime 或 Date 列名
            if 'Date' not in df.columns and 'Datetime' in df.columns:
                df = df.rename(columns={'Datetime': 'Date'})

            if 'Date' not in df.columns:
                time.sleep(0.5)
                continue

            # 统一为无时区规范日期
            df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
            df['Ticker'] = ticker

            required_cols = ['Date', 'Ticker', 'Open', 'High', 'Low', 'Close', 'Volume']
            missing = set(required_cols) - set(df.columns)
            if missing:
                time.sleep(0.5)
                continue

            df = df[required_cols].copy()
            df = df.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
            df = df[(df['Open'] > 0) & (df['Close'] > 0) & (df['High'] > 0) & (df['Low'] > 0)]
            df = df.sort_values('Date').reset_index(drop=True)

            if len(df) >= 20:
                return df
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[!] 拉取 {ticker} 失败: {e}")
            time.sleep(0.5)
    return None


def fetch_or_load_panel_data(
    cache_file: str = 'data_cache/ml_panel_5y.parquet',
    force_refresh: bool = False,
    start_date: str = '2021-11-01',
    max_workers: int = 8
) -> pd.DataFrame:
    """
    加载本地 Parquet 缓存或通过 yfinance 多线程拉取 35 只标的过去的 5 年历史行情。
    
    返回:
        pd.DataFrame: 包含 ['Date', 'Ticker', 'Open', 'High', 'Low', 'Close', 'Volume'] 的长表原始面板。
    """
    full_cache_path = _resolve_cache_path(cache_file)

    if os.path.exists(full_cache_path) and not force_refresh:
        try:
            print(f"[*] 📦 检测到本地 5 年行情缓存: {full_cache_path}")
            df = pd.read_parquet(full_cache_path, engine='pyarrow')
            df['Date'] = pd.to_datetime(df['Date'])
            tickers_in_cache = set(df['Ticker'].unique())
            missing_tickers = set(ALL_SYMBOLS) - tickers_in_cache
            if not missing_tickers and len(df) > 30000:
                print(f"[+] ✅ 缓存完整性校验通过: 共 {len(tickers_in_cache)} 只标的, {len(df)} 条交易日记录")
                return df
            else:
                print(f"[!] 缓存数据缺失标的: {missing_tickers}，重新拉取全量数据...")
        except Exception as e:
            print(f"[!] 读取缓存异常 ({e})，重新拉取...")

    print(f"[*] 🚀 启动多线程拉取 {len(ALL_SYMBOLS)} 只标的历史行情 (自 {start_date} 至今)...")
    ticker_dfs = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_fetch_single_ticker, tk, start_date): tk for tk in ALL_SYMBOLS}
        for fut in as_completed(future_map):
            tk = future_map[fut]
            try:
                res_df = fut.result()
                if res_df is not None and len(res_df) > 0:
                    ticker_dfs.append(res_df)
                    print(f"  [+] {tk:5s} 抓取成功 ({len(res_df)} 交易日, 起始 {res_df['Date'].min().strftime('%Y-%m-%d')})")
                else:
                    print(f"  [-] {tk:5s} 抓取失败或数据不足！")
            except Exception as e:
                print(f"  [-] {tk:5s} 抓取异常: {e}")

    if not ticker_dfs:
        raise RuntimeError("CRITICAL: 未能成功抓取任何标的行情数据！")

    combined_df = pd.concat(ticker_dfs, ignore_index=True)
    combined_df = combined_df.sort_values(['Date', 'Ticker']).reset_index(drop=True)

    # 持久化保存至 Parquet
    print(f"[*] 💾 正在将全量面板持久化写入: {full_cache_path} (pyarrow)...")
    combined_df.to_parquet(full_cache_path, engine='pyarrow', index=False)
    file_size_mb = os.path.getsize(full_cache_path) / (1024 * 1024)
    print(f"[+] ✅ 持久化缓存写入完成! 大小: {file_size_mb:.2f} MB, 总行数: {len(combined_df)}")

    return combined_df


# ==============================================================================
# 3. 严格 T 日因果特征工程 (100% Causal Feature Engineering)
# ==============================================================================

def compute_stock_features(df_stock: pd.DataFrame) -> pd.DataFrame:
    """
    计算单个股票的 15 维技术指标特征。
    严格基于 <= T 日已收盘历史数据，严禁未来函数 (no shift(-k), no bfill)。
    """
    df = df_stock.copy()
    if 'Date' in df.columns:
        df = df.sort_values('Date').reset_index(drop=True)

    close = df['Close'].astype(float)
    high = df['High'].astype(float)
    low = df['Low'].astype(float)
    volume = df['Volume'].astype(float)

    # 1. 均线乖离 Bias_MA20, Bias_MA50, Bias_SMA200
    ma20 = close.rolling(20, min_periods=1).mean()
    ma50 = close.rolling(50, min_periods=1).mean()
    sma200 = close.rolling(200, min_periods=1).mean()

    df['Bias_MA20'] = ((close - ma20) / (ma20 + 1e-8)) * 100.0
    df['Bias_MA50'] = ((close - ma50) / (ma50 + 1e-8)) * 100.0
    df['Bias_SMA200'] = ((close - sma200) / (sma200 + 1e-8)) * 100.0

    # 2. 指数移动均线差值比 EMA10_20_Spread
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    df['EMA10_20_Spread'] = ((ema10 - ema20) / (ema20 + 1e-8)) * 100.0

    # 3. 60 日滚动斐波那契黄金坑位置 Fib_Pos_60 (截断至 [-0.5, 1.5])
    low60 = low.rolling(60, min_periods=1).min()
    high60 = high.rolling(60, min_periods=1).max()
    range60 = high60 - low60
    fib_raw = (close - low60) / np.where(range60 > 1e-6, range60, 1.0)
    df['Fib_Pos_60'] = np.clip(fib_raw, -0.5, 1.5)

    # 4. RSI_14 与 3 日变化斜率 RSI_Slope_3d
    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    rs = gain / (loss + 1e-8)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    df['RSI_14'] = rsi.fillna(50.0)
    df['RSI_Slope_3d'] = (df['RSI_14'] - df['RSI_14'].shift(3)).fillna(0.0)

    # 5. ATR_Pct (ATR_14 / Close * 100)
    prev_close = close.shift(1).fillna(close)
    tr = np.maximum(
        high - low,
        np.maximum((high - prev_close).abs(), (low - prev_close).abs())
    )
    atr14 = tr.rolling(14, min_periods=1).mean()
    df['ATR_Pct'] = (atr14 / (close + 1e-8)) * 100.0

    # 6. 多周期历史收益率 Ret_1d, Ret_3d, Ret_5d, Ret_10d, Ret_20d
    df['Ret_1d'] = close.pct_change(1).fillna(0.0) * 100.0
    df['Ret_3d'] = close.pct_change(3).fillna(0.0) * 100.0
    df['Ret_5d'] = close.pct_change(5).fillna(0.0) * 100.0
    df['Ret_10d'] = close.pct_change(10).fillna(0.0) * 100.0
    df['Ret_20d'] = close.pct_change(20).fillna(0.0) * 100.0

    # 7. 量比与日内 K 线微观结构
    vol_ma20 = volume.rolling(20, min_periods=1).mean()
    df['Vol_Ratio_20'] = volume / (vol_ma20 + 1e-6)
    bar_range = high - low
    df['Bar_Strength'] = (close - low) / np.where(bar_range > 1e-6, bar_range, 1.0)

    # 安全清洗异常值
    for col in STOCK_FEATURE_COLS:
        df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    return df


def compute_macro_features(
    raw_panel: Optional[pd.DataFrame] = None,
    macro_map: Optional[Dict[str, pd.DataFrame]] = None
) -> pd.DataFrame:
    """
    基于宏观基准 ETF (QQQM, SMH, IGV, TLT, VIXY) 计算 6 维全局气象特征与基准收益率。
    支持直接传入 macro_map 字典或从 raw_panel 中提取。
    返回按 Date 索引/列的宏观特征表，用于后续左连接广播到每个股票上。
    """
    etf_dfs = {}
    if macro_map is not None:
        for k, v in macro_map.items():
            sub = v.copy().sort_values('Date')
            etf_dfs[k] = sub
    elif raw_panel is not None:
        for etf in MACRO_ETFS:
            sub = raw_panel[raw_panel['Ticker'] == etf].copy()
            if len(sub) > 0:
                sub = sub.sort_values('Date')
                etf_dfs[etf] = sub
    else:
        raise ValueError("CRITICAL: raw_panel 和 macro_map 至少需要提供一个！")

    for req_etf in ['QQQM', 'SMH', 'IGV', 'TLT', 'VIXY']:
        if req_etf not in etf_dfs:
            raise ValueError(f"CRITICAL: 缺失计算宏观气象必需的 ETF: {req_etf}")

    qqqm = etf_dfs['QQQM'].sort_values('Date').copy()
    smh = etf_dfs['SMH'].sort_values('Date').copy()
    igv = etf_dfs['IGV'].sort_values('Date').copy()
    tlt = etf_dfs['TLT'].sort_values('Date').copy()
    vixy = etf_dfs['VIXY'].sort_values('Date').copy()

    macro_df = pd.DataFrame({'Date': qqqm['Date']})

    # 1. QQQM_Bias_MA50 与 5 日收益率
    qqqm_ma50 = qqqm['Close'].rolling(50, min_periods=1).mean()
    macro_df['QQQM_Bias_MA50'] = ((qqqm['Close'] - qqqm_ma50) / (qqqm_ma50 + 1e-8)) * 100.0
    macro_df['QQQM_Ret_5d'] = qqqm['Close'].pct_change(5).fillna(0.0) * 100.0

    # 2. SMH_Bias_MA50 与 20 日收益率
    smh_ma50 = smh['Close'].rolling(50, min_periods=1).mean()
    macro_df['SMH_Bias_MA50'] = ((smh['Close'] - smh_ma50) / (smh_ma50 + 1e-8)) * 100.0
    macro_df['SMH_Ret_20d'] = smh['Close'].pct_change(20).fillna(0.0) * 100.0

    # 3. TLT_Bias_MA50 (利率/国债环境)
    tlt_ma50 = tlt['Close'].rolling(50, min_periods=1).mean()
    macro_df['TLT_Bias_MA50'] = ((tlt['Close'] - tlt_ma50) / (tlt_ma50 + 1e-8)) * 100.0

    # 4. VIXY_Bias_MA20 与 VIXY_Pos_60 (恐慌脉冲分位)
    vixy_ma20 = vixy['Close'].rolling(20, min_periods=1).mean()
    macro_df['VIXY_Bias_MA20'] = ((vixy['Close'] - vixy_ma20) / (vixy_ma20 + 1e-8)) * 100.0
    v_low60 = vixy['Low'].rolling(60, min_periods=1).min()
    v_high60 = vixy['High'].rolling(60, min_periods=1).max()
    v_range = v_high60 - v_low60
    v_pos = (vixy['Close'] - v_low60) / np.where(v_range > 1e-6, v_range, 1.0)
    macro_df['VIXY_Pos_60'] = np.clip(v_pos, 0.0, 1.0)

    # 5. SMH_IGV_Ratio_Bias: ((SMH / IGV) - MA20(SMH / IGV)) / MA20 * 100
    ratio = smh['Close'].values / (igv['Close'].values + 1e-8)
    ratio_s = pd.Series(ratio, index=macro_df.index)
    ratio_ma20 = ratio_s.rolling(20, min_periods=1).mean()
    macro_df['SMH_IGV_Ratio_Bias'] = ((ratio_s - ratio_ma20) / (ratio_ma20 + 1e-8)) * 100.0

    # 安全清洗异常值
    for col in macro_df.columns:
        if col != 'Date':
            macro_df[col] = macro_df[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    return macro_df


def build_feature_panel(
    raw_panel: pd.DataFrame,
    macro_map: Optional[Dict[str, pd.DataFrame]] = None
) -> pd.DataFrame:
    """
    构建 Qlib 风格长表截面数据 (Long Panel Format [Date, Ticker, Sector]):
    1. 独立计算每个股票自身的 15 维技术指标；
    2. 计算宏观 ETF 的 6 维全局气象与基准收益率；
    3. 按 Date 将宏观特征左连接广播到每个个股；
    4. 计算跨资产超额收益 Alpha_QQQM_5d, Alpha_SMH_20d；
    5. 纵向垂直堆叠为长表截面格式；
    6. 严格禁止宽表全局 dropna()，确保次新股 (CRWV, IBIT, CRDO) 不会截断成熟股票的 2021-2023 历史样本。
    
    返回:
        pd.DataFrame: 包含 Date, Ticker, Sector, 基础价格与 23 维特征的长表 DataFrame。
    """
    macro_df = compute_macro_features(raw_panel=raw_panel, macro_map=macro_map)

    # 识别股票池
    exclude_tickers = set(MACRO_ETFS)
    if macro_map is not None:
        exclude_tickers.update(macro_map.keys())

    stock_panels = []
    for ticker, group in raw_panel.groupby('Ticker'):
        if ticker in exclude_tickers:
            continue
        stock_feat = compute_stock_features(group)
        if 'Sector' not in stock_feat.columns:
            stock_feat['Sector'] = SECTOR_MAP.get(ticker, 'Unknown')
        stock_panels.append(stock_feat)

    if not stock_panels:
        return pd.DataFrame()

    long_panel = pd.concat(stock_panels, axis=0).reset_index(drop=True)

    # 按 Date 左连接宏观特征
    merged = pd.merge(long_panel, macro_df, on='Date', how='left')

    # 计算 2 维跨资产超额收益
    merged['Alpha_QQQM_5d'] = merged['Ret_5d'] - merged['QQQM_Ret_5d']
    merged['Alpha_SMH_20d'] = merged['Ret_20d'] - merged['SMH_Ret_20d']

    # 保证 23 维特征完整且无 unhandled NaN/Inf
    for feat in FEATURE_COLS:
        if feat not in merged.columns:
            merged[feat] = 0.0
        merged[feat] = merged[feat].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # 移除辅助对齐列
    merged = merged.drop(columns=['QQQM_Ret_5d', 'SMH_Ret_20d'], errors='ignore')

    return merged


# ==============================================================================
# 4. 三阈值科学打标引擎 (Triple Barrier Labeling Engine)
# ==============================================================================

def apply_triple_barrier_labeling(df: pd.DataFrame) -> pd.DataFrame:
    """
    三阈值动态止盈止损打标引擎 (Triple Barrier Labeling):
    1. 以次日开盘价 (NEXT_OPEN = Open.shift(-1)) 作为真实可成交基准入场价；
    2. 动态止盈上轨 (Upper Barrier): NEXT_OPEN * (1 + max(0.035, 1.5 * ATR_Pct_T))
    3. 动态止损下轨 (Lower Barrier): NEXT_OPEN * (1 - max(0.022, 1.0 * ATR_Pct_T))
    4. 观察未来 5 个交易日 (T+1 ~ T+5):
       - 先触及止盈上轨且未先触及止损下轨 -> Label = 1
       - 先触及止损下轨 -> Label = 0
       - 5 日内均未触及 -> Label = 0 (超时)
       - 同日同时触及上下轨 -> Label = 0 (风控第一，止损优先)
    5. 尾部隔离：最后 5 个交易日未来路径未走完，Label 标记为 NaN，切入实盘推理池。
    """
    groups = []
    for ticker, grp in df.groupby('Ticker'):
        grp = grp.copy().sort_values('Date').reset_index(drop=True)
        n = len(grp)

        grp['NEXT_OPEN'] = grp['Open'].shift(-1)
        grp['Entry_Price'] = grp['NEXT_OPEN']
        grp['Target_Upper'] = np.nan
        grp['Target_Lower'] = np.nan
        grp['Label'] = np.nan
        grp['Barrier_Hit'] = 'unresolved'

        if n <= 5:
            groups.append(grp)
            continue

        for i in range(n):
            if i >= n - 5:
                # 尾部隔离：最后 5 个交易日不进行打标，保留 NaN 作为实盘推理池
                continue

            entry_price = grp.at[i, 'Entry_Price']
            if pd.isna(entry_price) or entry_price <= 0:
                continue

            atr_pct = grp.at[i, 'ATR_Pct'] if 'ATR_Pct' in grp.columns else 2.0
            if pd.isna(atr_pct) or atr_pct <= 0:
                atr_pct = 2.0

            upper_mult = max(0.035, 1.5 * (atr_pct / 100.0))
            lower_mult = max(0.022, 1.0 * (atr_pct / 100.0))

            upper_barrier = entry_price * (1.0 + upper_mult)
            lower_barrier = entry_price * (1.0 - lower_mult)

            grp.at[i, 'Target_Upper'] = round(upper_barrier, 4)
            grp.at[i, 'Target_Lower'] = round(lower_barrier, 4)

            resolved = False
            for step in range(1, 6):
                k = i + step
                h = grp.at[k, 'High']
                l = grp.at[k, 'Low']

                hit_upper = (h >= upper_barrier)
                hit_lower = (l <= lower_barrier)

                if hit_upper and hit_lower:
                    # 同日碰撞：风控保守第一原则，判定为止损 Label = 0
                    grp.at[i, 'Label'] = 0.0
                    grp.at[i, 'Barrier_Hit'] = 'collision'
                    resolved = True
                    break
                elif hit_upper:
                    grp.at[i, 'Label'] = 1.0
                    grp.at[i, 'Barrier_Hit'] = 'upper'
                    resolved = True
                    break
                elif hit_lower:
                    grp.at[i, 'Label'] = 0.0
                    grp.at[i, 'Barrier_Hit'] = 'lower'
                    resolved = True
                    break

            if not resolved:
                # 超时未触及任何轨道，判定为 Label = 0
                grp.at[i, 'Label'] = 0.0
                grp.at[i, 'Barrier_Hit'] = 'timeout'

        groups.append(grp)

    return pd.concat(groups, axis=0).reset_index(drop=True)


# ==============================================================================
# 5. 后续里程碑接口骨架 (Interface Contracts for M2 & M3)
# ==============================================================================

def run_walk_forward_cv(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict, object]:
    """Milestone M2: 严格时序滚动窗口训练与 5 日 Purge 隔离验证"""
    raise NotImplementedError("run_walk_forward_cv 将在里程碑 M2 中实现")


def fetch_finnhub_realtime_quotes(tickers: List[str]) -> Dict[str, Dict]:
    """Milestone M3: Finnhub 实时盘前行情并发采集"""
    raise NotImplementedError("fetch_finnhub_realtime_quotes 将在里程碑 M3 中实现")


def generate_report_tables(oos_df: pd.DataFrame, model, feature_cols: List[str], latest_features_df: pd.DataFrame, macro_weather: Dict) -> Dict:
    """Milestone M3: 生成控制台与可视化战报 4 张核心数据表"""
    raise NotImplementedError("generate_report_tables 将在里程碑 M3 中实现")


def render_html_dashboard(tables_data: Dict, output_path: str = 'predict_radar_5y.html') -> None:
    """Milestone M3: 渲染暗黑买方风格 HTML 可视化看板"""
    raise NotImplementedError("render_html_dashboard 将在里程碑 M3 中实现")


# ==============================================================================
# 6. 命令行入口与校验运行 (CLI & Pipeline Verification)
# ==============================================================================

def verify_pipeline_integrity(feature_panel: pd.DataFrame) -> Dict:
    """
    严格校验长表面板的数据完整性与因果不变量
    """
    total_samples = len(feature_panel)
    dates = feature_panel['Date']
    tickers = feature_panel['Ticker']
    sectors = feature_panel['Sector']

    min_date = dates.min()
    max_date = dates.max()
    unique_tickers = len(tickers.unique())
    unique_sectors = len(sectors.unique())

    # 1. 检查 23 维特征是否存在 NaN 或 Inf
    nan_counts = feature_panel[FEATURE_COLS].isna().sum()
    has_nan = nan_counts.any()

    inf_counts = np.isinf(feature_panel[FEATURE_COLS].values).sum()
    has_inf = inf_counts > 0

    # 2. 检查成熟标的 (如 NVDA) 历史跨度是否保留 2021 年样本
    nvda_sub = feature_panel[feature_panel['Ticker'] == 'NVDA']
    nvda_min_date = nvda_sub['Date'].min()
    nvda_bars = len(nvda_sub)

    # 3. 检查次新股 (如 CRWV, IBIT) 样本是否合理包含
    crwv_bars = len(feature_panel[feature_panel['Ticker'] == 'CRWV']) if 'CRWV' in tickers.unique() else 0
    ibit_bars = len(feature_panel[feature_panel['Ticker'] == 'IBIT']) if 'IBIT' in tickers.unique() else 0

    report = {
        'total_samples': total_samples,
        'min_date': min_date.strftime('%Y-%m-%d'),
        'max_date': max_date.strftime('%Y-%m-%d'),
        'unique_tickers': unique_tickers,
        'unique_sectors': unique_sectors,
        'has_nan': bool(has_nan),
        'has_inf': bool(has_inf),
        'nvda_min_date': nvda_min_date.strftime('%Y-%m-%d'),
        'nvda_bars': nvda_bars,
        'crwv_bars': crwv_bars,
        'ibit_bars': ibit_bars,
        'features_count': len(FEATURE_COLS)
    }

    assert not has_nan, f"Integrity Failure: 特征中包含 NaN 值:\n{nan_counts[nan_counts > 0]}"
    assert not has_inf, f"Integrity Failure: 特征中包含 Inf 无穷大值 (共 {inf_counts} 个)"
    assert unique_tickers == 28, f"Expected 28 target stocks, got {unique_tickers}"
    assert unique_sectors == 7, f"Expected 7 sectors, got {unique_sectors}"
    assert nvda_bars >= 1200, f"NVDA 历史样本被截断 ({nvda_bars} < 1200), 说明发生了宽表全局 dropna 污染！"
    assert crwv_bars > 0, "CRWV 次新股样本丢失"
    assert ibit_bars > 0, "IBIT 次新股样本丢失"

    return report


def main():
    parser = argparse.ArgumentParser(description="AI Multi-Sector 5Y US Market Predictor")
    parser.add_argument('--refresh', action='store_true', help="强制从 yfinance 重新抓取 35 标的数据并刷新缓存")
    parser.add_argument('--cache-file', type=str, default='data_cache/ml_panel_5y.parquet', help="Parquet 缓存路径")
    parser.add_argument('--verify', action='store_true', help="执行数据管道完整性校验")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 AI Multi-Sector 5Y US Market Predictor (Milestone M1 Data & Feature Pipeline)")
    print("=" * 70)

    raw_panel = fetch_or_load_panel_data(cache_file=args.cache_file, force_refresh=args.refresh)
    feature_panel = build_feature_panel(raw_panel)

    report = verify_pipeline_integrity(feature_panel)
    print("\n" + "=" * 70)
    print("📋 数据与特征管道验证战报 (Data & Feature Pipeline Verification)")
    print("=" * 70)
    for k, v in report.items():
        print(f"  • {k:20s}: {v}")
    print("=" * 70)
    print("✅ Milestone M1 基础管道与 23 维因果特征构建成功！\n")


if __name__ == '__main__':
    main()
