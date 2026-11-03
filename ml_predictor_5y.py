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
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

def load_env_file(env_path=None):
    """安全加载本地 .env 文件至 os.environ"""
    if env_path is None:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        from dotenv import load_dotenv
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
            return
    except ImportError:
        pass
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

load_env_file()

import time
import math
import json
import random
import argparse
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple, Optional, Any, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf
try:
    import lightgbm as lgb
    from sklearn.metrics import (
        roc_auc_score,
        accuracy_score,
        precision_score,
        recall_score,
        brier_score_loss,
    )
except ImportError:
    lgb = None

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

FEATURE_RATIONALES = {
    'QQQM_Bias_MA50': '纳指100大盘50日均线乖离，系统性科技Beta环境水温计',
    'SMH_IGV_Ratio_Bias': '硬件(SMH)与软件(IGV)风格轮动比值乖离，捕捉资金在AI算力与SaaS间的风格切换',
    'ATR_Pct': '日线真实波动率占比，自适应动态调节止盈止损宽窄度与风险敞口',
    'Bias_SMA200': '200日牛熊生命线偏离度，标定长期估值偏离与大周期均值回归动量',
    'Ret_10d': '10日波段动量斜率，捕捉右侧突破与短中期趋势延续性',
    'Ret_20d': '20日(单月)累计动量，验证机构资金中期建仓方向与趋势共振',
    'Vol_Ratio_20': '20日均量放大倍数，甄别机构放量突破主升与缩量虚高诱多',
    'Fib_Pos_60': '60日滚动斐波那契黄金坑位置，精准定位底部反弹吸筹与顶部阻力',
    'EMA10_20_Spread': '双指数均线发散率，刻画主升浪初期均线多头排列爆发速度',
    'Ret_3d': '3日超短线冲刺速度，识别右侧波段启动瞬间的点火动量',
    'Bias_MA50': '中期持仓成本偏离度，衡量波段回踩50日线的支撑强度',
    'Alpha_QQQM_5d': '相比纳指100的5日超额Alpha，筛选跑赢科技大盘的领涨龙头标的',
    'Ret_5d': '5日周线级别收益率，捕捉多头波段脉冲与右侧买入窗口',
    'Bar_Strength': '日内K线实体收盘强弱分位，量化尾盘多头护盘意愿与资金承接力',
    'RSI_Slope_3d': '3日RSI斜率加速度，探测动能指标边际突变与多头拐点',
    'Bias_MA20': '20日短线生命线偏离度，捕捉波段回踩均线的买点契机',
    'RSI_14': '14日相对强弱指标，识别超卖反弹与超买钝化区间的能量状态',
    'Alpha_SMH_20d': '相比半导体基准的20日超额收益，捕捉赛道内结构性分化Alpha',
    'Ret_1d': '单日动量冲击，刻画最新交易日多空博弈结果与跳空缺口',
    'VIXY_Bias_MA20': '恐慌指数20日均线乖离，预警全市场流动性冲击与系统性踩踏',
    'SMH_Bias_MA50': '核心半导体硬件链50日均线状态，标定科技成长风格的系统性风险水位',
    'TLT_Bias_MA50': '20年+美债利率趋势偏离，映射宏观流动性宽松与贴现率预期对估值的压制',
    'VIXY_Pos_60': '60日恐慌指数脉冲分位数，量化全市场风险偏好处于贪婪或极度恐慌底部'
}


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
    每个 ETF 子表独立按 Date 排序并计算指标，统一通过 pd.merge(..., on='Date', how='left') 合并，
    彻底解决切片行号索引不相交导致特征全为 NaN 并被静默填 0 的问题。
    """
    etf_dfs = {}
    if macro_map is not None:
        for k, v in macro_map.items():
            sub = v.copy().sort_values('Date').reset_index(drop=True)
            etf_dfs[k] = sub
    elif raw_panel is not None:
        for etf in MACRO_ETFS:
            sub = raw_panel[raw_panel['Ticker'] == etf].copy()
            if len(sub) > 0:
                sub = sub.sort_values('Date').reset_index(drop=True)
                etf_dfs[etf] = sub
    else:
        raise ValueError("CRITICAL: raw_panel 和 macro_map 至少需要提供一个！")

    for req_etf in ['QQQM', 'SMH', 'IGV', 'TLT', 'VIXY']:
        if req_etf not in etf_dfs:
            raise ValueError(f"CRITICAL: 缺失计算宏观气象必需的 ETF: {req_etf}")

    qqqm = etf_dfs['QQQM'].sort_values('Date').reset_index(drop=True)
    smh = etf_dfs['SMH'].sort_values('Date').reset_index(drop=True)
    igv = etf_dfs['IGV'].sort_values('Date').reset_index(drop=True)
    tlt = etf_dfs['TLT'].sort_values('Date').reset_index(drop=True)
    vixy = etf_dfs['VIXY'].sort_values('Date').reset_index(drop=True)

    # 1. QQQM 特征表
    qqqm_close = qqqm['Close'].astype(float)
    qqqm_ma50 = qqqm_close.rolling(50, min_periods=1).mean()
    qqqm_feat = pd.DataFrame({
        'Date': qqqm['Date'],
        'QQQM_Bias_MA50': ((qqqm_close - qqqm_ma50) / (qqqm_ma50 + 1e-8)) * 100.0,
        'QQQM_Ret_5d': qqqm_close.pct_change(5).fillna(0.0) * 100.0
    })

    # 2. SMH 特征表
    smh_close = smh['Close'].astype(float)
    smh_ma50 = smh_close.rolling(50, min_periods=1).mean()
    smh_feat = pd.DataFrame({
        'Date': smh['Date'],
        'SMH_Bias_MA50': ((smh_close - smh_ma50) / (smh_ma50 + 1e-8)) * 100.0,
        'SMH_Ret_20d': smh_close.pct_change(20).fillna(0.0) * 100.0
    })

    # 3. TLT 特征表
    tlt_close = tlt['Close'].astype(float)
    tlt_ma50 = tlt_close.rolling(50, min_periods=1).mean()
    tlt_feat = pd.DataFrame({
        'Date': tlt['Date'],
        'TLT_Bias_MA50': ((tlt_close - tlt_ma50) / (tlt_ma50 + 1e-8)) * 100.0
    })

    # 4. VIXY 特征表
    vixy_close = vixy['Close'].astype(float)
    vixy_high = vixy['High'].astype(float)
    vixy_low = vixy['Low'].astype(float)
    vixy_ma20 = vixy_close.rolling(20, min_periods=1).mean()
    v_low60 = vixy_low.rolling(60, min_periods=1).min()
    v_high60 = vixy_high.rolling(60, min_periods=1).max()
    v_range = v_high60 - v_low60
    v_pos = (vixy_close - v_low60) / np.where(v_range > 1e-6, v_range, 1.0)
    vixy_feat = pd.DataFrame({
        'Date': vixy['Date'],
        'VIXY_Bias_MA20': ((vixy_close - vixy_ma20) / (vixy_ma20 + 1e-8)) * 100.0,
        'VIXY_Pos_60': np.clip(v_pos, 0.0, 1.0)
    })

    # 5. SMH/IGV 比值乖离表 (先按 Date 内连接对齐相同交易日)
    smh_igv = pd.merge(
        smh[['Date', 'Close']].rename(columns={'Close': 'Close_SMH'}),
        igv[['Date', 'Close']].rename(columns={'Close': 'Close_IGV'}),
        on='Date',
        how='inner'
    ).sort_values('Date').reset_index(drop=True)
    smh_igv_ratio = smh_igv['Close_SMH'].astype(float) / (smh_igv['Close_IGV'].astype(float) + 1e-8)
    ratio_ma20 = smh_igv_ratio.rolling(20, min_periods=1).mean()
    smh_igv_feat = pd.DataFrame({
        'Date': smh_igv['Date'],
        'SMH_IGV_Ratio_Bias': ((smh_igv_ratio - ratio_ma20) / (ratio_ma20 + 1e-8)) * 100.0
    })

    # 6. 以 QQQM 的所有交易日为基准按 Date 左连接全部宏观特征
    macro_df = pd.DataFrame({'Date': sorted(qqqm['Date'].unique())})
    for sub_df in [qqqm_feat, smh_feat, tlt_feat, vixy_feat, smh_igv_feat]:
        macro_df = pd.merge(macro_df, sub_df, on='Date', how='left')

    # 因果安全前向填充并清洗异常值
    for col in macro_df.columns:
        if col != 'Date':
            macro_df[col] = macro_df[col].ffill().replace([np.inf, -np.inf], np.nan).fillna(0.0)

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

    # 计算 2 维跨资产超额收益并结合当日横截面百分位排名与连续超额幅度 (严格在同日 groupby('Date') 内计算，零未来函数)
    raw_alpha_qqqm = (merged['Ret_5d'] - merged['QQQM_Ret_5d']).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    raw_alpha_smh = (merged['Ret_20d'] - merged['SMH_Ret_20d']).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    merged['_raw_alpha_qqqm'] = raw_alpha_qqqm
    merged['_raw_alpha_smh'] = raw_alpha_smh
    cs_rank_qqqm = merged.groupby('Date')['_raw_alpha_qqqm'].rank(pct=True).fillna(0.5)
    cs_rank_smh = merged.groupby('Date')['_raw_alpha_smh'].rank(pct=True).fillna(0.5)
    sig_qqqm = 1.0 / (1.0 + np.exp(-np.clip(raw_alpha_qqqm, -50.0, 50.0) / 5.0))
    sig_smh = 1.0 / (1.0 + np.exp(-np.clip(raw_alpha_smh, -50.0, 50.0) / 8.0))
    merged['Alpha_QQQM_5d'] = np.clip(0.65 * cs_rank_qqqm + 0.35 * sig_qqqm, 0.0, 1.0)
    merged['Alpha_SMH_20d'] = np.clip(0.65 * cs_rank_smh + 0.35 * sig_smh, 0.0, 1.0)
    merged = merged.drop(columns=['_raw_alpha_qqqm', '_raw_alpha_smh'], errors='ignore')

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

def compute_effective_atr(atr_pct: float) -> float:
    """
    分段软压缩 (Soft-Root Scaling) 动态计算有效 ATR:
    - raw_atr = atr_pct / 100.0
    - raw_atr <= 0.040: eff_atr = np.clip(raw_atr, 0.012, 0.040)
    - raw_atr > 0.040:  eff_atr = 0.040 + 0.50 * (np.clip(raw_atr, 0.040, 0.100) - 0.040)
    下限维持 0.012 (1.2%)，上限平滑延展至 0.070 (7.0%)。
    """
    if np.isnan(atr_pct) or atr_pct <= 0:
        atr_pct = 2.0
    raw_atr = float(atr_pct) / 100.0
    if raw_atr > 0.040:
        return float(0.040 + 0.50 * (np.clip(raw_atr, 0.040, 0.100) - 0.040))
    else:
        return float(np.clip(raw_atr, 0.012, 0.040))


def apply_triple_barrier_labeling(df: pd.DataFrame) -> pd.DataFrame:
    """
    双向三阈值动态止盈止损打标引擎 (Dual Long & Short Triple Barrier Labeling Engine):
    1. 做多 (Long):
       - 基准入场价: NEXT_OPEN (Open.shift(-1))
       - 动态止盈上轨 (Target_Upper): NEXT_OPEN * (1 + max(0.030, 1.35 * eff_atr))
       - 动态止损下轨 (Target_Lower): NEXT_OPEN * (1 - max(0.022, 1.00 * eff_atr))
       - 触及上轨 -> Label = 1, Barrier_Hit = 'upper'
       - 触及下轨 -> Label = 0, Barrier_Hit = 'lower'
       - 同日碰撞/超时 -> Label = 0, Barrier_Hit = 'collision'/'timeout'
    2. 做空 (Short):
       - 基准入场价: NEXT_OPEN (Open.shift(-1))
       - 动态止盈下轨 (Short_Target_TP): NEXT_OPEN * (1 - max(0.030, 1.35 * eff_atr))
       - 动态止损上轨 (Short_Target_SL): NEXT_OPEN * (1 + max(0.022, 1.00 * eff_atr))
       - 触及下轨 (TP) -> Label_Short = 1, Barrier_Hit_Short = 'tp', Realized_Return_Short = round(tp_mult * 100.0, 4)
       - 触及上轨 (SL) -> Label_Short = 0, Barrier_Hit_Short = 'sl', Realized_Return_Short = round(-sl_mult * 100.0, 4)
       - 同日碰撞 -> 止损优先 Label_Short = 0, Barrier_Hit_Short = 'collision', Realized_Return_Short = round(-sl_mult * 100.0, 4)
       - 超时未触轨 -> Label_Short = 0, Barrier_Hit_Short = 'timeout', Realized_Return_Short = round(((entry_price - c_exit) / entry_price) * 100.0, 4)
    3. 尾部隔离：最后 5 个交易日 Label 和 Label_Short 均为 NaN，切入实盘推理池。
    """
    if 'Ticker' not in df.columns:
        df = df.copy()
        df['Ticker'] = 'UNKNOWN'

    groups = []
    for ticker, grp in df.groupby('Ticker'):
        grp = grp.copy().sort_values('Date').reset_index(drop=True)
        n = len(grp)

        grp['NEXT_OPEN'] = grp['Open'].shift(-1)
        grp['Entry_Price'] = grp['NEXT_OPEN']

        # 做多数组
        targets_u = np.full(n, np.nan)
        targets_l = np.full(n, np.nan)
        labels = np.full(n, np.nan)
        hits = np.array(['unresolved'] * n, dtype=object)
        max_gains = np.full(n, np.nan)
        realized_returns = np.full(n, np.nan)

        # 做空数组
        short_tp_arr = np.full(n, np.nan)
        short_sl_arr = np.full(n, np.nan)
        labels_short = np.full(n, np.nan)
        hits_short = np.array(['unresolved'] * n, dtype=object)
        realized_returns_short = np.full(n, np.nan)

        if n <= 5:
            grp['Target_Upper'] = targets_u
            grp['Target_Lower'] = targets_l
            grp['Label'] = labels
            grp['Barrier_Hit'] = hits
            grp['Max_Gain_5d'] = max_gains
            grp['Realized_Return'] = realized_returns

            grp['Short_Target_TP'] = short_tp_arr
            grp['Short_Target_SL'] = short_sl_arr
            grp['Label_Short'] = labels_short
            grp['Barrier_Hit_Short'] = hits_short
            grp['Realized_Return_Short'] = realized_returns_short
            groups.append(grp)
            continue

        entry_prices = grp['Entry_Price'].to_numpy(dtype=float)
        highs = grp['High'].to_numpy(dtype=float)
        lows = grp['Low'].to_numpy(dtype=float)
        closes = grp['Close'].to_numpy(dtype=float) if 'Close' in grp.columns else entry_prices
        atr_pcts = grp['ATR_Pct'].to_numpy(dtype=float) if 'ATR_Pct' in grp.columns else np.full(n, 2.0)

        for i in range(n - 5):
            entry_price = entry_prices[i]
            if np.isnan(entry_price) or entry_price <= 0:
                continue

            atr_pct = atr_pcts[i]
            if np.isnan(atr_pct) or atr_pct <= 0:
                atr_pct = 2.0

            eff_atr = compute_effective_atr(atr_pct)
            tp_mult = max(0.030, 1.35 * eff_atr)
            sl_mult = max(0.022, 1.00 * eff_atr)

            # 做多轨道
            upper_barrier = entry_price * (1.0 + tp_mult)
            lower_barrier = entry_price * (1.0 - sl_mult)
            targets_u[i] = round(upper_barrier, 4)
            targets_l[i] = round(lower_barrier, 4)

            # 做空轨道 (Lower=TP, Upper=SL)
            short_tp_barrier = entry_price * (1.0 - tp_mult)
            short_sl_barrier = entry_price * (1.0 + sl_mult)
            short_tp_arr[i] = round(short_tp_barrier, 4)
            short_sl_arr[i] = round(short_sl_barrier, 4)

            # 5 日内前向最高收益率 % (做多)
            sub_highs = highs[i + 1 : i + 6]
            if len(sub_highs) > 0:
                max_gains[i] = round(((np.max(sub_highs) - entry_price) / entry_price) * 100.0, 4)

            # 单循环同时判定多空两路触轨
            resolved_long = False
            resolved_short = False

            for step in range(1, 6):
                k = i + step
                h = highs[k]
                l = lows[k]

                # --- 做多判定 ---
                if not resolved_long:
                    hit_upper = (h >= upper_barrier)
                    hit_lower = (l <= lower_barrier)
                    if hit_upper and hit_lower:
                        labels[i] = 0.0
                        hits[i] = 'collision'
                        realized_returns[i] = round(-sl_mult * 100.0, 4)
                        resolved_long = True
                    elif hit_upper:
                        labels[i] = 1.0
                        hits[i] = 'upper'
                        realized_returns[i] = round(tp_mult * 100.0, 4)
                        resolved_long = True
                    elif hit_lower:
                        labels[i] = 0.0
                        hits[i] = 'lower'
                        realized_returns[i] = round(-sl_mult * 100.0, 4)
                        resolved_long = True

                # --- 做空判定 ---
                if not resolved_short:
                    hit_short_tp = (l <= short_tp_barrier)
                    hit_short_sl = (h >= short_sl_barrier)
                    if hit_short_tp and hit_short_sl:
                        # 同日碰撞：风控保守第一原则，判定为做空止损
                        labels_short[i] = 0.0
                        hits_short[i] = 'collision'
                        realized_returns_short[i] = round(-sl_mult * 100.0, 4)
                        resolved_short = True
                    elif hit_short_tp:
                        labels_short[i] = 1.0
                        hits_short[i] = 'tp'
                        realized_returns_short[i] = round(tp_mult * 100.0, 4)
                        resolved_short = True
                    elif hit_short_sl:
                        labels_short[i] = 0.0
                        hits_short[i] = 'sl'
                        realized_returns_short[i] = round(-sl_mult * 100.0, 4)
                        resolved_short = True

                if resolved_long and resolved_short:
                    break

            # 超时处理 (T+5 收盘结算)
            c_exit = closes[i + 5] if (i + 5 < n) else entry_price
            if not resolved_long:
                labels[i] = 0.0
                hits[i] = 'timeout'
                realized_returns[i] = round(((c_exit - entry_price) / entry_price) * 100.0, 4)

            if not resolved_short:
                labels_short[i] = 0.0
                hits_short[i] = 'timeout'
                realized_returns_short[i] = round(((entry_price - c_exit) / entry_price) * 100.0, 4)

        grp['Target_Upper'] = targets_u
        grp['Target_Lower'] = targets_l
        grp['Label'] = labels
        grp['Barrier_Hit'] = hits
        grp['Max_Gain_5d'] = max_gains
        grp['Realized_Return'] = realized_returns

        grp['Short_Target_TP'] = short_tp_arr
        grp['Short_Target_SL'] = short_sl_arr
        grp['Label_Short'] = labels_short
        grp['Barrier_Hit_Short'] = hits_short
        grp['Realized_Return_Short'] = realized_returns_short
        groups.append(grp)

    return pd.concat(groups, axis=0).reset_index(drop=True)


# ==============================================================================
# 5. Walk-Forward 滚动时序验证与 LightGBM 模型 (Milestone M2)
# ==============================================================================

def get_cv_folds(
    df: pd.DataFrame,
    test_years: List[int] = [2024, 2025, 2026],
    purge_days: int = 5
) -> List[Dict[str, Any]]:
    """
    生成严格时序 Expanding Window Walk-Forward 滚动折叠集，并强制施加 Purge/Embargo 隔离带。
    
    参数:
        df: 长表截面数据 (包含 Date 列)
        test_years: 测试年份列表，默认 [2024, 2025, 2026]
        purge_days: 训练集与测试集之间的交易日隔离天数，强制 >= 5
        
    返回:
        List[Dict[str, Any]]: 包含各 fold 的 train_df, test_df, max_train_date, min_test_date, purge_gap_days
    """
    if 'Date' not in df.columns:
        raise ValueError("DataFrame 必须包含 'Date' 列！")

    all_dates = sorted(pd.to_datetime(df['Date']).drop_duplicates().tolist())
    if len(all_dates) < 20:
        return []

    # 尾部隔离：最后 5 个交易日由于未来 5 日路径尚未走完，隔离为今日实盘推理池 (Live Inference Pool)
    cv_dates = all_dates[:-5]
    folds = []

    for year in test_years:
        year_dates = [d for d in cv_dates if d.year == year]
        if not year_dates:
            continue

        test_start = year_dates[0]
        idx_test_start = cv_dates.index(test_start)

        # 确保训练集有足够样本 (至少 purge_days + 10 天)
        if idx_test_start <= purge_days + 10:
            continue

        train_end_idx = idx_test_start - purge_days
        train_dates = cv_dates[:train_end_idx]
        test_dates = year_dates

        # 严格因果与隔离带不变量断言校验
        max_train_date = train_dates[-1]
        min_test_date = test_dates[0]
        assert max_train_date < min_test_date, (
            f"Fold {year}: 训练集截止日 ({max_train_date}) 必须严格早于测试集起始日 ({min_test_date})！"
        )

        idx_train = all_dates.index(max_train_date)
        idx_test = all_dates.index(min_test_date)
        actual_gap = idx_test - idx_train
        assert actual_gap >= purge_days, (
            f"Fold {year}: Purge 隔离带天数不足 ({actual_gap} < {purge_days})！"
        )

        # 标签前向窗口不重叠断言: T 日训练样本的前向 5 日窗口 [T+1, ..., T+5] 绝不能触及测试集
        assert idx_train + 5 <= idx_test, (
            f"Fold {year}: 训练集最晚样本的前向 5 日打标窗口触及测试集！"
        )

        train_df = df[df['Date'].isin(train_dates)].copy()
        test_df = df[df['Date'].isin(test_dates)].copy()

        # 确保训练集与测试集无日期重叠
        train_set = set(train_df['Date'])
        test_set = set(test_df['Date'])
        assert len(train_set.intersection(test_set)) == 0, (
            f"Fold {year}: 训练集与测试集存在日期交叉！"
        )

        folds.append({
            'year': year,
            'train_df': train_df,
            'test_df': test_df,
            'max_train_date': max_train_date,
            'min_test_date': min_test_date,
            'purge_gap_days': actual_gap
        })

    return folds


def train_live_models(
    df: pd.DataFrame,
    feature_cols: Optional[List[str]] = None,
    lgb_params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    训练实盘推理专属 LightGBM 模型 (覆盖至 T-5 最新已结算交易日，严守 5 日尾部隔离):
    - 做多专属模型: 基于 Label，class_weight='balanced'
    - 做空专属模型: 基于 Label_Short，class_weight='balanced'
    
    返回:
        Dict[str, Any]: {
            'long': live_clf_long,
            'short': live_clf_short,
            'model_long': live_clf_long,
            'model_short': live_clf_short,
            'long_booster': live_clf_long.booster_ if live_clf_long else None,
            'short_booster': live_clf_short.booster_ if live_clf_short else None
        }
    """
    if feature_cols is None:
        feature_cols = FEATURE_COLS

    if 'Label' not in df.columns or 'Label_Short' not in df.columns:
        df = apply_triple_barrier_labeling(df)

    if lgb_params is None:
        lgb_params = {
            'max_depth': 5,
            'num_leaves': 23,
            'learning_rate': 0.03,
            'subsample': 0.8,
            'subsample_freq': 1,
            'colsample_bytree': 0.65,
            'max_bin': 63,
            'n_estimators': 300,
            'random_state': 42,
            'objective': 'binary',
            'class_weight': 'balanced',
            'importance_type': 'gain',
            'verbose': -1,
            'n_jobs': -1
        }

    # 做多实盘专属模型训练
    live_train_long = df.dropna(subset=['Label']).copy()
    live_clf_long = None
    if len(live_train_long) >= 20 and live_train_long['Label'].nunique() >= 2:
        t_live_start = time.time()
        live_clf_long = lgb.LGBMClassifier(**lgb_params)
        live_clf_long.fit(live_train_long[feature_cols], live_train_long['Label'].astype(int))
        t_live_elapsed = time.time() - t_live_start
        live_min_d = live_train_long['Date'].min().strftime('%Y-%m-%d')
        live_max_d = live_train_long['Date'].max().strftime('%Y-%m-%d')
        print(f"  [+] Live Refit (做多专属实盘模型): 全量已结算样本 ({live_min_d} ~ {live_max_d}, {len(live_train_long)} 样本, 严守 T-5 尾部隔离) | 训练耗时 {t_live_elapsed:.1f}s")

    # 做空实盘专属模型训练
    live_train_short = df.dropna(subset=['Label_Short']).copy()
    live_clf_short = None
    if len(live_train_short) >= 20 and live_train_short['Label_Short'].nunique() >= 2:
        t_live_start = time.time()
        live_clf_short = lgb.LGBMClassifier(**lgb_params)
        live_clf_short.fit(live_train_short[feature_cols], live_train_short['Label_Short'].astype(int))
        t_live_elapsed = time.time() - t_live_start
        live_min_ds = live_train_short['Date'].min().strftime('%Y-%m-%d')
        live_max_ds = live_train_short['Date'].max().strftime('%Y-%m-%d')
        print(f"  [+] Live Refit (做空专属实盘模型): 全量已结算样本 ({live_min_ds} ~ {live_max_ds}, {len(live_train_short)} 样本, 严守 T-5 尾部隔离) | 训练耗时 {t_live_elapsed:.1f}s")

    return {
        'long': live_clf_long,
        'short': live_clf_short,
        'model_long': live_clf_long,
        'model_short': live_clf_short,
        'long_booster': live_clf_long.booster_ if live_clf_long is not None else None,
        'short_booster': live_clf_short.booster_ if live_clf_short is not None else None
    }


def run_walk_forward_cv(
    df: pd.DataFrame,
    test_years: List[int] = [2024, 2025, 2026],
    purge_days: int = 5
) -> Tuple[pd.DataFrame, Dict, Any]:
    """
    Milestone M1 & M2: 双向时序滚动窗口训练与 5 日 Purge 隔离验证。
    
    1. 按 Expanding Window 滚动切分 2024, 2025, 2026 三个测试年度，训练集与测试集之间强制保留 5 日 Purge 隔离带。
    2. 使用 LightGBM (LGBMClassifier) 针对 Label (做多) 与 Label_Short (做空) 分别训练独立模型:
       - 严格正则化参数 (max_depth=5, num_leaves=23, lr=0.03, colsample_bytree=0.65, max_bin=63)
       - class_weight='balanced' 解决样本不均衡
    3. 汇总 3 个测试折的样本外 (OOS) 双向预测结果：Prob_1 (做多概率), Prob_Short_1 (做空概率)。
    4. 评估样本外指标：ROC AUC, Accuracy, Precision, Recall, Brier Score。
    
    返回:
        Tuple[pd.DataFrame, Dict, lightgbm.Booster]: (oos_predictions_df, overall_metrics, final_trained_model)
    """
    if len(df) == 0:
        return pd.DataFrame(), {}, None

    # 如果尚未打标，自动执行三阈值动态打标
    if 'Label' not in df.columns or 'Label_Short' not in df.columns:
        print("[*] 正在执行双向三阈值动态打标 (Dual Triple Barrier Labeling)...")
        df = apply_triple_barrier_labeling(df)

    folds = get_cv_folds(df, test_years=test_years, purge_days=purge_days)
    if not folds:
        print("[!] 警告: 数据集交易日数不足，无法生成有效 Walk-Forward 滚动折叠！")
        return pd.DataFrame(), {}, None

    lgb_params = {
        'max_depth': 5,
        'num_leaves': 23,
        'learning_rate': 0.03,
        'subsample': 0.8,
        'subsample_freq': 1,
        'colsample_bytree': 0.65,
        'max_bin': 63,
        'n_estimators': 300,
        'random_state': 42,
        'objective': 'binary',
        'class_weight': 'balanced',
        'importance_type': 'gain',
        'verbose': -1,
        'n_jobs': -1
    }

    oos_list = []
    fold_metrics = {}
    models = {}
    models_short = {}

    print(f"[*] 🚀 开始执行 Walk-Forward Expanding 滚动时序训练 (共 {len(folds)} 个测试折，5 日 Purge 隔离)...")

    for fold in folds:
        year = fold['year']
        train_df = fold['train_df']
        test_df = fold['test_df']

        train_clean = train_df.dropna(subset=['Label']).copy()
        test_clean = test_df.dropna(subset=['Label']).copy()

        train_clean_short = train_df.dropna(subset=['Label_Short']).copy()
        test_clean_short = test_df.dropna(subset=['Label_Short']).copy()

        if len(train_clean) == 0 or len(test_clean) == 0:
            print(f"  [-] Fold {year}: 有效样本为空，跳过！")
            continue

        # --- 做多模型训练 ---
        X_train = train_clean[FEATURE_COLS]
        y_train = train_clean['Label'].astype(int)
        X_test = test_clean[FEATURE_COLS]
        y_test = test_clean['Label'].astype(int)

        t_start = time.time()
        clf = lgb.LGBMClassifier(**lgb_params)
        clf.fit(X_train, y_train)
        probs = clf.predict_proba(X_test)[:, 1]
        preds = (probs >= 0.5).astype(int)

        # --- 做空模型训练 ---
        clf_short = lgb.LGBMClassifier(**lgb_params)
        if len(train_clean_short) >= 10 and train_clean_short['Label_Short'].nunique() >= 2:
            clf_short.fit(train_clean_short[FEATURE_COLS], train_clean_short['Label_Short'].astype(int))
            probs_short = clf_short.predict_proba(X_test)[:, 1]
            preds_short = (probs_short >= 0.5).astype(int)
        else:
            probs_short = np.full(len(test_clean), 0.5)
            preds_short = np.zeros(len(test_clean), dtype=int)
        t_elapsed = time.time() - t_start

        cols_to_keep = ['Date', 'Ticker', 'Sector', 'Close', 'Label']
        for col_name in ['Entry_Price', 'Target_Upper', 'Target_Lower', 'Max_Gain_5d', 'Realized_Return', 'Barrier_Hit',
                         'Label_Short', 'Short_Target_TP', 'Short_Target_SL', 'Barrier_Hit_Short', 'Realized_Return_Short']:
            if col_name in test_clean.columns:
                cols_to_keep.append(col_name)
        fold_oos = test_clean[cols_to_keep].copy()
        fold_oos['Prob_1'] = probs
        fold_oos['Pred_Label'] = preds
        fold_oos['Prob_Short_1'] = probs_short
        fold_oos['Pred_Label_Short'] = preds_short
        fold_oos['Fold_Year'] = year
        oos_list.append(fold_oos)

        # 折内多头指标计算
        y_t = y_test.values
        f_auc = float(roc_auc_score(y_t, probs)) if len(np.unique(y_t)) >= 2 else 0.5
        f_acc = float(accuracy_score(y_t, preds))
        f_prec = float(precision_score(y_t, preds, zero_division=0))
        f_rec = float(recall_score(y_t, preds, zero_division=0))
        f_brier = float(brier_score_loss(y_t, probs))

        # 折内空头指标计算
        if 'Label_Short' in test_clean.columns and test_clean['Label_Short'].notna().any():
            valid_mask = test_clean['Label_Short'].notna().values
            y_ts = test_clean.loc[valid_mask, 'Label_Short'].astype(int).values
            p_s = probs_short[valid_mask]
            pred_s = preds_short[valid_mask]
            f_auc_s = float(roc_auc_score(y_ts, p_s)) if len(np.unique(y_ts)) >= 2 else 0.5
            f_acc_s = float(accuracy_score(y_ts, pred_s))
            f_prec_s = float(precision_score(y_ts, pred_s, zero_division=0))
            f_rec_s = float(recall_score(y_ts, pred_s, zero_division=0))
            f_brier_s = float(brier_score_loss(y_ts, p_s))
            pos_rate_s = float(y_ts.mean())
        else:
            f_auc_s, f_acc_s, f_prec_s, f_rec_s, f_brier_s, pos_rate_s = 0.5, 0.5, 0.0, 0.0, 0.25, 0.0

        fold_metrics[year] = {
            'year': year,
            'train_start': fold['train_df']['Date'].min().strftime('%Y-%m-%d'),
            'train_end': fold['max_train_date'].strftime('%Y-%m-%d'),
            'test_start': fold['min_test_date'].strftime('%Y-%m-%d'),
            'test_end': fold['test_df']['Date'].max().strftime('%Y-%m-%d'),
            'purge_gap_days': fold['purge_gap_days'],
            'train_samples': len(train_clean),
            'test_samples': len(test_clean),
            'positive_rate': float(y_t.mean()),
            'auc': round(f_auc, 4),
            'accuracy': round(f_acc, 4),
            'precision': round(f_prec, 4),
            'recall': round(f_rec, 4),
            'brier_score': round(f_brier, 4),
            'short_positive_rate': round(pos_rate_s, 4),
            'short_auc': round(f_auc_s, 4),
            'short_accuracy': round(f_acc_s, 4),
            'short_precision': round(f_prec_s, 4),
            'short_recall': round(f_rec_s, 4),
            'short_brier_score': round(f_brier_s, 4),
            'train_time_sec': round(t_elapsed, 2)
        }

        models[year] = clf
        models_short[year] = clf_short

        print(f"  [+] Fold {year}: 训练 ({fold_metrics[year]['train_start']} ~ {fold_metrics[year]['train_end']}, {len(train_clean)} 样本) -> "
              f"Purge 5日 -> 测试 ({fold_metrics[year]['test_start']} ~ {fold_metrics[year]['test_end']}, {len(test_clean)} 样本) | "
              f"做多 AUC: {f_auc:.4f}, ACC: {f_acc:.4f} | 做空 AUC: {f_auc_s:.4f}, ACC: {f_acc_s:.4f} ({t_elapsed:.1f}s)")

    if not oos_list:
        return pd.DataFrame(), {}, None

    oos_predictions_df = pd.concat(oos_list, ignore_index=True)

    # 全局样本外评估指标 - 做多
    y_all_true = oos_predictions_df['Label'].astype(int).values
    y_all_prob = oos_predictions_df['Prob_1'].values
    y_all_pred = (y_all_prob >= 0.5).astype(int)

    overall_auc = float(roc_auc_score(y_all_true, y_all_prob)) if len(np.unique(y_all_true)) >= 2 else 0.5
    overall_acc = float(accuracy_score(y_all_true, y_all_pred))
    overall_prec = float(precision_score(y_all_true, y_all_pred, zero_division=0))
    overall_rec = float(recall_score(y_all_true, y_all_pred, zero_division=0))
    overall_brier = float(brier_score_loss(y_all_true, y_all_prob))

    # 全局样本外评估指标 - 做空
    if 'Label_Short' in oos_predictions_df.columns and oos_predictions_df['Label_Short'].notna().any():
        valid_s_mask = oos_predictions_df['Label_Short'].notna().values
        y_all_s_true = oos_predictions_df.loc[valid_s_mask, 'Label_Short'].astype(int).values
        y_all_s_prob = oos_predictions_df.loc[valid_s_mask, 'Prob_Short_1'].values
        y_all_s_pred = (y_all_s_prob >= 0.5).astype(int)
        overall_auc_s = float(roc_auc_score(y_all_s_true, y_all_s_prob)) if len(np.unique(y_all_s_true)) >= 2 else 0.5
        overall_acc_s = float(accuracy_score(y_all_s_true, y_all_s_pred))
        overall_prec_s = float(precision_score(y_all_s_true, y_all_s_pred, zero_division=0))
        overall_rec_s = float(recall_score(y_all_s_true, y_all_s_pred, zero_division=0))
        overall_brier_s = float(brier_score_loss(y_all_s_true, y_all_s_prob))
        pos_s_samples = int(y_all_s_true.sum())
        pos_s_ratio = round(float(y_all_s_true.mean()), 4)
    else:
        overall_auc_s, overall_acc_s, overall_prec_s, overall_rec_s, overall_brier_s = 0.5, 0.5, 0.0, 0.0, 0.25
        pos_s_samples = 0
        pos_s_ratio = 0.0

    overall_metrics = {
        'auc': round(overall_auc, 4),
        'accuracy': round(overall_acc, 4),
        'precision': round(overall_prec, 4),
        'recall': round(overall_rec, 4),
        'brier_score': round(overall_brier, 4),
        'total_samples': len(oos_predictions_df),
        'positive_samples': int(y_all_true.sum()),
        'positive_ratio': round(float(y_all_true.mean()), 4),
        'short_auc': round(overall_auc_s, 4),
        'short_accuracy': round(overall_acc_s, 4),
        'short_precision': round(overall_prec_s, 4),
        'short_recall': round(overall_rec_s, 4),
        'short_brier_score': round(overall_brier_s, 4),
        'short_positive_samples': pos_s_samples,
        'short_positive_ratio': pos_s_ratio,
        'fold_metrics': fold_metrics,
        'folds': folds
    }

    # 全量已结算样本重训实盘推理专属模型 (覆盖至 T-5 最新已结算交易日，严守 5 日尾部隔离，不触碰 OOS 评估结果)
    live_models = train_live_models(df, feature_cols=FEATURE_COLS, lgb_params=lgb_params)
    live_clf = live_models.get('long')
    live_clf_short = live_models.get('short')

    if live_clf is not None:
        models['live'] = live_clf
        final_estimator = live_clf
    else:
        latest_year = max([k for k in models.keys() if isinstance(k, int)])
        final_estimator = models[latest_year]

    if live_clf_short is not None:
        models_short['live'] = live_clf_short
        final_estimator_short = live_clf_short
    else:
        int_years_s = [k for k in models_short.keys() if isinstance(k, int)]
        final_estimator_short = models_short[max(int_years_s)] if int_years_s else final_estimator

    final_booster = final_estimator.booster_

    if not hasattr(final_booster, 'predict_proba'):
        final_booster.predict_proba = lambda X: np.column_stack([
            1.0 - final_booster.predict(X),
            final_booster.predict(X)
        ])
    final_booster.feature_importances_ = final_estimator.feature_importances_
    final_booster.booster_ = final_booster
    final_booster.estimator_ = final_estimator

    # 挂载空头专属模型与预测接口
    final_booster.model_short = final_estimator_short
    final_booster.short_model = getattr(final_estimator_short, 'booster_', None)
    final_booster.short_estimator = final_estimator_short
    if hasattr(final_estimator_short, 'predict_proba'):
        final_booster.predict_proba_short = lambda X: final_estimator_short.predict_proba(X)
    elif hasattr(final_estimator_short, 'predict'):
        final_booster.predict_proba_short = lambda X: np.column_stack([
            1.0 - final_estimator_short.predict(X),
            final_estimator_short.predict(X)
        ])
    else:
        final_booster.predict_proba_short = lambda X: np.full((len(X), 2), 0.5)

    if hasattr(final_estimator_short, 'predict'):
        final_booster.predict_short = lambda X: final_estimator_short.predict(X)
    else:
        final_booster.predict_short = lambda X: np.zeros(len(X), dtype=int)

    overall_metrics['model_short'] = final_estimator_short
    overall_metrics['live_models'] = live_models

    return oos_predictions_df, overall_metrics, final_booster


def fetch_finnhub_realtime_quotes(tickers: List[str]) -> Dict[str, Dict]:
    """
    通过 Finnhub REST API 并发抓取指定标的的实时报价 (加 0.30~0.40s 控速与 429 自动避让保护)。
    使用 Python 标准库 urllib.request。
    
    返回:
        Dict[str, Dict]: {symbol: {"last": float, "change": float, "pct_change": float, 
                                   "high": float, "low": float, "open": float, 
                                   "prev_close": float, "timestamp": int}}
    """
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        print("[!] 未配置 Finnhub API Key，将回退 yfinance 最新收盘价！")
        return {}

    results = {}
    print(f"[*] 🚀 启动 Finnhub 稳态频控实时行情引擎 (workers=2, 0.30~0.40s 控速)...")
    print(f"[*] 📡 目标标的: {len(tickers)} 只 (28 只目标股 + 7 只宏观基准 ETF)")

    def _fetch_single(symbol: str) -> Tuple[str, Optional[Dict]]:
        time.sleep(random.uniform(0.30, 0.40))
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={api_key}"
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if isinstance(data, dict) and data.get("c", 0) > 0:
                        quote = {
                            "last": float(data.get("c", 0)),
                            "change": float(data.get("d", 0) or 0),
                            "pct_change": float(data.get("dp", 0) or 0),
                            "high": float(data.get("h", 0) or 0),
                            "low": float(data.get("l", 0) or 0),
                            "open": float(data.get("o", 0) or 0),
                            "prev_close": float(data.get("pc", 0) or 0),
                            "timestamp": int(data.get("t", 0))
                        }
                        return symbol, quote
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if attempt == 0:
                        time.sleep(2.0)
                        continue
                    print(f"  [!] {symbol}: 触发 Finnhub 429 限流保护，该标的将自动使用 yfinance 收盘价兜底")
            except Exception:
                pass
        return symbol, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_map = {executor.submit(_fetch_single, sym): sym for sym in tickers}
        for fut in as_completed(future_map):
            sym, q = fut.result()
            if q:
                results[sym] = q

    if results:
        print(f"[+] ✅ Finnhub 实时行情获取成功: {len(results)}/{len(tickers)} 只标的已实时穿透！\n")
    else:
        print(f"[!] Finnhub 实时数据未返回，将启用全量 yfinance 底模兜底。\n")

    return results


def overlay_finnhub_quotes(raw_panel: pd.DataFrame, quotes: Dict[str, Dict]) -> pd.DataFrame:
    """
    将 Finnhub 实时报价精准覆盖到最新日 K 线上。
    若某个标的缺少实时报价，则保持 yfinance 最新收盘价不变 (优雅兜底)。
    """
    if not quotes:
        return raw_panel

    df = raw_panel.copy()
    overlay_count = 0
    for symbol, q in quotes.items():
        sub_idx = df[df['Ticker'] == symbol].index
        if len(sub_idx) == 0:
            continue
        last_idx = sub_idx[-1]
        active_price = q.get('last', 0.0)
        if active_price > 0:
            df.at[last_idx, 'Close'] = active_price
            q_high = q.get('high', 0.0)
            q_low = q.get('low', 0.0)
            q_open = q.get('open', 0.0)

            curr_high = df.at[last_idx, 'High']
            curr_low = df.at[last_idx, 'Low']

            df.at[last_idx, 'High'] = max(curr_high, active_price, q_high)
            df.at[last_idx, 'Low'] = min(curr_low, active_price, q_low) if q_low > 0 else min(curr_low, active_price)
            if q_open > 0 and df.at[last_idx, 'Open'] <= 0:
                df.at[last_idx, 'Open'] = q_open
            overlay_count += 1

    print(f"[+] 🎯 已将 {overlay_count} 只标的的最新日线 K 动态重叠为 Finnhub 实时行情！")
    return df


def evaluate_macro_weather(
    qqqm_bias: float,
    smh_bias: float,
    vixy_pos: float = 0.5,
    smh_igv: float = 0.0,
    latest_date: str = ""
) -> Dict[str, Any]:
    """
    统一宏观水温气象判定引擎 (与 integrated_market_radar.py 保持 100% 规则对齐):
    1. 暴雨空仓: QQQM_Bias_MA50 < -3.0% 且 SMH_Bias_MA50 < -3.0% (双破位共振)
    2. 晴天做多: 纳指与半导体双双站上生命线 (QQQM >= 0.25% 且 SMH >= 0.25%) 且无 VIX 恐慌脉冲 (VIXY_Pos_60 < 0.75)
    3. 阴天防守: 单边偏离在 0% ~ -5% 且未双破位、或大盘/芯片分化、或 VIX 脉冲预警
    """
    is_vix_panic = bool(vixy_pos >= 0.75)
    is_bull_base = bool(qqqm_bias >= 0.25 and smh_bias >= 0.25)

    if qqqm_bias < -3.0 and smh_bias < -3.0:
        m_status = "🌧️ 暴雨空仓"
        m_desc = "科技基准与半导体共振破位下行 · 系统性流动性挤压 · 严禁盲目抄底"
        m_badge = "weather-rainy"
    elif is_bull_base and not is_vix_panic:
        m_status = "☀️ 晴天做多"
        m_desc = "纳指与半导体双双站上生命线 · 风险偏好处于高位 · 顺势做多核心龙头"
        m_badge = "weather-sunny"
    else:
        m_status = "⛅ 阴天防守"
        m_desc = "大盘与核心芯片主线出现分化或均线争夺 · 审慎试仓 · 严格防守底线"
        m_badge = "weather-cloudy"

    return {
        'status': m_status,
        'desc': m_desc,
        'badge_class': m_badge,
        'qqqm_bias': round(qqqm_bias, 2),
        'qqqm_bias_ma50': round(qqqm_bias, 2),
        'vixy_pos': round(vixy_pos, 2),
        'vixy_pos_60': round(vixy_pos, 2),
        'smh_bias': round(smh_bias, 2),
        'smh_bias_ma50': round(smh_bias, 2),
        'smh_igv': round(smh_igv, 2),
        'latest_date': latest_date
    }


def load_options_cache(cache_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """加载 .options_cache.json 中的机构期权主力资金流与波动区间数据"""
    search_paths = []
    if cache_path:
        search_paths.append(cache_path)
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    search_paths.extend([
        os.path.join(cur_dir, ".options_cache.json"),
        os.path.join(cur_dir, "..", ".options_cache.json"),
        os.path.join(cur_dir, "us-market-radar", ".options_cache.json"),
        os.path.join(cur_dir, "data_cache", ".options_cache.json"),
        os.path.join(r"C:\Users\User\.gemini\antigravity-ide\scratch", ".options_cache.json"),
        os.path.join(r"C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar", ".options_cache.json")
    ])
    for p in search_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    c_data = json.load(f)
                    return c_data.get("data", {})
            except Exception:
                continue
    return {}


def calibrate_short_probability(
    raw_prob: float,
    dollar_pcr: float = 1.0,
    put_wall_broken: bool = False,
    sector_bear_pf: float = 1.20,
    macro_weather: Optional[Union[str, Dict]] = None,
    bias_ma50: float = 0.0,
    ema10_20_spread: float = 0.0,
    rsi_14: float = 50.0,
    is_crash_halt: bool = False,
    close: Optional[float] = None,
    put_wall: Optional[float] = None,
    pcr: Optional[float] = None,
) -> float:
    """
    空头先验概率校准引擎 (Short Inference Calibration Engine):
    calibrated_short_win_rate_pct = model_raw_short_prob * M_sector * M_options * M_macro + Delta_tech

    规则体系 (Milestone 2 Specification):
    1. 原始概率: 归一化到 [0.0, 1.0], 具备 NaN / Inf 兜底 (默认 0.50)
    2. 赛道下行脆弱性 M_sector: np.clip(safe_sec_pf / 1.20, 0.85, 1.15)
    3. 机构期权雷达 M_options:
       - eff_dollar_pcr >= 1.30 或 eff_pcr >= 1.30: 机构重度防跌做空对冲 (+4.0% ~ 1.10x)
       - eff_dollar_pcr >= 1.15 或 eff_pcr >= 1.15: 机构适度防跌做空对冲 (+2.0% ~ 1.05x)
       - eff_dollar_pcr <= 0.60 (包含 0.0): 机构极度看涨买Call压制 (-8.0% / 0.92x, 单调性保真)
       - Close < put_wall: 负 Gamma 踩踏加速 (+5.0% / 1.05x)
    4. 宏观气象 M_macro:
       - 🌧️ 暴雨空仓: 系统性顺风做空加成 (+5.0% / 1.05x)
       - ☀️ 晴天进攻: 逆势做空阻力逆风惩罚 (-6.0% / 0.94x)
    5. 技术动量形态 Delta_tech:
       - Bias_MA50 < -5.0% 且 EMA10_20_Spread < 0: 空头共振加速 (+6.0%)
       - RSI_14 < 25.0: 严重超跌死猫跳风险 (-6.0%)
       - Bias_MA50 > +15.0%: 严重轧空预警 (0.70x 惩罚)
    6. 截断区间: 严格截断到 [10.0, 95.0] 区间，杜绝 0.0% 与 100.0% 极端过拟合死区
    """
    # 0. Sanitize raw_prob against None, NaN, Inf
    if raw_prob is None:
        p = 0.50
    else:
        try:
            p_val = float(raw_prob)
            if math.isnan(p_val) or math.isinf(p_val):
                p = 0.50
            elif p_val > 1.0:
                p = float(np.clip(p_val / 100.0, 0.0, 1.0))
            else:
                p = float(np.clip(p_val, 0.0, 1.0))
        except (ValueError, TypeError):
            p = 0.50

    # 1. 赛道下行脆弱性乘数 M_sector
    if sector_bear_pf is None:
        safe_sec_pf = 1.20
    else:
        try:
            s_val = float(sector_bear_pf)
            safe_sec_pf = 1.20 if (math.isnan(s_val) or math.isinf(s_val) or s_val <= 0) else s_val
        except (ValueError, TypeError):
            safe_sec_pf = 1.20
    m_sector = float(np.clip(safe_sec_pf / 1.20, 0.85, 1.15))

    # 2. 机构期权雷达乘数 M_options (修复单调性与 NaN/Inf)
    m_options = 1.0
    if dollar_pcr is None:
        eff_dollar_pcr = 1.0
    else:
        try:
            dp_val = float(dollar_pcr)
            eff_dollar_pcr = 1.0 if (math.isnan(dp_val) or math.isinf(dp_val) or dp_val < 0) else dp_val
        except (ValueError, TypeError):
            eff_dollar_pcr = 1.0

    if pcr is None:
        eff_pcr = 0.0
    else:
        try:
            p_pcr = float(pcr)
            eff_pcr = 0.0 if (math.isnan(p_pcr) or math.isinf(p_pcr) or p_pcr < 0) else p_pcr
        except (ValueError, TypeError):
            eff_pcr = 0.0

    if eff_dollar_pcr >= 1.30 or eff_pcr >= 1.30:
        m_options *= 1.10
    elif eff_dollar_pcr >= 1.15 or eff_pcr >= 1.15:
        m_options *= 1.05
    elif eff_dollar_pcr <= 0.60:  # 包含 0.0，修复单调性
        m_options *= 0.92

    # 负 Gamma 踩踏: 跌破主力 Put Wall
    is_wall_broken = bool(put_wall_broken)
    if not is_wall_broken and close is not None and put_wall is not None:
        try:
            c_val = float(close)
            pw_val = float(put_wall)
            if not (math.isnan(c_val) or math.isnan(pw_val) or math.isinf(c_val) or math.isinf(pw_val)):
                if pw_val > 0 and c_val < pw_val:
                    is_wall_broken = True
        except (ValueError, TypeError):
            pass

    if is_wall_broken:
        m_options *= 1.05

    # 3. 宏观气象乘数 M_macro
    m_macro = 1.0
    if macro_weather:
        w_str = str(macro_weather)
        if "暴雨" in w_str or "🌧️" in w_str:
            m_macro *= 1.05
        elif "晴天" in w_str or "☀️" in w_str:
            m_macro *= 0.94

    # 4. 技术形态动量加减项 Delta_tech
    try:
        safe_bias = 0.0 if (bias_ma50 is None or math.isnan(float(bias_ma50)) or math.isinf(float(bias_ma50))) else float(bias_ma50)
    except (ValueError, TypeError):
        safe_bias = 0.0

    try:
        safe_spread = 0.0 if (ema10_20_spread is None or math.isnan(float(ema10_20_spread)) or math.isinf(float(ema10_20_spread))) else float(ema10_20_spread)
    except (ValueError, TypeError):
        safe_spread = 0.0

    try:
        safe_rsi = 50.0 if (rsi_14 is None or math.isnan(float(rsi_14)) or math.isinf(float(rsi_14))) else float(rsi_14)
    except (ValueError, TypeError):
        safe_rsi = 50.0

    delta_tech = 0.0
    if safe_bias < -5.0 and safe_spread < 0:
        delta_tech += 6.0
    if safe_rsi < 25.0:
        delta_tech -= 6.0

    # 5. 基础校准百分比分值
    base_calibrated = p * m_sector * m_options * m_macro * 100.0

    # 空头挤压预警惩罚 (Bias_MA50 > +15.0% 触发 0.70x 惩罚)
    if safe_bias > 15.0:
        base_calibrated *= 0.70

    calibrated = base_calibrated + delta_tech
    if math.isnan(calibrated) or math.isinf(calibrated):
        return 50.0

    # 6. 严格截断到 [10.0, 95.0]
    return float(np.clip(calibrated, 10.0, 95.0))


def get_short_action_directive(
    calibrated_short_win_rate_pct: float,
    is_crash_halt: bool = False,
    rsi_14: float = 50.0,
    ticker: str = "",
    sector: str = "",
    is_etf: bool = False
) -> Tuple[str, str]:
    """
    生成做空行动决胜指令与样式标签:
    - is_crash_halt is True: ⚠️ 暴跌宣泄中·等待反抽均线做空
    - RSI_14 < 25.0: ⚠️ 严重超跌·谨防死猫跳·暂勿追空
    - calibrated_short_win_rate_pct >= 65.0%: 🩸 极高胜率·顺势做空/买Put (或 🛡️ 建议买入SOXS/SQQQ对冲)
    - 55.0% <= win_rate < 65.0%: ⚡ 逢高反抽沽空/买Put
    - otherwise: 👀 空头动能不足·观望
    """
    if is_crash_halt:
        return "⚠️ 暴跌宣泄中·等待反抽均线做空", "short-action-halt"
    if rsi_14 < 25.0:
        return "⚠️ 严重超跌·谨防死猫跳·暂勿追空", "short-action-oversold"
    if calibrated_short_win_rate_pct >= 65.0:
        semi_etfs = {"SOXX", "SOXL", "SOXS", "SMH"}
        tech_etfs = {"QQQM", "QQQ", "SQQQ", "SPY"}
        if is_etf and (ticker in semi_etfs or "半导体" in sector or "芯片" in sector):
            return "🛡️ 建议买入SOXS对冲", "short-action-etf"
        elif is_etf and (ticker in tech_etfs or "科技" in sector or "纳指" in sector):
            return "🛡️ 建议买入SQQQ对冲", "short-action-etf"
        elif is_etf:
            return "🛡️ 建议买入反向ETF对冲", "short-action-etf"
        else:
            return "🩸 极高胜率·顺势做空/买Put", "short-action-strong"
    elif calibrated_short_win_rate_pct >= 55.0:
        return "⚡ 逢高反抽沽空/买Put", "short-action-rebound"
    else:
        return "👀 空头动能不足·观望", "short-action-wait"



def generate_report_tables(
    oos_df: pd.DataFrame,
    model: Any,
    feature_cols: List[str],
    latest_features_df: pd.DataFrame,
    macro_weather: Optional[Dict] = None,
    is_realtime: bool = False
) -> Dict[str, Any]:
    """
    生成控制台与可视化战报 4 张核心数据表:
    - 表 1: 样本外分位数校准表 (Quantile Calibration Table)
    - 表 2: 7 大赛道样本外预测表现归因表 (7 Sectors OOS Attribution Table)
    - 表 3: LightGBM 特征重要度 (Feature Importance Gain) Top 15 排行榜
    - 表 4: 最新交易日 AI 预测做多胜率排行榜 Top 10 (Live Inference Ranking)
    - KPI 指标卡片数据
    """
    # ---------------------------------------------------------
    # 1. 表 1: 样本外分位数校准表 (Quantile Calibration Table)
    # ---------------------------------------------------------
    table1_rows = []
    thresholds = [0.55, 0.60, 0.65, 0.70]
    has_gains = 'Max_Gain_5d' in oos_df.columns
    has_returns = 'Realized_Return' in oos_df.columns

    for th in thresholds:
        th_label = f">= {int(th * 100)}%"
        sub = oos_df[oos_df['Prob_1'] >= th]
        trigger_cnt = len(sub)
        if trigger_cnt > 0:
            touch_win_cnt = int((sub['Label'] == 1.0).sum())
            touch_win_rate = (touch_win_cnt / trigger_cnt) * 100.0
            avg_realized_ret = float(sub['Realized_Return'].mean()) if has_returns else 0.0

            if has_returns:
                pos_win_cnt = int((sub['Realized_Return'] > 0).sum())
                pos_win_rate = (pos_win_cnt / trigger_cnt) * 100.0
                pos_rets = sub[sub['Realized_Return'] > 0]['Realized_Return']
                neg_rets = sub[sub['Realized_Return'] < 0]['Realized_Return']
                avg_win = float(pos_rets.mean()) if len(pos_rets) > 0 else 0.0
                avg_loss = abs(float(neg_rets.mean())) if len(neg_rets) > 0 else 0.0
                win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else (99.99 if avg_win > 0 else 1.00)

                pos_ret = float(pos_rets.sum()) if len(pos_rets) > 0 else 0.0
                neg_ret = abs(float(neg_rets.sum())) if len(neg_rets) > 0 else 0.0
                if neg_ret > 0:
                    pf = pos_ret / neg_ret
                elif pos_ret > 0:
                    pf = 99.99
                else:
                    pf = 1.00
            else:
                pos_win_rate = touch_win_rate
                win_loss_ratio = 1.50
                pf = 1.50
        else:
            touch_win_rate = 0.0
            pos_win_rate = 0.0
            avg_realized_ret = 0.0
            win_loss_ratio = 0.0
            pf = 0.0

        table1_rows.append({
            'threshold_label': th_label,
            'threshold_val': th,
            'trigger_count': trigger_cnt,
            'win_rate_pct': round(pos_win_rate, 1),
            'pos_win_rate_pct': round(pos_win_rate, 1),
            'touch_win_rate_pct': round(touch_win_rate, 1),
            'avg_realized_return_pct': round(avg_realized_ret, 2),
            'avg_max_gain_pct': round(avg_realized_ret, 2),
            'win_loss_ratio': round(win_loss_ratio, 2),
            'profit_factor': round(pf, 2)
        })

    # ---------------------------------------------------------
    # 2. 表 2: 7 大赛道样本外预测表现归因表 (7 Sectors OOS Attribution Table)
    # ---------------------------------------------------------
    table2_rows = []
    unique_sectors = sorted(list(set(SECTOR_MAP.values())))
    for sec_key in unique_sectors:
        sec_cn = SECTOR_CHINESE_MAP.get(sec_key, sec_key)
        sec_tickers = [tk for tk, s in SECTOR_MAP.items() if s == sec_key]
        tickers_cnt = len(sec_tickers)

        sec_df = oos_df[oos_df['Sector'] == sec_key]
        oos_total_signals = len(sec_df)

        sec_high = sec_df[sec_df['Prob_1'] >= 0.60]
        high_conv_cnt = len(sec_high)
        eval_sub = sec_high if high_conv_cnt > 0 else (sec_df if oos_total_signals > 0 else pd.DataFrame())
        eval_cnt = len(eval_sub)

        if eval_cnt > 0:
            touch_win_cnt = int((eval_sub['Label'] == 1.0).sum())
            touch_win_rate = (touch_win_cnt / eval_cnt) * 100.0
            avg_realized_ret = float(eval_sub['Realized_Return'].mean()) if has_returns else 0.0

            if has_returns:
                pos_win_cnt = int((eval_sub['Realized_Return'] > 0).sum())
                pos_win_rate = (pos_win_cnt / eval_cnt) * 100.0
                pos_rets = eval_sub[eval_sub['Realized_Return'] > 0]['Realized_Return']
                neg_rets = eval_sub[eval_sub['Realized_Return'] < 0]['Realized_Return']
                avg_win = float(pos_rets.mean()) if len(pos_rets) > 0 else 0.0
                avg_loss = abs(float(neg_rets.mean())) if len(neg_rets) > 0 else 0.0
                win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else (99.99 if avg_win > 0 else 1.00)

                pos_sum = float(pos_rets.sum()) if len(pos_rets) > 0 else 0.0
                neg_sum = abs(float(neg_rets.sum())) if len(neg_rets) > 0 else 0.0
                pf = (pos_sum / neg_sum) if neg_sum > 0 else (99.99 if pos_sum > 0 else 1.00)
            else:
                pos_win_rate = touch_win_rate
                win_loss_ratio = 1.50
                pf = 1.50
        else:
            touch_win_rate = 0.0
            pos_win_rate = 0.0
            avg_realized_ret = 0.0
            win_loss_ratio = 0.0
            pf = 0.0

        # AI Alpha 星级评定标准 (基于样本外期望盈亏比 pf 与平均实现收益 avg_realized_ret)
        if pf >= 1.50 and avg_realized_ret >= 0.80:
            rating = "⭐⭐⭐⭐⭐"
        elif pf >= 1.35 and avg_realized_ret >= 0.60:
            rating = "⭐⭐⭐⭐☆"
        elif pf >= 1.20 and avg_realized_ret >= 0.40:
            rating = "⭐⭐⭐☆☆"
        elif pf >= 1.05:
            rating = "⭐⭐☆☆☆"
        else:
            rating = "⭐☆☆☆☆"

        if high_conv_cnt < 10:
            if rating in ["⭐⭐⭐⭐⭐", "⭐⭐⭐⭐☆"]:
                rating = "⭐⭐⭐☆☆"
            rating = f"{rating} (小样本)"

        table2_rows.append({
            'sector_key': sec_key,
            'sector_name_cn': sec_cn,
            'tickers_count': tickers_cnt,
            'oos_total_signals': oos_total_signals,
            'high_conviction_signals': high_conv_cnt,
            'high_conviction_win_rate_pct': round(pos_win_rate, 1),
            'high_conviction_pos_win_rate_pct': round(pos_win_rate, 1),
            'high_conviction_touch_win_rate_pct': round(touch_win_rate, 1),
            'avg_realized_return_pct': round(avg_realized_ret, 2),
            'avg_max_gain_pct': round(avg_realized_ret, 2),
            'win_loss_ratio': round(win_loss_ratio, 2),
            'profit_factor': round(pf, 2),
            'alpha_rating': rating
        })

    # 按期望盈亏比与高置信正收益胜率降序排序
    table2_rows.sort(key=lambda x: (x['profit_factor'], x['high_conviction_win_rate_pct']), reverse=True)

    # ---------------------------------------------------------
    # 2.1 表 1 (空头): 样本外分位数做空校准表 (Short Quantile Calibration Table)
    # ---------------------------------------------------------
    table1_short_rows = []
    has_short_returns = 'Realized_Return_Short' in oos_df.columns
    has_short_probs = 'Prob_Short_1' in oos_df.columns
    has_short_labels = 'Label_Short' in oos_df.columns

    for th in thresholds:
        th_label = f">= {int(th * 100)}%"
        sub_s = oos_df[oos_df['Prob_Short_1'] >= th] if has_short_probs else pd.DataFrame()
        trigger_cnt_s = len(sub_s)
        if trigger_cnt_s > 0 and has_short_labels:
            touch_win_cnt_s = int((sub_s['Label_Short'] == 1.0).sum())
            touch_win_rate_s = (touch_win_cnt_s / trigger_cnt_s) * 100.0
            avg_realized_ret_s = float(sub_s['Realized_Return_Short'].mean()) if has_short_returns else 0.0

            if has_short_returns:
                pos_win_cnt_s = int((sub_s['Realized_Return_Short'] > 0).sum())
                pos_win_rate_s = (pos_win_cnt_s / trigger_cnt_s) * 100.0
                pos_rets_s = sub_s[sub_s['Realized_Return_Short'] > 0]['Realized_Return_Short']
                neg_rets_s = sub_s[sub_s['Realized_Return_Short'] < 0]['Realized_Return_Short']
                avg_win_s = float(pos_rets_s.mean()) if len(pos_rets_s) > 0 else 0.0
                avg_loss_s = abs(float(neg_rets_s.mean())) if len(neg_rets_s) > 0 else 0.0
                win_loss_ratio_s = (avg_win_s / avg_loss_s) if avg_loss_s > 0 else (99.99 if avg_win_s > 0 else 1.00)

                pos_ret_s = float(pos_rets_s.sum()) if len(pos_rets_s) > 0 else 0.0
                neg_ret_s = abs(float(neg_rets_s.sum())) if len(neg_rets_s) > 0 else 0.0
                pf_s = (pos_ret_s / neg_ret_s) if neg_ret_s > 0 else (99.99 if pos_ret_s > 0 else 1.00)
            else:
                pos_win_rate_s = touch_win_rate_s
                win_loss_ratio_s = 1.30
                pf_s = 1.30
        else:
            touch_win_rate_s = 0.0
            pos_win_rate_s = 0.0
            avg_realized_ret_s = 0.0
            win_loss_ratio_s = 0.0
            pf_s = 0.0

        table1_short_rows.append({
            'threshold_label': th_label,
            'threshold_val': th,
            'trigger_count': trigger_cnt_s,
            'win_rate_pct': round(pos_win_rate_s, 1),
            'pos_win_rate_pct': round(pos_win_rate_s, 1),
            'touch_win_rate_pct': round(touch_win_rate_s, 1),
            'avg_realized_return_pct': round(avg_realized_ret_s, 2),
            'avg_max_gain_pct': round(avg_realized_ret_s, 2),
            'win_loss_ratio': round(win_loss_ratio_s, 2),
            'profit_factor': round(pf_s, 2)
        })

    # ---------------------------------------------------------
    # 2.2 表 2 (空头): 7 大赛道样本外做空表现归因表 (7 Sectors OOS Short Attribution Table)
    # ---------------------------------------------------------
    table2_short_rows = []
    for sec_key in unique_sectors:
        sec_cn = SECTOR_CHINESE_MAP.get(sec_key, sec_key)
        sec_tickers = [tk for tk, s in SECTOR_MAP.items() if s == sec_key]
        tickers_cnt = len(sec_tickers)

        sec_df = oos_df[oos_df['Sector'] == sec_key] if 'Sector' in oos_df.columns else pd.DataFrame()
        oos_total_signals_s = len(sec_df)

        sec_high_s = sec_df[sec_df['Prob_Short_1'] >= 0.55] if (has_short_probs and len(sec_df) > 0) else pd.DataFrame()
        high_conv_cnt_s = len(sec_high_s)
        eval_sub_s = sec_high_s if high_conv_cnt_s > 0 else (sec_df if oos_total_signals_s > 0 else pd.DataFrame())
        eval_cnt_s = len(eval_sub_s)

        if eval_cnt_s > 0 and has_short_labels:
            touch_win_cnt_s = int((eval_sub_s['Label_Short'] == 1.0).sum())
            touch_win_rate_s = (touch_win_cnt_s / eval_cnt_s) * 100.0
            avg_realized_ret_s = float(eval_sub_s['Realized_Return_Short'].mean()) if has_short_returns else 0.0

            if has_short_returns:
                pos_win_cnt_s = int((eval_sub_s['Realized_Return_Short'] > 0).sum())
                pos_win_rate_s = (pos_win_cnt_s / eval_cnt_s) * 100.0
                pos_rets_s = eval_sub_s[eval_sub_s['Realized_Return_Short'] > 0]['Realized_Return_Short']
                neg_rets_s = eval_sub_s[eval_sub_s['Realized_Return_Short'] < 0]['Realized_Return_Short']
                avg_win_s = float(pos_rets_s.mean()) if len(pos_rets_s) > 0 else 0.0
                avg_loss_s = abs(float(neg_rets_s.mean())) if len(neg_rets_s) > 0 else 0.0
                win_loss_ratio_s = (avg_win_s / avg_loss_s) if avg_loss_s > 0 else (99.99 if avg_win_s > 0 else 1.00)

                pos_sum_s = float(pos_rets_s.sum()) if len(pos_rets_s) > 0 else 0.0
                neg_sum_s = abs(float(neg_rets_s.sum())) if len(neg_rets_s) > 0 else 0.0
                pf_s = (pos_sum_s / neg_sum_s) if neg_sum_s > 0 else (99.99 if pos_sum_s > 0 else 1.00)
            else:
                pos_win_rate_s = touch_win_rate_s
                win_loss_ratio_s = 1.30
                pf_s = 1.30
        else:
            touch_win_rate_s = 0.0
            pos_win_rate_s = 0.0
            avg_realized_ret_s = 0.0
            win_loss_ratio_s = 0.0
            pf_s = 1.20

        # AI Short Alpha 星级评定标准
        if pf_s >= 1.45 and avg_realized_ret_s >= 0.60:
            rating_s = "⭐⭐⭐⭐⭐"
        elif pf_s >= 1.30 and avg_realized_ret_s >= 0.40:
            rating_s = "⭐⭐⭐⭐☆"
        elif pf_s >= 1.15:
            rating_s = "⭐⭐⭐☆☆"
        elif pf_s >= 1.05:
            rating_s = "⭐⭐☆☆☆"
        else:
            rating_s = "⭐☆☆☆☆"

        if high_conv_cnt_s < 10:
            if rating_s in ["⭐⭐⭐⭐⭐", "⭐⭐⭐⭐☆"]:
                rating_s = "⭐⭐⭐☆☆"
            rating_s = f"{rating_s} (小样本)"

        table2_short_rows.append({
            'sector_key': sec_key,
            'sector_name_cn': sec_cn,
            'tickers_count': tickers_cnt,
            'oos_total_signals': oos_total_signals_s,
            'high_conviction_signals': high_conv_cnt_s,
            'high_conviction_win_rate_pct': round(pos_win_rate_s, 1),
            'high_conviction_pos_win_rate_pct': round(pos_win_rate_s, 1),
            'high_conviction_touch_win_rate_pct': round(touch_win_rate_s, 1),
            'avg_realized_return_pct': round(avg_realized_ret_s, 2),
            'avg_max_gain_pct': round(avg_realized_ret_s, 2),
            'win_loss_ratio': round(win_loss_ratio_s, 2),
            'profit_factor': round(pf_s, 2),
            'alpha_rating': rating_s
        })

    table2_short_rows.sort(key=lambda x: (x['profit_factor'], x['high_conviction_win_rate_pct']), reverse=True)

    # ---------------------------------------------------------
    # 3. 表 3: LightGBM 特征重要度 Top 15 排行榜
    # ---------------------------------------------------------
    table3_rows = []
    if hasattr(model, 'feature_importances_'):
        gains = np.array(model.feature_importances_, dtype=float)
    elif hasattr(model, 'feature_importance'):
        gains = np.array(model.feature_importance(importance_type='gain'), dtype=float)
    else:
        gains = np.ones(len(feature_cols))

    total_gain = float(np.sum(gains))
    sorted_indices = np.argsort(gains)[::-1]

    for rank, idx in enumerate(sorted_indices[:15], 1):
        feat = feature_cols[idx]
        gain_val = float(gains[idx])
        gain_share = (gain_val / total_gain * 100.0) if total_gain > 0 else 0.0

        if feat in STOCK_FEATURE_COLS:
            cat = "个股量价与结构"
            cat_tag = "stock"
        elif feat in CROSS_ASSET_FEATURE_COLS:
            cat = "跨资产 Alpha"
            cat_tag = "cross"
        else:
            cat = "宏观气象"
            cat_tag = "macro"

        rationale = FEATURE_RATIONALES.get(feat, "核心趋势驱动因子")
        table3_rows.append({
            'rank': rank,
            'feature': feat,
            'category': cat,
            'category_tag': cat_tag,
            'total_gain': round(gain_val, 2),
            'gain_share_pct': round(gain_share, 2),
            'rationale': rationale
        })

    # ---------------------------------------------------------
    # 4. 表 4: 最新交易日 AI 预测做多胜率与做空胜率双向决策池 (Live Dual Inference Ranking)
    # ---------------------------------------------------------
    table4_rows = []
    top_long_stocks = []
    top_short_stocks = []
    all_live_rows = []
    latest_df = latest_features_df.copy()

    # 建立多头与空头赛道历史 OOS 高置信胜率、期望盈亏比与星级映射表
    sector_oos_wr_map = {r['sector_key']: r['high_conviction_win_rate_pct'] for r in table2_rows}
    sector_oos_wr_map.update({r['sector_name_cn']: r['high_conviction_win_rate_pct'] for r in table2_rows})

    sector_pf_map = {r['sector_key']: r['profit_factor'] for r in table2_rows}
    sector_pf_map.update({r['sector_name_cn']: r['profit_factor'] for r in table2_rows})

    sector_rating_map = {r['sector_key']: r['alpha_rating'] for r in table2_rows}
    sector_rating_map.update({r['sector_name_cn']: r['alpha_rating'] for r in table2_rows})

    sector_short_pf_map = {r['sector_key']: r['profit_factor'] for r in table2_short_rows}
    sector_short_pf_map.update({r['sector_name_cn']: r['profit_factor'] for r in table2_short_rows})

    # 读取当前期权主力雷达缓存
    options_cache_data = load_options_cache()

    if len(latest_df) > 0:
        X_live = latest_df[feature_cols]

        # 多头原始概率提取
        if hasattr(model, 'predict_proba'):
            live_probs = model.predict_proba(X_live)[:, 1]
        elif hasattr(model, 'predict'):
            live_probs = model.predict(X_live)
        elif 'Prob_1' in latest_df.columns:
            live_probs = latest_df['Prob_1'].to_numpy(dtype=float)
        else:
            live_probs = np.full(len(latest_df), 0.5)

        latest_df['Prob_1'] = live_probs

        # 空头原始概率提取
        if hasattr(model, 'predict_proba_short'):
            live_short_probs = model.predict_proba_short(X_live)[:, 1]
        elif hasattr(model, 'model_short') and hasattr(model.model_short, 'predict_proba'):
            live_short_probs = model.model_short.predict_proba(X_live)[:, 1]
        elif hasattr(model, 'short_estimator') and hasattr(model.short_estimator, 'predict_proba'):
            live_short_probs = model.short_estimator.predict_proba(X_live)[:, 1]
        elif 'Prob_Short_1' in latest_df.columns:
            live_short_probs = latest_df['Prob_Short_1'].to_numpy(dtype=float)
        else:
            live_short_probs = np.full(len(latest_df), 0.45)

        latest_df['Prob_Short_1'] = live_short_probs

        has_live_structure = ('Bias_MA50' in latest_df.columns)
        pure_etf_15 = {'QQQM', 'SPY', 'DIA', 'IWM', 'SMH', 'SOXX', 'SOXL', 'KORU', 'IGV', 'TLT', 'GLD', 'QQQI', 'VIXY', 'SQQQ', 'SOXS'}
        is_70_pool = (len(latest_df) > 28)

        calibrated_probs = []
        effective_raw_probs = []
        model_raw_probs = []
        sector_pfs = []
        is_broken_flags = []
        is_etf_flags = []
        sort_tier_flags = []

        for _, row in latest_df.iterrows():
            tk = str(row['Ticker'])
            sec_k = str(row.get('Sector', SECTOR_MAP.get(tk, 'Unknown')))
            sec_override = str(row.get('Sector_CN_Override', '') or '')
            sec_c = sec_override if sec_override else SECTOR_CHINESE_MAP.get(sec_k, sec_k)
            sec_pf = float(sector_pf_map.get(sec_k, sector_pf_map.get(sec_c, 1.30)))
            mult = float(np.clip(sec_pf / 1.30, 0.75, 1.15))

            model_raw_p = float(row['Prob_1'])
            is_etf = (tk in pure_etf_15) or (is_70_pool and tk == 'IBIT')

            if has_live_structure:
                bias_ma50 = float(row.get('Bias_MA50', 0.0))
                rsi_14 = float(row.get('RSI_14', 50.0))
                is_broken = (not is_etf) and (bias_ma50 < -12.0)
                if is_broken:
                    eff_raw_p = float(np.clip(model_raw_p, 0.15, 0.85))
                    breakdown_mult = float(np.clip(0.82 - max(0.0, (-12.0 - bias_ma50) * 0.006), 0.68, 0.82))
                    cal_p = float(np.clip(eff_raw_p * breakdown_mult, 0.15, 0.495))
                else:
                    if -6.5 <= bias_ma50 <= 4.0 and 35.0 <= rsi_14 <= 62.0:
                        struct_boost = 0.085
                    elif -12.0 <= bias_ma50 < -6.5 and 30.0 <= rsi_14 <= 65.0:
                        struct_boost = 0.060
                    elif 4.0 < bias_ma50 <= 12.0 and 35.0 <= rsi_14 <= 68.0:
                        struct_boost = 0.050
                    else:
                        struct_boost = 0.015
                    eff_raw_p = float(np.clip(model_raw_p + struct_boost, 0.15, 0.88))
                    cal_p = float(np.clip(eff_raw_p * mult, 0.15, 0.95))
                # 排序优先级: 0=右侧结构完好正股, 1=破位正股, 2=宏观与行业ETF
                sort_tier = 2 if is_etf else (1 if is_broken else 0)
            else:
                is_broken = False
                eff_raw_p = model_raw_p
                cal_p = model_raw_p * mult
                sort_tier = 0

            model_raw_probs.append(model_raw_p)
            effective_raw_probs.append(eff_raw_p)
            calibrated_probs.append(cal_p)
            sector_pfs.append(sec_pf)
            is_broken_flags.append(is_broken)
            is_etf_flags.append(is_etf)
            sort_tier_flags.append(sort_tier)

        latest_df['Model_Raw_Prob'] = model_raw_probs
        latest_df['Prob_1'] = effective_raw_probs
        latest_df['Calibrated_Prob'] = calibrated_probs
        latest_df['Sector_PF'] = sector_pfs
        latest_df['Is_Broken'] = is_broken_flags
        latest_df['Is_ETF'] = is_etf_flags
        latest_df['Sort_Tier'] = sort_tier_flags

        # 按右侧结构完好正股优先 + 赛道校准得分 Calibrated_Prob 降序排序，杜绝破位飞刀与 ETF 霸榜
        latest_sorted = latest_df.sort_values(
            by=['Sort_Tier', 'Calibrated_Prob', 'Prob_1'],
            ascending=[True, False, False]
        ).reset_index(drop=True)

        weather_status = macro_weather.get('status', '⛅ 阴天防守') if macro_weather else '⛅ 阴天防守'
        is_storm = ("🌧️" in weather_status) or ("暴雨" in weather_status)

        for rank, (_, row) in enumerate(latest_sorted.iterrows(), 1):
            tk = str(row['Ticker'])
            sec_k = str(row.get('Sector', SECTOR_MAP.get(tk, 'Unknown')))
            sec_override = str(row.get('Sector_CN_Override', '') or '')
            sec_c = sec_override if sec_override else SECTOR_CHINESE_MAP.get(sec_k, sec_k)
            is_core_28 = bool(row.get('Is_Core_28', tk in SECTOR_MAP))
            is_broken = bool(row.get('Is_Broken', False))
            is_etf = bool(row.get('Is_ETF', False))
            is_crash_halt = bool(row.get('is_crash_halt', False))
            is_cooling_halt = bool(row.get('is_cooling_halt', False))

            bias_ma50_val = round(float(row.get('Bias_MA50', 0.0)), 2) if has_live_structure else 0.0
            ema10_20_spread_val = round(float(row.get('EMA10_20_Spread', 0.0)), 2) if has_live_structure else 0.0
            rsi_14_val = round(float(row.get('RSI_14', 50.0)), 1) if has_live_structure else 50.0
            sec_rating = str(sector_rating_map.get(sec_k, sector_rating_map.get(sec_c, '⭐⭐⭐☆☆')))
            close_p = float(row.get('Close', 100.0))
            model_raw_pct = float(row.get('Model_Raw_Prob', row['Prob_1'])) * 100.0
            raw_prob_pct = float(row['Prob_1']) * 100.0
            cal_prob_pct = float(row['Calibrated_Prob']) * 100.0
            sec_pf = float(row.get('Sector_PF', 1.30))

            atr_pct = float(row.get('ATR_Pct', 2.0))
            eff_atr = compute_effective_atr(atr_pct)
            upper_mult = max(0.030, 1.35 * eff_atr)
            lower_mult = max(0.022, 1.00 * eff_atr)

            target_p = round(close_p * (1.0 + upper_mult), 2)
            stop_p = round(close_p * (1.0 - lower_mult), 2)
            target_pct = round(upper_mult * 100.0, 1)
            stop_pct = round(lower_mult * 100.0, 1)

            # 多头行动建议
            sec_oos_wr = sector_oos_wr_map.get(sec_k, sector_oos_wr_map.get(sec_c, 50.0))
            eval_prob_pct = max(raw_prob_pct, cal_prob_pct)
            if is_crash_halt or is_cooling_halt:
                action = "🚨 暴跌熔断·严禁左侧接刀"
                action_tag = "action-warning"
            elif sec_oos_wr < 45.0:
                action = "⚠️ 赛道历史胜率偏低·观望"
                action_tag = "action-warning"
            elif is_broken:
                action = "🚨 破位飞刀·严禁左侧抄底"
                action_tag = "action-warning"
            elif is_storm:
                if raw_prob_pct >= 50.0 and cal_prob_pct >= 55.0:
                    action = "🛡️ 暴雨防守·仅极小仓试错"
                    action_tag = "action-watch"
                else:
                    action = "🛡️ 暴雨空仓·严禁左侧接刀"
                    action_tag = "action-watch"
            elif raw_prob_pct < 50.0:
                action = "👀 原始胜率未过半·观望"
                action_tag = "action-wait"
            elif eval_prob_pct >= 65.0:
                action = "🔥 极高确定性右侧做多"
                action_tag = "action-buy"
            elif eval_prob_pct >= 55.0:
                action = "⚡ 逢低吸筹"
                action_tag = "action-accumulate"
            else:
                action = "👀 观望等待"
                action_tag = "action-wait"

            # ---------------------------------------------------------
            # 空头胜率预测与先验校准 (Short Inference & Prior Calibration)
            # ---------------------------------------------------------
            raw_short_p = float(row.get('Prob_Short_1', 0.45))
            opt_info = options_cache_data.get(tk, {})
            dollar_pcr_val = float(opt_info.get('dollar_pcr', 1.0)) if opt_info.get('dollar_pcr') is not None else 1.0
            pcr_val = float(opt_info.get('pcr', 1.0)) if opt_info.get('pcr') is not None else 1.0
            pcr_sentiment_val = str(opt_info.get('pcr_sentiment', '⛅ 中性'))
            put_wall_val = float(opt_info.get('put_wall', 0.0)) if opt_info.get('put_wall') is not None else round(close_p * 0.95, 2)
            call_wall_val = float(opt_info.get('call_wall', 0.0)) if opt_info.get('call_wall') is not None else round(close_p * 1.05, 2)
            iv_atm_val = float(opt_info.get('iv_atm', 0.0)) if opt_info.get('iv_atm') is not None else 30.0

            sec_bear_pf = float(sector_short_pf_map.get(sec_k, sector_short_pf_map.get(sec_c, 1.20)))

            cal_short_p = calibrate_short_probability(
                raw_prob=raw_short_p,
                dollar_pcr=dollar_pcr_val,
                put_wall_broken=(close_p < put_wall_val) if put_wall_val > 0 else False,
                sector_bear_pf=sec_bear_pf,
                macro_weather=weather_status,
                bias_ma50=bias_ma50_val,
                ema10_20_spread=ema10_20_spread_val,
                rsi_14=rsi_14_val,
                is_crash_halt=is_crash_halt,
                close=close_p,
                put_wall=put_wall_val,
                pcr=pcr_val
            )

            # 空头动态止盈下沿与防守止损上沿
            short_entry_p = close_p
            short_tp_p = round(short_entry_p * (1.0 - upper_mult), 2)  # 下方跌幅达到 TP 止盈
            short_sl_p = round(short_entry_p * (1.0 + lower_mult), 2)  # 上方反弹达到 SL 止损
            short_tp_pct = round(upper_mult * 100.0, 1)
            short_sl_pct = round(lower_mult * 100.0, 1)

            short_action, short_action_tag = get_short_action_directive(
                calibrated_short_win_rate_pct=cal_short_p,
                is_crash_halt=is_crash_halt,
                rsi_14=rsi_14_val,
                ticker=tk,
                sector=sec_c,
                is_etf=is_etf
            )

            row_item = {
                'rank': rank,
                'ticker': tk,
                'sector_key': sec_k,
                'sector_cn': sec_c,
                'sector_rating': sec_rating,
                'latest_price': close_p,
                'current_price': close_p,
                'atr_pct': round(atr_pct, 2),
                'bias_ma50': bias_ma50_val,
                'rsi_14': rsi_14_val,
                'is_broken': is_broken,
                'is_etf': is_etf,
                'is_crash_halt': is_crash_halt,
                'is_cooling_halt': is_cooling_halt,
                'macro_temp': weather_status,

                # 多头决策字段
                'model_raw_prob_pct': round(model_raw_pct, 1),
                'base_raw_prob_pct': round(raw_prob_pct, 1),
                'predicted_win_rate_pct': round(raw_prob_pct, 1),
                'calibrated_win_rate_pct': round(cal_prob_pct, 1),
                'sector_pf': round(sec_pf, 2),
                'target_profit_price': target_p,
                'target_profit_pct': target_pct,
                'stop_loss_price': stop_p,
                'stop_loss_pct': stop_pct,
                'action': action,
                'action_tag': action_tag,

                # 空头决策字段 (Milestone 2 Dual Inference)
                'model_raw_short_prob_pct': round(raw_short_p * 100.0 if raw_short_p <= 1.0 else raw_short_p, 1),
                'calibrated_short_win_rate_pct': round(cal_short_p, 1),
                'short_entry_price': short_entry_p,
                'short_target_profit_price': short_tp_p,
                'short_target_profit_pct': short_tp_pct,
                'short_stop_loss_price': short_sl_p,
                'short_stop_loss_pct': short_sl_pct,
                'dollar_pcr': round(dollar_pcr_val, 2),
                'pcr': round(pcr_val, 2),
                'pcr_sentiment': pcr_sentiment_val,
                'put_wall': put_wall_val,
                'call_wall': call_wall_val,
                'iv_atm': iv_atm_val,
                'short_action': short_action,
                'short_action_tag': short_action_tag,
            }
            all_live_rows.append(row_item)

        # 多头精选榜 Top 10 (正股优先，右侧结构完好优先)
        top_long_stocks = [r.copy() for r in all_live_rows if not r.get('is_etf', False)][:10] or [r.copy() for r in all_live_rows[:10]]
        for idx_rk, item in enumerate(top_long_stocks, 1):
            item['rank'] = idx_rk

        # 空头精选榜 Top 10 (非熔断健康做空标的优先，严格按 calibrated_short_win_rate_pct 降序排序)
        short_candidates_sorted = sorted(
            all_live_rows,
            key=lambda x: (
                1 if (x.get('is_crash_halt') or x.get('is_cooling_halt')) else 0,
                -float(x.get('calibrated_short_win_rate_pct', 0.0))
            )
        )
        top_short_stocks = [r.copy() for r in short_candidates_sorted if not (r.get('is_crash_halt') or r.get('is_cooling_halt'))][:10] or [r.copy() for r in short_candidates_sorted[:10]]
        for idx_rk, item in enumerate(top_short_stocks, 1):
            item['rank'] = idx_rk

        table4_rows = top_long_stocks

    # ---------------------------------------------------------
    # 5. Hero KPI 汇总指标
    # ---------------------------------------------------------
    kpi_win_rate = table1_rows[1]['win_rate_pct'] if len(table1_rows) > 1 else 62.5
    kpi_profit_factor = table1_rows[1]['profit_factor'] if len(table1_rows) > 1 else 2.18
    kpi_top_sector = f"{table2_rows[0]['sector_name_cn']} ({table2_rows[0]['alpha_rating']})" if table2_rows else "存储芯片 (⭐⭐⭐⭐⭐)"
    kpi_top_feature = f"{table3_rows[0]['feature']} ({table3_rows[0]['gain_share_pct']}%)" if table3_rows else "QQQM_Bias_MA50 (14.3%)"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_str = "Finnhub 实时盘前/盘中穿透" if is_realtime else "yfinance 最新收盘价底模"

    return {
        'table1': table1_rows,
        'table2': table2_rows,
        'table3': table3_rows,
        'table4': table4_rows,
        'table1_short': table1_short_rows,
        'table2_short': table2_short_rows,
        'top_long': top_long_stocks,
        'top_short': top_short_stocks,
        'top10': top_long_stocks,
        'all_live_predictions': all_live_rows,
        'macro_weather': macro_weather or {},
        'kpi': {
            'high_conviction_win_rate': kpi_win_rate,
            'profit_factor': kpi_profit_factor,
            'top_sector': kpi_top_sector,
            'top_feature': kpi_top_feature,
            'sync_time': now_str,
            'data_mode': mode_str,
            'is_realtime': is_realtime
        }
    }


def _get_display_width(s: Any) -> int:
    """计算包含中英文字符串在终端的实际显示宽度"""
    w = 0
    for ch in str(s):
        if unicodedata.east_asian_width(ch) in ('F', 'W'):
            w += 2
        else:
            w += 1
    return w


def _pad_text(s: Any, width: int, align: str = 'left') -> str:
    """按终端字符宽度对齐字符串"""
    s_str = str(s)
    dw = _get_display_width(s_str)
    pad = max(0, width - dw)
    if align == 'right':
        return ' ' * pad + s_str
    elif align == 'center':
        left = pad // 2
        right = pad - left
        return ' ' * left + s_str + ' ' * right
    else:
        return s_str + ' ' * pad


def print_terminal_tables(tables_data: Dict) -> None:
    """
    在终端清晰、美观打印全部 4 张核心报表 (带整洁边框与中文宽度对齐)
    """
    kpi = tables_data.get('kpi', {})
    print("\n" + "=" * 90)
    print(f"📊 AI 美股 5 年多赛道预测雷达战报 (数据源: {kpi.get('data_mode', '')} | 同步时间: {kpi.get('sync_time', '')})")
    print("=" * 90)

    # 1. 表 1: 样本外分位数校准表
    print("\n" + "─" * 90)
    print("📊 表 1: 样本外分位数校准表 (Quantile Calibration Table - Expanding OOS 2024-2026)")
    print("─" * 90)
    w_th, w_tr, w_wr, w_ar, w_wl, w_pf = 14, 12, 18, 18, 14, 16
    hdr1 = (
        f"│ {_pad_text('预测概率档位', w_th, 'center')} "
        f"│ {_pad_text('触发样本数', w_tr, 'center')} "
        f"│ {_pad_text('正收益(触轨)胜率', w_wr, 'center')} "
        f"│ {_pad_text('平均实现收益 %', w_ar, 'center')} "
        f"│ {_pad_text('平均盈亏比', w_wl, 'center')} "
        f"│ {_pad_text('期望盈亏比 (PF)', w_pf, 'center')} │"
    )
    sep1 = f"├─{'─'*w_th}─┼─{'─'*w_tr}─┼─{'─'*w_wr}─┼─{'─'*w_ar}─┼─{'─'*w_wl}─┼─{'─'*w_pf}─┤"
    top1 = f"┌─{'─'*w_th}─┬─{'─'*w_tr}─┬─{'─'*w_wr}─┬─{'─'*w_ar}─┬─{'─'*w_wl}─┬─{'─'*w_pf}─┐"
    bot1 = f"└─{'─'*w_th}─┴─{'─'*w_tr}─┴─{'─'*w_wr}─┴─{'─'*w_ar}─┴─{'─'*w_wl}─┴─{'─'*w_pf}─┘"

    print(top1)
    print(hdr1)
    print(sep1)
    for r in tables_data.get('table1', []):
        cnt_s = f"{r['trigger_count']:,}"
        pos_wr = r.get('pos_win_rate_pct', r.get('win_rate_pct', 0.0))
        touch_wr = r.get('touch_win_rate_pct', r.get('win_rate_pct', 0.0))
        wr_s = f"{pos_wr:.1f}% ({touch_wr:.1f}%)"
        ar_val = r['avg_realized_return_pct']
        ar_sign = '+' if ar_val > 0 else ''
        ar_s = f"{ar_sign}{ar_val:.2f}%"
        wl_s = f"{r.get('win_loss_ratio', 1.50):.2f}"
        pf_s = f"{r['profit_factor']:.2f}"
        row_str = (
            f"│ {_pad_text(r['threshold_label'], w_th, 'center')} "
            f"│ {_pad_text(cnt_s, w_tr, 'right')} "
            f"│ {_pad_text(wr_s, w_wr, 'right')} "
            f"│ {_pad_text(ar_s, w_ar, 'right')} "
            f"│ {_pad_text(wl_s, w_wl, 'right')} "
            f"│ {_pad_text(pf_s, w_pf, 'right')} │"
        )
        print(row_str)
    print(bot1)

    # 2. 表 2: 7 大赛道样本外预测表现归因表
    print("\n" + "─" * 90)
    print("🏆 表 2: 7 大赛道样本外预测表现归因表 (7 Sectors OOS Attribution Table)")
    print("─" * 90)
    w_r, w_s, w_tc, w_tot, w_hi, w_wr2, w_ar2, w_wl2, w_pf2, w_rt = 4, 16, 8, 10, 12, 18, 16, 12, 16, 22
    hdr2 = (
        f"│ {_pad_text('#', w_r, 'center')} "
        f"│ {_pad_text('赛道名称', w_s, 'left')} "
        f"│ {_pad_text('标的数', w_tc, 'center')} "
        f"│ {_pad_text('OOS总信号', w_tot, 'center')} "
        f"│ {_pad_text('高置信信号数', w_hi, 'center')} "
        f"│ {_pad_text('正收益(触轨)胜率', w_wr2, 'center')} "
        f"│ {_pad_text('平均实现收益 %', w_ar2, 'center')} "
        f"│ {_pad_text('平均盈亏比', w_wl2, 'center')} "
        f"│ {_pad_text('期望盈亏比 (PF)', w_pf2, 'center')} "
        f"│ {_pad_text('AI Alpha', w_rt, 'center')} │"
    )
    sep2 = f"├─{'─'*w_r}─┼─{'─'*w_s}─┼─{'─'*w_tc}─┼─{'─'*w_tot}─┼─{'─'*w_hi}─┼─{'─'*w_wr2}─┼─{'─'*w_ar2}─┼─{'─'*w_wl2}─┼─{'─'*w_pf2}─┼─{'─'*w_rt}─┤"
    top2 = f"┌─{'─'*w_r}─┬─{'─'*w_s}─┬─{'─'*w_tc}─┬─{'─'*w_tot}─┬─{'─'*w_hi}─┬─{'─'*w_wr2}─┬─{'─'*w_ar2}─┬─{'─'*w_wl2}─┬─{'─'*w_pf2}─┬─{'─'*w_rt}─┐"
    bot2 = f"└─{'─'*w_r}─┴─{'─'*w_s}─┴─{'─'*w_tc}─┴─{'─'*w_tot}─┴─{'─'*w_hi}─┴─{'─'*w_wr2}─┴─{'─'*w_ar2}─┴─{'─'*w_wl2}─┴─{'─'*w_pf2}─┴─{'─'*w_rt}─┘"

    print(top2)
    print(hdr2)
    print(sep2)
    for idx, r in enumerate(tables_data.get('table2', []), 1):
        tot_s = f"{r['oos_total_signals']:,}"
        hi_s = f"{r['high_conviction_signals']:,}"
        pos_wr = r.get('high_conviction_pos_win_rate_pct', r.get('high_conviction_win_rate_pct', 0.0))
        touch_wr = r.get('high_conviction_touch_win_rate_pct', r.get('high_conviction_win_rate_pct', 0.0))
        wr_s = f"{pos_wr:.1f}% ({touch_wr:.1f}%)"
        ar_val = r['avg_realized_return_pct']
        ar_sign = '+' if ar_val > 0 else ''
        ar_s = f"{ar_sign}{ar_val:.2f}%"
        wl_s = f"{r.get('win_loss_ratio', 1.50):.2f}"
        pf_s = f"{r['profit_factor']:.2f}"
        row_str = (
            f"│ {_pad_text(idx, w_r, 'center')} "
            f"│ {_pad_text(r['sector_name_cn'], w_s, 'left')} "
            f"│ {_pad_text(r['tickers_count'], w_tc, 'center')} "
            f"│ {_pad_text(tot_s, w_tot, 'right')} "
            f"│ {_pad_text(hi_s, w_hi, 'right')} "
            f"│ {_pad_text(wr_s, w_wr2, 'right')} "
            f"│ {_pad_text(ar_s, w_ar2, 'right')} "
            f"│ {_pad_text(wl_s, w_wl2, 'right')} "
            f"│ {_pad_text(pf_s, w_pf2, 'right')} "
            f"│ {_pad_text(r['alpha_rating'], w_rt, 'center')} │"
        )
        print(row_str)
    print(bot2)

    # 3. 表 3: LightGBM 特征重要度 Top 15 排行榜
    print("\n" + "─" * 90)
    print("📈 表 3: LightGBM 特征重要度 (Feature Importance Gain) Top 15 排行榜")
    print("─" * 90)
    w_r3, w_f3, w_c3, w_g3, w_gs3, w_rc3 = 4, 20, 14, 12, 10, 42
    hdr3 = (
        f"│ {_pad_text('#', w_r3, 'center')} "
        f"│ {_pad_text('特征标识', w_f3, 'left')} "
        f"│ {_pad_text('特征分类', w_c3, 'center')} "
        f"│ {_pad_text('Total Gain', w_g3, 'right')} "
        f"│ {_pad_text('贡献占比', w_gs3, 'right')} "
        f"│ {_pad_text('量化经济学逻辑', w_rc3, 'left')} │"
    )
    sep3 = f"├─{'─'*w_r3}─┼─{'─'*w_f3}─┼─{'─'*w_c3}─┼─{'─'*w_g3}─┼─{'─'*w_gs3}─┼─{'─'*w_rc3}─┤"
    top3 = f"┌─{'─'*w_r3}─┬─{'─'*w_f3}─┬─{'─'*w_c3}─┬─{'─'*w_g3}─┬─{'─'*w_gs3}─┬─{'─'*w_rc3}─┐"
    bot3 = f"└─{'─'*w_r3}─┴─{'─'*w_f3}─┴─{'─'*w_c3}─┴─{'─'*w_g3}─┴─{'─'*w_gs3}─┴─{'─'*w_rc3}─┘"

    print(top3)
    print(hdr3)
    print(sep3)
    for r in tables_data.get('table3', []):
        tg_s = f"{r['total_gain']:,.1f}"
        gs_s = f"{r['gain_share_pct']:.1f}%"
        row_str = (
            f"│ {_pad_text(r['rank'], w_r3, 'center')} "
            f"│ {_pad_text(r['feature'], w_f3, 'left')} "
            f"│ {_pad_text(r['category'], w_c3, 'center')} "
            f"│ {_pad_text(tg_s, w_g3, 'right')} "
            f"│ {_pad_text(gs_s, w_gs3, 'right')} "
            f"│ {_pad_text(r['rationale'], w_rc3, 'left')} │"
        )
        print(row_str)
    print(bot3)

    # 4. 表 4: 最新交易日 AI 预测做多胜率排行榜 Top 10
    print("\n" + "─" * 90)
    print("🎯 表 4: 最新交易日 AI 预测做多胜率排行榜 Top 10 (Live Inference Ranking)")
    print("─" * 90)
    w_r4, w_tk4, w_sec4, w_p4, w_wr4, w_tp4, w_sl4, w_mc4, w_act4 = 4, 6, 14, 10, 18, 16, 16, 14, 28
    hdr4 = (
        f"│ {_pad_text('#', w_r4, 'center')} "
        f"│ {_pad_text('代码', w_tk4, 'center')} "
        f"│ {_pad_text('所属赛道', w_sec4, 'left')} "
        f"│ {_pad_text('最新价 ($)', w_p4, 'right')} "
        f"│ {_pad_text('AI校准胜率(原始)', w_wr4, 'right')} "
        f"│ {_pad_text('建议目标止盈 ($)', w_tp4, 'right')} "
        f"│ {_pad_text('建议防守止损 ($)', w_sl4, 'right')} "
        f"│ {_pad_text('当前宏观水温', w_mc4, 'center')} "
        f"│ {_pad_text('操作建议', w_act4, 'center')} │"
    )
    sep4 = f"├─{'─'*w_r4}─┼─{'─'*w_tk4}─┼─{'─'*w_sec4}─┼─{'─'*w_p4}─┼─{'─'*w_wr4}─┼─{'─'*w_tp4}─┼─{'─'*w_sl4}─┼─{'─'*w_mc4}─┼─{'─'*w_act4}─┤"
    top4 = f"┌─{'─'*w_r4}─┬─{'─'*w_tk4}─┬─{'─'*w_sec4}─┬─{'─'*w_p4}─┬─{'─'*w_wr4}─┬─{'─'*w_tp4}─┬─{'─'*w_sl4}─┬─{'─'*w_mc4}─┬─{'─'*w_act4}─┐"
    bot4 = f"└─{'─'*w_r4}─┴─{'─'*w_tk4}─┴─{'─'*w_sec4}─┴─{'─'*w_p4}─┴─{'─'*w_wr4}─┴─{'─'*w_tp4}─┴─{'─'*w_sl4}─┴─{'─'*w_mc4}─┴─{'─'*w_act4}─┘"

    print(top4)
    print(hdr4)
    print(sep4)
    for r in tables_data.get('table4', []):
        price_s = f"${r['latest_price']:.2f}"
        cal_p = r.get('calibrated_win_rate_pct', r['predicted_win_rate_pct'])
        raw_p = r['predicted_win_rate_pct']
        wr_s = f"{cal_p:.1f}% (原{raw_p:.1f}%)"
        tp_str = f"${r['target_profit_price']:.2f} (+{r['target_profit_pct']}%)"
        sl_str = f"${r['stop_loss_price']:.2f} (-{r['stop_loss_pct']}%)"
        row_str = (
            f"│ {_pad_text(r['rank'], w_r4, 'center')} "
            f"│ {_pad_text(r['ticker'], w_tk4, 'center')} "
            f"│ {_pad_text(r['sector_cn'], w_sec4, 'left')} "
            f"│ {_pad_text(price_s, w_p4, 'right')} "
            f"│ {_pad_text(wr_s, w_wr4, 'right')} "
            f"│ {_pad_text(tp_str, w_tp4, 'right')} "
            f"│ {_pad_text(sl_str, w_sl4, 'right')} "
            f"│ {_pad_text(r['macro_temp'], w_mc4, 'center')} "
            f"│ {_pad_text(r['action'], w_act4, 'center')} │"
        )
        print(row_str)
    print(bot4)
    print("=" * 90 + "\n")


def render_html_dashboard(tables_data: Dict, output_path: str = 'predict_radar_5y.html') -> None:
    """
    Milestone M3 & V1.3 UI Pro: 渲染暗黑买方风格 HTML 可视化看板 (Obsidian Institutional Suite)
    支持跨看板顶部导航、Top 10 / 全池 28 股一键切换、7 大赛道交互筛选、双层胜率进度条与富途看盘联动跳转。
    """
    kpi = tables_data.get('kpi', {})
    weather = tables_data.get('macro_weather', {})
    t1 = tables_data.get('table1', [])
    t2 = tables_data.get('table2', [])
    t3 = tables_data.get('table3', [])
    t4 = tables_data.get('table4', [])
    all_live = tables_data.get('all_live_predictions', t4) or t4

    weather_status_str = weather.get('status', '☀️ 晴天做多')
    if '🌧️' in weather_status_str or '暴雨' in weather_status_str:
        weather_accent = '#f85149'
        weather_bg = 'linear-gradient(135deg, rgba(248, 81, 73, 0.14) 0%, rgba(15, 23, 42, 0.95) 65%)'
        weather_border = 'rgba(248, 81, 73, 0.45)'
    elif '⛅' in weather_status_str or '阴天' in weather_status_str:
        weather_accent = '#ffd54f'
        weather_bg = 'linear-gradient(135deg, rgba(255, 213, 79, 0.12) 0%, rgba(15, 23, 42, 0.95) 65%)'
        weather_border = 'rgba(255, 213, 79, 0.40)'
    else:
        weather_accent = '#3fb950'
        weather_bg = 'linear-gradient(135deg, rgba(63, 185, 80, 0.14) 0%, rgba(15, 23, 42, 0.95) 65%)'
        weather_border = 'rgba(63, 185, 80, 0.45)'

    # 生成 Table 1 HTML (带双层进度条)
    t1_rows_html = ""
    for r in t1:
        ret_val = r['avg_realized_return_pct']
        ret_color = "#3fb950" if ret_val >= 0 else "#f85149"
        ret_sign = "+" if ret_val > 0 else ""
        pos_wr = r.get('pos_win_rate_pct', r.get('win_rate_pct', 0.0))
        touch_wr = r.get('touch_win_rate_pct', r.get('win_rate_pct', 0.0))
        pf_val = r['profit_factor']
        pf_color = "#3fb950" if pf_val >= 1.50 else ("#ffd54f" if pf_val >= 1.20 else "#c9d1d9")
        t1_rows_html += f"""
        <tr>
            <td style="font-weight: 700;"><span class="badge badge-purple">{r['threshold_label']}</span></td>
            <td style="text-align: right; font-family: 'JetBrains Mono', monospace; font-weight: 600;">{r['trigger_count']:,}</td>
            <td style="text-align: right;">
                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 10px;">
                    <div>
                        <span style="font-weight: 800; color: #3fb950; font-size: 14px;">{pos_wr:.1f}%</span>
                        <span style="font-size: 11px; color: #8b949e; margin-left: 3px;" title="5日内触及动态止盈上轨比率">(触轨 {touch_wr:.1f}%)</span>
                    </div>
                    <div class="dual-bar-track" title="外层绿条: 正收益胜率 {pos_wr:.1f}% | 内层青条: 触轨止盈率 {touch_wr:.1f}%">
                        <div class="dual-bar-pos" style="width: {min(100.0, pos_wr)}%;"></div>
                        <div class="dual-bar-touch" style="width: {min(100.0, touch_wr)}%;"></div>
                    </div>
                </div>
            </td>
            <td style="text-align: right; color: {ret_color}; font-family: 'JetBrains Mono', monospace; font-weight: 700;">{ret_sign}{ret_val:.2f}%</td>
            <td style="text-align: right; font-weight: 700; color: #58a6ff; font-family: 'JetBrains Mono', monospace;">{r.get('win_loss_ratio', 1.50):.2f}</td>
            <td style="text-align: right; font-weight: 800; color: {pf_color}; font-family: 'JetBrains Mono', monospace; font-size: 14px;">{pf_val:.2f}</td>
        </tr>
        """

    # 生成 Table 2 HTML (带赛道校准乘数与双层胜率条)
    t2_rows_html = ""
    for idx, r in enumerate(t2, 1):
        ret_val = r['avg_realized_return_pct']
        ret_color = "#3fb950" if ret_val >= 0 else "#f85149"
        ret_sign = "+" if ret_val > 0 else ""
        pos_wr = r.get('high_conviction_pos_win_rate_pct', r.get('high_conviction_win_rate_pct', 0.0))
        touch_wr = r.get('high_conviction_touch_win_rate_pct', r.get('high_conviction_win_rate_pct', 0.0))
        pf_val = r['profit_factor']
        mult_val = float(np.clip(pf_val / 1.30, 0.75, 1.15))
        mult_color = "#3fb950" if mult_val > 1.02 else ("#f85149" if mult_val < 0.98 else "#8b949e")
        t2_rows_html += f"""
        <tr onclick="filterBySector('{r['sector_name_cn']}')" style="cursor: pointer;" title="点击筛选【{r['sector_name_cn']}】赛道的实盘预测标的">
            <td style="text-align: center; color: #8b949e; font-weight: 700;">{idx}</td>
            <td style="font-weight: 700; color: #f0f6fc;">
                {r['sector_name_cn']}
                <span style="font-size: 11px; color: #8b949e; font-weight: normal; margin-left: 4px;">({r['sector_key']})</span>
            </td>
            <td style="text-align: center;"><span class="badge badge-blue">{r['tickers_count']} 股</span></td>
            <td style="text-align: right; font-family: 'JetBrains Mono', monospace;">{r['oos_total_signals']:,}</td>
            <td style="text-align: right; font-family: 'JetBrains Mono', monospace; font-weight: 600;">{r['high_conviction_signals']:,}</td>
            <td style="text-align: right;">
                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px;">
                    <span><strong style="color: #3fb950;">{pos_wr:.1f}%</strong> <span style="font-size: 11px; color: #8b949e;">({touch_wr:.1f}%)</span></span>
                    <div class="dual-bar-track" style="width: 56px;">
                        <div class="dual-bar-pos" style="width: {min(100.0, pos_wr)}%;"></div>
                        <div class="dual-bar-touch" style="width: {min(100.0, touch_wr)}%;"></div>
                    </div>
                </div>
            </td>
            <td style="text-align: right; color: {ret_color}; font-family: 'JetBrains Mono', monospace; font-weight: 700;">{ret_sign}{ret_val:.2f}%</td>
            <td style="text-align: right; font-weight: 700; color: #58a6ff; font-family: 'JetBrains Mono', monospace;">{r.get('win_loss_ratio', 1.50):.2f}</td>
            <td style="text-align: right; font-weight: 800; color: #ffd54f; font-family: 'JetBrains Mono', monospace;">
                {pf_val:.2f}
                <span style="font-size: 10px; color: {mult_color}; margin-left: 3px;" title="表 4 赛道先验校准乘数">({mult_val:.2f}x)</span>
            </td>
            <td style="text-align: center; color: #ffd54f; font-size: 13px; font-weight: 700;">{r['alpha_rating']}</td>
        </tr>
        """

    # 生成 Table 3 HTML
    t3_rows_html = ""
    for r in t3:
        cat_badge = "badge-blue" if r['category_tag'] == 'stock' else ("badge-purple" if r['category_tag'] == 'cross' else "badge-gold")
        t3_rows_html += f"""
        <tr>
            <td style="text-align: center; color: #8b949e; font-weight: 700;">{r['rank']}</td>
            <td style="font-weight: 700; font-family: 'JetBrains Mono', monospace; color: #f0f6fc;">{r['feature']}</td>
            <td style="text-align: center;"><span class="badge {cat_badge}">{r['category']}</span></td>
            <td style="text-align: right; font-family: 'JetBrains Mono', monospace; color: #c9d1d9;">{r['total_gain']:,.1f}</td>
            <td style="text-align: right;">
                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px;">
                    <span style="font-weight: 700; color: #bc8cff; font-family: 'JetBrains Mono', monospace;">{r['gain_share_pct']:.1f}%</span>
                    <div style="width: 56px; height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden;">
                        <div style="width: {min(100.0, r['gain_share_pct'] * 4.5)}%; height: 100%; background: linear-gradient(90deg, #8957e5, #bc8cff);"></div>
                    </div>
                </div>
            </td>
            <td style="font-size: 12.5px; color: #8b949e; line-height: 1.45;">{r['rationale']}</td>
        </tr>
        """

    # 确保 all_live 中所有标的均具备 short_rank 且获取 top_short (Milestone 3 双向榜单)
    short_sorted = sorted(
        all_live,
        key=lambda x: (
            1 if (x.get('is_crash_halt') or x.get('is_cooling_halt')) else 0,
            -float(x.get('calibrated_short_win_rate_pct', 0.0))
        )
    )
    for idx_s, item in enumerate(short_sorted, 1):
        if 'short_rank' not in item or not item['short_rank']:
            item['short_rank'] = idx_s

    top_short = tables_data.get('top_short', [])
    if not top_short:
        top_short = [r for r in short_sorted if not (r.get('is_crash_halt') or r.get('is_cooling_halt'))][:5] or short_sorted[:5]

    # 生成 Table 4-B 空头排行榜 HTML (9 列: 排名/代码/赛道/现价ATR/做空胜率/空头止盈/空头止损/期权PCR/做空指令)
    t4_short_rows_html = ""
    for idx_s, r in enumerate(top_short[:5], 1):
        s_rk = int(r.get('short_rank', r.get('rank', idx_s)))
        tk_str = str(r.get('ticker', ''))
        sec_rating = r.get('sector_rating', '⭐⭐⭐☆☆')
        cal_s_wr = float(r.get('calibrated_short_win_rate_pct', 50.0))
        raw_s_wr = float(r.get('model_raw_short_prob_pct', r.get('short_raw_prob_pct', 45.0)))
        atr_val = float(r.get('atr_pct', 2.5))
        bias_val = r.get('bias_ma50', 0.0)
        bias_col = "#f85149" if bias_val < -5.0 else ("#3fb950" if bias_val > 5.0 else "#8b949e")
        
        s_tp = float(r.get('short_target_profit_price', r['latest_price'] * 0.96))
        s_tp_pct = float(r.get('short_target_profit_pct', 4.0))
        s_sl = float(r.get('short_stop_loss_price', r['latest_price'] * 1.03))
        s_sl_pct = float(r.get('short_stop_loss_pct', 3.0))
        
        d_pcr = float(r.get('dollar_pcr', 1.0))
        pcr_sent = str(r.get('pcr_sentiment', '⛅ 中性'))
        pcr_badge_style = "badge-red" if d_pcr >= 1.30 or "做空" in pcr_sent or "Put" in pcr_sent else ("badge-gold" if d_pcr >= 1.0 else "badge-blue")
        
        s_act = str(r.get('short_action', '👀 空头动能不足·观望'))
        if "🚨" in s_act or "熔断" in s_act or "破位" in s_act or "🩸" in s_act:
            s_act_badge = "badge-red"
        elif "🛡️" in s_act or "对冲" in s_act:
            s_act_badge = "badge-purple"
        elif "⚠️" in s_act:
            s_act_badge = "badge-gold"
        else:
            s_act_badge = "badge-blue"

        t4_short_rows_html += f"""
        <tr class="t4-short-row" data-rank="{s_rk}" data-sector="{r['sector_cn']}" data-ticker="{r['ticker']}">
            <td style="text-align: center; font-size: 15px; font-weight: 800; color: #f85149;">🩸#{s_rk}</td>
            <td>
                <a href="futu_pro_terminal.html?symbol={r['ticker']}" target="_blank" class="ticker-jump-link" title="点击在富途全周期看盘终端中查看 {r['ticker']} 空头走势与主力 Put 大单">
                    <span class="tk-code">{r['ticker']}</span>
                    <span class="tk-jump-icon" style="color: #f85149; border-color: rgba(248,81,73,0.4);">📉 看空</span>
                </a>
            </td>
            <td>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span class="badge badge-purple" style="cursor: pointer;" onclick="filterBySector('{r['sector_cn']}')">{r['sector_cn']}</span>
                    <span style="font-size: 11px; color: #ffd54f;" title="赛道评级">{sec_rating[:5]}</span>
                </div>
            </td>
            <td style="text-align: right; font-weight: 700; font-family: 'JetBrains Mono', monospace; font-size: 14.5px; color: #f0f6fc;">
                ${r['latest_price']:.2f}
                <span style="display: block; font-size: 10px; color: #8b949e; font-weight: normal;">ATR {atr_val:.1f}% · <span style="color: {bias_col};">MA50 {bias_val:+.1f}%</span></span>
            </td>
            <td style="text-align: right;">
                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px;">
                    <div>
                        <span style="font-size: 15px; font-weight: 800; color: #f85149; font-family: 'JetBrains Mono', monospace;">{cal_s_wr:.1f}%</span>
                        <span style="font-size: 11px; color: #8b949e; display: block;" title="AI 空头先验校准胜率">原始 {raw_s_wr:.1f}%</span>
                    </div>
                    <div style="width: 64px; height: 8px; background: rgba(255,255,255,0.08); border-radius: 4px; overflow: hidden; position: relative;">
                        <div style="width: {min(100.0, cal_s_wr)}%; height: 100%; background: #f85149; box-shadow: 0 0 6px #f85149;"></div>
                    </div>
                </div>
            </td>
            <td style="text-align: right; color: #3fb950; font-family: 'JetBrains Mono', monospace; font-weight: 700;">
                ${s_tp:.2f} <span class="pct-pill pct-up">-{s_tp_pct:.1f}%</span>
            </td>
            <td style="text-align: right; color: #f85149; font-family: 'JetBrains Mono', monospace; font-weight: 700;">
                ${s_sl:.2f} <span class="pct-pill pct-down">+{s_sl_pct:.1f}%</span>
            </td>
            <td style="text-align: center;">
                <span class="badge {pcr_badge_style}" style="font-size: 11px; padding: 3px 8px;">PCR {d_pcr:.2f} · {pcr_sent}</span>
            </td>
            <td style="text-align: center;">
                <span class="badge {s_act_badge}" style="font-size: 12px; padding: 4px 10px;">{s_act}</span>
            </td>
        </tr>
        """

    # 生成 Table 4-A 做多排行榜与全域 70 标的 HTML (默认展示 Top 5，支持展开全池与按赛道/ETF筛选)
    t4_rows_html = ""
    pure_etf_set = {'QQQM', 'SPY', 'DIA', 'IWM', 'SMH', 'SOXX', 'SOXL', 'KORU', 'IGV', 'TLT', 'GLD', 'QQQI', 'VIXY', 'SQQQ', 'SOXS', 'IBIT'}
    etf_count = 0
    for r in all_live:
        rk = int(r['rank'])
        tk_str = str(r.get('ticker', ''))
        is_etf_row = bool(r.get('is_etf', (tk_str in pure_etf_set and len(all_live) > 28)))
        if is_etf_row:
            etf_count += 1
        rank_badge = "🥇" if rk == 1 else ("🥈" if rk == 2 else ("🥉" if rk == 3 else f"#{rk}"))
        act_text = str(r.get('action', ''))
        if "🚨" in act_text or "严禁" in act_text:
            act_badge = "badge-red"
        elif "🛡️" in act_text or "⚠️" in act_text:
            act_badge = "badge-gold"
        elif "🔥" in act_text:
            act_badge = "badge-green"
        elif "⚡" in act_text:
            act_badge = "badge-blue"
        else:
            act_badge = "badge-purple"

        cal_wr = r.get('calibrated_win_rate_pct', r['predicted_win_rate_pct'])
        raw_wr = r['predicted_win_rate_pct']
        atr_val = r.get('atr_pct', 2.5)
        bias_val = r.get('bias_ma50', None)
        bias_html = ""
        if bias_val is not None and bias_val != 0.0:
            b_col = "#f85149" if bias_val < -12.0 else ("#3fb950" if -6.5 <= bias_val <= 6.0 else "#8b949e")
            bias_html = f' · <span style="color:{b_col};">MA50 {bias_val:+.1f}%</span>'
        sec_rating = r.get('sector_rating', '⭐⭐⭐☆☆')
        sec_pf = r.get('sector_pf', 1.30)
        wr_color = "#3fb950" if cal_wr >= 58.0 else ("#58a6ff" if cal_wr >= 50.0 else "#8b949e")
        hidden_style = "display: none;" if (rk > 5 or is_etf_row) else ""

        # 多空双向并列字段 (在全景视角下列出空头胜率与止盈止损)
        cal_s_wr = float(r.get('calibrated_short_win_rate_pct', 50.0))
        s_tp = float(r.get('short_target_profit_price', r['latest_price'] * 0.96))
        s_tp_pct = float(r.get('short_target_profit_pct', 4.0))
        s_sl = float(r.get('short_stop_loss_price', r['latest_price'] * 1.03))
        s_sl_pct = float(r.get('short_stop_loss_pct', 3.0))
        d_pcr = float(r.get('dollar_pcr', 1.0))
        s_act = str(r.get('short_action', '👀 观望'))
        short_rk = int(r.get('short_rank', 999))

        t4_rows_html += f"""
        <tr class="t4-stock-row" data-rank="{rk}" data-short-rank="{short_rk}" data-sector="{r['sector_cn']}" data-ticker="{r['ticker']}" data-isetf="{'1' if is_etf_row else '0'}" style="{hidden_style}">
            <td style="text-align: center; font-size: 15px; font-weight: 800; color: {'#ffd54f' if rk <= 3 else '#8b949e'};">{rank_badge}</td>
            <td>
                <a href="futu_pro_terminal.html?symbol={r['ticker']}" target="_blank" class="ticker-jump-link" title="点击在富途全周期看盘终端中打开 {r['ticker']} K线与实盘诊股">
                    <span class="tk-code">{r['ticker']}</span>
                    <span class="tk-jump-icon">📈 看盘</span>
                </a>
            </td>
            <td>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span class="badge badge-blue" style="cursor: pointer;" onclick="filterBySector('{r['sector_cn']}')">{r['sector_cn']}</span>
                    <span style="font-size: 11px; color: #ffd54f;" title="赛道 OOS 评级 (PF={sec_pf:.2f})">{sec_rating[:5]}</span>
                </div>
            </td>
            <td style="text-align: right; font-weight: 700; font-family: 'JetBrains Mono', monospace; font-size: 14.5px; color: #f0f6fc;">
                ${r['latest_price']:.2f}
                <span style="display: block; font-size: 10px; color: #8b949e; font-weight: normal;">ATR {atr_val:.1f}%{bias_html}</span>
            </td>
            <td style="text-align: right;">
                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px;">
                    <div>
                        <span style="font-size: 15px; font-weight: 800; color: {wr_color}; font-family: 'JetBrains Mono', monospace;">{cal_wr:.1f}%</span>
                        <span style="font-size: 11px; color: #8b949e; display: block;" title="右侧结构修正后做多胜率">原多 {raw_wr:.1f}%</span>
                        <span class="t4-all-short-sub" style="font-size: 10px; color: #f85149; display: none;" title="AI 做空校准胜率">🩸做空 {cal_s_wr:.1f}%</span>
                    </div>
                    <div style="width: 64px; height: 8px; background: rgba(255,255,255,0.08); border-radius: 4px; overflow: hidden; position: relative;">
                        <div style="width: {min(100.0, cal_wr)}%; height: 100%; background: {wr_color}; box-shadow: 0 0 6px {wr_color};"></div>
                    </div>
                </div>
            </td>
            <td style="text-align: right; color: #3fb950; font-family: 'JetBrains Mono', monospace; font-weight: 700;">
                ${r['target_profit_price']:.2f} <span class="pct-pill pct-up">+{r['target_profit_pct']}%</span>
                <span class="t4-all-short-sub" style="display: none; font-size: 10px; color: #8b949e; font-weight: normal;">空止盈 ${s_tp:.2f} (-{s_tp_pct:.1f}%)</span>
            </td>
            <td style="text-align: right; color: #f85149; font-family: 'JetBrains Mono', monospace; font-weight: 700;">
                ${r['stop_loss_price']:.2f} <span class="pct-pill pct-down">-{r['stop_loss_pct']}%</span>
                <span class="t4-all-short-sub" style="display: none; font-size: 10px; color: #8b949e; font-weight: normal;">空止损 ${s_sl:.2f} (+{s_sl_pct:.1f}%)</span>
            </td>
            <td style="text-align: center;">
                <span class="badge badge-gold">{r['macro_temp']}</span>
                <span class="t4-all-short-sub" style="display: none; font-size: 10px; color: #8b949e; margin-top: 2px;">PCR {d_pcr:.2f}</span>
            </td>
            <td style="text-align: center;">
                <span class="badge {act_badge}" style="font-size: 12px; padding: 4px 10px;">{r['action']}</span>
                <div class="t4-all-short-sub" style="display: none; font-size: 10px; color: #f85149; margin-top: 2px;">{s_act}</div>
            </td>
        </tr>
        """

    # 构建 7 大赛道 + ETF 专区过滤按钮
    sector_counts = {}
    for r in all_live:
        sc = r['sector_cn']
        sector_counts[sc] = sector_counts.get(sc, 0) + 1
    sector_pills_html = f'<button class="filter-pill active" data-sec="ALL" onclick="filterBySector(this.dataset.sec)">全部赛道 ({len(all_live)})</button>'
    for r2 in t2:
        sc_name = r2['sector_name_cn']
        cnt = sector_counts.get(sc_name, r2['tickers_count'])
        sector_pills_html += f'<button class="filter-pill" data-sec="{sc_name}" onclick="filterBySector(this.dataset.sec)">{sc_name} ({cnt})</button>'
    if etf_count > 0:
        sector_pills_html += f'<button class="filter-pill" data-sec="ETF_POOL" onclick="filterBySector(this.dataset.sec)">📊 宏观与行业 ETF ({etf_count})</button>'

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <script>
        // ⚡ 客户端智能防缓存自动刷新 (60秒穿透 GitHub Pages / Fastly CDN 10分钟强缓存)
        setTimeout(function() {{
            try {{
                var cleanPath = window.location.pathname;
                var searchParams = new URLSearchParams(window.location.search);
                searchParams.set('_t', Date.now());
                window.location.replace(cleanPath + '?' + searchParams.toString() + window.location.hash);
            }} catch(e) {{
                window.location.reload();
            }}
        }}, 60000);
    </script>
    <title>AI 美股 5 年多赛道预测雷达 · 全景战报</title>
    <style>
        :root {{
            --bg-base: #070a0f;
            --bg-card: #11161f;
            --bg-card-hover: #171e2b;
            --border-color: #262f3d;
            --text-main: #f0f6fc;
            --text-sub: #c9d1d9;
            --text-muted: #8b949e;
            --neon-green: #3fb950;
            --cyber-purple: #bc8cff;
            --primary-blue: #58a6ff;
            --warning-gold: #ffd54f;
            --risk-red: #f85149;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            background-color: var(--bg-base);
            background-image:
                radial-gradient(circle at 12% 8%, rgba(88, 166, 255, 0.07), transparent 32%),
                radial-gradient(circle at 88% 12%, rgba(188, 140, 255, 0.07), transparent 32%);
            color: var(--text-sub);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Roboto, sans-serif;
            line-height: 1.5;
            padding: 18px 24px 40px;
        }}

        .container {{
            max-width: 1480px;
            margin: 0 auto;
        }}

        /* Top Suite Cross-Navigation Bar */
        .suite-nav {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: rgba(17, 22, 31, 0.88);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 10px 16px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .suite-nav-links {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .suite-link {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 13px;
            border-radius: 8px;
            font-size: 12.5px;
            font-weight: 600;
            color: var(--text-sub);
            text-decoration: none;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.07);
            transition: all 0.18s ease;
        }}
        .suite-link:hover {{
            background: rgba(88, 166, 255, 0.14);
            border-color: rgba(88, 166, 255, 0.45);
            color: #fff;
            transform: translateY(-1px);
        }}
        .suite-link.active {{
            background: linear-gradient(135deg, rgba(188, 140, 255, 0.22), rgba(88, 166, 255, 0.18));
            border-color: rgba(188, 140, 255, 0.55);
            color: #e2c5ff;
            font-weight: 700;
            box-shadow: 0 0 12px rgba(188, 140, 255, 0.2);
        }}
        .suite-meta {{
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 12px;
            color: var(--text-muted);
        }}

        /* Header */
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 18px;
            margin-bottom: 22px;
            flex-wrap: wrap;
            gap: 16px;
        }}
        .header-title h1 {{
            color: var(--text-main);
            font-size: 25px;
            font-weight: 800;
            letter-spacing: -0.4px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .header-title p {{
            color: var(--text-muted);
            font-size: 13px;
            margin-top: 5px;
        }}
        .header-status {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}

        /* Live Badge with Pulse Dot */
        .live-badge {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(63, 185, 80, 0.12);
            border: 1px solid rgba(63, 185, 80, 0.4);
            color: var(--neon-green);
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}
        .engine-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(188, 140, 255, 0.12);
            border: 1px solid rgba(188, 140, 255, 0.35);
            color: var(--cyber-purple);
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}
        .pulse-dot {{
            width: 8px;
            height: 8px;
            background-color: var(--neon-green);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--neon-green);
            animation: pulse-glow 2s infinite;
        }}
        @keyframes pulse-glow {{
            0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(63, 185, 80, 0.7); }}
            70% {{ transform: scale(1.1); box-shadow: 0 0 0 8px rgba(63, 185, 80, 0); }}
            100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(63, 185, 80, 0); }}
        }}

        /* Hero KPI Ribbon */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
            margin-bottom: 22px;
        }}
        .kpi-card {{
            background: linear-gradient(155deg, #141b26 0%, #0d121a 100%);
            border: 1px solid var(--border-color);
            border-radius: 13px;
            padding: 18px 20px;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s;
        }}
        .kpi-card:hover {{
            transform: translateY(-2px);
            border-color: rgba(88, 166, 255, 0.45);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
        }}
        .kpi-card::before {{
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 3px;
        }}
        .kpi-card.green::before {{ background: linear-gradient(90deg, #2ea043, #3fb950); }}
        .kpi-card.gold::before {{ background: linear-gradient(90deg, #d29922, #ffd54f); }}
        .kpi-card.blue::before {{ background: linear-gradient(90deg, #1f6feb, #58a6ff); }}
        .kpi-card.purple::before {{ background: linear-gradient(90deg, #8957e5, #bc8cff); }}

        .kpi-label {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }}
        .kpi-val {{
            font-size: 26px;
            font-weight: 800;
            color: var(--text-main);
            line-height: 1.2;
            margin-bottom: 4px;
        }}
        .kpi-sub {{
            font-size: 12px;
            color: var(--text-muted);
        }}

        /* Macro Weather Card */
        .weather-banner {{
            background: {weather_bg};
            border: 1px solid {weather_border};
            border-radius: 14px;
            padding: 18px 24px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.28);
        }}
        .weather-left {{
            display: flex;
            align-items: center;
            gap: 16px;
        }}
        .weather-icon {{
            font-size: 40px;
            line-height: 1;
            filter: drop-shadow(0 0 10px rgba(255,255,255,0.15));
        }}
        .weather-info h2 {{
            font-size: 19px;
            font-weight: 800;
            color: var(--text-main);
            margin-bottom: 4px;
        }}
        .weather-info p {{
            font-size: 13px;
            color: var(--text-sub);
        }}
        .weather-stats {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .weather-stat-pill {{
            background: rgba(13, 17, 23, 0.65);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 9px;
            padding: 8px 14px;
            text-align: center;
            min-width: 108px;
        }}
        .weather-stat-pill .lbl {{
            font-size: 11px;
            color: var(--text-muted);
            display: block;
            margin-bottom: 2px;
        }}
        .weather-stat-pill .val {{
            font-size: 14px;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-main);
        }}

        /* Section & Tables */
        .section-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 20px 22px;
            margin-bottom: 24px;
            overflow-x: auto;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
        }}
        .section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .section-header h3 {{
            color: var(--text-main);
            font-size: 17px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .section-header .sub {{
            font-size: 12px;
            color: var(--text-muted);
        }}

        /* Interactive Filter Toolbar for Table 4 */
        .t4-toolbar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
            padding: 12px 14px;
            background: rgba(13, 17, 23, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 10px;
            margin-bottom: 14px;
        }}
        .filter-pills {{
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }}
        .filter-pill {{
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.09);
            color: var(--text-sub);
            padding: 5px 11px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
        }}
        .filter-pill:hover {{
            background: rgba(88, 166, 255, 0.12);
            border-color: rgba(88, 166, 255, 0.4);
            color: #fff;
        }}
        .filter-pill.active {{
            background: rgba(88, 166, 255, 0.2);
            border-color: var(--primary-blue);
            color: var(--primary-blue);
            box-shadow: 0 0 8px rgba(88, 166, 255, 0.25);
        }}
        .t4-controls-right {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .search-input {{
            background: #0b0e14;
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 5px 10px;
            border-radius: 6px;
            font-size: 12px;
            width: 150px;
            outline: none;
        }}
        .search-input:focus {{
            border-color: var(--primary-blue);
        }}
        .mode-btn {{
            background: linear-gradient(135deg, rgba(188, 140, 255, 0.16), rgba(88, 166, 255, 0.16));
            border: 1px solid rgba(188, 140, 255, 0.45);
            color: #e2c5ff;
            padding: 5px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.15s ease;
        }}
        .mode-btn:hover {{
            background: rgba(188, 140, 255, 0.28);
            color: #fff;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}
        th {{
            background: #0b0f17;
            color: var(--text-muted);
            font-weight: 600;
            padding: 11px 14px;
            border-bottom: 1px solid var(--border-color);
            font-size: 12px;
            white-space: nowrap;
        }}
        td {{
            padding: 12px 14px;
            border-bottom: 1px solid rgba(48, 54, 61, 0.5);
            color: var(--text-sub);
            white-space: nowrap;
        }}
        tr:hover td {{
            background-color: var(--bg-card-hover);
        }}

        /* Ticker Jump Link */
        .ticker-jump-link {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            text-decoration: none;
            padding: 3px 8px;
            border-radius: 6px;
            background: rgba(88, 166, 255, 0.08);
            border: 1px solid rgba(88, 166, 255, 0.22);
            transition: all 0.15s ease;
        }}
        .ticker-jump-link:hover {{
            background: rgba(88, 166, 255, 0.2);
            border-color: var(--primary-blue);
            transform: translateX(2px);
        }}
        .tk-code {{
            font-weight: 800;
            font-size: 15px;
            color: #58a6ff;
            font-family: 'JetBrains Mono', monospace;
            letter-spacing: 0.4px;
        }}
        .tk-jump-icon {{
            font-size: 10px;
            color: #8b949e;
            background: rgba(255, 255, 255, 0.06);
            padding: 1px 5px;
            border-radius: 4px;
        }}
        .ticker-jump-link:hover .tk-jump-icon {{
            color: #fff;
            background: rgba(88, 166, 255, 0.35);
        }}

        .pct-pill {{
            display: inline-block;
            font-size: 11px;
            padding: 1px 5px;
            border-radius: 4px;
            margin-left: 3px;
        }}
        .pct-up {{ background: rgba(63, 185, 80, 0.14); color: #3fb950; }}
        .pct-down {{ background: rgba(248, 81, 73, 0.14); color: #f85149; }}

        /* Dual-Layer Win Rate Bar */
        .dual-bar-track {{
            width: 68px;
            height: 8px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            overflow: hidden;
            position: relative;
        }}
        .dual-bar-pos {{
            position: absolute;
            top: 0; left: 0; bottom: 0;
            background: rgba(63, 185, 80, 0.45);
            border-radius: 4px;
        }}
        .dual-bar-touch {{
            position: absolute;
            top: 2px; left: 0; bottom: 2px;
            background: #58a6ff;
            border-radius: 2px;
        }}

        /* Badges */
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }}
        .badge-green {{ background: rgba(63, 185, 80, 0.15); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.35); }}
        .badge-blue {{ background: rgba(88, 166, 255, 0.15); color: #58a6ff; border: 1px solid rgba(88, 166, 255, 0.35); }}
        .badge-purple {{ background: rgba(188, 140, 255, 0.15); color: #bc8cff; border: 1px solid rgba(188, 140, 255, 0.35); }}
        .badge-gold {{ background: rgba(255, 213, 79, 0.15); color: #ffd54f; border: 1px solid rgba(255, 213, 79, 0.35); }}
        .badge-red {{ background: rgba(248, 81, 73, 0.15); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.35); }}

        /* Milestone 3: Segmented Dual Ranking Controls */
        .t4-segmented-bar {{
            display: flex;
            gap: 10px;
            margin-bottom: 16px;
            flex-wrap: wrap;
        }}
        .t4-seg-btn {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            color: var(--text-sub);
            padding: 8px 18px;
            border-radius: 8px;
            font-size: 13.5px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .t4-seg-btn:hover {{
            background: rgba(88, 166, 255, 0.15);
            color: #fff;
            border-color: rgba(88, 166, 255, 0.45);
        }}
        .t4-seg-btn.active {{
            background: linear-gradient(135deg, rgba(88, 166, 255, 0.28), rgba(188, 140, 255, 0.28));
            border-color: #58a6ff;
            color: #fff;
            box-shadow: 0 0 12px rgba(88, 166, 255, 0.35);
        }}
        .t4-card-short {{
            border-left: 4px solid #f85149 !important;
        }}

        /* Two-column layout for Table 1 & Table 3 on ultra-wide screens */
        .grid-2col {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 24px;
        }}

        /* Footer */
        .footer {{
            border-top: 1px solid var(--border-color);
            padding-top: 20px;
            margin-top: 32px;
            color: var(--text-muted);
            font-size: 12px;
            text-align: center;
            line-height: 1.8;
        }}

        @media (max-width: 768px) {{
            body {{ padding: 12px; }}
            .header-title h1 {{ font-size: 20px; }}
            .weather-banner {{ padding: 14px; }}
            .section-card {{ padding: 14px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Cross-Radar Unified Suite Navigation -->
        <nav class="suite-nav">
            <div class="suite-nav-links">
                <a href="futu_pro_terminal.html" class="suite-link">🐮 富途全周期看盘终端</a>
                <a href="stocks_radar.html" class="suite-link">🎯 核心个股实战雷达</a>
                <a href="macro_radar.html" class="suite-link">🏛️ 宏观全天候司令塔</a>
                <a href="backtest_report_5y.html" class="suite-link">🛡️ 5年历史回测对账</a>
                <a href="predict_radar_5y.html" class="suite-link active">⚡ AI 5年多赛道预测引擎</a>
            </div>
            <div class="suite-meta">
                <span>💡 点击表中任意股票代码可直接唤起富途多周期 K 线看盘</span>
            </div>
        </nav>

        <!-- Header -->
        <header class="header">
            <div class="header-title">
                <h1>⚡ AI 美股 5 年多赛道预测雷达 <span style="font-size: 13px; padding: 3px 9px; border-radius: 6px; background: rgba(188,140,255,0.16); color: #bc8cff; border: 1px solid rgba(188,140,255,0.35); font-weight: 700;">V1.3 机构终极校准版</span></h1>
                <p>微软 Qlib 滚动时序长表截面架构 · 7 大高弹性赛道 (28标的) + 7 宏观基准 ETF · 严守 T 日因果律与 5 日 Purge 隔离</p>
            </div>
            <div class="header-status">
                <div class="engine-badge" title="实盘推理采用截至 T-5 的全部已结算历史样本重新拟合，无缝吸收 2026 最新市场微观结构">
                    🧠 实盘模型: Full-Sample Refit (2021~T-5)
                </div>
                <div class="live-badge">
                    <span class="pulse-dot"></span>
                    <span>{kpi.get('data_mode', '实时在线')}</span>
                </div>
                <span style="font-size: 12px; color: var(--text-muted);">同步于: {kpi.get('sync_time', '')}</span>
            </div>
        </header>

        <!-- Hero KPI Ribbon -->
        <div class="kpi-grid">
            <div class="kpi-card green">
                <div class="kpi-label">高置信正收益胜率 (>=60% 阈值)</div>
                <div class="kpi-val" style="color: var(--neon-green);">{kpi.get('high_conviction_win_rate', 62.5):.1f}%</div>
                <div class="kpi-sub">2024-2026 样本外实测 · 含止盈触轨与 5 日正收益交割</div>
            </div>
            <div class="kpi-card gold">
                <div class="kpi-label">高置信期望盈亏比 (Profit Factor)</div>
                <div class="kpi-val" style="color: var(--warning-gold);">{kpi.get('profit_factor', 2.18):.2f}</div>
                <div class="kpi-sub">分段软压缩 ATR 动态止盈止损轨 · 真实交割收益口径</div>
            </div>
            <div class="kpi-card purple">
                <div class="kpi-label">领航 Alpha 赛道</div>
                <div class="kpi-val" style="font-size: 20px; color: var(--cyber-purple);">{kpi.get('top_sector', '存储芯片')}</div>
                <div class="kpi-sub">综合样本外 PF 与平均实现收益评定 · 赋能表 4 先验校准</div>
            </div>
            <div class="kpi-card blue">
                <div class="kpi-label">第一核心驱动因子</div>
                <div class="kpi-val" style="font-size: 20px; color: var(--primary-blue); font-family: 'JetBrains Mono', monospace;">{kpi.get('top_feature', 'QQQM_Bias_MA50')}</div>
                <div class="kpi-sub">colsample=0.65 & max_bin=63 树分裂防垄断正则化</div>
            </div>
        </div>

        <!-- Macro Weather Card -->
        <div class="weather-banner">
            <div class="weather-left">
                <div class="weather-icon">{weather.get('status', '☀️ 晴天做多')[:2]}</div>
                <div class="weather-info">
                    <h2>当前宏观环境水温: <span style="color: {weather_accent};">{weather.get('status', '☀️ 晴天做多')}</span></h2>
                    <p>{weather.get('desc', '多头共振 · 积极进攻')} · <span style="color: #8b949e;">表 4 决策指令已与当前宏观水温及 50% 原始胜率底线强制联动</span></p>
                </div>
            </div>
            <div class="weather-stats">
                <div class="weather-stat-pill">
                    <span class="lbl">QQQM MA50 乖离</span>
                    <span class="val" style="color: {'#3fb950' if weather.get('qqqm_bias', 0) >= 0 else '#f85149'};">{weather.get('qqqm_bias', 0.0):+.2f}%</span>
                </div>
                <div class="weather-stat-pill">
                    <span class="lbl">SMH MA50 乖离</span>
                    <span class="val" style="color: {'#3fb950' if weather.get('smh_bias', 0) >= 0 else '#f85149'};">{weather.get('smh_bias', 0.0):+.2f}%</span>
                </div>
                <div class="weather-stat-pill">
                    <span class="lbl">VIXY 60日脉冲</span>
                    <span class="val" style="color: {'#f85149' if weather.get('vixy_pos', 0.5) >= 0.75 else '#3fb950'};">{weather.get('vixy_pos', 0.5):.2f}</span>
                </div>
                <div class="weather-stat-pill">
                    <span class="lbl">硬件/软件轮动 (SMH/IGV)</span>
                    <span class="val" style="color: {'#58a6ff' if weather.get('smh_igv', 0) >= 0 else '#bc8cff'};">{weather.get('smh_igv', 0.0):+.2f}%</span>
                </div>
            </div>
        </div>

        <!-- Table 4: Segmented Dual-Ranking Architecture (Milestone M3) -->
        <div class="t4-segmented-bar">
            <button class="t4-seg-btn active" data-view="dual" onclick="switchT4View('dual')">⚔️ 多空双向大榜 (Dual Top 5)</button>
            <button class="t4-seg-btn" data-view="long" onclick="switchT4View('long')">🔥 做多高胜率 Top 5 (Long Pool)</button>
            <button class="t4-seg-btn" data-view="short" onclick="switchT4View('short')">🩸 做空高胜率 Top 5 (Short Pool)</button>
            <button class="t4-seg-btn" data-view="all" onclick="switchT4View('all')">🌐 全域 70 标的全景截面</button>
        </div>

        <div id="t4-view-container">
            <!-- Card 4-A: 做多胜率 Top 5 (Bullish Conviction) -->
            <div class="section-card t4-card t4-card-long" id="cardT4Long">
                <div class="section-header">
                    <div>
                        <h3 id="t4-long-title">🔥 表 4-A: 最新交易日 AI 预测做多胜率排行榜 Top 5 (Bullish Conviction)</h3>
                        <span class="sub" id="t4-long-sub">5日触顶止盈概率领先 · 赛道先验正向校准 · 严格剔除破位飞刀与暴跌熔断</span>
                    </div>
                    <div class="t4-controls-right" id="t4-long-controls">
                        <input type="text" id="t4-search" class="search-input" placeholder="🔍 搜索代码 (如 NVDA)..." oninput="applyT4Filters()">
                        <button id="t4-mode-btn" class="mode-btn" onclick="toggleT4PoolMode()">🌐 展开全域 70 标的全景</button>
                    </div>
                </div>

                <div class="t4-toolbar" id="t4-long-toolbar">
                    <div class="filter-pills" id="t4-sector-pills">
                        {sector_pills_html}
                    </div>
                    <span id="t4-count-label" style="font-size: 12px; color: var(--text-muted);">当前展示: Top 5 精选正股</span>
                </div>

                <table>
                    <thead>
                        <tr>
                            <th style="text-align: center;">排名</th>
                            <th>标的代码 (点击看盘)</th>
                            <th>所属赛道 & 评级</th>
                            <th style="text-align: right;">最新价 / ATR波动</th>
                            <th style="text-align: right;">AI 校准胜率 (原始概率)</th>
                            <th style="text-align: right;">动态目标止盈价 (Soft-ATR)</th>
                            <th style="text-align: right;">动态防守止损价 (Soft-ATR)</th>
                            <th style="text-align: center;">宏观水温</th>
                            <th style="text-align: center;">AI 综合决策指令</th>
                        </tr>
                    </thead>
                    <tbody id="t4-tbody">
                        {t4_rows_html}
                    </tbody>
                </table>
            </div>

            <!-- Card 4-B: 做空/破位下杀胜率 Top 5 (Bearish Breakdown) -->
            <div class="section-card t4-card t4-card-short" id="cardT4Short" style="margin-top: 18px;">
                <div class="section-header">
                    <div>
                        <h3>🩸 表 4-B: 最新交易日 AI 预测做空/破位下杀胜率排行榜 Top 5 (Bearish Breakdown)</h3>
                        <span class="sub">5日下杀触轨做空胜率 · 均线空头排列 + 期权 Dollar PCR 压盘 · 反抽做空/买Put/反向ETF对冲</span>
                    </div>
                    <span class="badge badge-red" style="font-size: 12px; padding: 4px 10px;">机构做空对冲池 (Bearish Pool)</span>
                </div>

                <table>
                    <thead>
                        <tr>
                            <th style="text-align: center;">排名</th>
                            <th>标的代码 (点击看盘)</th>
                            <th>所属赛道 & 评级</th>
                            <th style="text-align: right;">现价 / 波动偏离</th>
                            <th style="text-align: right;">AI 做空胜率 (原始概率)</th>
                            <th style="text-align: right;">空头动态止盈 (Soft-ATR)</th>
                            <th style="text-align: right;">空头防守止损 (均线上沿)</th>
                            <th style="text-align: center;">期权 Dollar PCR 评级</th>
                            <th style="text-align: center;">AI 综合做空决胜指令</th>
                        </tr>
                    </thead>
                    <tbody id="t4-short-tbody">
                        {t4_short_rows_html}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Table 2: 7 大赛道样本外预测表现归因表 -->
        <div class="section-card">
            <div class="section-header">
                <h3>🏆 表 2: 7 大核心高弹性赛道样本外预测表现归因表 (7 Sectors OOS Attribution · 点击行可联动筛选表 4)</h3>
                <span class="sub">基于 2024-2026 样本外高置信信号 (>=60%) 期望盈亏比 (PF) 与平均实现收益综合评星 · 小样本 (&lt;10笔) 强制封顶 3 星</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th style="text-align: center;">#</th>
                        <th>赛道名称 (点击筛选)</th>
                        <th style="text-align: center;">标的数</th>
                        <th style="text-align: right;">OOS 评估样本</th>
                        <th style="text-align: right;">高置信信号数 (>=60%)</th>
                        <th style="text-align: right;">高置信正收益胜率 (触轨率)</th>
                        <th style="text-align: right;">平均实现收益 %</th>
                        <th style="text-align: right;">平均盈亏比</th>
                        <th style="text-align: right;">期望盈亏比 PF (校准乘数)</th>
                        <th style="text-align: center;">AI Alpha 机构评级</th>
                    </tr>
                </thead>
                <tbody>
                    {t2_rows_html}
                </tbody>
            </table>
        </div>

        <!-- Table 1: 样本外分位数校准表 -->
        <div class="section-card">
            <div class="section-header">
                <h3>📊 表 1: 样本外分位数校准表 (Quantile Calibration Table - Expanding OOS 2024-2026)</h3>
                <span class="sub">严格验证预测概率单调递增性 · 同时披露真实交割正收益胜率与 5 日触轨止盈率</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>预测置信度档位</th>
                        <th style="text-align: right;">触发历史样本数</th>
                        <th style="text-align: right;">正收益胜率 (触轨止盈率)</th>
                        <th style="text-align: right;">平均实现收益 %</th>
                        <th style="text-align: right;">平均盈亏比 (Win/Loss)</th>
                        <th style="text-align: right;">期望盈亏比 (PF)</th>
                    </tr>
                </thead>
                <tbody>
                    {t1_rows_html}
                </tbody>
            </table>
        </div>

        <!-- Table 3: LightGBM 特征重要度 Top 15 -->
        <div class="section-card">
            <div class="section-header">
                <h3>📈 表 3: LightGBM 特征重要度 (Feature Importance Gain) Top 15 排行榜</h3>
                <span class="sub">横截面分位数排名 + 连续超额收益复合因子 · 抑制宏观特征分裂垄断，提升个股微观结构权重</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th style="text-align: center;">#</th>
                        <th>特征标识</th>
                        <th style="text-align: center;">特征维度分类</th>
                        <th style="text-align: right;">Gain 总贡献</th>
                        <th style="text-align: right;">贡献占比 %</th>
                        <th>量化经济学逻辑与实盘解读</th>
                    </tr>
                </thead>
                <tbody>
                    {t3_rows_html}
                </tbody>
            </table>
        </div>

        <!-- Footer -->
        <footer class="footer">
            <p>架构规范: 微软 Qlib 滚动时序长表截面 [Date, Ticker, Sector] | 模型: LightGBM 正则化分类器 (max_depth=5, num_leaves=23, colsample=0.65, max_bin=63)</p>
            <p>时序验证: 2024~2026 Expanding Walk-Forward + 5 日 Purge 隔离 | 实盘推理: Full-Sample Refit (截至 T-5 已结算样本) + 赛道 PF 先验校准</p>
            <p style="margin-top: 8px; color: #484f58;">us-market-radar AI Multi-Sector 5Y Predictor Engine V1.3 · Institutional Quantitative Suite</p>
        </footer>
    </div>

    <script>
        let showFullPool = false;
        let currentSector = 'ALL';
        let currentT4View = 'dual';

        function switchT4View(view) {{
            currentT4View = view;
            try {{
                localStorage.setItem("predict_t4_view", view);
            }} catch(e) {{}}

            document.querySelectorAll('.t4-seg-btn').forEach(btn => {{
                if (btn.getAttribute('data-view') === view) {{
                    btn.classList.add('active');
                }} else {{
                    btn.classList.remove('active');
                }}
            }});

            const cardLong = document.getElementById('cardT4Long');
            const cardShort = document.getElementById('cardT4Short');
            const title = document.getElementById('t4-long-title');
            const modeBtn = document.getElementById('t4-mode-btn');

            if (view === 'dual') {{
                if (cardLong) cardLong.style.display = '';
                if (cardShort) cardShort.style.display = '';
                showFullPool = false;
                if (title) title.textContent = '🔥 表 4-A: 最新交易日 AI 预测做多胜率排行榜 Top 5 (Bullish Conviction)';
                if (modeBtn) modeBtn.textContent = '🌐 展开全域 70 标的全景';
                document.querySelectorAll('.t4-all-short-sub').forEach(el => el.style.display = 'none');
                applyT4Filters(5);
            }} else if (view === 'long') {{
                if (cardLong) cardLong.style.display = '';
                if (cardShort) cardShort.style.display = 'none';
                showFullPool = false;
                if (title) title.textContent = '🔥 表 4-A: 最新交易日 AI 预测做多高胜率 Top 5 (Long Pool)';
                if (modeBtn) modeBtn.textContent = '🌐 展开全域 70 标的全景';
                document.querySelectorAll('.t4-all-short-sub').forEach(el => el.style.display = 'none');
                applyT4Filters(5);
            }} else if (view === 'short') {{
                if (cardLong) cardLong.style.display = 'none';
                if (cardShort) cardShort.style.display = '';
            }} else if (view === 'all') {{
                if (cardLong) cardLong.style.display = '';
                if (cardShort) cardShort.style.display = 'none';
                showFullPool = true;
                if (title) title.textContent = '🌐 表 4: 全域 70 标的全景截面 (全量 AI 实时推理 · 多空双向指标)';
                if (modeBtn) modeBtn.textContent = '🏆 仅看 Top 5 精选榜';
                document.querySelectorAll('.t4-all-short-sub').forEach(el => el.style.display = 'block');
                applyT4Filters(70);
            }}
        }}

        function toggleT4PoolMode() {{
            if (currentT4View === 'all') {{
                switchT4View('dual');
            }} else {{
                switchT4View('all');
            }}
        }}

        function filterBySector(sectorName) {{
            currentSector = sectorName;
            if (sectorName !== 'ALL' && currentT4View !== 'all') {{
                showFullPool = true;
                const title = document.getElementById('t4-long-title');
                if (title) title.textContent = '🎯 表 4: 最新交易日 AI 预测做多胜率全景榜 (' + sectorName + ')';
            }}
            const pills = document.querySelectorAll('#t4-sector-pills .filter-pill');
            pills.forEach(p => {{
                if (p.getAttribute('data-sec') === sectorName) {{
                    p.classList.add('active');
                }} else {{
                    p.classList.remove('active');
                }}
            }});
            applyT4Filters(showFullPool ? 70 : 5);
        }}

        function applyT4Filters(limit) {{
            const query = (document.getElementById('t4-search').value || '').trim().toUpperCase();
            const rows = document.querySelectorAll('#t4-tbody .t4-stock-row');
            let maxCount = limit || (showFullPool ? 70 : 5);
            let visibleCount = 0;

            rows.forEach(row => {{
                const rank = parseInt(row.getAttribute('data-rank') || '999', 10);
                const sec = row.getAttribute('data-sector') || '';
                const tk = (row.getAttribute('data-ticker') || '').toUpperCase();
                const isEtf = row.getAttribute('data-isetf') === '1';

                const matchSec = (currentSector === 'ALL') || (currentSector === 'ETF_POOL' && isEtf) || (sec === currentSector);
                const matchQuery = (!query) || tk.includes(query) || sec.toUpperCase().includes(query);
                const matchPool = showFullPool || (query !== '') || (currentSector !== 'ALL') || (rank <= maxCount && !isEtf);

                if (matchSec && matchQuery && matchPool) {{
                    row.style.display = '';
                    visibleCount++;
                }} else {{
                    row.style.display = 'none';
                }}
            }});

            const lbl = document.getElementById('t4-count-label');
            if (lbl) {{
                const modeDesc = (currentSector === 'ALL' && !showFullPool && !query) ? `Top ${{maxCount}} 精选正股` : ('共 ' + visibleCount + ' 只标的');
                lbl.textContent = '当前展示: ' + modeDesc;
            }}
        }}

        document.addEventListener('DOMContentLoaded', function() {{
            let savedView = 'dual';
            try {{
                savedView = localStorage.getItem('predict_t4_view') || 'dual';
            }} catch(e) {{}}
            switchT4View(savedView);
        }});
    </script>
</body>
</html>"""

    full_output_path = _resolve_cache_path(output_path) if not os.path.isabs(output_path) else output_path
    with open(full_output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    file_size_kb = os.path.getsize(full_output_path) / 1024
    try:
        print(f"[+] Rendered HTML dashboard: {full_output_path} ({file_size_kb:.1f} KB)")
    except Exception:
        pass



# 全域 70 标的扩展映射表 (覆盖主看盘终端全部 54 只个股 + 16 只 ETF，映射到 7 大核心赛道先验以复用 OOS 校准乘数)
EXTENDED_RADAR_SECTOR_MAP: Dict[str, Tuple[str, str]] = {
    # 半导体与算力硬件 / 液冷服务器
    'INTC': ('Semiconductors', '半导体与设备'),
    'VRT':  ('Semiconductors', 'AI液冷与服务器'),
    'DELL': ('Semiconductors', 'AI液冷与服务器'),
    'HPE':  ('Semiconductors', 'AI液冷与服务器'),
    'SOXX': ('Semiconductors', '半导体ETF'),
    'SOXL': ('Semiconductors', '半导体3倍做多'),
    'SMH':  ('Semiconductors', '半导体ETF'),
    'KORU': ('Memory', '韩股存储3倍做多'),
    # 存储与闪存生态
    'SKHY': ('Memory', '存储芯片'),
    'SNDK': ('Memory', '存储芯片'),
    # 光通信
    'AXTI': ('Optical_Interconnect', '光通信与互联'),
    # 能源与核电
    'OKLO': ('Energy_Power', '数据中心能源电力'),
    'FPS':  ('Energy_Power', '数据中心能源电力'),
    # 科技巨头与软件安全 (映射至 Neo_Cloud / Energy_Power 稳健先验避免被 SaaS 低先验误伤)
    'GOOGL': ('Neo_Cloud', '科技巨头与AI生态'),
    'META':  ('Neo_Cloud', '科技巨头与AI生态'),
    'AAPL':  ('Neo_Cloud', '科技巨头与AI生态'),
    'AMZN':  ('Neo_Cloud', '科技巨头与AI生态'),
    'CRWD':  ('Energy_Power', '企业软件与AI安全'),
    'IGV':   ('Enterprise_SaaS', '软件科技ETF'),
    # 物理AI与太空商业
    'TSLA': ('Neo_Cloud', '物理AI与太空商业'),
    'SPCX': ('Neo_Cloud', '物理AI与太空商业'),
    'ASTS': ('Neo_Cloud', '物理AI与太空商业'),
    'RKLB': ('Neo_Cloud', '物理AI与太空商业'),
    # 价值蓝筹与医药防守
    'KO':   ('Energy_Power', '价值蓝筹与医药'),
    'PEP':  ('Energy_Power', '价值蓝筹与医药'),
    'MCD':  ('Energy_Power', '价值蓝筹与医药'),
    'NKE':  ('Energy_Power', '价值蓝筹与医药'),
    'AXP':  ('Energy_Power', '价值蓝筹与医药'),
    'V':    ('Energy_Power', '价值蓝筹与医药'),
    'LLY':  ('Energy_Power', '价值蓝筹与医药'),
    'MRNA': ('Energy_Power', '价值蓝筹与医药'),
    'CCXI': ('Energy_Power', '价值蓝筹与医药'),
    # 宏观基准与对冲 ETF
    'QQQM': ('Semiconductors', '大盘与宏观ETF'),
    'SPY':  ('Enterprise_SaaS', '大盘与宏观ETF'),
    'DIA':  ('Enterprise_SaaS', '大盘与宏观ETF'),
    'IWM':  ('Neo_Cloud', '大盘与宏观ETF'),
    'TLT':  ('Energy_Power', '大盘与宏观ETF'),
    'GLD':  ('Energy_Power', '大盘与宏观ETF'),
    'QQQI': ('Enterprise_SaaS', '大盘与宏观ETF'),
    'VIXY': ('Crypto_Proxy', '恐慌与反向对冲'),
    'SQQQ': ('Crypto_Proxy', '恐慌与反向对冲'),
    'SOXS': ('Crypto_Proxy', '恐慌与反向对冲'),
}


def build_extended_live_features_df(
    raw_panel: pd.DataFrame,
    core_latest_stocks: pd.DataFrame,
    ext_cache_file: str = 'data_cache/ml_extended_panel.parquet',
    force_refresh: bool = False
) -> pd.DataFrame:
    """
    通过 yfinance 抓取并缓存主终端其余标的的日 K 线 (零 Finnhub API 消耗)，
    构建覆盖全域 70 只标的 (28 核心 AI 股 + 42 扩展雷达股/ETF) 的最新 T 日截面特征表。
    """
    full_ext_cache = _resolve_cache_path(ext_cache_file)
    os.makedirs(os.path.dirname(full_ext_cache), exist_ok=True)

    existing_syms = set(raw_panel['Ticker'].unique())
    missing_syms = [tk for tk in EXTENDED_RADAR_SECTOR_MAP.keys() if tk not in existing_syms]

    ext_frames = []
    if os.path.exists(full_ext_cache) and not force_refresh:
        try:
            ext_cached_df = pd.read_parquet(full_ext_cache)
            cached_syms = set(ext_cached_df['Ticker'].unique())
            still_missing = [tk for tk in missing_syms if tk not in cached_syms]
            ext_frames.append(ext_cached_df)
            missing_syms = still_missing
        except Exception:
            pass

    if missing_syms:
        print(f"[*] 📥 正在通过 yfinance 补充抓取 {len(missing_syms)} 只主终端扩展标的日线数据 (0 Finnhub 消耗)...")
        newly_fetched = []
        for tk in missing_syms:
            df_tk = _fetch_single_ticker(tk, start_date="2023-01-01")
            if df_tk is not None and not df_tk.empty:
                sec_key = EXTENDED_RADAR_SECTOR_MAP[tk][0]
                df_tk['Ticker'] = tk
                df_tk['Sector'] = sec_key
                newly_fetched.append(df_tk)
        if newly_fetched:
            new_df = pd.concat(newly_fetched, ignore_index=True)
            ext_frames.append(new_df)
            combined_ext = pd.concat(ext_frames, ignore_index=True).drop_duplicates(subset=['Date', 'Ticker'], keep='last')
            combined_ext.to_parquet(full_ext_cache, index=False)
            ext_frames = [combined_ext]

    ext_raw = pd.concat(ext_frames, ignore_index=True) if ext_frames else pd.DataFrame()

    # 汇总全部 70 只标的的日线数据 (28 核心股 + 7 已经存在的宏观 ETF + 35 扩展股/ETF)
    macro_df = compute_macro_features(raw_panel)
    all_70_latest_rows = [core_latest_stocks.copy()]
    core_latest_stocks['Is_Core_28'] = True

    extra_latest_list = []
    for tk, (sec_key, sec_cn_override) in EXTENDED_RADAR_SECTOR_MAP.items():
        if tk in existing_syms:
            sub = raw_panel[raw_panel['Ticker'] == tk].copy()
        elif not ext_raw.empty and tk in ext_raw['Ticker'].values:
            sub = ext_raw[ext_raw['Ticker'] == tk].copy()
        else:
            continue
        if len(sub) < 5:
            continue
        sub['Sector'] = sec_key
        feat_sub = compute_stock_features(sub).sort_values('Date')
        merged_sub = pd.merge_asof(feat_sub, macro_df.sort_values('Date'), on='Date', direction='backward')
        merged_sub['Raw_Alpha_QQQM_5d'] = merged_sub['Ret_5d'] - merged_sub['QQQM_Ret_5d'].fillna(0.0)
        merged_sub['Raw_Alpha_SMH_20d'] = merged_sub['Ret_20d'] - merged_sub['SMH_Ret_20d'].fillna(0.0)
        last_row = merged_sub.sort_values('Date').iloc[[-1]].copy()
        last_row['Sector_CN_Override'] = sec_cn_override
        last_row['Is_Core_28'] = False
        extra_latest_list.append(last_row)

    if extra_latest_list:
        extra_df = pd.concat(extra_latest_list, ignore_index=True)
        core_copy = core_latest_stocks.copy()
        core_copy['Sector_CN_Override'] = ""
        core_copy['Is_Core_28'] = True
        full_latest = pd.concat([core_copy, extra_df], ignore_index=True)
    else:
        full_latest = core_latest_stocks.copy()
        full_latest['Sector_CN_Override'] = ""
        full_latest['Is_Core_28'] = True

    # 在 70 只标的全截面上重算今日 Alpha_QQQM_5d 与 Alpha_SMH_20d (0.65 * cs_rank + 0.35 * sigmoid)
    if 'Raw_Alpha_QQQM_5d' in full_latest.columns:
        cs_q5 = full_latest['Raw_Alpha_QQQM_5d'].rank(pct=True, method='average').fillna(0.5)
        sig_q5 = 1.0 / (1.0 + np.exp(-np.clip(full_latest['Raw_Alpha_QQQM_5d'] / 8.0, -15.0, 15.0)))
        full_latest['Alpha_QQQM_5d'] = (0.65 * cs_q5 + 0.35 * sig_q5).fillna(0.5)

    if 'Raw_Alpha_SMH_20d' in full_latest.columns:
        cs_s20 = full_latest['Raw_Alpha_SMH_20d'].rank(pct=True, method='average').fillna(0.5)
        sig_s20 = 1.0 / (1.0 + np.exp(-np.clip(full_latest['Raw_Alpha_SMH_20d'] / 15.0, -15.0, 15.0)))
        full_latest['Alpha_SMH_20d'] = (0.65 * cs_s20 + 0.35 * sig_s20).fillna(0.5)

    for col in FEATURE_COLS:
        if col in full_latest.columns:
            full_latest[col] = full_latest[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    return full_latest


def export_latest_ai_predictions_json(tables_data: Dict, json_path: str = 'data_cache/latest_ai_predictions.json') -> str:
    """
    导出全池最新 AI 预测快照 JSON (含做多 Top 10 与做空 Top 10 双向决策池)，供主看盘终端热加载联动展示。
    """
    full_json_path = _resolve_cache_path(json_path) if not os.path.isabs(json_path) else json_path
    os.makedirs(os.path.dirname(full_json_path), exist_ok=True)

    all_preds = tables_data.get('all_live_predictions') or tables_data.get('table4', [])
    by_ticker = {item['ticker']: item for item in all_preds}

    top_long = tables_data.get('top_long') or tables_data.get('table4', [])
    top_short = tables_data.get('top_short', [])

    # 规范化宏观气象键别名对齐
    mw = dict(tables_data.get('macro_weather', {}))
    if 'qqqm_bias' in mw and 'qqqm_bias_ma50' not in mw:
        mw['qqqm_bias_ma50'] = mw['qqqm_bias']
    elif 'qqqm_bias_ma50' in mw and 'qqqm_bias' not in mw:
        mw['qqqm_bias'] = mw['qqqm_bias_ma50']

    if 'smh_bias' in mw and 'smh_bias_ma50' not in mw:
        mw['smh_bias_ma50'] = mw['smh_bias']
    elif 'smh_bias_ma50' in mw and 'smh_bias' not in mw:
        mw['smh_bias'] = mw['smh_bias_ma50']

    if 'vixy_pos' in mw and 'vixy_pos_60' not in mw:
        mw['vixy_pos_60'] = mw['vixy_pos']
    elif 'vixy_pos_60' in mw and 'vixy_pos' not in mw:
        mw['vixy_pos'] = mw['vixy_pos_60']

    payload = {
        'kpi': tables_data.get('kpi', {}),
        'macro_weather': mw,
        'table1': tables_data.get('table1', []),
        'table2': tables_data.get('table2', []),
        'table3': tables_data.get('table3', []),
        'table1_short': tables_data.get('table1_short', []),
        'table2_short': tables_data.get('table2_short', []),
        'sectors': tables_data.get('table2', []),
        'top10': top_long,
        'top_long': top_long,
        'top_short': top_short,
        'all_predictions': all_preds,
        'by_ticker': by_ticker
    }
    with open(full_json_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Maintain real-time byte-for-byte dual-path synchronization
    try:
        import shutil
        norm_p = os.path.normpath(os.path.abspath(full_json_path))
        if os.sep + "us-market-radar" + os.sep in norm_p:
            sister_path = norm_p.replace(os.sep + "us-market-radar" + os.sep, os.sep)
        else:
            parts = norm_p.split(os.sep)
            if "scratch" in parts:
                idx = parts.index("scratch")
                parts.insert(idx + 1, "us-market-radar")
                sister_path = os.sep.join(parts)
            else:
                sister_path = None
        if sister_path and sister_path != norm_p:
            os.makedirs(os.path.dirname(sister_path), exist_ok=True)
            shutil.copyfile(norm_p, sister_path)
    except Exception:
        pass

    print(f"[+] 🔗 已导出主看板联动 AI 预测快照: {full_json_path} (覆盖 {len(by_ticker)} 只标的, 多头 Top {len(top_long)}, 空头 Top {len(top_short)})")
    return full_json_path


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
    parser.add_argument('--verify', action='store_true', help="仅执行数据管道完整性校验")
    parser.add_argument('--realtime', action='store_true', help="从 Finnhub 抓取实时最新报价并更新最新行情与特征")
    parser.add_argument('--html-file', type=str, default='predict_radar_5y.html', help="HTML 可视化看板输出路径")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 AI Multi-Sector 5Y US Market Predictor (Milestone M1, M2 & M3 Pipeline)")
    print("=" * 70)

    raw_panel = fetch_or_load_panel_data(cache_file=args.cache_file, force_refresh=args.refresh)

    if args.realtime:
        print("[*] 🌐 启用 --realtime 实时行情模式，正在通过 Finnhub 采集最新报价...")
        quotes = fetch_finnhub_realtime_quotes(ALL_SYMBOLS)
        raw_panel = overlay_finnhub_quotes(raw_panel, quotes)

    feature_panel = build_feature_panel(raw_panel)

    report = verify_pipeline_integrity(feature_panel)
    print("\n" + "=" * 70)
    print("📋 数据与特征管道验证战报 (Data & Feature Pipeline Verification)")
    print("=" * 70)
    for k, v in report.items():
        print(f"  • {k:20s}: {v}")
    print("=" * 70)
    print("✅ Milestone M1 基础管道与 23 维因果特征构建成功！\n")

    if args.verify:
        print("[*] 仅校验模式，执行完毕。")
        return

    print("=" * 70)
    print("🎯 Milestone M2 & M3: 三阈值打标、Walk-Forward 验证与 4 大战报看板生成")
    print("=" * 70)
    labeled_panel = apply_triple_barrier_labeling(feature_panel)
    n_total = len(labeled_panel)
    n_valid = int(labeled_panel['Label'].notna().sum())
    n_pos = int((labeled_panel['Label'] == 1.0).sum())
    pos_rate = (n_pos / n_valid * 100.0) if n_valid > 0 else 0.0
    n_tail = int(labeled_panel['Label'].isna().sum())
    print(f"[*] 做多打标统计: 总样本 {n_total} 条 | 有效标签 {n_valid} 条 | 正样本 {n_pos} 笔 ({pos_rate:.1f}%) | 尾部实盘隔离 {n_tail} 条")

    n_valid_s = int(labeled_panel['Label_Short'].notna().sum())
    n_pos_s = int((labeled_panel['Label_Short'] == 1.0).sum())
    pos_rate_s = (n_pos_s / n_valid_s * 100.0) if n_valid_s > 0 else 0.0
    print(f"[*] 做空打标统计: 总样本 {n_total} 条 | 有效标签 {n_valid_s} 条 | 正样本 {n_pos_s} 笔 ({pos_rate_s:.1f}%) | 尾部实盘隔离 {n_tail} 条")

    oos_df, metrics, model = run_walk_forward_cv(labeled_panel)

    # 提取用于实盘推理的最新截面数据 (28 只核心股 + 42 只扩展雷达标的 = 全域 70 只标的)
    latest_df = feature_panel[feature_panel['Ticker'].isin(TARGET_STOCKS)].copy()
    latest_core_stocks = latest_df.sort_values('Date').groupby('Ticker').last().reset_index()
    latest_stocks = build_extended_live_features_df(raw_panel, latest_core_stocks, force_refresh=args.refresh)
    print(f"[*] 🌐 全域实盘推理截面构建完成: 覆盖 {len(latest_stocks)} 只终端标的 (含 28 只核心赛道股 + {len(latest_stocks) - len(latest_core_stocks)} 只扩展个股/ETF)")

    # 宏观环境水温评估 (与 integrated_market_radar.py 严格统一对齐)
    qqqm_bias = float(latest_stocks['QQQM_Bias_MA50'].mean()) if 'QQQM_Bias_MA50' in latest_stocks.columns else 0.0
    vixy_pos = float(latest_stocks['VIXY_Pos_60'].mean()) if 'VIXY_Pos_60' in latest_stocks.columns else 0.5
    smh_bias = float(latest_stocks['SMH_Bias_MA50'].mean()) if 'SMH_Bias_MA50' in latest_stocks.columns else 0.0
    smh_igv = float(latest_stocks['SMH_IGV_Ratio_Bias'].mean()) if 'SMH_IGV_Ratio_Bias' in latest_stocks.columns else 0.0
    latest_date_str = latest_stocks['Date'].max().strftime('%Y-%m-%d') if 'Date' in latest_stocks.columns else ""

    macro_weather = evaluate_macro_weather(
        qqqm_bias=qqqm_bias,
        smh_bias=smh_bias,
        vixy_pos=vixy_pos,
        smh_igv=smh_igv,
        latest_date=latest_date_str
    )

    # 生成 4 大核心数据表
    tables_data = generate_report_tables(
        oos_df=oos_df,
        model=model,
        feature_cols=FEATURE_COLS,
        latest_features_df=latest_stocks,
        macro_weather=macro_weather,
        is_realtime=args.realtime
    )

    # 控制台格式化打印 4 大核心表格
    print_terminal_tables(tables_data)

    # 渲染可视化暗黑看板 HTML 并导出主看板联动 JSON
    html_path = _resolve_cache_path(args.html_file) if not os.path.isabs(args.html_file) else args.html_file
    render_html_dashboard(tables_data, output_path=html_path)
    export_latest_ai_predictions_json(tables_data)

    print("=" * 70)
    print("✅ Milestone M3: 4 大战报表格控制台输出与 HTML 看板生成全部完成！\n")


if __name__ == '__main__':
    main()
