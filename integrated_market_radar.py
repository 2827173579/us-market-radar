# -*- coding: utf-8 -*-
"""
Integrated Market Radar & Futu Pro Terminal - 37 标的全量量化终端与司令塔
【顶尖量化与金融终端全栈架构】
1. 标的池: 10 只宏观/行业 ETF + 27 只核心科技与蓝筹个股 (共 37 只标的)
2. API 频控: Finnhub REST API (/quote), max_workers=4, 0.02~0.06s 随机抖动 (Jitter), 429 智能降级保护
3. 调度引擎: 3 分钟 (180 秒) 自动轮询 + 本地轻量同步锁 (.radar_last_sync), 手动刷新实时重置倒计时
4. 多维可视化:
   - futu_pro_terminal.html (富途牛牛专业全周期看盘终端: 分时/5日/日K/周K/月K/1~240分钟下拉)
   - stocks_radar.html (27 只核心个股碎股雷达与黄金坑作战矩阵)
   - macro_radar.html (10 只 ETF 宏观水温与市场气象卡片)
"""

import sys
import socket
import subprocess
WIN_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
import os
ANDROID_MODE = ("--android" in sys.argv) or bool(os.environ.get("TERMUX_VERSION"))
NO_BROWSER = ("--no-browser" in sys.argv) or ANDROID_MODE
import time
import random
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import json
try:
    import shared_alert_state
    _SHARED_ALERTS = True
except Exception:
    shared_alert_state = None
    _SHARED_ALERTS = False
from concurrent.futures import ThreadPoolExecutor, as_completed
import webbrowser
import numpy as np
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import math
from typing import Any, Optional, Union, Dict, Tuple

from create_futu_terminal import build_futu_terminal_html

# 终端编码与实时行缓冲适配 (Windows CMD / PowerShell)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

# ==============================================================================
# 一、 标的池全量配置字典 (14 只 ETF + 43 只个股 = 57 只标的)
# ==============================================================================
MONITORED_TARGETS = {
    "ETFS": {
        "QQQ": {"name": "纳指100ETF", "sub": "科技大盘Beta", "cat": "大盘基准"},
        "SPY":  {"name": "标普500", "sub": "美股全市场基石", "cat": "大盘基准"},
        "DIA":  {"name": "道琼斯蓝筹", "sub": "美国30大工业蓝筹基石", "cat": "大盘基准"},
        "IWM":  {"name": "罗素2000", "sub": "小盘流动性温度计", "cat": "小盘弹性"},
        "SMH":  {"name": "半导体ETF", "sub": "硬科技与周期先导", "cat": "半导体"},
        "SOXX": {"name": "费城半导体ETF", "sub": "iShares核心半导体指数基准", "cat": "半导体"},
        "SOXL": {"name": "3倍做多半导体", "sub": "费半三倍正向杠杆·实盘底仓跟踪", "cat": "正向杠杆", "cost": 0.0, "shares": 0.0, "trim1": 94.50, "trim1_shares": 4.22594, "trim2": 102.50, "base_val": 82.09, "target": 0.0},
        "KORU": {"name": "3倍做多韩国", "sub": "韩国股市3倍正向杠杆·三星海力士先导", "cat": "正向杠杆"},
        "IGV":  {"name": "软件科技ETF", "sub": "SaaS与AI企业级应用", "cat": "云与软件"},
        "TLT":  {"name": "20年+美债", "sub": "长端利率与避险情绪", "cat": "利率债券"},
        "GLD":  {"name": "黄金现货ETF", "sub": "抗通胀与终极地缘避险", "cat": "大宗商品"},
        "VIXY": {"name": "短期恐慌指数", "sub": "尾部黑天鹅对冲工具", "cat": "黑天鹅对冲"},
        "SQQQ": {"name": "3倍做空纳指", "sub": "纳指100三倍反向对冲", "cat": "反向对冲"},
        "SOXS": {"name": "3倍做空半导体", "sub": "费半半导体三倍反向对冲", "cat": "反向对冲"},
    },
    "STOCKS": {
        # 1. 底层算力与半导体设备
        "NVDA":  {"name": "NVIDIA", "sub": "全球算力总龙头", "cat": "芯片算力", "cost": 0.0, "shares": 0.0, "base_val": 130.0},
        "TSM":   {"name": "台积电", "sub": "全球先进制程代工绝对垄断", "cat": "晶圆代工", "cost": 0.0, "shares": 0.0, "base_val": 185.0},
        "AMD":   {"name": "AMD", "sub": "算力双雄与x86挑战者", "cat": "芯片算力", "cost": 0.0, "shares": 0.0, "base_val": 155.0},
        "AVGO":  {"name": "博通", "sub": "网络交换与ASIC定制芯片之王", "cat": "通信芯片", "cost": 0.0, "shares": 0.0, "base_val": 170.0},
        "QCOM":  {"name": "高通", "sub": "全球移动基带与端侧AI芯片霸主", "cat": "通信芯片", "cost": 0.0, "shares": 0.0, "base_val": 160.0},
        "ASML":  {"name": "阿斯麦", "sub": "光刻机绝对霸主", "cat": "制造设备", "cost": 0.0, "shares": 0.0, "base_val": 820.0},
        "AMAT":  {"name": "应用材料", "sub": "半导体薄膜沉积与设备龙头", "cat": "制造设备", "cost": 0.0, "shares": 0.0, "base_val": 210.0},
        
        # 2. 商业化巨头与企业级AI
        "MSFT":  {"name": "微软", "sub": "企业软件与Copilot商业化", "cat": "云与软件", "cost": 0.0, "shares": 0.0, "base_val": 420.0},
        "GOOGL": {"name": "Alphabet", "sub": "全栈AI生态与搜索基本盘", "cat": "生态平台", "cost": 0.0, "shares": 0.0, "base_val": 170.0},
        "META":  {"name": "Meta", "sub": "开源Llama与最强广告变现", "cat": "社交AI", "cost": 0.0, "shares": 0.0, "base_val": 560.0},
        "AAPL":  {"name": "苹果", "sub": "端侧AI设备与消费电子闭环", "cat": "终端硬件", "cost": 0.0, "shares": 0.0, "base_val": 225.0},
        "AMZN":  {"name": "亚马逊", "sub": "AWS算力底座与电商飞轮", "cat": "云与电商", "cost": 0.0, "shares": 0.0, "base_val": 185.0},
        "PLTR":  {"name": "Palantir", "sub": "AIP企业级与军工落地标杆", "cat": "企业AI", "cost": 0.0, "shares": 0.0, "base_val": 42.0},
        "NOW":   {"name": "ServiceNow", "sub": "企业级AI工作流与SaaS自动化龙头", "cat": "云与软件", "cost": 0.0, "shares": 0.0, "base_val": 850.0},
        
        # 3. 新算力租赁 (Neo-Cloud)
        "CRWV":  {"name": "CoreWeave", "sub": "纯血GPU算力租赁高弹性领头羊", "cat": "算力租赁", "cost": 0.0, "shares": 0.0, "base_val": 95.0},
        "NBIS":  {"name": "Nebius", "sub": "欧洲与全球新兴AI云服务商", "cat": "算力租赁", "cost": 0.0, "shares": 0.0, "base_val": 28.0},
        "APLD":  {"name": "Applied Digital", "sub": "北美高性能HPC与AI算力中心基建", "cat": "算力租赁", "cost": 0.0, "shares": 0.0, "base_val": 7.5},
        
        # 4. AI数据中心能源与电力底座
        "VST":   {"name": "Vistra Energy", "sub": "AI数据中心独立电力与核能标杆", "cat": "能源电力", "cost": 0.0, "shares": 0.0, "base_val": 125.0},
        "BE":    {"name": "Bloom Energy", "sub": "数据中心分布式固体氧化物燃料电池", "cat": "能源电力", "cost": 0.0, "shares": 0.0, "base_val": 18.0},
        
        # 5. 光通信与电光高速互联
        "CRDO":  {"name": "Credo", "sub": "AEC有源电缆与高速互联黑马", "cat": "高速互联", "cost": 0.0, "shares": 0.0, "base_val": 42.0},
        "COHR":  {"name": "Coherent", "sub": "800G/1.6T光模块与碳化硅材料", "cat": "光通信", "cost": 0.0, "shares": 0.0, "base_val": 95.0},
        "MRVL":  {"name": "Marvell", "sub": "光互联DSP芯片与定制AI芯片", "cat": "通信芯片", "cost": 0.0, "shares": 0.0, "base_val": 85.0},
        "AAOI":  {"name": "AOI", "sub": "北美数据中心高弹性光模块", "cat": "光通信", "cost": 0.0, "shares": 0.0, "base_val": 22.0},
        "AXTI":  {"name": "AXTI", "sub": "磷化铟/砷化镓光通信半导体衬底", "cat": "光通信", "cost": 0.0, "shares": 0.0, "base_val": 45.0},
        "LITE":  {"name": "Lumentum", "sub": "光纤通信器件与超快激光技术", "cat": "光通信", "cost": 0.0, "shares": 0.0, "base_val": 75.0},
        "FN":    {"name": "Fabrinet", "sub": "800G/1.6T光模块精密光学封装纯代工龙头", "cat": "光通信", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        "GLW":   {"name": "康宁", "sub": "AI数据中心高密度光纤光缆与光连接器巨头", "cat": "光通信", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        
        # 6. 太空商业与低轨卫星
        "SPCX":  {"name": "SpaceX", "sub": "星链与可重复火箭先驱", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 147.0},
        "ASTS":  {"name": "AST SpaceMobile", "sub": "全球手机直连卫星宽带网络", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 26.0},
        "RKLB":  {"name": "Rocket Lab", "sub": "中小型火箭发射与卫星制造", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 19.5},
        "FLY":   {"name": "Firefly Aerospace", "sub": "小型运载火箭与太空国防任务", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        "LUNR":  {"name": "Intuitive Machines", "sub": "月球着陆器与深空通信基建", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        "NASA":  {"name": "NASA太空主题ETF", "sub": "商业航天与深空探测主题ETF", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        
        # 7. 价值蓝筹与核心风控仓位
        "KO":    {"name": "可口可乐", "sub": "全球消费防御与抗通胀现金奶牛", "cat": "价值蓝筹", "cost": 0.0, "shares": 0.0, "base_val": 68.0},
        "AXP":   {"name": "美国运通", "sub": "高端消费信贷与抗衰退金融底座", "cat": "金融蓝筹", "cost": 0.0, "shares": 0.0, "base_val": 270.0},
        "INTC":  {"name": "Intel", "sub": "IDM2.0转型", "cat": "芯片算力", "cost": 0.0, "shares": 0.0, "base_val": 0.0},

        # 8. 物理AI与自动驾驶
        "TSLA":  {"name": "特斯拉", "sub": "FSD端到端与物理AI人形机器人", "cat": "物理AI", "cost": 0.0, "shares": 0.0, "base_val": 320.0},

        # 9. AI液冷散热与服务器硬件
        "VRT":   {"name": "Vertiv", "sub": "AI数据中心液冷散热与配电垄断", "cat": "液冷散热", "cost": 0.0, "shares": 0.0, "base_val": 165.0},
        "DELL":  {"name": "戴尔科技", "sub": "企业级AI服务器与算力集成交付", "cat": "服务器ODM", "cost": 0.0, "shares": 0.0, "base_val": 380.0},
        "HPE":   {"name": "慧与科技", "sub": "企业级AI服务器与超算基础设施", "cat": "服务器ODM", "cost": 0.0, "shares": 0.0, "base_val": 45.0},

        # 10. 核电直供与SMR先进微堆
        "CEG":   {"name": "星座能源", "sub": "微软三哩岛核电机组20年直签巨头", "cat": "核能电力", "cost": 0.0, "shares": 0.0, "base_val": 220.0},
        "OKLO":  {"name": "Oklo", "sub": "Sam Altman背书的SMR先进微裂变堆", "cat": "核能电力", "cost": 0.0, "shares": 0.0, "base_val": 20.0},
        "FPS":   {"name": "Forgent Power", "sub": "AI数据中心关键电力基础设施", "cat": "能源电力", "cost": 0.0, "shares": 0.0, "base_val": 20.0},

        # 11. HBM与存储/硬盘/NAND闪存生态
        "MU":    {"name": "美光科技", "sub": "HBM3e高带宽内存与存储周期龙头", "cat": "存储芯片", "cost": 0.0, "shares": 0.0, "base_val": 750.0},
        "SKHY":  {"name": "SK Hynix", "sub": "美股HBM第一龙头与先进制程", "cat": "存储芯片", "cost": 0.0, "shares": 0.0, "base_val": 115.0},
        "WDC":   {"name": "西部数据", "sub": "AI近线高密HDD与企业级存储巨头", "cat": "数据存储", "cost": 0.0, "shares": 0.0, "base_val": 290.0},
        "SNDK":  {"name": "闪迪", "sub": "独立上市NAND闪存与企业级SSD", "cat": "闪存存储", "cost": 0.0, "shares": 0.0, "base_val": 1050.0},
        "STX":   {"name": "希捷科技", "sub": "AI数据中心大容量机械存储霸主", "cat": "数据存储", "cost": 0.0, "shares": 0.0, "base_val": 540.0},

        # 12. 零信任网络安全与全球流动性杠杆
        "CRWD":  {"name": "CrowdStrike", "sub": "AI驱动SecOps与零信任终端安全龙头", "cat": "AI安全", "cost": 0.0, "shares": 0.0, "base_val": 220.0},
        "MSTR":  {"name": "微策略", "sub": "比特币储备国库与全球流动性杠杆", "cat": "数字资产", "cost": 0.0, "shares": 0.0, "base_val": 130.0},
        "BTC-USD": {"name": "比特币现货", "sub": "全球数字黄金与加密风向标", "cat": "数字资产", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        "COIN":  {"name": "Coinbase", "sub": "北美合规加密资产交易所与链上托管核心", "cat": "数字资产", "cost": 0.0, "shares": 0.0, "base_val": 180.0},

        # 13. 企业级算力云与资本载体
        "ORCL":  {"name": "甲骨文", "sub": "OCI云基础设施与企业大模型底座", "cat": "云与软件", "cost": 0.0, "shares": 0.0, "base_val": 110.0},

        # 14. 医药健康、消费龙头与全球支付网络
        "LLY":   {"name": "礼来", "sub": "全球GLP-1减肥药与制药万亿新贵", "cat": "生物医药", "cost": 0.0, "shares": 0.0, "base_val": 780.0},
        "MRNA":  {"name": "Moderna", "sub": "mRNA疫苗与肿瘤前沿疗法先锋", "cat": "生物医药", "cost": 0.0, "shares": 0.0, "base_val": 45.0},
        "MCD":   {"name": "麦当劳", "sub": "全球快餐连锁与抗周期现金流巨无霸", "cat": "价值蓝筹", "cost": 0.0, "shares": 0.0, "base_val": 280.0},
        "NKE":   {"name": "耐克", "sub": "全球运动鞋服总龙头·去库存与品牌复苏", "cat": "可选消费", "cost": 0.0, "shares": 0.0, "base_val": 70.0},
        "V":     {"name": "Visa", "sub": "全球卡组织支付网络垄断寡头", "cat": "金融科技", "cost": 0.0, "shares": 0.0, "base_val": 290.0},
        "PEP":   {"name": "百事可乐", "sub": "食品饮料防守抗通胀消费龙头", "cat": "价值蓝筹", "cost": 0.0, "shares": 0.0, "base_val": 160.0},

        # 15. 四大传统行业宏观风向标 (金融 / 能源 / 零售消费 / 工业机械)
        "JPM":   {"name": "摩根大通", "sub": "美国最大商业银行与投行风向标", "cat": "传统金融", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        "XOM":   {"name": "埃克森美孚", "sub": "全球传统油气与能源巨头", "cat": "传统能源", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        "WMT":   {"name": "沃尔玛", "sub": "全美必选消费与零售防御基石", "cat": "传统消费", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        "CAT":   {"name": "卡特彼勒", "sub": "全球重型机械与工业周期龙头", "cat": "传统工业", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        # 16. 现货贵金属与 AI 前沿新股
        "XAUUSD": {"name": "伦敦金现货", "sub": "国际现货黄金/美元 (24h Spot · 免费数据源)", "cat": "大宗商品", "cost": 0.0, "shares": 0.0, "base_val": 0.0},
        "ANT":    {"name": "Anthropic (IPO)", "sub": "Claude 大模型巨头 · 11/19 Nasdaq 上市 ($83)", "cat": "云与软件", "cost": 0.0, "shares": 0.0, "base_val": 83.0, "ipo_price": 83.0}
    }
}

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

# ==============================================================================
# 二、 Finnhub 频控与并发抓取引擎 (Jitter + 429 智能降级)
# ==============================================================================
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()

# PushPlus 微信推送配置与去重缓存
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()
PUSHPLUS_CHANNEL = os.environ.get("PUSHPLUS_CHANNEL", "clawbot").strip()
DISCORD_CLAW_WEBHOOK_URL = os.environ.get("DISCORD_CLAW_WEBHOOK_URL", "").strip()
DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL = os.environ.get(
    "DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL",
    ""
).strip()


def get_claw_webhook_url():
    """买点/止损唯一来源：DISCORD_CLAW_WEBHOOK_URL，缺失返回空。"""
    return os.environ.get("DISCORD_CLAW_WEBHOOK_URL", "").strip()


def get_defensive_webhook_url():
    """防御因子唯一来源：DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL（兼容 DISCORD_DEFENSIVE_WEBHOOK_URL），缺失返回空。"""
    return (
        os.environ.get("DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL", "").strip()
        or os.environ.get("DISCORD_DEFENSIVE_WEBHOOK_URL", "").strip()
    )


def _external_alerts_disabled():
    """开发验证/干跑保护：设置 RADAR_DISABLE_EXTERNAL_ALERTS=1 或 --no-alerts 时严禁任何真实外发推送。"""
    val = str(os.environ.get("RADAR_DISABLE_EXTERNAL_ALERTS", "")).strip().lower()
    return val in ("1", "true", "yes", "on") or ("--no-alerts" in sys.argv)

# ------------------------------------------------------------------------------
# 🎯 账户资金与风控配置 (V3.1 旗舰纯比例制 · 单股上限 25% · 1.5%风险预算一次性建仓)
# ------------------------------------------------------------------------------
TOTAL_ACCOUNT_EQUITY = 10000.0      # 参考基准账户总资产 ($10,000)
MAX_POSITION_PCT = 0.25            # 单股最大配置上限 25% (V3.1 旗舰标准)
MAX_ALLOC_PER_STOCK = round(TOTAL_ACCOUNT_EQUITY * MAX_POSITION_PCT, 2)  # $2,500.00
BATCH1_PCT = 1.00                  # V3.1 1.5% 账户风险预算一次性建仓到位 (100%到位，不再 3-3-4 碎步)
SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_HTML_FILE = os.path.join(SCRATCH_DIR, "macro_radar.html")
STOCKS_HTML_FILE = os.path.join(SCRATCH_DIR, "stocks_radar.html")
FUTU_HTML_FILE = os.path.join(SCRATCH_DIR, "futu_pro_terminal.html")
LATEST_RADAR_SNAPSHOT_FILE = os.path.join(SCRATCH_DIR, "data_cache", "latest_radar_snapshot.json")
ALERT_CACHE_FILE = os.path.join(SCRATCH_DIR, ".radar_alert_history.json")
TARGET_WATCHLIST_FILE = os.path.join(SCRATCH_DIR, "target_watchlist.json")
USER_PORTFOLIO_FILE = os.path.join(SCRATCH_DIR, "user_portfolio.json")
NOTIFICATION_CACHE_FILE = os.path.join(SCRATCH_DIR, ".terminal_notifications.json")
ALERT_COOLDOWN_SECONDS = 7200   # 2 小时同状态防刷冷却周期 (避免夜盘/低频期长时间静默)
ENTRY_COOLDOWN_SECONDS = 3600   # 自选日内建仓 1 小时通知冷却周期

def load_terminal_notifications():
    """读取终端消息通知中心存档"""
    try:
        if os.path.exists(NOTIFICATION_CACHE_FILE):
            with open(NOTIFICATION_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []

def save_terminal_notifications(notifs):
    """保存终端消息通知中心存档 (最多保留最新 100 条)"""
    try:
        with open(NOTIFICATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(notifs[:100], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] 保存终端通知异常: {e}")

def load_user_portfolio():
    return {}


def save_user_portfolio(portfolio):
    """保存用户自定义独立实盘持仓配置 (user_portfolio.json)"""
    try:
        with open(USER_PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] 保存用户持仓文件异常: {e}")

def load_target_watchlist():
    """读取用户标记的自选建仓盯盘列表"""
    try:
        if os.path.exists(TARGET_WATCHLIST_FILE):
            with open(TARGET_WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [str(x).upper() for x in data]
    except Exception:
        pass
    return []

def save_target_watchlist(watchlist):
    """保存用户自选建仓盯盘列表"""
    try:
        with open(TARGET_WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(watchlist, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] 保存自选盯盘列表异常: {e}")

def get_local_lan_ip():
    """获取本机局域网 IP (如 192.168.x.x)，便于手机同一 Wi-Fi 下直连"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"

class WatchlistApiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            # 1. 移动端/局域网 Web 静态服务 (主页与看板 HTML 零缓存直出)
            target_file = None
            if parsed.path in ["/", "/index.html", "/futu_pro_terminal.html"]:
                target_file = "futu_pro_terminal.html"
            elif parsed.path.strip("/").endswith(".html"):
                target_file = os.path.basename(parsed.path)
            elif parsed.path.startswith("/data_cache/") and parsed.path.endswith(".json"):
                target_file = os.path.join("data_cache", os.path.basename(parsed.path))

            if target_file:
                html_path = os.path.join(SCRATCH_DIR, target_file)
                if os.path.exists(html_path):
                    with open(html_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    ct = "text/html; charset=utf-8" if target_file.endswith(".html") else "application/json; charset=utf-8"
                    self.send_header("Content-Type", ct)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            elif parsed.path == "/manifest.json":
                manifest_data = {
                    "name": "富途牛牛专业全周期看盘终端",
                    "short_name": "富途雷达",
                    "start_url": "/",
                    "display": "standalone",
                    "background_color": "#0b0e14",
                    "theme_color": "#0b0e14",
                    "orientation": "portrait-primary",
                    "icons": [
                        {
                            "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%23ff6900'/><text y='70' x='15' font-size='65'>⚡</text></svg>",
                            "sizes": "192x192 512x512",
                            "type": "image/svg+xml"
                        }
                    ]
                }
                raw_m = json.dumps(manifest_data, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(raw_m)))
                self.end_headers()
                self.wfile.write(raw_m)
                return
            elif parsed.path.startswith("/sounds/"):
                sound_name = os.path.basename(parsed.path)
                sound_path = os.path.join(SCRATCH_DIR, "sounds", sound_name)
                if os.path.exists(sound_path):
                    with open(sound_path, "rb") as f:
                        s_data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(s_data)))
                    self.end_headers()
                    self.wfile.write(s_data)
                    return

            # 2. REST API 接口服务
            if parsed.path == "/api/toggle_star":
                qs = parse_qs(parsed.query)
                ticker = qs.get("ticker", [""])[0].upper()
                w = load_target_watchlist()
                if ticker:
                    if ticker in w:
                        w.remove(ticker)
                    else:
                        w.append(ticker)
                    save_target_watchlist(w)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "ticker": ticker, "watchlist": w}).encode("utf-8"))
                return
            elif parsed.path == "/api/watchlist":
                w = load_target_watchlist()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"watchlist": w}).encode("utf-8"))
                return
            elif parsed.path == "/api/portfolio":
                p = load_user_portfolio()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "portfolio": p}, ensure_ascii=False).encode("utf-8"))
                return
            elif parsed.path == "/api/save_portfolio":
                qs = parse_qs(parsed.query)
                raw_json = qs.get("data", ["{}"])[0]
                p_data = json.loads(raw_json)
                save_user_portfolio(p_data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "portfolio": p_data}, ensure_ascii=False).encode("utf-8"))
                return
            elif parsed.path == "/api/notifications":
                notifs = load_terminal_notifications()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "notifications": notifs}, ensure_ascii=False).encode("utf-8"))
                return
            elif parsed.path == "/api/clear_notifications":
                save_terminal_notifications([])
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "notifications": []}, ensure_ascii=False).encode("utf-8"))
                return
        except Exception as e:
            print(f"[!] API GET 异常: {e}")
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/portfolio":
                content_len = int(self.headers.get("Content-Length", 0))
                post_body = self.rfile.read(content_len)
                p_data = json.loads(post_body.decode("utf-8"))
                save_user_portfolio(p_data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "portfolio": p_data}, ensure_ascii=False).encode("utf-8"))
                return
        except Exception as e:
            print(f"[!] API POST 异常: {e}")
        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # 静默处理，不污染主控制台日志

_star_server_started = False
def start_star_api_server(port=8765):
    """启动本地常驻 HTTP 接口服务，确保即使 --once 单次脚本退出后网页端点击 ⭐ 依然实时写入 target_watchlist.json"""
    global _star_server_started
    if _star_server_started:
        return
    import socket
    import subprocess
    # 1. 检测 8765 端口是否已有常驻服务在监听
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            _star_server_started = True
            return
    except Exception:
        pass

    # 2. 若当前是 --once 模式，拉起独立后台守护进程常驻监听 8765 端口
    if "--once" in sys.argv and "--star-server-daemon" not in sys.argv:
        try:
            creation_flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--star-server-daemon"],
                creationflags=creation_flags,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            _star_server_started = True
            return
        except Exception:
            pass

    # 3. 常规长轮询模式下直接启动守护线程
    def _run():
        try:
            server = HTTPServer(('0.0.0.0', port), WatchlistApiHandler)
            server.serve_forever()
        except Exception:
            pass
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    _star_server_started = True
    lan_ip = get_local_lan_ip()
    print(f"[*] 📱 移动端/局域网看盘地址: http://{lan_ip}:{port} (同一Wi-Fi下手机浏览器直接打开)")
    print(f"[*] 💻 电脑端本地看盘地址: http://127.0.0.1:{port}")

# 本地轻量同步锁与 1 分钟 (60秒) 极速轮询周期
LOCK_FILE = os.path.join(SCRATCH_DIR, ".radar_last_sync")
SYNC_INTERVAL = 60  # 1 分钟 (60 秒) 极速自动轮询周期

def get_last_sync_time():
    """读取本地同步时间戳"""
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return float(content)
    except Exception:
        pass
    return 0.0

def update_last_sync_time():
    """写入最新同步时间戳"""
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception as e:
        print(f"[!] 同步锁写入异常: {e}")

class FinnhubRateLimiter:
    """
    稳态令牌桶速率限制器 (Token Bucket Rate Limiter)
    - 免费额度红线: 60 次/分钟
    - 稳态流控目标: 最大 50 次/分钟 (0.833 req/s)
    - 最小请求间隔 (min_interval): 1.05 秒 (严防突发并发打穿 Finnhub 滑动窗口)
    - 线程安全: threading.Lock 保护状态
    - 遭遇 429 智能指数退避: 首次 10 秒，二次 20 秒
    """
    def __init__(self, max_per_minute: float = 50.0, min_interval: float = 1.05):
        self.capacity = float(max_per_minute)
        self.tokens = float(max_per_minute)
        self.fill_rate = float(max_per_minute) / 60.0
        self.min_interval = float(min_interval)
        self.last_fill_time = time.time()
        self.last_request_time = 0.0
        self.lock = threading.Lock()

    def acquire(self):
        """阻塞获取调用令牌，确保请求速率与间隔满足安全红线"""
        with self.lock:
            now = time.time()
            # 1. 刷新令牌桶
            elapsed = now - self.last_fill_time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            self.last_fill_time = now

            # 2. 如果令牌不足，等待补充
            if self.tokens < 1.0:
                needed = (1.0 - self.tokens) / self.fill_rate
                if needed > 0:
                    time.sleep(needed)
                now = time.time()
                self.tokens = 0.0
                self.last_fill_time = now
            else:
                self.tokens -= 1.0

            # 3. 强制最小请求间隔 (min_interval = 1.05s)
            time_since_last = now - self.last_request_time
            if time_since_last < self.min_interval:
                sleep_needed = self.min_interval - time_since_last
                time.sleep(sleep_needed)
                now = time.time()

            self.last_request_time = now

    def handle_429(self, attempt: int = 0):
        """429 限流保护与指数退避 (首次 10 秒，二次 20 秒)"""
        with self.lock:
            backoff = 10.0 if attempt == 0 else 20.0
            print(f"[!] ⚠️ Finnhub 触发 429 限流，执行指数退避休眠 {backoff:.1f} 秒...")
            # 清空令牌，避免退避结束后瞬间再次并发
            self.tokens = 0.0
            self.last_fill_time = time.time() + backoff
            self.last_request_time = time.time() + backoff
        time.sleep(backoff)

FINNHUB_LIMITER = FinnhubRateLimiter(max_per_minute=50.0, min_interval=1.05)

def fetch_single_finnhub_quote(symbol, api_key):
    """单标的通过 Finnhub REST API 获取秒级报价 (加 1.05s 稳态令牌桶频控与 429 指数退避保护)"""
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={api_key}"
    for attempt in range(2):
        try:
            FINNHUB_LIMITER.acquire()
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
                if isinstance(data, dict) and data.get("c", 0) > 0:
                    return symbol, {
                        "last": float(data.get("c", 0)),
                        "change": float(data.get("d", 0) or 0),
                        "pct_change": float(data.get("dp", 0) or 0),
                        "high": float(data.get("h", 0) or 0),
                        "low": float(data.get("l", 0) or 0),
                        "open": float(data.get("o", 0) or 0),
                        "prev_close": float(data.get("pc", 0) or 0),
                        "timestamp": data.get("t", 0)
                    }
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == 0:
                    FINNHUB_LIMITER.handle_429(attempt)
                    continue
                print(f"[!] [Finnhub 频控预警] {symbol} 触发 429 限流保护，该标的将自动使用 yfinance 底模数据。")
            else:
                pass
        except Exception:
            pass
    return symbol, None

def fetch_yahoo_crypto_quote(symbol="BTC-USD"):
    """BTC-USD 绕过 Finnhub，直接用 Yahoo 5m/1d 数据。"""
    try:
        tk = yf.Ticker(symbol)
        h = tk.history(period="2d", interval="5m", prepost=True)
        if h is None or h.empty:
            return {}
        closes = h["Close"].dropna()
        if closes.empty:
            return {}
        last = float(closes.iloc[-1])
        prev = float(tk.fast_info.get("previous_close") or 0) if hasattr(tk, "fast_info") else 0.0
        if prev <= 0:
            daily = tk.history(period="5d", interval="1d")
            dc = daily["Close"].dropna()
            prev = float(dc.iloc[-2]) if len(dc) >= 2 else last
        change = last - prev
        return {"last": last, "change": change, "pct_change": (change / prev * 100.0) if prev > 0 else 0.0,
                "high": float(h["High"].max()), "low": float(h["Low"].min()), "prev_close": prev, "source": "yahoo"}
    except Exception as e:
        print(f"[!] BTC-USD Yahoo quote failed: {e}")
        return {}


def fetch_xauusd_spot_quote(timeout=6):
    """伦敦金现货 (XAUUSD) 免费实时报价瀑布流 (0 Finnhub 消耗，复用 ml_predictor_5y 统一多源实现)。"""
    try:
        import ml_predictor_5y as _mlp
        return _mlp.fetch_xauusd_spot_quote(timeout=timeout)
    except Exception as e:
        print(f"[!] XAUUSD spot quote failed: {e}")
        return {}


def fetch_tradingview_equity_quote(symbol, exchange="NASDAQ", timeout=6):
    """
    免费美股 / 首日 IPO 实时报价回退源 (TradingView America Scanner -> Yahoo 1m)：
    当 Finnhub 暂未收录次新 IPO 报价或盘前返回 0 时启用。
    """
    try:
        payload = json.dumps({
            "symbols": {"tickers": [f"{exchange}:{symbol}"]},
            "columns": ["name", "close", "premarket_close", "postmarket_close", "change", "change_abs", "high", "low", "open"],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://scanner.tradingview.com/america/scan",
            data=payload,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for row in (data.get("data") or []):
            vals = row.get("d") or []
            if len(vals) >= 4:
                pre_c = float(vals[2] or 0.0) if vals[2] is not None else 0.0
                reg_c = float(vals[1] or 0.0) if vals[1] is not None else 0.0
                post_c = float(vals[3] or 0.0) if vals[3] is not None else 0.0
                last = pre_c if (get_us_market_session()[0] == "PRE_MARKET" and pre_c > 0) else (reg_c or pre_c or post_c)
                if last > 0:
                    pct_chg = float(vals[4] or 0.0) if (len(vals) > 4 and vals[4] is not None) else 0.0
                    chg = float(vals[5] or 0.0) if (len(vals) > 5 and vals[5] is not None) else 0.0
                    hi = float(vals[6] or last) if (len(vals) > 6 and vals[6] is not None) else last
                    lo = float(vals[7] or last) if (len(vals) > 7 and vals[7] is not None) else last
                    return {
                        "last": round(last, 2),
                        "change": round(chg, 2),
                        "pct_change": round(pct_chg, 2),
                        "high": round(hi, 2),
                        "low": round(lo, 2),
                        "prev_close": round(last - chg, 2),
                        "source": "tradingview",
                    }
    except Exception:
        pass
    try:
        h1 = yf.Ticker(symbol).history(period="1d", interval="1m", prepost=True)
        if h1 is not None and not h1.empty and "Close" in h1.columns:
            cs = h1["Close"].dropna()
            if len(cs) > 0 and float(cs.iloc[-1]) > 0:
                last = float(cs.iloc[-1])
                return {
                    "last": round(last, 2),
                    "change": 0.0,
                    "pct_change": 0.0,
                    "high": round(float(h1["High"].max()), 2),
                    "low": round(float(h1["Low"].min()), 2),
                    "prev_close": 0.0,
                    "source": "yahoo_1m",
                }
    except Exception:
        pass
    return {}


def _apply_xauusd_spot_basis(history_dict, intraday_map, realtime_quotes):
    """
    将 GC=F 代理的历史日线与 5m 指标按现价价差比例 (xau_spot / gc_ref) 校准为纯伦敦金现货美元计价，
    并在缺失涨跌幅时（如 Swissquote/Gold-API 回退）自动由前收估算。
    """
    xau_rq = realtime_quotes.get("XAUUSD") if isinstance(realtime_quotes, dict) else None
    if not xau_rq or float(xau_rq.get("last", 0) or 0) <= 0:
        return
    xau_spot = float(xau_rq["last"])
    intra = intraday_map.get("XAUUSD") if isinstance(intraday_map, dict) else None
    df_hist = history_dict.get("XAUUSD") if isinstance(history_dict, dict) else None

    gc_ref = 0.0
    if isinstance(intra, dict):
        gc_ref = float(intra.get("ext_price") or intra.get("rth_close") or 0.0)
    if gc_ref <= 0 and df_hist is not None and not df_hist.empty:
        gc_ref = float(df_hist["Close"].iloc[-1])

    if gc_ref > 0:
        ratio = xau_spot / gc_ref
        if 0.8 <= ratio <= 1.2:
            if df_hist is not None and not df_hist.empty:
                for col in ("Open", "High", "Low", "Close"):
                    if col in df_hist.columns:
                        df_hist[col] = (df_hist[col].astype(float) * ratio).round(2)
            if isinstance(intra, dict):
                for key in ("vwap", "rth_close", "rth_high", "rth_low", "last_5m_low", "prev_5m_low"):
                    val = intra.get(key)
                    if val is not None and float(val) > 0:
                        intra[key] = round(float(val) * ratio, 2)
                c24 = intra.get("continuous_24h")
                if isinstance(c24, dict):
                    for sk in ("utc_open", "high_24h", "low_24h"):
                        sv = c24.get(sk)
                        if sv is not None and float(sv) > 0:
                            c24[sk] = round(float(sv) * ratio, 2)
                    for ak in ("actual_prices", "vwap_prices"):
                        arr = c24.get(ak)
                        if isinstance(arr, list) and arr:
                            c24[ak] = [round(float(x) * ratio, 2) if (x is not None and float(x) > 0) else x for x in arr]
                    if isinstance(c24.get("vwap_prices"), list) and c24["vwap_prices"]:
                        last_vw = c24["vwap_prices"][-1]
                        if last_vw is not None and float(last_vw) > 0:
                            intra["vwap"] = round(float(last_vw), 2)
                intra["rth_close"] = round(xau_spot, 2)
                intra["ext_price"] = 0.0

    if not xau_rq.get("has_pct") and df_hist is not None and not df_hist.empty:
        prev_c = 0.0
        try:
            from zoneinfo import ZoneInfo as _ZI
            _ny_today = datetime.now(_ZI("America/New_York")).date()
        except Exception:
            _ny_today = datetime.now().date()
        _last_idx = df_hist.index[-1]
        if hasattr(_last_idx, "date"):
            _last_dt = _last_idx.date()
            if _last_dt >= _ny_today and len(df_hist) >= 2:
                prev_c = float(df_hist["Close"].iloc[-2])
            else:
                prev_c = float(df_hist["Close"].iloc[-1])
        elif len(df_hist) >= 2:
            prev_c = float(df_hist["Close"].iloc[-2])
        if prev_c > 0:
            chg = xau_spot - prev_c
            xau_rq["change"] = round(chg, 2)
            xau_rq["pct_change"] = round((chg / prev_c) * 100.0, 2)
            xau_rq["prev_close"] = round(prev_c, 2)


def fetch_finnhub_quotes_batch(tickers, api_key):
    """限额并发抓取 (max_workers=2)，由 FinnhubRateLimiter 严控在 50 次/分安全红线内"""
    if not api_key:
        return {}
    results = {}
    print(f"[*] 🚀 启动 Finnhub 稳态频控引擎 (workers=2, bucket=50/min, min_interval=1.05s)，平稳抓取 {len(tickers)} 只标的实时报价...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_to_sym = {executor.submit(fetch_single_finnhub_quote, t, api_key): t for t in tickers}
        for future in as_completed(future_to_sym):
            sym, q = future.result()
            if q:
                results[sym] = q
    if results:
        print(f"[+] ✅ Finnhub 实时行情获取成功: {len(results)}/{len(tickers)} 只标的已实时穿透！\n")
    else:
        print(f"[!] Finnhub 实时数据未返回，将启用全量 yfinance 底模兜底。\n")
    return results

def now_et_str(fmt="%Y-%m-%d %H:%M:%S"):
    """统一使用美东时间显示/日志/推送。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime(fmt)
    except Exception:
        return datetime.now(timezone(timedelta(hours=-5))).strftime(fmt)


def get_us_market_session():
    """
    判断当前美东时间 (America/New_York) 交易时段 (精准对齐美股 24/5 夜盘日历):
    - 周末休市 (WEEKEND): 周五 20:00 ET 盘后结束 ~ 周日 20:00 ET 夜盘开启前
      (即 weekday == 4 and hour >= 20.0, 或 weekday == 5 全天, 或 weekday == 6 and hour < 20.0)
    - 美股夜盘 (OVERNIGHT, Blue Ocean ATS 20:00 ET ~ 次日 04:00 ET):
      * 周日 20:00 ~ 24:00 ET (对应亚洲周一早盘: 夏令时北京时间 08:00/首尔 09:00 起，冬令时北京时间 09:00/首尔 10:00 起)
      * 周一至周四 20:00 ~ 24:00 ET 及 周一至周五 00:00 ~ 04:00 ET
    - 盘前交易 (PRE_MARKET): 周一至周五 04:00 ~ 09:30 ET
    - 常规盘中 (REGULAR): 周一至周五 09:30 ~ 16:00 ET
    - 盘后交易 (POST_MARKET): 周一至周五 16:00 ~ 20:00 ET
    """
    try:
        import zoneinfo
        ny_tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        from datetime import timezone, timedelta
        ny_tz = timezone(timedelta(hours=-5))
    now_ny = datetime.now(ny_tz)
    weekday = now_ny.weekday()  # 0=Mon ... 4=Fri, 5=Sat, 6=Sun
    hour = now_ny.hour + now_ny.minute / 60.0

    if weekday == 5 or (weekday == 4 and hour >= 20.0) or (weekday == 6 and hour < 20.0):
        return "WEEKEND", "☕ 周末休市"
    elif weekday == 6 and hour >= 20.0:
        return "OVERNIGHT", "🌌 美股夜盘"
    elif 9.5 <= hour < 16.0:
        return "REGULAR", "🟢 常规盘中"
    elif 4.0 <= hour < 9.5:
        return "PRE_MARKET", "🌅 盘前交易"
    elif 16.0 <= hour < 20.0:
        return "POST_MARKET", "🌙 盘后交易"
    else:
        return "OVERNIGHT", "🌌 美股夜盘"

def fetch_intraday_5m_and_ext_data(tickers):
    """
    批量抓取全量 55 只标的近 2 日 5 分钟级 K 线 (含盘前/盘后 prepost=True，总耗时约 2 秒)
    精准计算：
    1. 盘前/盘后最新成交价 (ext_price)
    2. 当日正股成交量加权均价 (VWAP，自动过滤盘前盘后 0 成交量暗池毛刺)
    3. 5 分钟级 RSI(14) 及其近 30 分钟极值 (min_rsi_5m_30m)
    4. 5 分钟 K 线止跌企稳形态 (收阳 is_5m_green、不创新低 is_5m_higher_low)
    """
    results = {}
    try:
        try:
            import zoneinfo
            ny_tz = zoneinfo.ZoneInfo("America/New_York")
        except Exception:
            from datetime import timezone as _fb_tz, timedelta as _fb_td
            ny_tz = _fb_tz(_fb_td(hours=-4))

        _proxy_map = {"XAUUSD": "GC=F"}
        _dl_tickers = list(dict.fromkeys([_proxy_map.get(t, t) for t in tickers]))
        df = yf.download(_dl_tickers, period="2d", interval="5m", prepost=True, progress=False)
        if df is None or df.empty or "Close" not in df:
            return results

        _is_multi = isinstance(df.columns, pd.MultiIndex)
        for t in tickers:
            try:
                src_t = _proxy_map.get(t, t)
                if _is_multi:
                    if src_t not in df["Close"]:
                        continue
                    c = df["Close"][src_t].dropna()
                    if len(c) < 5:
                        continue
                    o = df["Open"][src_t].reindex(c.index).fillna(c)
                    h = df["High"][src_t].reindex(c.index).fillna(c)
                    l = df["Low"][src_t].reindex(c.index).fillna(c)
                    v = df["Volume"][src_t].reindex(c.index).fillna(0)
                else:
                    if len(_dl_tickers) != 1 or src_t != _dl_tickers[0]:
                        continue
                    c = df["Close"].dropna()
                    if len(c) < 5:
                        continue
                    o = df["Open"].reindex(c.index).fillna(c)
                    h = df["High"].reindex(c.index).fillna(c)
                    l = df["Low"].reindex(c.index).fillna(c)
                    v = df["Volume"].reindex(c.index).fillna(0)

                # 先按美东当前时段选取真实扩展 bar；盘前绝不复用昨晚盘后/昨日收盘 bar
                ny_idx = c.index.tz_convert(ny_tz)
                ny_hr = ny_idx.hour + ny_idx.minute / 60.0
                last_date = ny_idx[-1].date()
                _now_et = datetime.now(ny_tz)
                _today = _now_et.date()
                _d = _now_et.hour * 60 + _now_et.minute
                _ext_mask = None
                _price_kind = "regular"
                if 4 * 60 <= _d < 9 * 60 + 30:
                    _ext_mask = (ny_idx.date == _today) & (ny_hr >= 4.0) & (ny_hr < 9.5)
                    _price_kind = "premarket"
                elif 16 * 60 <= _d < 20 * 60:
                    _ext_mask = (ny_idx.date == _today) & (ny_hr >= 16.0) & (ny_hr < 20.0)
                    _price_kind = "postmarket"
                elif _d >= 20 * 60 or _d < 4 * 60:
                    _ext_mask = (ny_idx.date == _today) & (ny_hr >= 20.0) if _d >= 20 * 60 else (ny_idx.date == _today) & (ny_hr < 4.0)
                    _price_kind = "overnight"
                    if not bool(np.any(_ext_mask)):
                        _ext_mask = (ny_idx.date == last_date) & (ny_hr >= 16.0) & (ny_hr < 20.0)
                        _price_kind = "postmarket"
                ext_price = 0.0
                ext_time = ""
                if _ext_mask is not None and bool(np.any(_ext_mask)):
                    _ext_c = c[_ext_mask]
                    ext_price = float(_ext_c.iloc[-1])
                    ext_time = str(_ext_c.index[-1])
                # 正股收盘 bar 仍按今日 RTH 计算

                # 过滤出最新交易日正股常规时段 (09:30 ~ 16:00 EDT) 用于纯净 VWAP 与日高低点计算
                rth_mask = (ny_idx.date == last_date) & (ny_hr >= 9.5) & (ny_hr < 16.0)
                if not rth_mask.any():
                    # 若当前处于盘前 (当日尚未开盘)，则回溯前一个正股交易日计算基准 VWAP
                    unique_dates = sorted(list(set(ny_idx.date)))
                    prev_date = unique_dates[-2] if len(unique_dates) >= 2 else last_date
                    rth_mask = (ny_idx.date == prev_date) & (ny_hr >= 9.5) & (ny_hr < 16.0)

                pmh_val = 0.0
                pml_val = 0.0
                pm_target_date = _today if (4 * 60 <= _d < 9 * 60 + 30) else last_date
                pm_mask = (ny_idx.date == pm_target_date) & (ny_hr >= 4.0) & (ny_hr < 9.5)
                if pm_mask.any():
                    pmh_val = float(h[pm_mask].max())
                    pml_val = float(l[pm_mask].min())

                if rth_mask.any():
                    tp_rth = (h[rth_mask] + l[rth_mask] + c[rth_mask]) / 3.0
                    vol_rth = v[rth_mask]
                    vwap = float((tp_rth * vol_rth).sum() / vol_rth.sum()) if vol_rth.sum() > 0 else float(c[rth_mask].mean())
                    rth_low = float(l[rth_mask].min())
                    rth_high = float(h[rth_mask].max())
                    rth_open_val = float(o[rth_mask].iloc[0])
                    rth_close = float(c[rth_mask].iloc[-1])
                    rth_date = str(ny_idx[rth_mask][-1].date())
                    if pmh_val <= 0:
                        _rth_dt_obj = ny_idx[rth_mask][-1].date()
                        _pm_day_mask = (ny_idx.date == _rth_dt_obj) & (ny_hr >= 4.0) & (ny_hr < 9.5)
                        if _pm_day_mask.any():
                            pmh_val = float(h[_pm_day_mask].max())
                            pml_val = float(l[_pm_day_mask].min())
                    rth_5m_series = []
                    for _dt_r, _px_r in zip(ny_idx[rth_mask], c[rth_mask]):
                        _s_idx = (int(_dt_r.hour) * 60 + int(_dt_r.minute) - 570) // 5
                        if 0 <= _s_idx < 78 and math.isfinite(float(_px_r)) and float(_px_r) > 0:
                            rth_5m_series.append([_s_idx, round(float(_px_r), 2)])
                    # 剔除 15:55 收盘集合竞价(MOC)脉冲毛刺，计算真实 5m 相对量比
                    rth_no_moc = (ny_idx.date == (ny_idx[rth_mask][-1].date())) & (ny_hr >= 9.5) & (ny_hr < 15.9)
                    vol_clean = v[rth_no_moc] if rth_no_moc.sum() >= 5 else vol_rth
                    if len(vol_clean) >= 4:
                        recent_5m_v = float(vol_clean.iloc[-2:].mean())
                        base_5m_v = float(vol_clean.iloc[max(0, len(vol_clean)-14):-2].mean()) if len(vol_clean) >= 6 else float(vol_clean.mean())
                        rvol_5m = (recent_5m_v / base_5m_v) if base_5m_v > 0 else 1.0
                    else:
                        rvol_5m = 1.0
                else:
                    vwap = float(c.iloc[-20:].mean())
                    rth_low = float(l.iloc[-20:].min())
                    rth_high = float(h.iloc[-20:].max())
                    rth_open_val = float(o.iloc[-1])
                    rth_close = float(c.iloc[-1])
                    rth_date = str(last_date)
                    rth_5m_series = []
                    rvol_5m = 1.0

                # 计算 5 分钟级别 Wilder's RSI(14)
                delta = c.diff()
                gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
                loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
                rs = gain / loss.replace(0, np.nan)
                rsi_5m_s = (100 - (100 / (1 + rs))).fillna(50.0)

                rsi_5m = float(rsi_5m_s.iloc[-1])
                prev_rsi_5m = float(rsi_5m_s.iloc[-2]) if len(rsi_5m_s) >= 2 else rsi_5m
                min_rsi_5m_30m = float(rsi_5m_s.iloc[-6:].min())

                is_5m_green = bool(float(c.iloc[-1]) >= float(o.iloc[-1]))
                is_5m_higher_low = bool(float(l.iloc[-1]) >= float(l.iloc[-2]) * 0.999) if len(l) >= 2 else True

                continuous_24h_payload = None
                vwap_final = vwap
                if t in ("BTC-USD", "XAUUSD"):
                    try:
                        utc_idx = (
                            c.index.tz_convert("UTC")
                            if getattr(c.index, "tz", None) is not None
                            else pd.to_datetime(c.index).tz_localize("UTC")
                        )
                        df_5m = pd.DataFrame(
                            {
                                "Open": pd.to_numeric(o, errors="coerce"),
                                "High": pd.to_numeric(h, errors="coerce"),
                                "Low": pd.to_numeric(l, errors="coerce"),
                                "Close": pd.to_numeric(c, errors="coerce"),
                                "Volume": pd.to_numeric(v, errors="coerce").fillna(0.0),
                            },
                            index=utc_idx,
                        ).dropna(subset=["Close"]).sort_index()
                        df_5m = df_5m[df_5m["Close"] > 0]
                        if len(df_5m) >= 2:
                            last_ts = df_5m.index[-1]
                            win = df_5m[df_5m.index >= (last_ts - pd.Timedelta(hours=24))]
                            if len(win) < 2:
                                win = df_5m
                            high_24h = float(win["High"].max())
                            low_24h = float(win["Low"].min())
                            if len(win) > 96:
                                bars = (
                                    win.resample("15min")
                                    .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
                                    .dropna(subset=["Close"])
                                )
                                interval_lbl = "15m"
                                if len(bars) < 2:
                                    bars = win.copy()
                                    interval_lbl = "5m"
                            else:
                                bars = win.copy()
                                interval_lbl = "5m"

                            last_date_utc = last_ts.date()
                            same_day_df = df_5m[df_5m.index.date == last_date_utc]
                            utc_open = float(same_day_df["Open"].iloc[0]) if not same_day_df.empty else float(bars["Open"].iloc[0])

                            same_utc_idxs = [idx_b for idx_b, ts_b in enumerate(bars.index) if ts_b.date() == last_date_utc]
                            utc00_idx = None
                            if same_utc_idxs:
                                cand_i = same_utc_idxs[0]
                                ts0 = bars.index[cand_i]
                                if cand_i > 0 or (ts0.hour == 0 and ts0.minute < 30):
                                    utc00_idx = int(cand_i)

                            actual_24h: list = []
                            vwap_24h: list = []
                            cum_pv = 0.0
                            cum_vol = 0.0
                            tp_sum = 0.0
                            tp_cnt = 0
                            for _, row_b in bars.iterrows():
                                cl_b = float(row_b["Close"])
                                hi_b = float(row_b["High"]) if math.isfinite(float(row_b["High"])) else cl_b
                                lo_b = float(row_b["Low"]) if math.isfinite(float(row_b["Low"])) else cl_b
                                vo_b = max(0.0, float(row_b["Volume"])) if math.isfinite(float(row_b["Volume"])) else 0.0
                                tp_b = (hi_b + lo_b + cl_b) / 3.0
                                tp_sum += tp_b
                                tp_cnt += 1
                                if vo_b > 0:
                                    cum_pv += tp_b * vo_b
                                    cum_vol += vo_b
                                vw_b = (cum_pv / cum_vol) if cum_vol > 0 else (tp_sum / max(1, tp_cnt))
                                actual_24h.append(round(cl_b, 2))
                                vwap_24h.append(round(vw_b, 2))

                            if vwap_24h:
                                vwap_final = float(vwap_24h[-1])
                            is_wknd_closed = bool(
                                t == "XAUUSD"
                                and (datetime.now(timezone.utc) - last_ts.to_pydatetime()) > timedelta(hours=3)
                            )
                            continuous_24h_payload = {
                                "time_labels": [ts_b.strftime("%H:%M") for ts_b in bars.index],
                                "time_labels_full": [ts_b.strftime("%m-%d %H:%M UTC") for ts_b in bars.index],
                                "actual_prices": actual_24h,
                                "vwap_prices": vwap_24h,
                                "utc_open": round(utc_open, 2),
                                "utc00_idx": utc00_idx,
                                "high_24h": round(high_24h, 2),
                                "low_24h": round(low_24h, 2),
                                "last_bar_utc": last_ts.strftime("%Y-%m-%d %H:%M UTC"),
                                "window_start_utc": bars.index[0].strftime("%Y-%m-%d %H:%M UTC"),
                                "interval_label": interval_lbl,
                                "is_weekend_closed": is_wknd_closed,
                            }
                    except Exception:
                        continuous_24h_payload = None

                results[t] = {
                    "has_5m_intraday": bool(len(c) >= 2),
                    "ext_price": ext_price,
                    "ext_time": ext_time,
                    "price_kind": _price_kind,
                    "vwap": round(vwap_final, 2),
                    "rsi_5m": round(rsi_5m, 1) if len(c) >= 2 else None,
                    "prev_rsi_5m": round(prev_rsi_5m, 1) if len(c) >= 2 else None,
                    "min_rsi_5m_30m": round(min_rsi_5m_30m, 1) if len(c) >= 2 else None,
                    "rvol_5m": round(rvol_5m, 2) if len(c) >= 2 else None,
                    "is_5m_green": is_5m_green,
                    "is_5m_higher_low": is_5m_higher_low,
                    "low_5m": round(float(l.iloc[-1]), 4) if len(l) else round(float(c.iloc[-1]), 4),
                    "rth_low": round(rth_low, 2),
                    "rth_high": round(rth_high, 2),
                    "rth_open": round(rth_open_val, 4),
                    "rth_close": round(rth_close, 4),
                    "rth_date": rth_date,
                    "pmh": round(pmh_val, 2) if pmh_val > 0 else 0.0,
                    "pml": round(pml_val, 2) if pml_val > 0 else 0.0,
                    "rth_5m_series": rth_5m_series,
                }
                if continuous_24h_payload is not None:
                    results[t]["continuous_24h"] = continuous_24h_payload
            except Exception:
                continue
    except Exception as e:
        print(f"[!] 5分钟日内与盘前盘后数据计算回退: {e}")
    return results


def fetch_yahoo_overnight_quotes(tickers):
    """夜盘只认 Yahoo overnightMarketPrice (需显式携带 overnightPrice=true 参数)。没有这个字段就返回空，不许用盘后价顶上。"""
    tickers = [t for t in (tickers or []) if t not in ("BTC-USD", "XAUUSD")]
    if not tickers:
        return {}
    rows = []
    try:
        d = yf.data.YfData()
        resp = d.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": ",".join(tickers), "overnightPrice": "true"},
            timeout=15,
        )
        rows = resp.json().get("quoteResponse", {}).get("result", [])
    except Exception:
        import urllib.parse
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            try:
                opener.open(urllib.request.Request("https://fc.yahoo.com", headers=headers), timeout=8).read()
            except Exception:
                pass
            crumb = opener.open(
                urllib.request.Request("https://query1.finance.yahoo.com/v1/test/getcrumb", headers=headers),
                timeout=8,
            ).read().decode().strip()
            url = (
                "https://query1.finance.yahoo.com/v7/finance/quote?symbols="
                + ",".join(tickers)
                + "&overnightPrice=true&crumb="
                + urllib.parse.quote(crumb)
            )
            raw = opener.open(urllib.request.Request(url, headers=headers), timeout=20).read()
            rows = json.loads(raw.decode()).get("quoteResponse", {}).get("result", [])
        except Exception as exc:
            print(f"[!] 夜盘报价不可用，回退正规盘收盘: {exc}")
            return {}
    out = {}
    for row in rows:
        sym = row.get("symbol")
        px = row.get("overnightMarketPrice")
        if not sym or px is None:
            continue
        try:
            px = float(px)
        except Exception:
            continue
        if px <= 0:
            continue
        when = ""
        stamp = row.get("overnightMarketTime")
        if stamp:
            try:
                when = datetime.fromtimestamp(int(stamp), tz=timezone.utc).isoformat()
            except Exception:
                when = ""
        out[sym] = {"price": px, "time": when}
    return out


EARNINGS_CACHE_FILE = os.path.join(SCRATCH_DIR, ".earnings_cache.json")
OPTIONS_CACHE_FILE = os.path.join(SCRATCH_DIR, ".options_cache.json")

def fetch_earnings_calendar_map(tickers, api_key):
    """
    获取未来 45 天个股财报日历 (12小时本地缓存，零延迟防刷)：
    用于识别【财报盲盒避雷区】(距财报 <= 5天自动提示减半或暂缓建仓)
    """
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    raw_map = {}

    # 1. 尝试读取 12 小时内的本地缓存
    try:
        if os.path.exists(EARNINGS_CACHE_FILE):
            with open(EARNINGS_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if time.time() - cached.get("updated_at", 0) < 43200 and cached.get("base_date") == today_str:
                    raw_map = cached.get("data", {})
    except Exception:
        pass

    # 2. 若缓存过期或不存在，则按 4 天分片并发拉取未来 44 天 Finnhub 财报日历
    if not raw_map and api_key:
        try:
            from datetime import timedelta
            ticker_set = set(tickers)
            # 从 -1 天开始抓取分片，完美覆盖跨时区（美东昨天/刚发完24h内）的财报
            chunks = [(today + timedelta(days=i), today + timedelta(days=i+3)) for i in range(-1, 44, 4)]

            def _fetch_chunk(d_pair):
                d_from, d_to = d_pair
                url = f"https://finnhub.io/api/v1/calendar/earnings?from={d_from}&to={d_to}&token={api_key}"
                try:
                    FINNHUB_LIMITER.acquire()
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        cal = json.loads(resp.read().decode()).get("earningsCalendar", [])
                        return [(x.get("symbol"), x.get("date"), x.get("hour", "")) for x in cal if x.get("symbol") in ticker_set and x.get("date")]
                except Exception:
                    return []

            with ThreadPoolExecutor(max_workers=2) as ex:
                for res_list in ex.map(_fetch_chunk, chunks):
                    for sym, d_str, hr in res_list:
                        if sym not in raw_map or d_str < raw_map[sym]["date"]:
                            raw_map[sym] = {"date": d_str, "hour": hr}

            with open(EARNINGS_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"updated_at": time.time(), "base_date": today_str, "data": raw_map}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[!] 财报日历同步异常: {e}")

    # 3. 转换为距今天数与实战避雷标签
    result = {}
    for t in tickers:
        info = raw_map.get(t)
        if not info or not info.get("date"):
            result[t] = {"earnings_date": "", "days_to_earnings": None, "earnings_tag": "", "is_earnings_risk": False}
            continue
        try:
            e_date = datetime.strptime(info["date"], "%Y-%m-%d").date()
            days = (e_date - today).days
            # 扩展到 -1 天容差，解决跨时区导致的当天盘后财报在亚洲次日清晨被当作过期丢弃的致命Bug
            if days < -1:
                result[t] = {"earnings_date": "", "days_to_earnings": None, "earnings_tag": "", "is_earnings_risk": False}
                continue
            hr_code = info.get("hour", "")
            hr_str = "盘前" if hr_code == "bmo" else ("盘后" if hr_code == "amc" else "")
            short_d = info["date"][5:].replace("-", "/")
            if days in [0, -1]:
                tag = f"🔴 财报大考/刚披露 ({short_d}{hr_str})"
                is_risk = True
            elif 1 <= days <= 5:
                tag = f"⚠️ {days}天后{hr_str}财报({short_d})"
                is_risk = True
            else:
                tag = f"📅 {short_d}{hr_str}({days}天)"
                is_risk = False
            result[t] = {
                "earnings_date": info["date"],
                "days_to_earnings": days,
                "earnings_tag": tag,
                "is_earnings_risk": is_risk
            }
        except Exception:
            result[t] = {"earnings_date": "", "days_to_earnings": None, "earnings_tag": "", "is_earnings_risk": False}
    return result

def filter_directional_options_band(df_opt, opt_type, spot):
    """过滤深度实值 (Deep ITM) 内在价值污染，统一 dollar_pcr 与主力墙筛选口径 (Call >= 0.975*spot, Put <= 1.025*spot)。"""
    if df_opt is None or df_opt.empty or not spot or float(spot) <= 0:
        return df_opt
    sp = float(spot)
    if opt_type == "Call":
        return df_opt[(df_opt["strike"] >= sp * 0.975) & (df_opt["strike"] <= sp * 1.40)]
    return df_opt[(df_opt["strike"] >= sp * 0.65) & (df_opt["strike"] <= sp * 1.025)]


def compute_directional_dollar_pcr(calls, puts, spot, fallback_pcr=1.0):
    """基于收紧后的平/虚值方向性合约带计算真实资金沽购比 (dollar_pcr)。"""
    if calls is not None and not calls.empty:
        dir_calls = filter_directional_options_band(calls, "Call", spot) if (spot and spot > 0) else calls
        c_dollar = float(dir_calls["dollar_val"].sum()) if (dir_calls is not None and not dir_calls.empty) else float(calls["dollar_val"].sum())
    else:
        c_dollar = 0.0
    if puts is not None and not puts.empty:
        dir_puts = filter_directional_options_band(puts, "Put", spot) if (spot and spot > 0) else puts
        p_dollar = float(dir_puts["dollar_val"].sum()) if (dir_puts is not None and not dir_puts.empty) else float(puts["dollar_val"].sum())
    else:
        p_dollar = 0.0
    dollar_pcr = round(p_dollar / c_dollar, 2) if c_dollar > 0 else fallback_pcr
    return dollar_pcr, c_dollar, p_dollar


def fetch_options_anomaly_map(focus_tickers, spot_map=None):
    """
    获取全量标的机构期权多空异动、资金加权 PCR 与 ATM 预期波动幅度 (30分钟本地缓存，零消耗 Finnhub API 额度)：
    1. 智能优选本周五主周期合约 (自动过滤已收盘 0DTE 过期合约与非主力周中短约)
    2. 按真实权利金成交总额 (Dollar Premium = Volume * Price * 100) 排序，剔除 <$0.05 末日彩票单
    3. 综合计算张数 PCR 与资金加权 Dollar PCR，并对反向 ETF (SQQQ/SOXS/VIXY) 做语义纠偏
    4. 基于平值跨式期权 (ATM Straddle) 测算华尔街定价的本周预期波动上下轨 (Expected Move ±%) 与隐含波动率 IV
    """
    now_ts = time.time()
    spot_map = spot_map or {}
    cached_map = {}
    try:
        if os.path.exists(OPTIONS_CACHE_FILE):
            with open(OPTIONS_CACHE_FILE, "r", encoding="utf-8") as f:
                c_data = json.load(f)
                if c_data.get("schema_version") == 4 and (now_ts - c_data.get("updated_at", 0) < 1800):
                    cached_map = c_data.get("data", {})
    except Exception:
        pass

    needed = [t for t in focus_tickers if t not in ("BTC-USD", "XAUUSD") and t not in cached_map]
    if needed:
        print(f"[*] ⚡ 正在同步 {len(needed)} 只标的【机构期权主力资金流、PCR & 预期波动区间】(免额度·6线程并发)...")
        try:
            ny_now = datetime.now(ZoneInfo("America/New_York"))
            ny_today = ny_now.date()
            ny_hr = ny_now.hour + ny_now.minute / 60.0
        except Exception:
            ny_today = datetime.now().date()
            ny_hr = 16.5

        def _fetch_single_option(t):
            try:
                tk = yf.Ticker(t)
                exps = tk.options
                if not exps:
                    return t, {"has_options": False}

                # 1. 智能优选主力到期日：跳过已收盘 0DTE，优先锁定 2~11 天内的周五主周期 (Weekly Main Board)
                valid_exps = []
                friday_exps = []
                for e_str in exps[:6]:
                    try:
                        ed = datetime.strptime(e_str, "%Y-%m-%d").date()
                        days_out = (ed - ny_today).days
                        if days_out < 0 or (days_out == 0 and ny_hr >= 15.8):
                            continue
                        valid_exps.append((days_out, e_str))
                        if 2 <= days_out <= 11 and ed.weekday() == 4:
                            friday_exps.append((days_out, e_str))
                    except Exception:
                        continue

                if friday_exps:
                    exp = friday_exps[0][1]
                elif valid_exps:
                    ge2 = [x[1] for x in valid_exps if x[0] >= 2]
                    exp = ge2[0] if ge2 else valid_exps[0][1]
                else:
                    exp = exps[0]

                chain = tk.option_chain(exp)
                calls = chain.calls.copy() if chain.calls is not None else pd.DataFrame()
                puts = chain.puts.copy() if chain.puts is not None else pd.DataFrame()
                if calls.empty and puts.empty:
                    return t, {"has_options": False}

                for df_opt in (calls, puts):
                    if not df_opt.empty:
                        df_opt["volume"] = df_opt["volume"].fillna(0.0)
                        df_opt["openInterest"] = df_opt["openInterest"].fillna(0.0)
                        df_opt["lastPrice"] = df_opt["lastPrice"].fillna(0.0)
                        bid = df_opt["bid"].fillna(0.0) if "bid" in df_opt else 0.0
                        ask = df_opt["ask"].fillna(0.0) if "ask" in df_opt else 0.0
                        mid = (bid + ask) * 0.5
                        df_opt["eff_price"] = np.where(df_opt["lastPrice"] > 0, df_opt["lastPrice"], np.where(mid > 0, mid, 0.0))
                        df_opt["dollar_val"] = df_opt["volume"] * df_opt["eff_price"] * 100.0

                spot = float(spot_map.get(t, 0.0) or 0.0)
                if spot <= 0 and not calls.empty:
                    active_c = calls[calls["volume"] > 0]
                    spot = float(active_c["strike"].median()) if not active_c.empty else float(calls["strike"].median())

                c_vol = float(calls["volume"].sum()) if not calls.empty else 0.0
                p_vol = float(puts["volume"].sum()) if not puts.empty else 0.0
                if c_vol <= 0 and p_vol <= 0:
                    return t, {"has_options": False}

                pcr = round(p_vol / c_vol, 2) if c_vol > 0 else 1.0
                dollar_pcr, c_dollar, p_dollar = compute_directional_dollar_pcr(calls, puts, spot, fallback_pcr=pcr)
                eff_pcr = round(0.45 * pcr + 0.55 * dollar_pcr, 2)

                is_inverse_etf = t in ["SQQQ", "SOXS", "VIXY"]
                if is_inverse_etf:
                    if eff_pcr <= 0.75:
                        pcr_sentiment = "🛡️ 买Call对冲(看跌大盘)"
                        pcr_color = "#ffa726"
                    elif eff_pcr >= 1.25:
                        pcr_sentiment = "🟢 买Put看空恐慌(利好大盘)"
                        pcr_color = "#4ade80"
                    else:
                        pcr_sentiment = "🟡 对冲博弈均衡"
                        pcr_color = "#ffd700"
                else:
                    # 统一口径: PCR<0.85 偏多 / 0.85~1.15 均衡 / >1.15 偏空
                    if eff_pcr < 0.85:
                        pcr_sentiment = "🟢 资金偏多看涨"
                        pcr_color = "#4ade80"
                    elif eff_pcr <= 1.15:
                        pcr_sentiment = "⚪ 资金多空均衡"
                        pcr_color = "#9ca3af"
                    else:
                        pcr_sentiment = "🔴 资金偏空对冲"
                        pcr_color = "#ff5252"

                # 2. 按真实方向性权利金成交额 (Directional Dollar Premium) 筛选主力大单：
                #    - 过滤 <$0.05 末日彩票废约
                #    - 过滤深度实值 (Deep ITM) 机构换仓合成头寸，与 dollar_pcr 严格同口径 (Call >= 0.975*spot, Put <= 1.025*spot)
                def _pick_top_flow(df_opt, opt_type):
                    if df_opt.empty:
                        return "--", 0.0, 0.0
                    sub = df_opt[df_opt["volume"] >= 10].copy()
                    if sub.empty:
                        sub = df_opt[df_opt["volume"] > 0].copy()
                    if sub.empty:
                        return "--", 0.0, 0.0
                    non_penny = sub[sub["eff_price"] >= 0.05]
                    if not non_penny.empty:
                        sub = non_penny.copy()
                    if spot > 0:
                        otm_band = filter_directional_options_band(sub, opt_type, spot)
                        if otm_band is not None and not otm_band.empty:
                            sub = otm_band.copy()
                        else:
                            near_spot = sub[(sub["strike"] >= spot * 0.70) & (sub["strike"] <= spot * 1.40)]
                            if not near_spot.empty:
                                sub = near_spot.copy()
                    # 若当日成交量 > 未平仓量 1.2 倍 (新开仓异动)，赋予 1.35x 权重加成，按主力资金量排序
                    unusual_boost = np.where((sub["openInterest"] > 0) & (sub["volume"] > sub["openInterest"] * 1.2), 1.35, 1.0)
                    sub["rank_score"] = sub["dollar_val"] * unusual_boost
                    sub = sub.sort_values("rank_score", ascending=False)
                    top_r = sub.iloc[0]
                    d_val = float(top_r["dollar_val"])
                    vol_i = int(top_r["volume"])
                    strike_v = float(top_r["strike"])
                    if d_val >= 1000000:
                        v_str = f"${d_val/1000000:.2f}M"
                    elif d_val >= 1000:
                        v_str = f"${d_val/1000:.0f}k"
                    else:
                        v_str = f"${d_val:.0f}"
                    info_str = f"${strike_v:g} {opt_type} ({vol_i:,}张 · {v_str})"
                    return info_str, round(d_val, 1), strike_v

                top_call_info, top_call_val, call_wall = _pick_top_flow(calls, "Call")
                top_put_info, top_put_val, put_wall = _pick_top_flow(puts, "Put")

                # 3. 测算平值跨式期权 (ATM Straddle) 隐含波动率 IV 与本周预期波动上下轨 (Expected Move)
                iv_atm = 0.0
                exp_move_pct = 0.0
                exp_low = 0.0
                exp_high = 0.0
                exp_move_str = "--"
                if spot > 0 and not calls.empty and not puts.empty:
                    calls["dist_spot"] = (calls["strike"] - spot).abs()
                    puts["dist_spot"] = (puts["strike"] - spot).abs()
                    atm_c = calls.sort_values("dist_spot").iloc[0]
                    atm_p = puts.sort_values("dist_spot").iloc[0]
                    straddle = float(atm_c["eff_price"]) + float(atm_p["eff_price"])
                    if straddle > 0:
                        exp_move_pct = round(min(35.0, max(0.8, (straddle * 0.85 / spot) * 100.0)), 1)
                        exp_low = round(spot * (1.0 - exp_move_pct / 100.0), 2)
                        exp_high = round(spot * (1.0 + exp_move_pct / 100.0), 2)
                        exp_move_str = f"±{exp_move_pct:.1f}% (${exp_low:.2f}~${exp_high:.2f})"
                    c_iv = float(atm_c.get("impliedVolatility", 0.0) or 0.0)
                    p_iv = float(atm_p.get("impliedVolatility", 0.0) or 0.0)
                    iv_list = [v for v in (c_iv, p_iv) if 0.05 < v < 4.5]
                    if iv_list:
                        iv_atm = round(float(np.mean(iv_list)) * 100.0, 1)
                    elif straddle > 0:
                        try:
                            d_to_exp = max(1.0, float((datetime.strptime(exp, "%Y-%m-%d").date() - ny_today).days))
                        except Exception:
                            d_to_exp = 3.0
                        bs_iv = (straddle / (0.798 * spot * np.sqrt(d_to_exp / 365.0))) * 100.0
                        iv_atm = round(min(280.0, max(8.0, float(bs_iv))), 1)

                return t, {
                    "has_options": True,
                    "options_exp": exp,
                    "call_vol": int(c_vol),
                    "put_vol": int(p_vol),
                    "call_dollar": round(c_dollar, 1),
                    "put_dollar": round(p_dollar, 1),
                    "pcr": pcr,
                    "dollar_pcr": dollar_pcr,
                    "pcr_sentiment": pcr_sentiment,
                    "pcr_color": pcr_color,
                    "top_call_info": top_call_info,
                    "top_call_val": top_call_val,
                    "call_wall": call_wall,
                    "top_put_info": top_put_info,
                    "top_put_val": top_put_val,
                    "put_wall": put_wall,
                    "iv_atm": iv_atm,
                    "exp_move_pct": exp_move_pct,
                    "exp_low": exp_low,
                    "exp_high": exp_high,
                    "exp_move_str": exp_move_str
                }
            except Exception:
                return t, {"has_options": False}

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(_fetch_single_option, t): t for t in needed}
            for f in as_completed(futures):
                sym, opt_data = f.result()
                if opt_data:
                    cached_map[sym] = opt_data

        try:
            with open(OPTIONS_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"schema_version": 4, "updated_at": now_ts, "data": cached_map}, f, ensure_ascii=False, indent=2)
            valid_opt_cnt = sum(1 for v in cached_map.values() if v.get("has_options"))
            print(f"[+] ⚡ 机构期权异动同步完成: 成功解析并缓存 {valid_opt_cnt}/{len(cached_map)} 只标的期权主力资金流。")
        except Exception as e:
            print(f"[!] 期权缓存写入异常: {e}")

    return cached_map


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
        tech_etfs = {"QQQ", "SQQQ", "SPY"}
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


_AI_LEVELS_CACHE = None
_AI_LEVELS_MTIME = 0.0


def load_ai_levels_map():
    """读取 AI 5Y 预测快照中的 S1/R1 与校准胜率，供破坑分级与防左侧误导使用（文件更新自动重载，失败时静默降级）。"""
    global _AI_LEVELS_CACHE, _AI_LEVELS_MTIME
    ai_path = os.path.join(SCRATCH_DIR, "data_cache", "latest_ai_predictions.json")
    try:
        cur_mtime = os.path.getmtime(ai_path) if os.path.exists(ai_path) else 0.0
    except Exception:
        cur_mtime = 0.0
    if _AI_LEVELS_CACHE is not None and cur_mtime == _AI_LEVELS_MTIME:
        return _AI_LEVELS_CACHE
    levels = {}
    try:
        if os.path.exists(ai_path):
            with open(ai_path, "r", encoding="utf-8") as f:
                ai_doc = json.load(f)
            for tk, item in (ai_doc.get("by_ticker") or {}).items():
                if isinstance(item, dict):
                    levels[str(tk)] = {
                        "s1": item.get("s1"),
                        "r1": item.get("r1"),
                        "cal_win_rate": item.get("calibrated_win_rate_pct"),
                    }
    except Exception:
        levels = {}
    _AI_LEVELS_CACHE = levels
    _AI_LEVELS_MTIME = cur_mtime
    return levels


_BOUNCE_STOP_BROAD_1X = frozenset({
    "QQQ", "QQQM", "SPY", "VOO", "IVV", "IWM", "DIA",
    "TLT", "GLD", "XAUUSD", "KO", "PEP", "MCD", "SCHD", "V", "AXP",
})
_BOUNCE_STOP_SECTOR_1X = frozenset({"SOXX", "SMH", "IGV", "XLK"})
_BOUNCE_STOP_LEVERED = frozenset({
    "SOXL", "SOXS", "TQQQ", "SQQQ", "KORU", "QLD", "SSO", "UPRO", "SPXU",
    "TNA", "TZA", "TECL", "TECS", "NVDL", "NVDX", "MSTU", "MSTZ", "TSLL", "CONL",
})
_BOUNCE_STOP_THICK = frozenset({
    "NVDA", "AMD", "AVGO", "TSM", "ASML", "AMAT", "MSFT", "AAPL",
    "GOOGL", "GOOG", "META", "AMZN", "ORCL", "NOW",
})


def _bounce_rebound_stop_band(ticker: str):
    """5 档博反抽止损：返回 (最小距离%, 最大距离%, 失效位下方缓冲垫%)。未点名的个股走薄盘口档。"""
    tk = str(ticker or "").upper().strip()
    if tk in _BOUNCE_STOP_BROAD_1X:
        return 0.8, 1.3, 0.15
    if tk in _BOUNCE_STOP_SECTOR_1X:
        return 1.2, 2.0, 0.20
    if tk in _BOUNCE_STOP_LEVERED:
        return 1.6, 2.6, 0.15
    if tk in _BOUNCE_STOP_THICK:
        return 1.6, 2.4, 0.25
    return 2.2, 3.2, 0.40


def _stop_below_invalidation(entry: float, invalidation: float, lo: float, hi: float, pad: float):
    """止损必须留在失效位下方。能落进 [lo, hi] 就落进去；落不进就保持在失效位下，不许上移穿过它。"""
    entry = float(entry) if entry and entry > 0 else float(invalidation)
    invalidation = float(invalidation)
    pad_stop = invalidation * (1.0 - max(pad, 0.05) / 100.0)
    if pad_stop >= invalidation:
        pad_stop = invalidation * 0.999
    tight = entry * (1.0 - lo / 100.0)
    wide = entry * (1.0 - hi / 100.0)
    stop_px = min(pad_stop, tight)
    inside = stop_px >= wide - 1e-9
    if not inside:
        stop_px = pad_stop
    if stop_px >= invalidation:
        stop_px = invalidation * (1.0 - max(pad, 0.05) / 100.0)
        inside = False
    if stop_px >= entry:
        stop_px = entry * (1.0 - lo / 100.0)
    return stop_px, inside


def _card_stop_under_structure(ticker, entry, vwap, low_5m):
    """卡片上的防守止损：有失效位就画在它下面，没有就贴该档最近的一边。不再使用 -6.8% 地板。"""
    lo, hi, pad = _bounce_rebound_stop_band(ticker)
    entry = float(entry)
    anchors = []
    if vwap and float(vwap) > 0 and float(vwap) < entry:
        anchors.append(float(vwap))
    if low_5m and float(low_5m) > 0 and float(low_5m) < entry:
        anchors.append(float(low_5m))
    if anchors:
        invalidation = max(anchors)
        stop_px, _inside = _stop_below_invalidation(entry, invalidation, lo, hi, pad)
    else:
        stop_px = entry * (1.0 - lo / 100.0)
    if stop_px >= entry:
        stop_px = entry * (1.0 - lo / 100.0)
    pct = round((stop_px - entry) / entry * 100.0, 1) if entry > 0 else -lo
    return round(stop_px, 2), pct


def _stop_from_reanchored_limit(ticker, limit, vwap, low_5m):
    """挂单价被拉回 VWAP 之后，止损只相对这个新挂单价往下算，不许停在挂单价上面。"""
    lo, hi, pad = _bounce_rebound_stop_band(ticker)
    limit = float(limit)
    under = []
    for raw in (vwap, low_5m):
        try:
            px = float(raw or 0.0)
        except Exception:
            px = 0.0
        if 0.0 < px < limit:
            under.append(px)
    if under:
        stop_px, _inside = _stop_below_invalidation(limit, max(under), lo, hi, pad)
    else:
        stop_px = limit * (1.0 - lo / 100.0)
    if stop_px >= limit:
        stop_px = limit * (1.0 - lo / 100.0)
    stop_px = round(float(stop_px), 2)
    if stop_px >= limit:
        stop_px = round(limit * (1.0 - lo / 100.0) - 0.01, 2)
    pct = round((stop_px - limit) / limit * 100.0, 1) if limit > 0 else -lo
    if pct >= 0:
        pct = round(-lo, 1)
        stop_px = round(limit * (1.0 + pct / 100.0), 2)
    return stop_px, pct


def evaluate_intraday_entry(
    active_price, fib_low, fib_high, ma50, is_pit, is_stop, dist_to_pit, intra_info,
    earnings_info=None, rs_20d=0.0, atr=0.0, daily_rsi=50.0, options_info=None,
    pct_change=0.0, ext_pct=None, rth_high=None, prev_crash_3d=False,
    prev_is_right_turn=False, ema5=0.0, prev_crash_body=0.0, prev_crash_close=0.0,
    session_code="REGULAR", ticker="", crash_3d=False, ai_s1=None, ai_r1=None,
    ai_cal_win_rate=None
):
    """
    真正的 5 分钟级日内量化建仓评估器 (V3.2 终极版)：
    1. ATR 双轨自适应暴跌熔断阈值 (-max(3.8, min(5.2, 2.0 * atr_pct)))
    2. 冲高跳水【断头铡刀】日内防接刀 (rth_high 跳水 <= -6.0% 且当日 <= -1.0%)
    3. 盘前盘后 +3% 强势反转死锁自动解除机制
    4. 隔夜失忆症防御：3 日暴跌冷却期冻结 (需满足右侧反转阳线或日内收复50%大阴线实体强反抽)
    5. V3.1 9:30 开盘执行指令与挂单上限价计算
    """
    intra_info = intra_info or {}
    if "has_5m_intraday" in intra_info:
        has_5m_intraday = bool(intra_info.get("has_5m_intraday")) and (intra_info.get("rsi_5m") is not None)
    else:
        has_5m_intraday = (intra_info.get("rsi_5m") is not None)
    vwap = intra_info.get("vwap", 0.0) or active_price
    rsi_5m = float(intra_info.get("rsi_5m")) if (has_5m_intraday and intra_info.get("rsi_5m") is not None) else 50.0
    prev_rsi_5m = float(intra_info.get("prev_rsi_5m")) if (has_5m_intraday and intra_info.get("prev_rsi_5m") is not None) else rsi_5m
    min_rsi_30m = float(intra_info.get("min_rsi_5m_30m")) if (has_5m_intraday and intra_info.get("min_rsi_5m_30m") is not None) else rsi_5m
    rvol_5m = float(intra_info.get("rvol_5m")) if (has_5m_intraday and intra_info.get("rvol_5m") is not None) else 1.0
    out_rsi_5m = round(rsi_5m, 1) if has_5m_intraday else None
    out_rvol_5m = round(rvol_5m, 2) if has_5m_intraday else None
    out_rs_20d = round(float(rs_20d), 2) if (rs_20d is not None and math.isfinite(float(rs_20d))) else None
    is_5m_green = intra_info.get("is_5m_green", True)
    is_higher_low = intra_info.get("is_5m_higher_low", True)
    rth_low = intra_info.get("rth_low", active_price)
    curr_rth_high = rth_high if (rth_high is not None and rth_high > 0) else intra_info.get("rth_high", active_price)
    rth_close = intra_info.get("rth_close", active_price) or active_price
    e_info = earnings_info or {}
    days_to_e = e_info.get("days_to_earnings")
    is_e_risk = e_info.get("is_earnings_risk", False)
    e_tag = e_info.get("earnings_tag", "")
    safe_ext = ext_pct if ext_pct is not None else 0.0

    # --------------------------------------------------------------------------
    # -1. 反向/波动率衰减型对冲工具专属分支 (SQQQ, SOXS, VIXY)
    #     绝不适用多头黄金坑埋伏，也不因大盘上涨引发的正常做空衰减触发多头暴跌熔断
    # --------------------------------------------------------------------------
    if ticker in ("SQQQ", "SOXS", "VIXY"):
        bias_ema5 = ((active_price - ema5) / ema5 * 100.0) if ema5 > 0 else 0.0
        hedge_stop = round(ema5 if (ema5 > 0 and ema5 < active_price) else active_price * 0.94, 2)
        hedge_stop_pct = round((hedge_stop - active_price) / active_price * 100.0, 1) if active_price > 0 else -6.0
        if pct_change >= 7.0 or bias_ema5 >= 6.0:
            h_status = "🟣 对冲恐慌极值 · 建议逢高分批收割"
            h_reason = f"反向衰减对冲工具({ticker})日内脉冲({pct_change:+.2f}%)，不设多头黄金坑埋伏单；持仓者建议逢高分批止盈收割，未持仓者严禁追高"
            h_note = "反向衰减对冲工具·逢高收割不挂抄底单"
        elif ema5 > 0 and active_price < ema5:
            h_status = "⚪ 对冲脉冲退潮 · 跌破EMA5观望"
            h_reason = f"反向衰减对冲工具({ticker})位于5日线(EMA5 ${ema5:.2f})下方，存在长期波动率损耗，严禁按多头黄金坑逻辑左侧挂单抄底"
            h_note = "反向衰减对冲工具·严禁黄金坑抄底"
        else:
            h_status = "🛡️ 反向对冲观察 · 仅限短线防御"
            h_reason = f"反向衰减对冲工具({ticker})具备长期波动率损耗，不参与60日斐波那契黄金坑埋伏，仅在大盘系统性破位时短线快进快出"
            h_note = "反向衰减对冲工具·不设黄金坑埋伏单"
        return {
            "optimal_limit": round(active_price, 2),
            "limit_note": h_note,
            "entry_stop_loss": hedge_stop,
            "stop_loss_pct": hedge_stop_pct,
            "intraday_entry_ready": False,
            "intraday_status_text": h_status,
            "intraday_reason": h_reason,
            "is_crash_halt": False,
            "is_cooling_halt": False,
            "is_inverse_hedge": True,
            "rth_low": round(rth_low, 2),
            "rth_high": round(curr_rth_high, 2),
            "vwap": round(vwap, 2),
            "has_5m_intraday": has_5m_intraday,
            "rsi_5m": out_rsi_5m,
            "rvol_5m": out_rvol_5m,
            "rs_20d": out_rs_20d,
            **e_info
        }

    # --------------------------------------------------------------------------
    # 0. 极端跳空/暴跌防手痒熔断闸判定 (V3.2 ATR 双轨自适应 + 盘前盘后反转解锁 + 断头铡刀跳水)
    # --------------------------------------------------------------------------
    atr_pct = (atr / active_price * 100.0) if (atr > 0 and active_price > 0) else 3.0
    crash_thresh = -max(3.8, min(5.2, 2.0 * atr_pct))

    # 基础暴跌判定
    is_base_crash = (pct_change <= crash_thresh)
    # 盘前盘后 +3% 强势反转死锁解除 (V3.2 R1.4)
    is_ext_reversal = bool(safe_ext >= 3.0 and (pct_change + safe_ext) > -2.5)
    if is_ext_reversal:
        is_base_crash = False

    # 冲高跳水断头铡刀防接刀 (V3.2 R1.2)
    intraday_flush_pct = ((active_price - curr_rth_high) / curr_rth_high * 100.0) if (curr_rth_high and curr_rth_high > 0) else 0.0
    is_flush_crash = (intraday_flush_pct <= -6.0 and pct_change <= -1.0)

    is_crash_halt = (
        is_base_crash or
        is_flush_crash or
        (ext_pct is not None and ext_pct <= -4.5) or
        ((pct_change + safe_ext) <= -5.5) or
        (fib_low > 0 and active_price < fib_low * 0.92 and ((pct_change <= -2.5 and not is_ext_reversal) or (ext_pct is not None and ext_pct <= -2.5)))
    )

    if is_crash_halt:
        crash_reason = f"日内冲高跳水 {intraday_flush_pct:+.2f}% (断头铡刀防接刀)" if is_flush_crash else f"标的触发ATR自适应暴跌熔断(日盘 {pct_change:+.2f}% ≤ {crash_thresh:.1f}%, 盘后 {safe_ext:+.2f}%)，严禁首日左侧挂单接飞刀(需等待3日宣泄企稳)"
        return {
            "optimal_limit": 0.0,
            "limit_note": "单日/盘后剧烈跳空破位，严禁首日左侧挂单接飞刀",
            "entry_stop_loss": 0.0,
            "stop_loss_pct": 0.0,
            "intraday_entry_ready": False,
            "intraday_status_text": "⛔ 暂停挂单 (暴跌宣泄期)",
            "intraday_reason": crash_reason,
            "is_crash_halt": True,
            "is_cooling_halt": False,
            "rth_low": round(rth_low, 2),
            "rth_high": round(curr_rth_high, 2),
            "vwap": round(vwap, 2),
            "has_5m_intraday": has_5m_intraday,
            "rsi_5m": out_rsi_5m,
            "rvol_5m": out_rvol_5m,
            "rs_20d": out_rs_20d,
            **e_info
        }

    # --------------------------------------------------------------------------
    # 0.5 隔夜失忆症与 3 日暴跌冷却期判定 (V3.2 R1.1，含当日跳空低开 <= -4.5% 后弱反抽拦截)
    # --------------------------------------------------------------------------
    if prev_crash_3d or crash_3d:
        # 严格企稳解锁边界：
        # 条件 a: 前一交易日收盘已确认右侧反转阳线 (仅当暴跌来自前1~3日且今日未发生新跳空暴跌时有效)
        unlock_a = bool(prev_is_right_turn) and bool(prev_crash_3d) and (pct_change > -3.8) and (ext_pct is None or ext_pct > -4.5)
        # 条件 b: 日内强劲反弹 >= +2.5%，收复前次暴跌大阴线实体 > 50%，且稳居 VWAP 和 EMA5 之上
        rebound_body_ok = (active_price >= prev_crash_close + 0.5 * prev_crash_body) if prev_crash_body > 0 else True
        unlock_b = (pct_change >= 2.5) and rebound_body_ok and (active_price > vwap) and (ema5 > 0 and active_price > ema5)

        if not (unlock_a or unlock_b):
            return {
                "optimal_limit": 0.0,
                "limit_note": "3日内曾大跌·等待日线企稳确认",
                "entry_stop_loss": 0.0,
                "stop_loss_pct": 0.0,
                "intraday_entry_ready": False,
                "intraday_status_text": "⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)",
                "intraday_reason": "标的处于3日暴跌冷却期中，日线未完成企稳确认(等待收出右侧止跌K线或日内强反抽>+2.5%且收复大阴线实体50%站稳VWAP与EMA5)",
                "is_crash_halt": False,
                "is_cooling_halt": True,
                "rth_low": round(rth_low, 2),
                "rth_high": round(curr_rth_high, 2),
                "vwap": round(vwap, 2),
                "has_5m_intraday": has_5m_intraday,
                "rsi_5m": out_rsi_5m,
                "rvol_5m": out_rvol_5m,
                "rs_20d": out_rs_20d,
                **e_info
            }

    # --------------------------------------------------------------------------
    # 1. 科学挂单买入价测算 (V3.1 T+1 开盘买入标准)
    # --------------------------------------------------------------------------
    is_rth_session = (session_code == "REGULAR")
    pit_mid_low = round(fib_low + 0.382 * (fib_high - fib_low), 2) if fib_high > fib_low else active_price
    if is_pit:
        optimal_limit = round(active_price * 1.005, 2) if is_rth_session else round(rth_close * 1.020, 2)
        limit_note = "V3.1右侧确认·9:30开盘限价上限(昨收+2.0%防追高,低于昨收*0.985作废)"
    else:
        if fib_high > fib_low and fib_low > 0:
            if active_price > fib_high:
                optimal_limit = round(fib_high, 2)
                diff_p = (optimal_limit - active_price) / active_price * 100
                limit_note = f"黄金坑上沿埋伏单 ({diff_p:+.1f}%)"
            elif active_price >= pit_mid_low:
                optimal_limit = pit_mid_low
                diff_p = (optimal_limit - active_price) / active_price * 100
                limit_note = f"黄金坑0.382中下轴接针 ({diff_p:+.1f}%)"
            elif active_price >= fib_low * 0.98:
                ref_support = min(active_price * 0.997, vwap * 0.998) if vwap > 0 else active_price * 0.997
                optimal_limit = round(max(fib_low * 0.985, ref_support), 2)
                diff_p = (optimal_limit - active_price) / active_price * 100
                limit_note = f"已入坑底核心区·贴支撑低吸 ({diff_p:+.1f}%)"
            else:
                vwap_anchor = vwap * 0.995 if (vwap > 0 and vwap <= active_price) else active_price * 0.985
                raw_def_limit = min(active_price * 0.985, vwap_anchor)
                optimal_limit = round(max(active_price * 0.962, raw_def_limit), 2)
                diff_p = (optimal_limit - active_price) / active_price * 100
                limit_note = f"破坑深调·支撑防守单 ({diff_p:+.1f}%)"
        else:
            optimal_limit = round(active_price * 0.99, 2)
            limit_note = "现价下方1%分批限价单 (-1.0%)"

    # 1.5 科学防守止损线测算 (对齐 V3.1 初始结构止损: Low-0.5*ATR 与 Close-1.8*ATR 中较宽保护，硬上限 -6.8%)
    ref_low = rth_low if rth_low > 0 else (optimal_limit * 0.985)
    struct_stop = min(ref_low - 0.5 * atr, optimal_limit - 1.8 * atr) if atr > 0 else (optimal_limit * 0.95)
    entry_stop_loss = round(max(struct_stop, optimal_limit * 0.932), 2)
    stop_loss_pct = round((entry_stop_loss - optimal_limit) / optimal_limit * 100, 1) if optimal_limit > 0 else -4.0

    # 2. 日内 5 分钟级买点触发判定 (融合 5m RVOL 量能与 5m RSI 动能共振确认)
    in_buy_zone = (is_pit or (-2.0 <= dist_to_pit <= 0.8)) and (not is_stop)

    # 触发器 A：5m RSI 近半小时曾超卖 (<36) 后拐头回升 + 5m 收阳止跌
    cond_oversold_turn = (min_rsi_30m <= 36.0) and (rsi_5m >= prev_rsi_5m + 1.0) and is_5m_green
    # 触发器 B：股价回落至中下轴挂单区或日内低点附近 + 连续两根 5m K 线不创新低 + 收阳且未超买
    cond_support_hold = (active_price <= pit_mid_low * 1.008 or active_price <= rth_low * 1.008) and is_higher_low and is_5m_green and (rsi_5m <= 56.0)
    # 触发器 C：日内下探黄金坑后，5m 收盘右侧重新站上日内 VWAP 均价线 + 5m 量能或动能确认
    cond_vwap_reclaim = (rth_low <= fib_high * 1.005) and (vwap > 0) and (vwap <= active_price <= vwap * 1.012) and (38.0 <= rsi_5m <= 62.0) and is_5m_green and (rvol_5m >= 0.95 or rsi_5m >= prev_rsi_5m + 0.8)
    # 触发器 D：深度回踩超跌标的 (未放量破位且日线 RSI<=42)，5m 出现「站稳VWAP + 5m量比>=1.20x + 5m RSI 42~62收阳」或「5m超卖放量拐头」真突破共振
    cond_deep_sync = (not is_stop) and (daily_rsi <= 42.0) and is_5m_green and (
        ((min_rsi_30m <= 35.0) and (rsi_5m >= prev_rsi_5m + 1.5) and (rvol_5m >= 1.10))
        or ((vwap > 0) and (vwap <= active_price <= vwap * 1.015) and (42.0 <= rsi_5m <= 62.0) and (rvol_5m >= 1.20))
    )

    # 破坑深调分级：左侧真飞刀 vs 右侧超跌博反抽
    try:
        s1_support = float(ai_s1) if (ai_s1 is not None and float(ai_s1) > 0) else 0.0
    except Exception:
        s1_support = 0.0
    try:
        r1_resist = float(ai_r1) if (ai_r1 is not None and float(ai_r1) > 0) else 0.0
    except Exception:
        r1_resist = 0.0
    if r1_resist <= 0:
        r1_resist = fib_high if fib_high > 0 else active_price * 1.05
    vwap_ok = bool(vwap > 0 and active_price >= vwap)
    under_vwap = bool(vwap > 0 and active_price < vwap)
    try:
        _ai_wr_val = float(ai_cal_win_rate) if ai_cal_win_rate is not None else None
    except Exception:
        _ai_wr_val = None
    ai_weak_under_vwap = bool(under_vwap and (_ai_wr_val is not None) and _ai_wr_val < 50.0)
    oversold_turn = bool((min_rsi_30m <= 30.0) and (rsi_5m >= 35.0) and is_5m_green)
    support_stabilize = False
    if s1_support > 0:
        dist_s1_pct = ((active_price - s1_support) / s1_support) * 100.0
        support_stabilize = bool(0.0 <= dist_s1_pct <= 3.5 and (is_5m_green or is_higher_low))
    # 品种过滤：低弹性慢股(ATR%<1.8)、暴雷首日(当日<=-7.5%)，以及 AI 做多胜率<50%且压制在 VWAP 下方时均不激进给右侧博反抽做多信号
    atr_pct_val = (atr / active_price) * 100.0 if (atr > 0 and active_price > 0) else 0.0
    low_elasticity = bool(0.0 < atr_pct_val < 1.8)
    crash_day = bool(pct_change <= -7.5)
    rebound_blocked = bool(low_elasticity or crash_day or ai_weak_under_vwap)
    broken_rebound_ready = bool(is_stop and (not rebound_blocked) and (vwap_ok or oversold_turn or support_stabilize))
    broken_bearish = bool(is_stop and not broken_rebound_ready)

    intraday_entry_ready = False
    tactical_stop_set = False
    if is_stop:
        if broken_rebound_ready:
            lo, hi, pad = _bounce_rebound_stop_band(ticker)
            try:
                low_5m = float(intra_info.get("low_5m") or 0.0)
            except Exception:
                low_5m = 0.0
            anchors = []
            if vwap > 0 and vwap < active_price:
                anchors.append(float(vwap))
            if low_5m > 0 and low_5m < active_price:
                anchors.append(low_5m)
            if anchors:
                invalidation = max(anchors)
            elif vwap > 0:
                invalidation = float(min(vwap, active_price))
            else:
                invalidation = active_price * (1.0 - lo / 100.0)
            gap_pct = ((active_price - invalidation) / active_price * 100.0) if active_price > 0 else 0.0
            too_far = bool(gap_pct > hi)
            if too_far and vwap > 0:
                optimal_limit = round(min(active_price, float(vwap)), 2)
                limit_note = "偏离失效位过远·等回踩 VWAP 再介入"
                intraday_entry_ready = False
                intraday_status_text = "偏离失效位过远·等回踩 VWAP 再介入"
            else:
                if active_price > invalidation * 1.008:
                    optimal_limit = round(min(active_price, invalidation * 1.008), 2)
                    limit_note = "回踩VWAP/5m企稳低点短线单 · 已冲高不追现价"
                else:
                    optimal_limit = round(active_price, 2)
                    limit_note = "回踩VWAP/5m企稳低点短线单"
                intraday_entry_ready = True
                intraday_status_text = "⚡ 超跌右侧博反抽 (破坑深调·快进快出)"
            if optimal_limit <= 0:
                optimal_limit = round(active_price, 2)
            stop_px, inside_band = _stop_below_invalidation(optimal_limit, invalidation, lo, hi, pad)
            if not inside_band:
                if vwap > 0 and optimal_limit > float(vwap):
                    optimal_limit = round(min(active_price, float(vwap)), 2)
                    stop_px, inside_band = _stop_below_invalidation(optimal_limit, invalidation, lo, hi, pad)
                intraday_entry_ready = False
                intraday_status_text = "偏离失效位过远·等回踩 VWAP 再介入"
                limit_note = "偏离失效位过远·等回踩 VWAP 再介入"
            entry_stop_loss = round(stop_px, 2)
            if entry_stop_loss >= invalidation:
                entry_stop_loss = round(invalidation * (1.0 - max(pad, 0.05) / 100.0), 2)
            stop_loss_pct = round((entry_stop_loss - optimal_limit) / optimal_limit * 100.0, 1) if optimal_limit > 0 else -lo
            if "偏离失效位过远" in intraday_status_text and optimal_limit > 0:
                entry_stop_loss, stop_loss_pct = _stop_from_reanchored_limit(
                    ticker, optimal_limit, vwap, low_5m
                )
            tactical_stop_set = True
            intraday_reason = (
                f"日线仍处黄金坑(${fib_low:.2f})下方，失效位 VWAP/5m低点 ${invalidation:.2f}；"
                f"止损画在失效位下方 ${entry_stop_loss:.2f}（{stop_loss_pct:.1f}%），"
                f"压力位(R1 ${r1_resist:.2f})不过即走，严禁中线死扛"
            )
            if intraday_entry_ready and (active_price > invalidation * 1.008 or rsi_5m >= 68.0):
                limit_note += " · 5m RSI/冲高提醒: 切勿追高现价"
                intraday_reason += " ｜ 日内已冲高偏离失效位，切勿追高现价，耐心等回踩低吸"
        else:
            intraday_entry_ready = False
            optimal_limit = 0.0
            limit_note = "暂缓左侧接刀·等站上VWAP右侧确认"
            entry_stop_loss = round(s1_support, 2) if s1_support > 0 else 0.0
            stop_loss_pct = 0.0
            if crash_day:
                intraday_status_text = "🔴 暴雷急杀·严禁左侧接刀"
                intraday_reason = f"今日跌幅{pct_change:.1f}% (≤-7.5%)，处于单日暴雷急杀宣泄当天；即便盘中短暂触碰VWAP也不触发博反抽，必须等次日/三日宣泄期后再评估右侧企稳信号"
            elif ai_weak_under_vwap:
                intraday_status_text = "⏳ 未站稳 VWAP·暂观望，站上 VWAP 再看反抽"
                limit_note = "AI胜率<50%且低于VWAP·站上VWAP再看反抽"
                intraday_reason = (
                    f"AI校准做多胜率仅{_ai_wr_val:.1f}% (<50%) 且现价(${active_price:.2f})仍压制于日内VWAP(${vwap:.2f})下方；"
                    f"未站稳 VWAP 暂观望，待重夺并站上 VWAP 再看反抽（S1参考 ${s1_support:.2f}）"
                )
            elif low_elasticity:
                intraday_status_text = "🔴 低弹性慢股·破坑观望"
                intraday_reason = f"14日ATR%仅{atr_pct_val:.1f}% (<1.8%)，低弹性慢股反弹空间过窄；破坑后不触发短线博反抽，统一保持观望等待日线右侧企稳确认"
            else:
                intraday_status_text = "🔴 空头宣泄·严禁左侧接刀"
                intraday_reason = f"股价跌穿黄金坑(${fib_low:.2f})且日内压制于VWAP(${vwap:.2f})下方，空头宣泄未止跌；请耐心等待日内重夺VWAP后再博右侧反抽"
    elif dist_to_pit < -2.0 and not is_pit:
        if ai_weak_under_vwap:
            intraday_entry_ready = False
            intraday_status_text = "⏳ 未站稳 VWAP·暂观望，站上 VWAP 再看反抽"
            intraday_reason = (
                f"AI校准做多胜率仅{_ai_wr_val:.1f}% (<50%) 且现价(${active_price:.2f})低于日内VWAP(${vwap:.2f})；"
                f"未站稳 VWAP 暂观望，站上 VWAP 并出量后再看反抽"
            )
        elif cond_deep_sync:
            intraday_entry_ready = True
            intraday_status_text = "🎯 日内买点触发 (超跌量价共振)"
            intraday_reason = f"日线超跌(RSI {daily_rsi:.1f})后触发5m量价同步共振：站稳VWAP(${vwap:.2f}) + 5m量比{rvol_5m:.2f}x(≥1.2x) + 5m RSI({rsi_5m:.1f})收阳确认"
        elif vwap > 0 and active_price > vwap * 1.015:
            intraday_status_text = f"⏳ 超跌反抽偏高·等回踩VWAP(${vwap:.2f})"
            intraday_reason = f"日线破坑({dist_to_pit:+.1f}%)但日内已拉升偏离VWAP(${vwap:.2f})，切勿追高；建议挂${optimal_limit:.2f}等回踩，或等「贴近VWAP+5m量比≥1.2x+5m RSI 42~62」共振"
        else:
            intraday_status_text = f"⏳ 破坑深调({dist_to_pit:+.1f}%)·等5m量价共振"
            intraday_reason = f"破坑深调中(坑底${fib_low:.2f})。右侧买点需3项同步配合：①站上VWAP(${vwap:.2f}) ②5m量比≥1.20x(现{rvol_5m:.2f}x) ③5m RSI 42~62收阳(现{rsi_5m:.1f})，达标自动告警"
    elif is_pit:
        # V3.1 右侧买点优先处理
        if is_rth_session:
            intraday_entry_ready = True
            intraday_status_text = "🟢 V3.1右侧买点就绪 (盘中执行)"
            intraday_reason = f"V3.1右侧确认，盘中以限价${optimal_limit:.2f}(现价+0.5%)直接挂单买入。"
        else:
            pre_change = safe_ext if safe_ext != 0.0 else pct_change
            if pre_change <= 2.0 and pre_change > -1.5:
                intraday_entry_ready = True
                intraday_status_text = "🟢 V3.1右侧买点就绪 (9:30开盘执行)"
                intraday_reason = f"V3.1右侧确认，开盘以限价上限${optimal_limit:.2f}(昨收+2.0%)直接下单；若开盘跌破${(rth_close*0.985):.2f}(-1.5%)则作废撤单。"
            else:
                intraday_entry_ready = False
                intraday_status_text = f"⏳ V3.1开盘观察·盘前偏离({pre_change:+.1f}%)"
                intraday_reason = f"已触发V3.1买点，但盘前偏离({pre_change:+.2f}%)超出[-1.5%, +2.0%]安全窗口，开盘暂缓下单观察。"
    elif in_buy_zone:
        if ai_weak_under_vwap:
            intraday_entry_ready = False
            intraday_status_text = "⏳ 未站稳 VWAP·暂观望，站上 VWAP 再看反抽"
            intraday_reason = (
                f"AI校准做多胜率仅{_ai_wr_val:.1f}% (<50%) 且现价(${active_price:.2f})低于日内VWAP(${vwap:.2f})；"
                f"未站稳 VWAP 暂观望，站上 VWAP 再看反抽"
            )
        elif cond_oversold_turn:
            intraday_entry_ready = True
            intraday_status_text = "🎯 日内买点触发 (5m超卖拐头)"
            intraday_reason = f"日线处于黄金坑区，5m RSI触底({min_rsi_30m:.1f})后拐头回升至{rsi_5m:.1f}且收阳止跌 (5m量比{rvol_5m:.2f}x)"
        elif cond_vwap_reclaim:
            intraday_entry_ready = True
            intraday_status_text = "🎯 日内买点触发 (右侧夺回VWAP)"
            intraday_reason = f"日内回踩黄金坑(${rth_low:.2f})后放量收阳(5m量比{rvol_5m:.2f}x)，右侧重新站稳日内VWAP均价线(${vwap:.2f})"
        elif cond_support_hold:
            intraday_entry_ready = True
            intraday_status_text = "🎯 日内买点触发 (支撑企稳确认)"
            intraday_reason = f"日内触及黄金坑支撑区(${active_price:.2f})，连续两根5m K线不创新低且收阳企稳(5m RSI {rsi_5m:.1f}，量比{rvol_5m:.2f}x)"
        else:
            if rsi_5m > 62.0 or (vwap > 0 and active_price > vwap * 1.015):
                intraday_status_text = "⏳ 坑内偏高·等回踩挂单价"
                intraday_reason = f"日线在黄金坑内，但短线5m RSI({rsi_5m:.1f})偏高且高于VWAP(${vwap:.2f})，建议在${optimal_limit:.2f}挂单等回落"
            else:
                intraday_status_text = "⏳ 坑内盯盘·等5m止跌阳线"
                intraday_reason = f"日线已入黄金坑，等待5m共振：①站上VWAP(${vwap:.2f})+5m量比≥0.95x(现{rvol_5m:.2f}x)，或 ②5m RSI<36超卖拐头(现{rsi_5m:.1f})"
    else:
        intraday_status_text = f"⏳ 未进坑(距坑+{abs(dist_to_pit):.1f}%)"
        intraday_reason = f"股价位于黄金坑上方(坑顶${fib_high:.2f}，日内VWAP ${vwap:.2f})，可预先在${optimal_limit:.2f}埋伏限价单"

    if optimal_limit > 0 and not tactical_stop_set:
        try:
            low_5m = float(intra_info.get("low_5m") or 0.0)
        except Exception:
            low_5m = 0.0
        entry_stop_loss, stop_loss_pct = _card_stop_under_structure(ticker, optimal_limit, vwap, low_5m)

    if is_e_risk and days_to_e is not None:
        intraday_reason += f" ｜ {e_tag}，谨防财报跳空盲盒(建议减半仓位或等财报落地)"

    o_info = options_info or {}
    if o_info.get("has_options"):
        o_sent = str(o_info.get("pcr_sentiment", "") or "")
        o_call = o_info.get("top_call_info", "")
        o_put = o_info.get("top_put_info", "")
        if intraday_entry_ready:
            o_vol_pcr = float(o_info.get("pcr", 1.0) or 1.0)
            o_dlr_pcr = float(o_info.get("dollar_pcr", 1.0) or 1.0)
            is_bear_sent = ("偏空" in o_sent) or ("看跌" in o_sent)
            vol_bull = o_vol_pcr <= 0.80
            dlr_bear = o_dlr_pcr >= 1.15
            dlr_bull = o_dlr_pcr <= 0.85
            if (not is_bear_sent) and vol_bull and (not dlr_bear):
                intraday_reason += f" ｜ ⚡期权多头共振({o_sent}, 主力Call {o_call})"
            elif (not is_bear_sent) and dlr_bull and o_vol_pcr <= 1.15:
                intraday_reason += f" ｜ ⚡期权多头共振({o_sent}, 主力Call {o_call})"
            elif (is_bear_sent or dlr_bear) and vol_bull:
                intraday_reason += f" ｜ ⚖️期权多空分歧({o_sent}, 主力Call {o_call} / 防守Put {o_put})"
            else:
                intraday_reason += f" ｜ ⚡期权对冲提醒({o_sent}, 防守Put {o_put})"

    return {
        "optimal_limit": optimal_limit,
        "limit_note": limit_note,
        "entry_stop_loss": entry_stop_loss,
        "stop_loss_pct": stop_loss_pct,
        "intraday_entry_ready": intraday_entry_ready,
        "intraday_status_text": intraday_status_text,
        "intraday_reason": intraday_reason,
        "is_crash_halt": False,
        "is_cooling_halt": False,
        "rth_low": round(rth_low, 2),
        "rth_high": round(curr_rth_high, 2),
        "vwap": round(vwap, 2),
        "has_5m_intraday": has_5m_intraday,
        "is_5m_green": bool(intra_info.get("is_5m_green", False)),
        "rsi_5m": out_rsi_5m,
        "rvol_5m": out_rvol_5m,
        "rs_20d": out_rs_20d,
        "broken_rebound_ready": broken_rebound_ready,
        "broken_bearish": broken_bearish,
        "broken_s1": round(s1_support, 2) if s1_support > 0 else 0.0,
        "broken_r1": round(r1_resist, 2),
        **e_info
    }

# ==============================================================================
# 二点五、 PushPlus 微信智能预警推送引擎 (智能去重与防轰炸机制)
# ==============================================================================
def load_alert_history():
    """读取本地预警去重历史"""
    try:
        if os.path.exists(ALERT_CACHE_FILE):
            with open(ALERT_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"weather": "", "targets": {}, "position_peaks": {}}

def save_alert_history(history):
    """保存预警去重历史到本地"""
    try:
        with open(ALERT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] 保存预警历史异常: {e}")

def _shared_dedupe_key(title, markdown_content=""):
    if not _SHARED_ALERTS:
        return ""
    import re
    tickers = re.findall(r"\b([A-Z]{1,6}(?:-[A-Z]{2,4})?)\b", str(title or ""))
    if markdown_content:
        for m in re.findall(r"(?:🚀|🚨|💰|🛡️)\s*\*\*([A-Z]{1,6}(?:-[A-Z]{2,4})?)\b", str(markdown_content)):
            if m not in tickers:
                tickers.append(m)
    ticker_str = "/".join(dict.fromkeys(tickers)) if tickers else None
    return shared_alert_state.make_key(shared_alert_state.et_day(), title, ticker=ticker_str)


def _shared_device_tag():
    return "ANDROID" if ANDROID_MODE else "PC"


def send_discord_claw_alert(title, markdown_content, webhook_url=None, dedupe_key=None, dedupe_max_age=600, color_override=None):
    """
    通过 Discord Webhook 向专属 Claw 预警频道推送与微信 100% 同步的 Markdown 战报卡片
    具备自动颜色适配、超时隔离与长文本安全收纳。
    """
    if _external_alerts_disabled():
        print(f"  [*] [Dry-Run Guard] 已屏蔽 Discord Claw 推送: {title}")
        return False

    url = webhook_url or get_claw_webhook_url()
    if not url:
        return False
    # 防御因子卡片只允许走专用频道，严禁由 Claw/主预警频道夹带发送
    _claw_blob = (title or "") + "\n" + (markdown_content or "")
    if any(_k in _claw_blob for _k in ("防御因子背离哨", "防御/科技超额背离", "Flight Score", "防御因子背离")):
        print("  [!] 拦截：防御因子卡片禁止通过 Claw/主预警频道发送")
        return False

    if dedupe_key is not None:
        _dedupe_key = str(dedupe_key).strip()
    else:
        _dedupe_key = "" if webhook_url else _shared_dedupe_key(title, markdown_content)

    if _dedupe_key and _SHARED_ALERTS and shared_alert_state.is_sent(_dedupe_key, max_age=dedupe_max_age):
        print(f"  [*] 跨设备去重跳过重复告警: {_dedupe_key}")
        return False

    # 1. 标题与状态智能着色
    if color_override is not None:
        color = int(color_override)
    else:
        color = 0xFF944D  # 默认橙色 (预警提示)
        if any(k in title for k in ("买点", "黄金坑", "企稳", "买入", "抄底", "出清", "突破")):
            color = 0x00E676  # 鲜亮翠绿
        elif any(k in title for k in ("止损", "破位", "熔断", "暴跌", "极限风控", "硬刹车", "倒挂", "跌破")):
            color = 0xF85149  # 警示鲜红
        elif any(k in title for k in ("止盈", "卖出", "冲顶")):
            color = 0xBC8CFF  # 阶梯紫色
        elif any(k in title for k in ("对冲", "反向", "雷达")):
            color = 0x58A6FF  # 科技天蓝

    # 2. 格式化并保护 Discord 4,096 字符上限
    desc = (markdown_content or "").strip()
    if len(desc) > 3900:
        desc = desc[:3850] + "\n\n... *(内容超长已由系统平滑收纳)*"

    embed = {
        "title": title,
        "description": desc,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {
            "text": "富途牛牛风格 · 美股全周期量化雷达实时预警 (Claw Mirror)"
        }
    }

    payload = {
        "username": "小豆泥·实盘预警 (Claw)",
        "embeds": [embed]
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                print(f"  [+] 💬 Discord ClawBot预警镜像通道推送成功: {title}")
                if _dedupe_key and _SHARED_ALERTS:
                    shared_alert_state.mark(_dedupe_key, _shared_device_tag())
                return True
            else:
                print(f"  [!] 💬 Discord ClawBot预警推送返回状态码: {resp.status}")
                return False
    except Exception as e:
        print(f"  [!] 💬 Discord ClawBot预警推送异常: {e}")
        return False


def send_discord_defensive_flight_alert(df_alert, live_lookup=None, update_time_str=None, webhook_url=None):
    """
    通过专属 Webhook 向【防御因子背离百分比】频道实时推送纯粹、聚焦的防御/科技背离因子战报。
    每次雷达刷新均精准播报，包含背离差值、防御/科技各自均值、各成分股涨跌幅细分及大白话操盘指引。
    """
    if _external_alerts_disabled():
        print("  [*] [Dry-Run Guard] 已屏蔽防御因子 Discord 推送")
        return False

    url = webhook_url or get_defensive_webhook_url()
    if not url:
        load_env_file()
        url = get_defensive_webhook_url()
    # 防御因子绝不串到买点/量化喵/镜像频道。地址只从环境变量读，仓库里不留 webhook。
    wrong_channel = any(ch in url for ch in (
        "1569762886996197499",
        "1569506393650634834",
        "1569534508212944997",
    ))
    if not url or wrong_channel:
        print("[!] 防御因子频道未配置 DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL，本轮跳过，避免串台。")
        return False

    is_alert = bool(df_alert.get("is_alert", False))
    score = float(df_alert.get("flight_score", 0.0))
    def_mean = float(df_alert.get("def_mean_chg", 0.0))
    off_mean = float(df_alert.get("off_mean_chg", 0.0))

    _score_bucket = f"{round(score, 1):+.1f}"
    _def_key = "" if webhook_url else f"{shared_alert_state.et_day()}:DEFENSIVE_FLIGHT:{_score_bucket}" if _SHARED_ALERTS else ""
    if _def_key and shared_alert_state.is_sent(_def_key):
        print(f"  [*] 跨设备去重跳过重复防御因子告警 ({_def_key})")
        return False

    live_lookup = live_lookup or {}
    _is_futures = (df_alert.get("score_source") == "futures_proxy")
    def_items = []
    off_items = []
    if _is_futures:
        _comps = df_alert.get("components", {}) or {}
        for _s in ("ZB=F", "GC=F"):
            if _s in _comps:
                def_items.append(f"{_s} `{float(_comps[_s].get('pct', 0.0)):+.2f}%`")
        if "NQ=F" in _comps:
            off_items.append(f"NQ=F `{float(_comps['NQ=F'].get('pct', 0.0)):+.2f}%`")
    else:
        for s in ["KO", "PEP", "MCD", "LLY", "MRNA", "TLT", "GLD", "VIXY"]:
            if s in live_lookup:
                lv = live_lookup[s]
                pct = float(lv.get("ext_pct") if lv.get("ext_price", 0) > 0 else lv.get("pct_change", lv.get("change_pct", 0.0)))
                def_items.append(f"{s} `{pct:+.2f}%`")
        for s in ["SMH", "SOXL", "NVDA", "QQQ"]:
            if s in live_lookup:
                lv = live_lookup[s]
                pct = float(lv.get("ext_pct") if lv.get("ext_price", 0) > 0 else lv.get("pct_change", lv.get("change_pct", 0.0)))
                off_items.append(f"{s} `{pct:+.2f}%`")

    if is_alert:
        color = 0xF85149  # 警示红
        status_tag = f"🚨 **【防御因子起飞 · 警惕机构盘中去杠杆踩踏】**"
        advice = "⚠️ **操盘警示**：防御避险资产超额走强，科技成长股正被机构急促抽离资金！短线多头切勿盲目抄底接飞刀，实盘注意上移保本止损，防范盘中踩踏！"
    elif score >= 1.5:
        color = 0xFF944D  # 警觉橙
        status_tag = f"⚠️ **【避险情绪升温 · 防御因子走强】**"
        advice = "👀 **盘面观察**：防御因子相对科技出现温和背离，机构资金偏向保守防御，多头进攻动能有所收敛，持仓宜设移动止盈保护。"
    elif score <= -1.0:
        color = 0x00E676  # 顺风绿
        status_tag = f"🚀 **【进攻科技占优 · 市场风险偏好高昂】**"
        advice = "🟢 **多头顺风**：进攻型科技成长股显著领跑防御资产，市场风险偏好健康，多头仓位顺势拿稳。"
    else:
        color = 0x58A6FF  # 平稳蓝
        status_tag = f"🛡️ **【因子表现均衡 · 机构仓位平稳】**"
        advice = "✅ **常规格局**：防御因子与进攻科技相对强弱处于正常均衡区间，机构未见系统性去杠杆调仓。"

    def_str = " ｜ ".join(def_items) if def_items else "暂无分项明细"
    off_str = " ｜ ".join(off_items) if off_items else "暂无分项明细"
    if _is_futures:
        if is_alert:
            status_tag = "🚨 **【夜盘·期货代理】防御因子起飞 · 期货代理显示机构去杠杆方向**"
            advice = "⚠️ **期货代理提示**：数据来自 NQ=F(进攻) / ZB=F、GC=F(防御) 期货，不是现货篮子；阈值仍为 2.2 / 1.8。"
        else:
            status_tag = "🛡️ **【夜盘·期货代理】防御因子平稳 · 期货代理非现货篮子**"
            advice = "✅ **期货代理**：NQ=F / ZB=F / GC=F 夜盘期货方向；这不是现货篮子分数，不改变白天现货计算。"
    print(f"  [+] 防御因子真实成分: 防御 {len(def_items)} 只 / 进攻 {len(off_items)} 只")
    now_t = update_time_str or now_et_str("%Y-%m-%d %H:%M:%S")

    desc = f"""### {status_tag}

> ⚖️ **防御/科技超额背离 (Flight Score)**：**`{score:+.2f}%`** {("🚨" if is_alert else "")}
> 
> 🛡️ **防御避险篮子** (平均 `{def_mean:+.2f}%`)：
> {def_str}
> 
> ⚔️ **进攻科技成长** (平均 `{off_mean:+.2f}%`)：
> {off_str}

{advice}"""

    embed = {
        "title": f"🚨【机构避险】防御/科技背离因子 · 实时追踪" if is_alert else f"🛡️【机构仓位】防御/科技背离因子 · 实时追踪",
        "description": desc,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {
            "text": f"美股全周期量化雷达 · 每次刷新实时监测 | {now_t}"
        }
    }

    payload = {
        "username": "小豆泥·防御因子背离哨",
        "embeds": [embed]
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                print(f"  [+] 💬 Discord 防御因子背离专属频道推送成功: {score:+.2f}% (is_alert={is_alert})")
                if _def_key:
                    shared_alert_state.mark(_def_key, _shared_device_tag())
                return True
            else:
                print(f"  [!] 💬 Discord 防御因子推送返回状态码: {resp.status}")
                return False
    except Exception as e:
        print(f"  [!] 💬 Discord 防御因子推送异常: {e}")
        return False


INTRADAY_CORRIDOR_ALERT_TICKERS = ("SPY", "QQQ", "SOXL")
_DEFENSIVE_WRONG_CHANNEL_IDS = (
    "1569762886996197499",
    "1569506393650634834",
    "1569534508212944997",
)


def _fmt_opt_num(val, fmt=".2f", default="--"):
    try:
        if val is None:
            return default
        fv = float(val)
        if not math.isfinite(fv):
            return default
        return format(fv, fmt)
    except Exception:
        return default


def _extract_live_quote_price_pct(symbol, quote_lookup, intraday_forecasts=None):
    """提取标的最新实时价格与当日涨跌幅（优先 live lookup，缺失回退走廊 actual_price）。"""
    q = (quote_lookup or {}).get(symbol)
    px = None
    pct = None
    if isinstance(q, dict):
        for k in ("active_price", "last", "ext_price", "price"):
            try:
                v = q.get(k)
                if v is not None and math.isfinite(float(v)) and float(v) > 0:
                    px = float(v)
                    break
            except Exception:
                continue
        for k in ("effective_pct", "pct_change", "change_pct", "ext_pct"):
            try:
                v = q.get(k)
                if v is not None and math.isfinite(float(v)):
                    pct = float(v)
                    break
            except Exception:
                continue
    if px is None and isinstance(intraday_forecasts, dict):
        it = (intraday_forecasts.get("by_ticker") or {}).get(symbol)
        if isinstance(it, dict):
            div = it.get("divergence") or {}
            try:
                v = div.get("actual_price")
                if v is not None and math.isfinite(float(v)) and float(v) > 0:
                    px = float(v)
            except Exception:
                pass
    return px, pct


def send_discord_vxn_deleveraging_alert(vix_regime, def_flight=None, update_time_str=None, webhook_url=None, dedupe_day=None):
    """
    频道二（#防御因子背离百分比 / DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL）：
    当 ^VXN >= 32.0 且科技恐慌溢价 VXN - VIX >= 5.5 时，推送科技/半导体极端去杠杆预警。
    纳入 shared_alert_state.py 跨设备去重锁，每个美东交易日最多推送 1 次。
    """
    if not isinstance(vix_regime, dict) or bool(vix_regime.get("degraded", False)):
        return False

    try:
        vxn_val = float(vix_regime.get("vxn_spot")) if vix_regime.get("vxn_spot") is not None else float("nan")
        vix_val = float(vix_regime.get("vix_spot")) if vix_regime.get("vix_spot") is not None else float("nan")
    except Exception:
        return False
    if not (math.isfinite(vxn_val) and math.isfinite(vix_val)):
        return False

    raw_spread = vix_regime.get("vxn_vix_spread")
    try:
        spread_val = float(raw_spread) if raw_spread is not None else round(vxn_val - vix_val, 2)
    except Exception:
        spread_val = round(vxn_val - vix_val, 2)
    if not (math.isfinite(spread_val) and vxn_val >= 32.0 and spread_val >= 5.5):
        return False

    day_et = dedupe_day or (shared_alert_state.et_day() if _SHARED_ALERTS else now_et_str("%Y-%m-%d"))
    dedupe_key = f"{day_et}:VXN_TECH_DELEVERAGING"
    if _SHARED_ALERTS and shared_alert_state.is_sent(dedupe_key, max_age=None):
        print(f"  [*] 跨设备去重跳过重复 VXN 去杠杆预警 ({dedupe_key})")
        return False

    if _external_alerts_disabled():
        print(f"  [*] [Dry-Run Guard] 已屏蔽 VXN 极端去杠杆 Discord 推送 ({dedupe_key})")
        return False

    url = webhook_url or get_defensive_webhook_url()
    if not url and webhook_url is None:
        load_env_file()
        url = get_defensive_webhook_url()
    wrong_channel = any(ch in url for ch in _DEFENSIVE_WRONG_CHANNEL_IDS)
    if not url or wrong_channel:
        print("[!] 防御因子频道未配置 DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL，跳过 VXN 去杠杆预警。")
        return False

    vvix_str = _fmt_opt_num(vix_regime.get("vvix_spot"), ".2f")
    ratio_str = _fmt_opt_num(vix_regime.get("vix_term_ratio"), ".2f")
    flight_str = "暂缺"
    if isinstance(def_flight, dict) and def_flight.get("flight_score") is not None and not def_flight.get("skip_reason"):
        try:
            flight_str = f"{float(def_flight.get('flight_score')):+.2f}%"
        except Exception:
            pass
    now_t = update_time_str or now_et_str("%Y-%m-%d %H:%M:%S")

    desc = (
        f"### 🔥 **【纳指/半导体极端去杠杆 · ^VXN 恐慌溢价飙升】**\n\n"
        f"> 📊 **^VXN (纳指100恐慌)**：**`{vxn_val:.2f}`** ｜ **^VIX (标普恐慌)**：**`{vix_val:.2f}`**\n"
        f"> ⚡ **科技恐慌溢价 (VXN - VIX)**：**`{spread_val:+.2f}`** (阈值 `≥ +5.5`)\n"
        f"> 🌊 **^VVIX (波动率之波动)**：**`{vvix_str}`** ｜ **期限比 VIX/VIX3M**：`{ratio_str}`\n"
        f"> 🛡️ **当前防御因子背离度 (Flight Score)**：**`{flight_str}`**\n\n"
        f"⚠️ **风控提示**：纳指/半导体成长板块恐慌度显著高于大盘，机构资金正在科技端集中去杠杆撤离！"
        f"高波 AI/芯片多头严禁重仓扛单或盲目左侧抄底，谨防流动性踩踏。"
    )

    embed = {
        "title": "🔥【科技极端去杠杆】^VXN 恐慌溢价预警",
        "description": desc,
        "color": 0xF85149,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {
            "text": f"美股全周期量化雷达 · 科技恐慌溢价监测 | {now_t} ET"
        },
    }
    payload = {
        "username": "小豆泥·防御因子背离哨",
        "embeds": [embed],
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                print(f"  [+] 💬 Discord 频道二 VXN 科技去杠杆预警推送成功: ^VXN={vxn_val:.2f}, spread={spread_val:+.2f}")
                if _SHARED_ALERTS:
                    shared_alert_state.mark(dedupe_key, _shared_device_tag())
                return True
            print(f"  [!] 💬 Discord VXN 去杠杆预警返回状态码: {resp.status}")
            return False
    except Exception as e:
        print(f"  [!] 💬 Discord VXN 去杠杆预警推送异常: {e}")
        return False


def _should_record_signal_ledger(webhook_url=None, explicit_ledger_path=None):
    """
    仅在真实推送生效或测试显式指定隔离账本路径时记账：
    - RADAR_DISABLE_EXTERNAL_ALERTS=1 / --no-alerts 下严格禁写；
    - 无显式隔离路径的单元测试或 example.invalid 测试 Webhook 严禁污染生产 data_cache/signal_ledger.json。
    """
    if _external_alerts_disabled():
        return False
    if explicit_ledger_path or os.environ.get("SIGNAL_LEDGER_PATH", "").strip():
        return True
    if "unittest" in sys.modules:
        return False
    eff_url = str(webhook_url or get_claw_webhook_url() or get_defensive_webhook_url() or "").strip().lower()
    if (not eff_url) or ("example.invalid" in eff_url) or ("localhost" in eff_url) or ("127.0.0.1" in eff_url):
        return False
    return True


def _build_quote_lookup_map(macro_data=None, stocks_data=None):
    ql = {}
    if isinstance(stocks_data, (list, tuple)):
        for s in stocks_data:
            if isinstance(s, dict) and s.get("ticker"):
                ql[s["ticker"]] = s
    elif isinstance(stocks_data, dict):
        ql.update(stocks_data)
    if isinstance(macro_data, dict):
        ql.update(macro_data)
    return ql


def dispatch_vix_regime_discord_alerts(
    vix_regime,
    weather_info=None,
    def_flight=None,
    now_str=None,
    claw_webhook_url=None,
    def_webhook_url=None,
    dedupe_day=None,
    session_code=None,
    macro_data=None,
    stocks_data=None,
    ledger_path=None,
):
    """
    统一调度 VIX/VXN 恐慌中枢跨频道预警：
    - 频道一 (Claw)：VIX 期限倒挂硬刹车 / 恐慌出清右侧抄底观察窗（同日同状态最多 1 次）；
      其中策略 A 绿灯 (WASHOUT_BOTTOM_WINDOW) 仅允许在美东常规盘中 (REGULAR) 推送与落盘去重锁；
    - 频道二 (防御因子背离)：^VXN >= 32 且 VXN - VIX >= 5.5 科技去杠杆预警（同日最多 1 次）。
    """
    summary = {
        "vix_brake_sent": False,
        "vix_bottom_sent": False,
        "vxn_deleveraging_sent": False,
        "skipped_premarket_bottom": False,
        "sent_keys": [],
        "skipped_keys": [],
    }
    if not isinstance(vix_regime, dict) or vix_regime.get("vix_state") == "UNAVAILABLE" or vix_regime.get("vix_spot") is None:
        return summary

    eff_session = str(session_code or vix_regime.get("session_code") or "REGULAR").strip().upper() or "REGULAR"
    day_et = dedupe_day or (shared_alert_state.et_day() if _SHARED_ALERTS else now_et_str("%Y-%m-%d"))
    now_t = now_str or now_et_str("%Y-%m-%d %H:%M:%S")
    vix_state = str(vix_regime.get("vix_state") or "").upper()
    vix_val = vix_regime.get("vix_spot")
    vxn_val = vix_regime.get("vxn_spot")
    spread_val = vix_regime.get("vxn_vix_spread")
    ratio_val = vix_regime.get("vix_term_ratio")
    vvix_val = vix_regime.get("vvix_spot")
    tnx_val = vix_regime.get("tnx_yield")

    try:
        is_hard_brake = (
            vix_state == "BACKWARDATION_BRAKE"
            or (ratio_val is not None and float(ratio_val) >= 1.00)
            or (vix_val is not None and float(vix_val) >= 30.0)
        )
    except Exception:
        is_hard_brake = (vix_state == "BACKWARDATION_BRAKE")

    vxn_desc = f"`{_fmt_opt_num(vxn_val)}`"
    if spread_val is not None:
        try:
            vxn_desc += f" (溢价 `{float(spread_val):+.2f}`)"
        except Exception:
            pass
    tnx_desc = f"`{_fmt_opt_num(tnx_val)}%`" if tnx_val is not None else "`--`"

    # 1A. 频道一：🔴 VIX 期限倒挂硬刹车
    if is_hard_brake:
        brake_key = f"{day_et}:VIX_REGIME:BACKWARDATION_BRAKE"
        if _SHARED_ALERTS and shared_alert_state.is_sent(brake_key, max_age=None):
            summary["skipped_keys"].append(brake_key)
        else:
            title = "🔴 VIX 期限倒挂硬刹车 · 机构恐慌踩踏风控"
            md_lines = [
                f"### 🔴 **VIX 恐慌状态切换总开关 · 期限倒挂硬刹车 ({now_t} ET)**",
                f"> • **`^VIX`**：`{_fmt_opt_num(vix_val)}` ｜ **`^VXN`**：{vxn_desc}",
                f"> • **`VIX/VIX3M`**：`{_fmt_opt_num(ratio_val)}` ({vix_regime.get('term_structure_cn', '期限倒挂')})",
                f"> • **`^VVIX`**：`{_fmt_opt_num(vvix_val)}` ｜ **`^TNX` (10Y)**：{tnx_desc}",
                "",
                "🛑 **操盘铁律**：机构恐慌倒挂踩踏期，常规多头已锁死上限，**严禁左侧接刀**，多头买点全面降级防守！",
            ]
            if send_discord_claw_alert(
                title,
                "\n".join(md_lines),
                webhook_url=claw_webhook_url,
                dedupe_key=brake_key,
                dedupe_max_age=None,
                color_override=0xF85149,
            ):
                summary["vix_brake_sent"] = True
                summary["sent_keys"].append(brake_key)
                if _should_record_signal_ledger(claw_webhook_url or get_claw_webhook_url(), ledger_path):
                    try:
                        import signal_ledger
                        _ql_brk = _build_quote_lookup_map(macro_data=macro_data, stocks_data=stocks_data)
                        signal_ledger.record_benchmark_signals(
                            "VIX_BACKWARDATION_BRAKE",
                            quote_lookup=_ql_brk,
                            vix_regime=vix_regime,
                            timestamp_et=now_t,
                            ledger_path=ledger_path,
                        )
                    except Exception as _sl_err:
                        print(f"  [!] 记账本写入跳过 (VIX刹车): {_sl_err}")

    # 1B. 频道一：🟢 恐慌出清 · 右侧抄底观察窗开启 (严格锁定美东常规盘中 REGULAR，盘前/夜盘只预览不推 Discord、不落锁)
    elif (
        bool(vix_regime.get("vix_bottom_signal", False))
        or bool(vix_regime.get("vix_bottom_premarket_preview", False))
        or bool(vix_regime.get("raw_bottom_signal", False))
    ):
        if (
            eff_session != "REGULAR"
            or bool(vix_regime.get("vix_bottom_premarket_preview", False))
            or not bool(vix_regime.get("vix_bottom_signal", False))
        ):
            summary["skipped_premarket_bottom"] = True
            print(f"  [*] [盘前防误触发锁] 当前处于 {eff_session}（^VIX3M 冻结），跳过策略 A Discord 绿灯推送且不写当日去重锁")
        else:
            bottom_key = f"{day_et}:VIX_REGIME:WASHOUT_BOTTOM_WINDOW"
            if _SHARED_ALERTS and shared_alert_state.is_sent(bottom_key, max_age=None):
                summary["skipped_keys"].append(bottom_key)
            else:
                peak_s = _fmt_opt_num(vix_regime.get("vix_10d_peak"))
                dd_s = _fmt_opt_num(vix_regime.get("vix_drawdown_from_peak_pct"))
                drop_s = _fmt_opt_num(vix_regime.get("vix_term_ratio_drop"))
                title = "🟢 恐慌出清 · 右侧抄底观察窗开启"
                md_lines = [
                    f"### 🟢 **VIX 恐慌状态切换总开关 · 右侧抄底观察窗开启 ({now_t} ET)**",
                    f"> • **`^VIX`**：`{_fmt_opt_num(vix_val)}`（10日峰值 `{peak_s}`，回落 **`-{dd_s}%`**）",
                    f"> • **`VIX/VIX3M` 恢复升水**：`{_fmt_opt_num(ratio_val)}`（回落 `{drop_s}` · {vix_regime.get('term_structure_cn', '升水恢复')}）",
                    f"> • **`^VXN`**：{vxn_desc} ｜ **`^VVIX`**：`{_fmt_opt_num(vvix_val)}` ｜ **`^TNX`**：{tnx_desc}",
                    "",
                    "✅ **门禁切换**：恐慌出清确认，门禁已对站稳日内 VWAP 的优质标的放宽至**右侧观察窗**，拒绝盲目追高、只做企稳确认！",
                ]
                if send_discord_claw_alert(
                    title,
                    "\n".join(md_lines),
                    webhook_url=claw_webhook_url,
                    dedupe_key=bottom_key,
                    dedupe_max_age=None,
                    color_override=0x00E676,
                ):
                    summary["vix_bottom_sent"] = True
                    summary["sent_keys"].append(bottom_key)
                    if _should_record_signal_ledger(claw_webhook_url or get_claw_webhook_url(), ledger_path):
                        try:
                            import signal_ledger
                            _ql_bot = _build_quote_lookup_map(macro_data=macro_data, stocks_data=stocks_data)
                            signal_ledger.record_benchmark_signals(
                                "VIX_STRATEGY_A_BOTTOM",
                                quote_lookup=_ql_bot,
                                vix_regime=vix_regime,
                                timestamp_et=now_t,
                                ledger_path=ledger_path,
                            )
                        except Exception as _sl_err:
                            print(f"  [!] 记账本写入跳过 (策略A): {_sl_err}")

    # 2. 频道二：🔥 科技/半导体极端去杠杆预警
    df_obj = def_flight if def_flight is not None else ((weather_info or {}).get("defensive_flight") if isinstance(weather_info, dict) else None)
    vxn_key = f"{day_et}:VXN_TECH_DELEVERAGING"
    if _SHARED_ALERTS and shared_alert_state.is_sent(vxn_key, max_age=None):
        summary["skipped_keys"].append(vxn_key)
    elif send_discord_vxn_deleveraging_alert(
        vix_regime=vix_regime,
        def_flight=df_obj,
        update_time_str=now_t,
        webhook_url=def_webhook_url,
        dedupe_day=day_et,
    ):
        summary["vxn_deleveraging_sent"] = True
        summary["sent_keys"].append(vxn_key)
        if _should_record_signal_ledger(def_webhook_url or get_defensive_webhook_url(), ledger_path):
            try:
                import signal_ledger
                _ql_vxn = _build_quote_lookup_map(macro_data=macro_data, stocks_data=stocks_data)
                signal_ledger.record_benchmark_signals(
                    "TECH_DELEVERAGE_ALERT",
                    quote_lookup=_ql_vxn,
                    vix_regime=vix_regime,
                    timestamp_et=now_t,
                    ledger_path=ledger_path,
                )
            except Exception as _sl_err:
                print(f"  [!] 记账本写入跳过 (VXN去杠杆): {_sl_err}")

    return summary


def dispatch_intraday_corridor_divergence_alerts(
    intraday_forecasts,
    macro_data=None,
    stocks_data=None,
    weather_info=None,
    now_et=None,
    session_code=None,
    webhook_url=None,
    ledger_path=None,
):
    """
    频道一（#买点警报大盘趋势提示 / DISCORD_CLAW_WEBHOOK_URL）：
    盘中预测走廊背离预警（严格限定 SPY/QQQ/SOXL 3 只风向标，绝不含 NVDA/SOXS/SQQQ 独立报警）。
    - 时间门禁：严格锁定美东常规盘中 09:45 ~ 15:50 ET，周末/盘前/盘后/夜盘严禁误报；
    - 反向对冲附注：QQQ 自动附带 SQQQ 现价与涨跌幅；SOXL 自动附带 SOXS 现价与涨跌幅；SPY 附带大盘体制与 VIX/VIX3M；
    - 跨设备去重：按 (date_et, ticker, divergence_dir) 锁定，同方向每交易日最多推送 1 次。
    """
    res = {"sent": [], "skipped_dedupe": [], "blocked_reason": ""}

    if now_et is None:
        try:
            from zoneinfo import ZoneInfo
            cur_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            cur_et = datetime.now(timezone(timedelta(hours=-5)))
    else:
        cur_et = now_et

    if cur_et.weekday() >= 5:
        res["blocked_reason"] = "WEEKEND"
        return res
    if session_code is not None and str(session_code).upper() != "REGULAR":
        res["blocked_reason"] = f"SESSION_{str(session_code).upper()}"
        return res

    hm_min = cur_et.hour * 60 + cur_et.minute
    if not (9 * 60 + 45 <= hm_min <= 15 * 60 + 50):
        res["blocked_reason"] = "OUTSIDE_0945_1550_ET"
        return res

    if not isinstance(intraday_forecasts, dict):
        res["blocked_reason"] = "NO_FORECAST_DATA"
        return res

    day_et = cur_et.strftime("%Y-%m-%d")
    doc_date = str(intraday_forecasts.get("forecast_date") or "")
    by_ticker = intraday_forecasts.get("by_ticker")
    if not isinstance(by_ticker, dict) or not by_ticker:
        res["blocked_reason"] = "EMPTY_FORECASTS"
        return res
    if doc_date and doc_date != day_et:
        res["blocked_reason"] = "STALE_FORECAST_DATE"
        return res

    quote_lookup = {}
    if isinstance(stocks_data, (list, tuple)):
        for s in stocks_data:
            if isinstance(s, dict) and s.get("ticker"):
                quote_lookup[s["ticker"]] = s
    elif isinstance(stocks_data, dict):
        quote_lookup.update(stocks_data)
    if isinstance(macro_data, dict):
        quote_lookup.update(macro_data)

    vix_r = (weather_info or {}).get("vix_regime") if isinstance(weather_info, dict) else {}
    if not isinstance(vix_r, dict):
        vix_r = {}
    regime_cn = str((weather_info or {}).get("market_regime_cn") or (weather_info or {}).get("market_regime") or intraday_forecasts.get("market_regime") or "UNKNOWN")

    for tk in INTRADAY_CORRIDOR_ALERT_TICKERS:
        item = by_ticker.get(tk)
        if not isinstance(item, dict) or str(item.get("status") or "").lower() != "ok":
            continue
        item_date = str(item.get("forecast_date") or doc_date or "")
        if item_date != day_et:
            continue

        div = item.get("divergence") or {}
        div_status = str(div.get("status") or "").upper()
        if div_status not in ("BELOW_P25", "ABOVE_P75"):
            continue
        if div.get("latest_slot") is not None:
            try:
                if int(div.get("latest_slot")) < 3:
                    continue
            except Exception:
                pass

        dedupe_key = f"{day_et}:INTRADAY_CORRIDOR:{tk}:{div_status}"
        if _SHARED_ALERTS and shared_alert_state.is_sent(dedupe_key, max_age=None):
            res["skipped_dedupe"].append(dedupe_key)
            continue

        actual_px = div.get("actual_price")
        if actual_px is None:
            actual_px, _ = _extract_live_quote_price_pct(tk, quote_lookup, intraday_forecasts)
        p25 = div.get("slot_p25")
        p50 = div.get("slot_p50")
        p75 = div.get("slot_p75")
        pct_vs_p50 = div.get("pct_vs_p50")
        slot_time = str(div.get("latest_time") or cur_et.strftime("%H:%M"))

        if div_status == "BELOW_P25":
            dir_icon = "📉"
            dir_title = "跌破盘前走廊下沿 (BELOW_P25)"
            action_note = "⚠️ **方向警示**：实际走势跌破盘前历史相似日 P25 下沿，警惕高开低走或趋势性加速走弱，注意防守与对冲！"
            color_hex = 0xF85149
        else:
            dir_icon = "🚀"
            dir_title = "突破盘前走廊上沿 (ABOVE_P75)"
            action_note = "🚀 **方向警示**：实际走势强势突破盘前历史相似日 P75 上沿，多头动能强于历史剧本，可顺势观察强弱延续！"
            color_hex = 0x00E676

        # 反向对冲现价“一鱼两吃”附注 & SPY 大盘体制附注
        if tk == "QQQ":
            sqqq_px, sqqq_pct = _extract_live_quote_price_pct("SQQQ", quote_lookup, intraday_forecasts)
            if sqqq_px is not None:
                sqqq_pct_s = f"（当日 `{sqqq_pct:+.2f}%`）" if sqqq_pct is not None else ""
                footnote = f"🛡️ **反向对冲联动 · `SQQQ` 现价**：**`${sqqq_px:.2f}`**{sqqq_pct_s}"
            else:
                footnote = "🛡️ **反向对冲联动 · `SQQQ`**：实时报价暂缺"
        elif tk == "SOXL":
            soxs_px, soxs_pct = _extract_live_quote_price_pct("SOXS", quote_lookup, intraday_forecasts)
            if soxs_px is not None:
                soxs_pct_s = f"（当日 `{soxs_pct:+.2f}%`）" if soxs_pct is not None else ""
                footnote = f"🛡️ **反向对冲联动 · `SOXS` 现价**：**`${soxs_px:.2f}`**{soxs_pct_s}"
            else:
                footnote = "🛡️ **反向对冲联动 · `SOXS`**：实时报价暂缺"
        else:  # SPY
            vix_s = _fmt_opt_num(vix_r.get("vix_spot"))
            ratio_s = _fmt_opt_num(vix_r.get("vix_term_ratio"))
            footnote = f"🧭 **大盘体制与恐慌读数**：体制 `{regime_cn}` ｜ `^VIX {vix_s}` ｜ `VIX/VIX3M {ratio_s}`"

        pct_p50_s = f"`{float(pct_vs_p50):+.2f}%`" if pct_vs_p50 is not None else "`--`"
        title = f"{dir_icon}【盘中走廊背离】{tk} {dir_title}"
        md_lines = [
            f"### {dir_icon} **{tk} · {dir_title}** (`{day_et} {slot_time} ET`)",
            f"> • **实时现价**：**`${_fmt_opt_num(actual_px)}`**（相对剧本 P50 偏离 {pct_p50_s}）",
            f"> • **盘前走廊区间**：P25 `${_fmt_opt_num(p25)}` ｜ P50 `${_fmt_opt_num(p50)}` ｜ P75 `${_fmt_opt_num(p75)}`",
            f"> • {footnote}",
            "",
            action_note,
        ]
        if send_discord_claw_alert(
            title,
            "\n".join(md_lines),
            webhook_url=webhook_url,
            dedupe_key=dedupe_key,
            dedupe_max_age=None,
            color_override=color_hex,
        ):
            res["sent"].append({"ticker": tk, "status": div_status, "key": dedupe_key})
            if _should_record_signal_ledger(webhook_url or get_claw_webhook_url(), ledger_path) and actual_px is not None:
                try:
                    import signal_ledger
                    sig_t = "CORRIDOR_BREAK_ABOVE" if div_status == "ABOVE_P75" else "CORRIDOR_BREAK_BELOW"
                    signal_ledger.record_signal(
                        signal_type=sig_t,
                        ticker=tk,
                        entry_price=actual_px,
                        timestamp_et=cur_et,
                        vix_at_entry=vix_r.get("vix_spot"),
                        vix_ratio_at_entry=vix_r.get("vix_term_ratio"),
                        meta={"slot_time": slot_time, "pct_vs_p50": pct_vs_p50},
                        ledger_path=ledger_path,
                    )
                except Exception as _sl_err:
                    print(f"  [!] 记账本写入跳过 (盘中走廊 {tk}): {_sl_err}")

    return res


VIX_INTRADAY_REBOUND_STOP_CAPS = {
    "SPY": "-1.2%",
    "QQQ": "-1.2%",
    "SOXL": "-2.5%",
}


def dispatch_vix_intraday_rebound_alerts(
    vix_regime,
    macro_data=None,
    stocks_data=None,
    intraday_map=None,
    weather_info=None,
    now_et=None,
    session_code=None,
    webhook_url=None,
    ledger_path=None,
):
    """
    策略 B：⚡ VIX 盘中真退潮 · 右侧博反抽（倒挂期绝对禁多）
    仅面向 SPY / QQQ / SOXL，路由至频道一（#买点警报大盘趋势提示 / DISCORD_CLAW_WEBHOOK_URL）。
    必须同时满足 5 道硬门禁：
    1) 严格倒挂禁令：vix_term_ratio < 1.00 且 vix_state != 'BACKWARDATION_BRAKE'（倒挂期绝对禁多）；
    2) 真实恐慌背景 + 当日真退潮：(vix_10d_peak >= 24.0 或 vix_term_ratio_10d_peak >= 1.00) 且当日 ^VIX 涨跌幅 <= -5.0%；
    3) 盘中时间窗口：严格锁定美东常规盘中 09:45 ~ 15:00 ET (REGULAR)；
    4) 微观买盘共振：VIXY 最新价 < 自身日内 VWAP，且目标标的 (SPY/QQQ/SOXL) 最新价 > 自身日内 VWAP 且最近 5m K 线收阳；
    5) 跨设备去重：写入 shared_alert_state.py 去重键 {date_et}:VIX_INTRADAY_REBOUND:{ticker}（每标的每日最多 1 次）。
    """
    res = {"sent": [], "skipped_dedupe": [], "blocked_reason": ""}

    if now_et is None:
        try:
            from zoneinfo import ZoneInfo
            cur_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            cur_et = datetime.now(timezone(timedelta(hours=-5)))
    else:
        cur_et = now_et

    # 门禁 3：美东常规盘中 09:45 ~ 15:00 ET
    if cur_et.weekday() >= 5:
        res["blocked_reason"] = "WEEKEND"
        return res
    if session_code is not None and str(session_code).upper() != "REGULAR":
        res["blocked_reason"] = f"SESSION_{str(session_code).upper()}"
        return res
    hm_min = cur_et.hour * 60 + cur_et.minute
    if not (9 * 60 + 45 <= hm_min <= 15 * 60):
        res["blocked_reason"] = "OUTSIDE_0945_1500_ET"
        return res

    vr = vix_regime if isinstance(vix_regime, dict) else ((weather_info or {}).get("vix_regime") if isinstance(weather_info, dict) else {})
    if not isinstance(vr, dict) or vr.get("vix_spot") is None:
        res["blocked_reason"] = "VIX_UNAVAILABLE"
        return res

    # 门禁 1：严格倒挂禁令（vix_term_ratio < 1.00 且 vix_state != BACKWARDATION_BRAKE）
    vix_state = str(vr.get("vix_state") or "").upper()
    if vix_state in ("UNAVAILABLE", "BACKWARDATION_BRAKE") or bool(vr.get("vix_hard_brake", False)):
        res["blocked_reason"] = "BACKWARDATION_BRAKE_ACTIVE"
        return res
    try:
        ratio_val = float(vr.get("vix_term_ratio")) if vr.get("vix_term_ratio") is not None else float("nan")
    except Exception:
        ratio_val = float("nan")
    if not math.isfinite(ratio_val) or ratio_val >= 1.00:
        res["blocked_reason"] = "BACKWARDATION_RATIO_GE_1"
        return res

    # 门禁 2：真实恐慌背景（10日峰值 >=24 或 期限比峰值 >=1.00）+ 当日 ^VIX 真退潮 (<= -5.0%)
    try:
        vix_peak = float(vr.get("vix_10d_peak")) if vr.get("vix_10d_peak") is not None else 0.0
    except Exception:
        vix_peak = 0.0
    try:
        ratio_peak = float(vr.get("vix_term_ratio_10d_peak")) if vr.get("vix_term_ratio_10d_peak") is not None else 0.0
    except Exception:
        ratio_peak = 0.0
    if not (vix_peak >= 24.0 or ratio_peak >= 1.00):
        res["blocked_reason"] = "NO_10D_PANIC_BACKGROUND"
        return res

    raw_vix_pct = vr.get("vix_pct")
    if raw_vix_pct is None and vr.get("vix_spot") is not None and vr.get("vix_prev") is not None:
        try:
            _vp = float(vr.get("vix_prev"))
            if _vp > 0:
                raw_vix_pct = ((float(vr.get("vix_spot")) - _vp) / _vp) * 100.0
        except Exception:
            raw_vix_pct = None
    try:
        vix_pct_val = float(raw_vix_pct) if raw_vix_pct is not None else float("nan")
    except Exception:
        vix_pct_val = float("nan")
    if not math.isfinite(vix_pct_val) or vix_pct_val > -5.0:
        res["blocked_reason"] = "VIX_NOT_COLLAPSING_5PCT"
        return res

    # 构建行情与日内微观指标查询表
    quote_lookup = {}
    if isinstance(stocks_data, (list, tuple)):
        for s in stocks_data:
            if isinstance(s, dict) and s.get("ticker"):
                quote_lookup[s["ticker"]] = s
    elif isinstance(stocks_data, dict):
        quote_lookup.update(stocks_data)
    if isinstance(macro_data, dict):
        quote_lookup.update(macro_data)

    def _get_micro_state(sym):
        q = quote_lookup.get(sym) if isinstance(quote_lookup.get(sym), dict) else {}
        im = (intraday_map or {}).get(sym) if isinstance((intraday_map or {}).get(sym), dict) else {}
        px = None
        for k in ("active_price", "last", "rth_close", "ext_price", "price"):
            v = q.get(k) if k in q else im.get(k)
            try:
                if v is not None and math.isfinite(float(v)) and float(v) > 0:
                    px = float(v)
                    break
            except Exception:
                continue
        vwap_v = None
        for src in (q, im):
            try:
                v = src.get("vwap")
                if v is not None and math.isfinite(float(v)) and float(v) > 0:
                    vwap_v = float(v)
                    break
            except Exception:
                continue
        if "is_5m_green" in q:
            green_v = bool(q.get("is_5m_green"))
        elif "is_5m_green" in im:
            green_v = bool(im.get("is_5m_green"))
        else:
            green_v = False
        return px, vwap_v, green_v

    # 门禁 4A：VIXY 最新价必须位于自身日内 VWAP 下方
    vixy_px, vixy_vwap, _ = _get_micro_state("VIXY")
    if vixy_px is None or vixy_vwap is None or not (vixy_px < vixy_vwap):
        res["blocked_reason"] = "VIXY_NOT_BELOW_VWAP"
        return res

    day_et = cur_et.strftime("%Y-%m-%d")
    time_et_str = cur_et.strftime("%H:%M")
    vix_spot_val = float(vr.get("vix_spot"))

    for tk in INTRADAY_CORRIDOR_ALERT_TICKERS:
        tk_px, tk_vwap, tk_green = _get_micro_state(tk)
        # 门禁 4B：目标标的站稳自身日内 VWAP 上方 且 最近 5m K 线收阳
        if tk_px is None or tk_vwap is None or not (tk_px > tk_vwap and tk_green):
            continue

        # 门禁 5：跨设备去重锁 {date_et}:VIX_INTRADAY_REBOUND:{ticker}
        dedupe_key = f"{day_et}:VIX_INTRADAY_REBOUND:{tk}"
        if _SHARED_ALERTS and shared_alert_state.is_sent(dedupe_key, max_age=None):
            res["skipped_dedupe"].append(dedupe_key)
            continue

        stop_cap = VIX_INTRADAY_REBOUND_STOP_CAPS.get(tk, "-1.2%")
        stop_px = round(tk_px * (1.0 - abs(float(stop_cap.rstrip("%"))) / 100.0), 2)
        vwap_diff_pct = ((tk_px - tk_vwap) / tk_vwap) * 100.0 if tk_vwap > 0 else 0.0
        vixy_diff_pct = ((vixy_px - vixy_vwap) / vixy_vwap) * 100.0 if vixy_vwap > 0 else 0.0

        title = f"⚡ VIX 盘中真退潮 · 右侧窄止损博反抽（{tk} · 轻仓看R1·严禁隔夜）"
        md_lines = [
            f"### ⚡ **VIX 盘中真退潮 · 右侧窄止损博反抽（轻仓看R1·严禁隔夜）**",
            f"> • **触发标的**：**`{tk}`**（`{day_et} {time_et_str} ET`）｜ **现价**：**`${tk_px:.2f}`** ＞ **日内 VWAP**：`${tk_vwap:.2f}` (`{vwap_diff_pct:+.2f}%` · 5m收阳确认)",
            f"> • **恐慌真退潮**：**`^VIX` `{vix_spot_val:.2f}`**（当日暴跌 **`{vix_pct_val:+.2f}%`** ≤ `-5.0%` ｜ 10日峰值 `{vix_peak:.2f}`）",
            f"> • **期限解除倒挂**：**`VIX/VIX3M` `{ratio_val:.3f}`** ＜ `1.00` ｜ **`VIXY` 跌破 VWAP**：`${vixy_px:.2f}` ＜ `${vixy_vwap:.2f}` (`{vixy_diff_pct:+.2f}%`)",
            f"> • **现行窄止损上限参考**：**`{tk}: {stop_cap}`**（硬止损参考价 `${stop_px:.2f}` ｜ 基准上限 `SPY/QQQ: -1.2%`、`SOXL: -2.5%`）",
            "",
            "🛡️ **实战纪律**：仅在倒挂解除（`VIX/VIX3M < 1.00`）且 `^VIX` 当日真退潮（`≤ -5%`）时博弈右侧反抽；**轻仓看 R1 · 严禁隔夜**，跌破 VWAP 或触及窄止损无条件离场，尾盘 `15:50 ET` 前落袋！",
        ]
        if send_discord_claw_alert(
            title,
            "\n".join(md_lines),
            webhook_url=webhook_url,
            dedupe_key=dedupe_key,
            dedupe_max_age=None,
            color_override=0x00E676,
        ):
            res["sent"].append({"ticker": tk, "key": dedupe_key, "stop_cap": stop_cap})
            if _should_record_signal_ledger(webhook_url or get_claw_webhook_url(), ledger_path):
                try:
                    import signal_ledger
                    signal_ledger.record_signal(
                        signal_type="VIX_STRATEGY_B_REBOUND",
                        ticker=tk,
                        entry_price=tk_px,
                        timestamp_et=cur_et,
                        vix_at_entry=vr.get("vix_spot"),
                        vix_ratio_at_entry=vr.get("vix_term_ratio"),
                        meta={"stop_cap": stop_cap, "vix_pct": round(vix_pct_val, 2)},
                        ledger_path=ledger_path,
                    )
                except Exception as _sl_err:
                    print(f"  [!] 记账本写入跳过 (策略B {tk}): {_sl_err}")

    return res


_DEFENSIVE_FLIGHT_PUSH_STATE = os.path.join(SCRATCH_DIR, ".defensive_flight_push_state.json")
_DEFENSIVE_FLIGHT_CORE_SYMBOLS = ["KO", "PEP", "MCD", "LLY", "MRNA", "TLT", "GLD", "VIXY", "SMH", "SOXL", "NVDA", "QQQ"]


def _defensive_component_pct(lv):
    try:
        if lv.get("effective_pct") is not None:
            return float(lv.get("effective_pct"))
        if lv.get("ext_price", 0) > 0:
            return float(lv.get("ext_pct", 0.0))
        return float(lv.get("pct_change", lv.get("change_pct", 0.0)))
    except Exception:
        return 0.0


def _load_defensive_flight_state():
    try:
        if not os.path.exists(_DEFENSIVE_FLIGHT_PUSH_STATE):
            return {}
        data = json.load(open(_DEFENSIVE_FLIGHT_PUSH_STATE, encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # migrate old flat state to day bucket
        if "last_score" in data:
            data = {"day": data}
        return data
    except Exception:
        return {}


def _defensive_flight_current_components(df_alert, live_lookup):
    if df_alert.get("score_source") == "futures_proxy":
        return {s: float(v.get("pct", 0.0)) for s, v in (df_alert.get("components", {}) or {}).items()}
    return {s: _defensive_component_pct(live_lookup.get(s, {})) for s in _DEFENSIVE_FLIGHT_CORE_SYMBOLS if s in (live_lookup or {})}


def should_send_defensive_flight_alert(df_alert, live_lookup, session_code="REGULAR"):
    """防刷屏：Score 变化 >=0.10%p 或核心成分变化 >=0.5%；夜盘/白天状态分离。"""
    try:
        if df_alert.get("skip_reason"):
            return False
        score = float(df_alert.get("flight_score", 0.0))
        comps = _defensive_flight_current_components(df_alert, live_lookup)
        key = "overnight" if session_code == "OVERNIGHT" else "day"
        state = _load_defensive_flight_state()
        prev = state.get(key) or {}
        if prev.get("last_score") is None:
            return True
        if abs(score - float(prev.get("last_score", 0.0))) >= 0.10:
            return True
        prev_comps = prev.get("components", {}) or {}
        for s, p in comps.items():
            if abs(p - float(prev_comps.get(s, 0.0))) >= 0.50:
                return True
        return False
    except Exception:
        return True


def record_defensive_flight_sent(df_alert, live_lookup, session_code="REGULAR"):
    try:
        comps = {s: round(float(p), 4) for s, p in _defensive_flight_current_components(df_alert, live_lookup).items()}
        key = "overnight" if session_code == "OVERNIGHT" else "day"
        state = _load_defensive_flight_state()
        state[key] = {"last_score": round(float(df_alert.get("flight_score", 0.0)), 4), "components": comps,
                      "session": session_code, "sent_at": now_et_str("%Y-%m-%d %H:%M:%S")}
        json.dump(state, open(_DEFENSIVE_FLIGHT_PUSH_STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass


def send_pushplus_wechat_alert(title, markdown_content, channel=None):
    """
    通过 PushPlus API 向微信推送 Markdown 卡片消息，并同步镜像推送至 Discord 预警频道
    采用【微信 + Discord 冗余直达体系】：
    1. 推送至 ClawBot 微信机器人专属私聊通道与官方服务号
    2. 同步推送到 Discord 预警镜像频道 (微信发什么，此频道同步发什么)
    """
    if _external_alerts_disabled():
        print(f"  [*] [Dry-Run Guard] 已屏蔽微信/Claw推送: {title}")
        return False

    # 0. 同步推送到 Discord 预警镜像频道 (零延迟并行镜像)
    discord_success = False
    try:
        discord_success = send_discord_claw_alert(title, markdown_content)
    except Exception as e:
        print(f"  [!] 💬 Discord 预警镜像触发异常: {e}")

    if not PUSHPLUS_TOKEN or PUSHPLUS_TOKEN == "YOUR_TOKEN":
        return discord_success
    
    url = "https://www.pushplus.plus/send"
    target_channel = channel or globals().get("PUSHPLUS_CHANNEL", "clawbot")
    
    # 建立双通道分发列表
    channels_to_push = []
    if target_channel and target_channel.lower() != "wechat":
        channels_to_push.append(target_channel)
    channels_to_push.append("wechat")  # 官方服务号兜底保底通道

    any_success = False
    for ch in channels_to_push:
        payload = {
            "token": PUSHPLUS_TOKEN,
            "title": title,
            "content": markdown_content,
            "template": "markdown",
            "channel": ch
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                if data.get("code") == 200:
                    ch_desc = "ClawBot机器人" if ch == "clawbot" else "微信服务号"
                    print(f"  [+] 📱 微信[{ch_desc}:{ch}]卡片推送成功: {title}")
                    any_success = True
                else:
                    print(f"  [!] 📱 微信[{ch}]推送返回错误: {data.get('msg')} (数据: {data.get('data')})")
        except Exception as e:
            print(f"  [!] 📱 微信[{ch}]推送网络异常: {e}")

    _dedupe_key = _shared_dedupe_key(title, markdown_content)
    _already_sent_discord = bool(_dedupe_key and _SHARED_ALERTS and shared_alert_state.is_sent(_dedupe_key, max_age=600))
    return bool(any_success or discord_success or _already_sent_discord)

_CANDIDATE_SPLIT_RATIOS = (
    2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0, 25.0, 50.0,
    1.5, 0.5, 1.0 / 3.0, 0.25, 0.2, 0.125, 0.1, 0.05, 0.04, 0.02,
)


def should_suppress_stop_loss_alert(item):
    """阶段一拆股/异常跳变防误报：未除权对齐或单日暴跌 <= -40% 时禁止触发虚假破位止损弹窗。"""
    if not isinstance(item, dict):
        return False
    if bool(item.get("split_suspect_unhealed", False)):
        return True
    try:
        pct_val = float(item.get("pct_change", 0.0) or 0.0)
    except Exception:
        pct_val = 0.0
    try:
        ext_val = float(item.get("ext_pct", 0.0) or 0.0)
    except Exception:
        ext_val = 0.0
    return bool(pct_val <= -40.0 or ext_val <= -40.0)


def _calc_bias_ma20(df_hist, live_px):
    try:
        if df_hist is None or len(df_hist) < 20 or "Close" not in df_hist.columns:
            return 0.0
        closes = pd.to_numeric(df_hist["Close"], errors="coerce").dropna()
        if len(closes) < 20:
            return 0.0
        ma20 = float(closes.iloc[-20:].mean())
        if ma20 <= 0:
            return 0.0
        return round(((float(live_px) - ma20) / ma20) * 100.0, 2)
    except Exception:
        return 0.0


def _normalize_ai_levels_for_price(ai_entry, live_px, split_meta=None):
    """若 AI JSON 中的 S1/R1 仍停留在拆股前量纲，则按拆股因子或合理阈值同步归一化。"""
    if not isinstance(ai_entry, dict):
        return None, None, None
    s1 = ai_entry.get("s1")
    r1 = ai_entry.get("r1")
    cwr = ai_entry.get("cal_win_rate")
    try:
        px = float(live_px or 0.0)
    except Exception:
        px = 0.0
    if px <= 0:
        return s1, r1, cwr

    sf = float((split_meta or {}).get("split_factor", 1.0) or 1.0)

    def _scale_lv(val):
        if val is None:
            return None
        try:
            fv = float(val)
        except Exception:
            return None
        if fv <= 0:
            return fv
        ratio = fv / px
        if ratio <= 0.60 or ratio >= 1.67:
            if sf != 1.0:
                cand = fv / sf
                if 0.60 < (cand / px) < 1.67:
                    return round(cand, 2)
            best_r = min(_CANDIDATE_SPLIT_RATIOS, key=lambda r: abs(ratio - r) / r)
            if abs(ratio - best_r) / best_r <= 0.25:
                return round(fv / best_r, 2)
            return None
        return fv

    return _scale_lv(s1), _scale_lv(r1), cwr


def reconcile_live_quote_with_split_guard(ticker, df_hist, rq=None, allow_network_on_jump=True):
    """
    阶段一核心防护：对齐实盘报价与本地历史序列的拆股/合股跳变 (如 SNDK 1拆8)。
    正常无 ±40% 跳变时 0 网络开销直接放行；
    一旦触发 ±40% 跳变，立即调用 ml_predictor_5y.detect_and_heal_stock_split 自愈复权历史与实时涨跌幅。
    """
    default_meta = {
        "split_detected": False,
        "split_factor": 1.0,
        "healed_rows": 0,
        "split_suspect_unhealed": False,
        "reason": "",
    }
    if ticker in ("ANT", "BTC-USD", "XAUUSD") or df_hist is None or len(df_hist) == 0 or "Close" not in df_hist.columns:
        return df_hist, rq, default_meta

    closes = pd.to_numeric(df_hist["Close"], errors="coerce").dropna()
    if len(closes) == 0:
        return df_hist, rq, default_meta

    live_px = None
    if isinstance(rq, dict):
        try:
            lp = float(rq.get("last") or 0.0)
            if lp > 0 and math.isfinite(lp):
                live_px = lp
        except Exception:
            live_px = None

    has_jump = False
    if len(closes) >= 2:
        tail_arr = closes.iloc[-6:].to_numpy(dtype=float)
        ratios = tail_arr[1:] / np.maximum(tail_arr[:-1], 1e-9)
        if np.any((ratios <= 0.60) | (ratios >= 1.67)):
            has_jump = True

    last_hist_c = float(closes.iloc[-1])
    if not has_jump and live_px is not None and last_hist_c > 0:
        live_ratio = live_px / last_hist_c
        if live_ratio <= 0.60 or live_ratio >= 1.67:
            has_jump = True

    if not has_jump:
        return df_hist, rq, default_meta

    try:
        import ml_predictor_5y
        df_healed, meta = ml_predictor_5y.detect_and_heal_stock_split(
            ticker=ticker,
            df_hist=df_hist,
            live_price=live_px,
            allow_network=allow_network_on_jump,
        )
    except Exception:
        return df_hist, rq, default_meta

    rq_out = dict(rq) if isinstance(rq, dict) else rq
    if isinstance(rq_out, dict) and live_px is not None:
        sf = float(meta.get("split_factor", 1.0) or 1.0)
        if meta.get("split_detected") and sf != 1.0:
            try:
                old_chg = float(rq_out.get("change") or 0.0)
                old_pct = float(rq_out.get("pct_change") or 0.0)
            except Exception:
                old_chg, old_pct = 0.0, 0.0

            implied_pc = live_px - old_chg
            adj_pc = None
            if implied_pc > 0 and (implied_pc / live_px >= 1.67 or live_px / implied_pc >= 1.67):
                adj_pc = implied_pc / sf
            elif abs(old_pct) >= 35.0 and df_healed is not None and len(df_healed) >= 1 and "Close" in df_healed.columns:
                hc = pd.to_numeric(df_healed["Close"], errors="coerce").dropna()
                if len(hc) >= 2:
                    last_dt = pd.to_datetime(hc.index[-1]).date() if hasattr(hc.index[-1], "date") else None
                    et_today = datetime.now(timezone(timedelta(hours=-4))).date()
                    adj_pc = float(hc.iloc[-2]) if (last_dt == et_today) else float(hc.iloc[-1])
                elif len(hc) == 1:
                    adj_pc = float(hc.iloc[-1])

            if adj_pc is not None and adj_pc > 0:
                rq_out["change"] = round(live_px - adj_pc, 2)
                rq_out["pct_change"] = round(((live_px - adj_pc) / adj_pc) * 100.0, 2)

            for k_hl in ("high", "low"):
                try:
                    hv = float(rq_out.get(k_hl) or 0.0)
                    if hv > 0 and (hv / live_px >= 1.67 or live_px / hv >= 1.67):
                        rq_out[k_hl] = round(hv / sf, 2)
                except Exception:
                    pass

    return df_healed, rq_out, meta


_EXT_SPLIT_CANDIDATES = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0, 20.0)


def _match_split_factor_value(raw_mult, explicit_factor=None):
    try:
        mv = float(raw_mult)
    except Exception:
        return None
    if not (math.isfinite(mv) and mv > 1.0):
        return None
    if explicit_factor is not None:
        try:
            ef = float(explicit_factor)
            eff = ef if ef >= 1.0 else (1.0 / ef if ef > 0 else 0.0)
            if eff > 1.0 and abs(mv - eff) / eff <= 0.22:
                return eff
        except Exception:
            pass
    best = min(_EXT_SPLIT_CANDIDATES, key=lambda r: abs(mv - r) / r)
    rel_err = abs(mv - best) / best
    tol = 0.10 if best == 2.0 else 0.18
    return float(best) if rel_err <= tol else None


def _scale_intra_split_fields(intra_info, ref_px, factor, is_forward_split=True):
    if not isinstance(intra_info, dict) or ref_px <= 0 or factor <= 1.0:
        return
    for _k in ("vwap", "rth_low", "rth_high", "rth_close", "rth_open", "pmh", "pml", "low_5m"):
        try:
            _v = float(intra_info.get(_k) or 0.0)
        except Exception:
            continue
        if _v <= 0 or not math.isfinite(_v):
            continue
        if is_forward_split and (_v / ref_px) >= 1.67:
            intra_info[_k] = round(_v / factor, 2)
        elif (not is_forward_split) and (ref_px / _v) >= 1.67:
            intra_info[_k] = round(_v * factor, 2)


def reconcile_extended_quote_with_split_guard(
    ticker,
    last_price,
    ext_price,
    df_hist=None,
    split_meta=None,
    intra_info=None,
):
    """
    盘前/盘后/夜盘拆股断层对齐与异常涨跌幅防护 (如 SNDK 1 拆 8 导致昨收未除权 vs 盘前已除权 -88.33% 假暴跌)：
    返回 (aligned_last, aligned_ext, ext_change, ext_pct, healed, reason)。
    """
    try:
        lp = float(last_price or 0.0)
        ep = float(ext_price or 0.0)
    except Exception:
        return last_price, ext_price, 0.0, 0.0, False, ""

    if ticker in ("ANT", "BTC-USD", "XAUUSD") or lp <= 0 or ep <= 0 or not (math.isfinite(lp) and math.isfinite(ep)):
        return lp, ep, 0.0, 0.0, False, ""

    raw_ratio = ep / lp
    if 0.40 < raw_ratio < 2.50:
        ext_chg = ep - lp
        ext_pct = (ext_chg / lp) * 100.0
        return lp, ep, ext_chg, ext_pct, False, ""

    explicit_sf = None
    if isinstance(split_meta, dict) and split_meta.get("split_detected"):
        try:
            sf_val = float(split_meta.get("split_factor") or 1.0)
            if sf_val > 0 and sf_val != 1.0:
                explicit_sf = sf_val
        except Exception:
            explicit_sf = None

    # 1. 若本地历史近期已复权 (last_hist_close 与盘前价处于同量纲，而 last 处于旧量纲)，优先用复权收盘价对齐昨收
    hist_c = None
    if df_hist is not None and len(df_hist) > 0 and "Close" in df_hist.columns:
        try:
            hc_ser = pd.to_numeric(df_hist["Close"], errors="coerce").dropna()
            if len(hc_ser) >= 2:
                # 倒数第二根常为未覆盖实时值的昨收
                cands = [float(hc_ser.iloc[-1]), float(hc_ser.iloc[-2])]
            elif len(hc_ser) == 1:
                cands = [float(hc_ser.iloc[-1])]
            else:
                cands = []
            for cand in cands:
                if cand > 0 and 0.60 <= (ep / cand) <= 1.67 and (lp / cand >= 1.67 or cand / lp >= 1.67):
                    hist_c = round(cand, 2)
                    break
        except Exception:
            hist_c = None

    if hist_c is not None and hist_c > 0:
        sf_est = _match_split_factor_value(max(lp / hist_c, hist_c / lp), explicit_factor=explicit_sf)
        if sf_est and lp > hist_c * 1.67:
            _scale_intra_split_fields(intra_info, ep, sf_est, is_forward_split=True)
        adj_chg = round(ep - hist_c, 2)
        adj_pct = round((adj_chg / hist_c) * 100.0, 2)
        return hist_c, ep, adj_chg, adj_pct, True, "ext_aligned_to_adjusted_hist"

    # 2. 拆股比例匹配 (例如 320.0 -> 37.34 命中 8x 拆股因子)
    inv_mult = (lp / ep) if raw_ratio <= 0.40 else (ep / lp)
    sf = _match_split_factor_value(inv_mult, explicit_factor=explicit_sf)
    if sf is not None and sf > 1.0:
        if raw_ratio <= 0.40:
            adj_last = round(lp / sf, 2)
            _scale_intra_split_fields(intra_info, ep, sf, is_forward_split=True)
            adj_chg = round(ep - adj_last, 2)
            adj_pct = round((adj_chg / adj_last) * 100.0, 2) if adj_last > 0 else 0.0
            return adj_last, ep, adj_chg, adj_pct, True, f"ext_split_aligned_{sf:g}x"
        else:
            # raw_ratio >= 2.50: 常见为 df_hist/last 已除权而盘前数据源仍报未除权旧价，或合股反转
            adj_ext = round(ep / sf, 2)
            adj_chg = round(adj_ext - lp, 2)
            adj_pct = round((adj_chg / lp) * 100.0, 2) if lp > 0 else 0.0
            return lp, adj_ext, adj_chg, adj_pct, True, f"ext_price_deinflated_{sf:g}x"

    # 3. 无法确认合理拆股因子的极端 >60% / +150% 毛刺跳变 -> 拦截扩展报价防自选榜假暴跌
    return lp, 0.0, 0.0, 0.0, True, "extreme_ext_cliff_suppressed"


def dispatch_wechat_radar_alerts(macro_data, stocks_data, weather_info, now_str, ledger_path=None):
    """
    智能去重微信预警分发引擎：
    1. 扫描 🎯自选盯盘5m日内买点触发、🟢黄金坑就位、🔴破位止损、🟣止盈卖出、INTC 重点防线 及 宏观气象异动
    2. 校验 .radar_alert_history.json:
       - 状态首次出现 / 改变 -> 立即通知
       - 持续同状态 -> 冷却满 2~4 小时才进行再次提醒 (杜绝每 3 分钟轰炸)
    3. 合并全量异动，单条 Markdown 汇总卡片推送
    """
    history = load_alert_history()
    targets_history = history.setdefault("targets", {})
    current_time = time.time()
    target_watchlist = load_target_watchlist()

    watchlist_entry_alerts = []
    pit_alerts = []
    stop_alerts = []
    tp_alerts = []
    intc_alerts = []
    pending_target_updates = {}

    # 1. 扫描全池个股及宏观/行业 ETF 重大异动 (全池均开放 5m 日内买点推送，不再限制自选盯盘)
    all_etfs_list = [{"ticker": t, **m} for t, m in macro_data.items()]
    for s in list(stocks_data) + all_etfs_list:
        ticker = s["ticker"]
        name = s["name"]
        cat = s["cat"]
        last = s["last"]
        active_price = s.get("active_price", last)
        ext_price = s.get("ext_price", 0.0)
        ext_pct = s.get("ext_pct", 0.0)
        session_tag = s.get("session_tag", "")
        bias = s["bias"]
        rsi = s.get("rsi", 0.0)
        rsi_5m = s.get("rsi_5m") if s.get("rsi_5m") is not None else 50.0
        rvol_5m = s.get("rvol_5m") if s.get("rvol_5m") is not None else 1.0
        rs_20d = s.get("rs_20d") if s.get("rs_20d") is not None else 0.0
        earnings_tag = s.get("earnings_tag", "")
        vwap = s.get("vwap", last)
        dist_pit = s.get("dist_to_pit", 0.0)
        pit_status = s.get("pit_status", "")
        fractional = s.get("fractional_shares", 0.0)
        fib_low = s.get("fib_low", 0.0)
        fib_high = s.get("fib_high", 0.0)
        pct_change = s.get("pct_change", 0.0)

        optimal_limit = s.get("optimal_limit", round(fib_low + 0.382 * (fib_high - fib_low), 2) if fib_high > fib_low else active_price)
        limit_note = s.get("limit_note", "黄金坑支撑限价单")
        intraday_ready = s.get("intraday_entry_ready", False)
        intraday_reason = s.get("intraday_reason", "")
        stop_loss = s.get("entry_stop_loss", round(optimal_limit * 0.96, 2))
        batch1_shares = round(fractional * 0.30, 2)
        batch1_val = round(batch1_shares * optimal_limit, 2)

        is_watched = ticker in target_watchlist
        is_obs_only = ticker in {"JPM", "XOM", "WMT", "CAT", "NASA"}
        is_pit = s.get("is_pit", False) and (not is_obs_only)
        is_stop = s.get("is_stop", False)
        is_etf = ticker in macro_data

        # 当前核心技术状态 (全池 5m 买点共振、黄金坑、技术止损、冲顶止盈；低波观察池不触发高弹性买点弹窗)
        current_state = "NORMAL"
        if is_stop and not is_etf and not is_obs_only and not should_suppress_stop_loss_alert(s):
            current_state = "STOP_LOSS"
        elif intraday_ready and not is_stop and not is_obs_only:
            current_state = "WATCHLIST_ENTRY_READY"
        elif is_pit and not is_etf and not s.get("is_crash_halt") and not s.get("is_cooling_halt"):
            current_state = "GOLDEN_PIT"
        elif s.get("is_take_profit") and not is_etf and not is_obs_only:
            current_state = "TAKE_PROFIT"

        # 1. 客观技术面报警判定 (自选建仓就位享受 2 小时冷却，其它状态 4 小时冷却)
        prev_record = targets_history.get(ticker, {})
        prev_state = prev_record.get("state", "NORMAL")
        last_notified_at = prev_record.get("last_notified_at", 0.0)
        cooldown = ENTRY_COOLDOWN_SECONDS if current_state == "WATCHLIST_ENTRY_READY" else ALERT_COOLDOWN_SECONDS

        should_alert = False
        if current_state != "NORMAL":
            if current_state != prev_state:
                should_alert = True  # 状态跃迁，立即报警
            elif (current_time - last_notified_at) >= cooldown:
                should_alert = True  # 持续异常且满冷却周期，定期提醒

        # 若恢复为 NORMAL，及时清空历史警报状态
        if current_state == "NORMAL" and prev_state != "NORMAL":
            targets_history[ticker] = {
                "state": "NORMAL",
                "last_notified_at": 0.0
            }

        if should_alert:
            if current_state == "WATCHLIST_ENTRY_READY":
                watchlist_entry_alerts.append({
                    "ticker": ticker, "name": name, "cat": cat, "last": last,
                    "active_price": active_price, "ext_price": ext_price, "ext_pct": ext_pct,
                    "bias": bias, "rsi": rsi, "rsi_5m": rsi_5m, "rvol_5m": rvol_5m,
                    "rs_20d": rs_20d, "earnings_tag": earnings_tag, "vwap": vwap,
                    "fib_low": fib_low, "fib_high": fib_high,
                    "optimal_limit": optimal_limit, "limit_note": limit_note,
                    "intraday_reason": intraday_reason,
                    "batch1_shares": batch1_shares,
                    "batch1_val": batch1_val, "stop_loss": stop_loss, "pit_status": pit_status,
                    "pct_change": pct_change, "session_tag": session_tag,
                    "has_options": s.get("has_options", False),
                    "options_exp": s.get("options_exp", ""),
                    "pcr": s.get("pcr", 0.0),
                    "dollar_pcr": s.get("dollar_pcr", 0.0),
                    "pcr_sentiment": s.get("pcr_sentiment", ""),
                    "top_call_info": s.get("top_call_info", "--"),
                    "top_put_info": s.get("top_put_info", "--"),
                    "exp_move_str": s.get("exp_move_str", "--")
                })
                pending_target_updates[ticker] = {"state": current_state, "last_notified_at": current_time}
            elif current_state == "GOLDEN_PIT":
                pit_alerts.append({
                    "ticker": ticker, "name": name, "cat": cat, "last": last,
                    "bias": bias, "rsi": rsi, "fib_low": fib_low, "fib_high": fib_high,
                    "fractional": fractional, "pit_status": pit_status,
                    "stop_loss": stop_loss, "low_price": s.get("low_price", s.get("rth_low", last * 0.985)),
                    "rth_low": s.get("rth_low", s.get("low_price", last * 0.985)),
                    "optimal_limit": s.get("optimal_limit", round(last * 1.020, 2))
                })
                pending_target_updates[ticker] = {"state": current_state, "last_notified_at": current_time}
            elif current_state == "STOP_LOSS":
                if bias < -15.0 and s.get("vol_ratio", 1.0) >= 1.5:
                    stop_alerts.append({
                        "ticker": ticker, "name": name, "cat": cat, "last": last,
                        "bias": bias, "rsi": rsi, "fib_low": fib_low, "pit_status": pit_status
                    })
                    pending_target_updates[ticker] = {"state": current_state, "last_notified_at": current_time}
            elif current_state == "TAKE_PROFIT":
                tp_alerts.append({
                    "ticker": ticker, "name": name, "cat": cat, "last": last,
                    "bias": bias, "rsi": rsi, "reason": s.get("tp_reason", "")
                })
                pending_target_updates[ticker] = {"state": current_state, "last_notified_at": current_time}

        # 2. 实盘持仓专属风控底线监测 (硬止损、动态保本、水下强阻与对冲收割)
        if s.get("shares", 0) > 0:
            cost = s.get("cost", 0.0)
            base_stop = s.get("base_val", 88.00)
            target = s.get("target", cost)
            shares = s.get("shares", 0.0)
            ma50 = s.get("ma50", 0.0)
            fib_low = s.get("fib_low", cost)
            ema5 = s.get("ema5", 0.0)
            atr = s.get("atr", 0.0)
            pnl_pct = ((active_price - cost) / cost) * 100 if cost > 0 else 0.0

            # 动态保本铁盾 (Breakeven Shield，基于历史最高价持久记忆)
            position_peaks = history.setdefault("position_peaks", {})
            saved_peak = float(position_peaks.get(ticker, cost))
            today_high = float(s.get("high", active_price))
            peak_price = max(saved_peak, float(cost), float(active_price), today_high)
            position_peaks[ticker] = round(peak_price, 4)
            s["peak_price"] = peak_price
            max_pnl_pct = ((peak_price - cost) / cost) * 100 if cost > 0 else 0.0
            s["max_pnl_pct"] = max_pnl_pct

            be_active = False
            be_stop = cost * 1.004 if ticker in ["SOXS", "SQQQ"] else cost * 1.005
            lock_label = "保本盾"
            if ticker in ["SOXS", "SQQQ"]:
                if max_pnl_pct >= 4.0:
                    be_active = True
            else:
                # 对齐 V2.4/V3.1 动态保本铁盾激活门槛: 浮盈达到 +4.2% 或 2.0*ATR
                if max_pnl_pct >= 4.2 or (atr > 0 and peak_price >= cost + 2.0 * atr):
                    be_active = True
                # V3.1 终极版阶梯锁利: 浮盈 >= 8% 锁利 +2.5%, 浮盈 >= 14% 锁利 +6.0%
                if be_active:
                    if max_pnl_pct >= 14.0:
                        be_stop = max(be_stop, round(cost * 1.060, 4))
                        lock_label = "阶梯锁利(+6.0%)"
                    elif max_pnl_pct >= 8.0:
                        be_stop = max(be_stop, round(cost * 1.025, 4))
                        lock_label = "阶梯锁利(+2.5%)"
            
            stop_val = max(base_stop, be_stop) if be_active else base_stop
            buffer_pct = (active_price - stop_val) / stop_val * 100 if stop_val > 0 else 99.0

            p_state = "PORT_NORMAL"
            
            # 对冲专属逻辑
            if ticker in ["SOXS", "SQQQ"]:
                smh_rsi = 50.0
                if macro_data and "SMH" in macro_data:
                    smh_rsi = macro_data["SMH"].get("rsi", 50.0)
                soxs_gain = s.get("pct_change", 0.0)
                bias_ema5 = ((active_price - ema5) / ema5) * 100 if ema5 > 0 else 0.0
                is_climax_hedge = (smh_rsi <= 33.0) or (soxs_gain >= 7.0) or (bias_ema5 >= 6.0)
                is_break_ema5 = (ema5 > 0 and active_price < ema5)
                
                if be_active and active_price <= be_stop:
                    p_state = f"{ticker}_BREAKEVEN_STOP"
                elif is_climax_hedge:
                    p_state = f"{ticker}_HEDGE_CLIMAX"
                elif is_break_ema5:
                    p_state = f"{ticker}_HEDGE_EXIT"
                elif stop_val > 0 and active_price <= stop_val:
                    p_state = f"{ticker}_CRITICAL_STOP"
                elif stop_val > 0 and buffer_pct <= 3.0:
                    p_state = f"{ticker}_NEAR_STOP"
            else:
                # V3.1 终极版：沿 EMA10/20 移动止盈与大牛股解封
                ema10 = s.get("ema10", ema5)
                ema20 = s.get("ema20", ma50)
                rsi = s.get("rsi", 50.0)
                bias_ma50 = ((active_price - ma50) / ma50) * 100 if ma50 > 0 else 0.0
                max_bias_5d = s.get("max_bias_5d", bias_ma50)
                max_rsi_5d = s.get("max_rsi_5d", rsi)

                # 极度高潮顶 (只在真正极端大顶清仓)
                is_climax_v31 = (rsi >= 82.0) or ((rsi >= 76.0 and bias_ma50 >= 20.0) and pnl_pct >= 10.0)

                # 水下抄底反抽强阻检查 (Trim_1 仅对水下逆势反抽股执行减半仓锁利，多头主升浪不半路切断)
                if cost > 0 and ma50 > 0 and cost < ma50:
                    res_level = fib_low * 0.99 if cost < fib_low * 0.97 else ma50 * 0.995
                    trim1_auto = max(res_level, cost * 1.045)
                else:
                    trim1_auto = 0.0
                dist_to_trim1 = ((trim1_auto - active_price) / trim1_auto) * 100 if trim1_auto > 0 else 99.0
                
                # EMA10/EMA20 移动止盈判断
                is_trailing_exit = False
                trailing_reason = ""
                if pnl_pct >= 5.0 or max_pnl_pct >= 8.0:
                    is_crazy = (max_bias_5d >= 12.0) or (max_rsi_5d >= 72.0) or (max_pnl_pct >= 12.0)
                    if is_crazy:
                        if ema10 > 0 and active_price < ema10:
                            is_trailing_exit = True
                            trailing_reason = "EMA10_CRAZY_EXIT"
                    else:
                        if ema20 > 0 and active_price < ema20 and active_price > ma50:
                            is_trailing_exit = True
                            trailing_reason = "EMA20_TREND_EXIT"

                if be_active and active_price <= be_stop:
                    p_state = f"{ticker}_TIERED_LOCK_STOP" if "阶梯锁利" in lock_label else f"{ticker}_BREAKEVEN_STOP"
                elif stop_val > 0 and active_price <= stop_val:
                    p_state = f"{ticker}_CRITICAL_STOP"
                elif stop_val > 0 and buffer_pct <= 3.0:
                    p_state = f"{ticker}_NEAR_STOP"
                elif is_climax_v31:
                    p_state = f"{ticker}_CLIMAX_EXIT"
                elif is_trailing_exit:
                    p_state = f"{ticker}_{trailing_reason}"
                elif cost < ma50 and trim1_auto > 0 and (active_price >= trim1_auto or dist_to_trim1 <= 1.0 or max_pnl_pct >= 6.0) and active_price > cost * 1.02:
                    p_state = f"{ticker}_TRIM1_RESISTANCE"
                elif target > 0 and active_price >= target:
                    p_state = f"{ticker}_GOAL_ACHIEVED"
                elif target > 0 and (target - active_price) / target <= 0.03:
                    p_state = f"{ticker}_NEAR_GOAL"
            
            p_key = f"PORT_{ticker}"
            p_prev = targets_history.get(p_key, {})
            p_prev_state = p_prev.get("state", "PORT_NORMAL")
            p_last_notified = p_prev.get("last_notified_at", 0.0)
            
            p_alert = False
            if p_state != "PORT_NORMAL":
                if p_state != p_prev_state:
                    p_alert = True
                elif (current_time - p_last_notified) >= ALERT_COOLDOWN_SECONDS:
                    p_alert = True
                if p_alert:
                    pending_target_updates[p_key] = {
                        "state": p_state,
                        "last_notified_at": current_time
                    }
                    intc_alerts.append({
                        "ticker": ticker, "name": name, "last": active_price,
                        "state": p_state, "cost": cost,
                        "shares": s.get("shares", 0.0),
                        "stop_val": stop_val, "target": target,
                        "be_stop": be_stop, "be_active": be_active,
                        "peak_price": peak_price, "max_pnl_pct": max_pnl_pct,
                        "session_tag": session_tag
                    })
            else:
                if p_prev_state != "PORT_NORMAL":
                    targets_history[p_key] = {
                        "state": "PORT_NORMAL",
                        "last_notified_at": 0.0
                    }

    # 2. 宏观气象突变检查
    current_weather = weather_info.get("weather", "晴天做多")
    prev_weather = history.get("weather", "")
    weather_changed = (prev_weather and prev_weather != current_weather)

    # 3. 组织并发送微信通知 (若无新增异动则静默)
    total_alerts = len(watchlist_entry_alerts) + len(pit_alerts) + len(stop_alerts) + len(tp_alerts) + len(intc_alerts)
    if total_alerts == 0 and not weather_changed:
        save_alert_history(history)
        return

    # 生成卡片标题与正文
    title_parts = []
    if watchlist_entry_alerts:
        w_tickers = "/".join([a["ticker"] for a in watchlist_entry_alerts])
        title_parts.append(f"🎯5m日内买点:{w_tickers}")
    if pit_alerts:
        title_parts.append(f"🟢{len(pit_alerts)}只黄金坑")
    if stop_alerts:
        title_parts.append(f"🔴{len(stop_alerts)}只破位")
    if tp_alerts:
        title_parts.append(f"🟣{len(tp_alerts)}只止盈")
    if weather_changed:
        title_parts.append(f"⛅气象变动:{current_weather}")

    msg_title = f"【美股量化雷达】{' · '.join(title_parts)}"

    md = [
        f"### 🔔 美股量化雷达 ｜ {current_weather}",
        f"> ⏱️ **时间**：`{now_str[5:16]}` ｜ **大盘**：{weather_info.get('desc', '')[:28]}",
        ""
    ]

    if watchlist_entry_alerts:
        md.append("### 🎯 5m日内量化买点触发")
        for a in watchlist_entry_alerts:
            ext_str = f" 🌙{a['session_tag']}:`${a['ext_price']:.2f}`" if a.get("session_tag") and a.get("ext_price") else ""
            e_str = f" ｜ 📅{a['earnings_tag']}" if a.get("earnings_tag") else ""
            opt_tag = f" ｜ ⚡**{a.get('pcr_sentiment','期权均衡')}** (资金PCR `{a.get('dollar_pcr',0):.2f}`)" if a.get("has_options") else ""

            md.append(f"> ### 🚀 **{a['ticker']}** {a['name']} ({a['cat']}){e_str}")
            md.append(f"> **现价**：`${a['last']:.2f}` (`{a['pct_change']:+.2f}%`){ext_str}{opt_tag}")
            md.append(f"> ")
            md.append(f"> 📋 **关键点位参考:**")
            md.append(f"> • **参考挂单**：**`${a['optimal_limit']:.2f}`** ({a['limit_note']})")
            md.append(f"> • **防守止损**：**`${a['stop_loss']:.2f}`** (结构止损单)")
            md.append(f"> ")
            
            clean_reason = a['intraday_reason'].split(" ｜ ⚡")[0]
            call_put_brief = f"主力Call `{a.get('top_call_info','--')}`" if a.get("has_options") and a.get("top_call_info") != "--" else ""
            logic_line = clean_reason
            if call_put_brief:
                logic_line += f" ｜ {call_put_brief}"
            md.append(f"> 💡 **触发逻辑**：{logic_line}")
            md.append("")
        md.append("")

    if pit_alerts:
        md.append("### 🟢 V3.1 黄金坑买点就绪 (美股盘中/开盘委托单)")
        for a in pit_alerts:
            t_last = a['last']
            t_buy_limit = t_last * 1.020
            t_cancel_line = max(a.get('low_price', a.get('rth_low', t_last * 0.985)), t_last * 0.985)
            t_stop_loss = a.get('stop_loss', round(t_last * 0.932, 2))

            md.append(f"> ### 🚀 **{a['ticker']}** {a['name']} ({a['cat']})")
            md.append(f"> • 现价 **`${t_last:.2f}`** ｜ 偏离度 `{a['bias']:+.1f}%` ｜ 14日RSI `{a['rsi']:.1f}`")
            md.append(f"> • 黄金坑区间：`${a['fib_low']:.2f} ~ ${a['fib_high']:.2f}` ({a['pit_status']})")
            md.append(f"> ")
            md.append(f"> 📋 **美股盘中/开盘执行指令 (V3.1 标准)**：")
            md.append(f"> 1️⃣ **9:30开盘买入上限价 (Limit)**：**`${t_buy_limit:.2f}`** (开盘高于此价放弃不追)")
            md.append(f"> 2️⃣ **隔夜低开作废底线**：**`${t_cancel_line:.2f}`** (开盘跌破此价撤单不买)")
            md.append(f"> 3️⃣ **GTC 铁血结构止损单**：**`${t_stop_loss:.2f}`**")
            md.append("")
        md.append("")

    if stop_alerts:
        md.append("### 🔴 破位止损 (风控防御警报)")
        for a in stop_alerts:
            md.append(f"> 🚨 **{a['ticker']}** ({a['name']}) 现价 **`${a['last']:.2f}`** (偏离度 `{a['bias']:+.1f}%`)")
            md.append(f"> • 跌破斐波那契下沿 **`${a['fib_low']:.2f}`**，进入空头宣泄期，严禁左侧接刀！")
            md.append("")
        md.append("")

    if tp_alerts:
        md.append("### 🟣 冲顶减仓 / 阶梯止盈")
        for a in tp_alerts:
            md.append(f"> 💰 **{a['ticker']}** ({a['name']}) 现价 **`${a['last']:.2f}`** (偏离度 `{a['bias']:+.1f}%` · RSI `{a['rsi']:.1f}`)")
            md.append(f"> • 触发原因：`{a['reason']}`，建议分批减半锁定利润！")
            md.append("")
        md.append("")

    send_success = send_pushplus_wechat_alert(msg_title, "\n".join(md))
    if send_success:
        targets_history.update(pending_target_updates)
        if weather_changed:
            history["weather"] = current_weather
        save_alert_history(history)
        print(f"  [+] 📱 微信预警成功推送并持久化冷却状态 ({len(pending_target_updates)} 项)")
        if watchlist_entry_alerts and _should_record_signal_ledger(explicit_ledger_path=ledger_path):
            try:
                import signal_ledger
                _vr_5m = (weather_info or {}).get("vix_regime") if isinstance(weather_info, dict) else {}
                _vr_5m = _vr_5m if isinstance(_vr_5m, dict) else {}
                for _item_5m in watchlist_entry_alerts:
                    _px_5m = _item_5m.get("active_price") or _item_5m.get("last")
                    signal_ledger.record_signal(
                        signal_type="INTRADAY_5M_BUY",
                        ticker=_item_5m.get("ticker", ""),
                        entry_price=_px_5m,
                        timestamp_et=now_str,
                        vix_at_entry=_vr_5m.get("vix_spot"),
                        vix_ratio_at_entry=_vr_5m.get("vix_term_ratio"),
                        meta={"optimal_limit": _item_5m.get("optimal_limit"), "rsi_5m": _item_5m.get("rsi_5m")},
                        ledger_path=ledger_path,
                    )
            except Exception as _sl_err:
                print(f"  [!] 记账本写入跳过 (5m买点): {_sl_err}")
    else:
        save_alert_history(history)
        print("  [!] 📱 微信推送未成功确认，保留预警重试资格 (不更新冷却时间戳)")

def dispatch_discord_sentinel_async(macro_data, stocks_sorted, weather_info, now_str):
    """
    异步、非阻塞触发美股五大梯队量化哨兵与板块巡检 (Discord Webhook 富文本通道)。
    在独立守护线程中运行，具备完全异常隔离，绝不阻塞主雷达 60s 轮询与终端渲染。
    """
    try:
        import threading
        import discord_sentinel

        thread = threading.Thread(
            target=discord_sentinel.run_sentinel_cycle_safe,
            args=(macro_data, stocks_sorted, weather_info, now_str),
            kwargs={"scheduled_only": True},
            daemon=True,
            name="DiscordSentinelDaemon"
        )
        thread.start()
    except Exception as e:
        print(f"[!] ⚠️ Discord Sentinel async launch failed: {e}")


def dispatch_jin10_sentinel_async():
    """
    异步、非阻塞触发金十数据红字宏观快讯与美国重磅经济日历哨兵。
    严格隔离在独立守护线程中，强制 3 秒超时降级，绝不写入公开 HTML 且绝不阻塞主雷达。
    """
    try:
        import jin10_news_sentinel
        jin10_news_sentinel.run_jin10_sentinel_async()
    except Exception as e:
        print(f"[!] ⚠️ Jin10 News Sentinel async launch failed: {e}")

def generate_and_sync_notifications(macro_data, stocks_data, weather_info, now_str, session_code="REGULAR"):
    """
    生成与微信通知 100% 同步的结构化分类消息中心数据，并持久化到 .terminal_notifications.json
    采用【日期+类型+标的+子状态】作为唯一 ID，同日同状态原地更新最新价格，杜绝每3分钟重复堆叠与误报响铃。
    """
    target_watchlist = load_target_watchlist()
    existing_notifs = load_terminal_notifications()
    new_notifs = []
    date_tag = now_str[:10]

    # 0. 🚨 防御因子起飞与机构去杠杆踩踏警报 (Defensive Flight Alert)
    try:
        import ml_predictor_5y
        live_lookup = {s["ticker"]: s for s in stocks_data if isinstance(s, dict) and "ticker" in s}
        live_lookup.update(macro_data or {})
        df_alert = ml_predictor_5y.compute_defensive_flight_alert(live_lookup=live_lookup, session_code=session_code)
        if weather_info and isinstance(weather_info, dict):
            weather_info["defensive_flight"] = df_alert
        if df_alert.get("skip_reason"):
            print(f"  [!] {df_alert.get('skip_reason')}；夜盘防御卡片跳过")
        if df_alert.get("is_alert"):
            flight_score = df_alert.get("flight_score", 0.0)
            new_notifs.append({
                "id": f"DEF_FLIGHT_{date_tag}",
                "time": now_str[5:16],
                "category": "defensive_flight",
                "cat_label": "🚨 防御起飞",
                "level": "danger",
                "ticker": "DEFENSE",
                "name": "去杠杆风控",
                "cat": "系统警报",
                "price": flight_score,
                "pct_change": flight_score,
                "title": f"🚨 防御因子起飞 · 警惕机构盘中去杠杆踩踏 (+{flight_score:.1f}%)",
                "desc": f"防御避险资产(KO/PEP/MCD/LLY/TLT/GLD/VIXY)超额走强 +{flight_score:.1f}%，科技成长显著弱势，机构正在盘中去杠杆避险，警惕踩踏风险！"
            })
    except Exception:
        pass

    all_etfs_list = [{"ticker": t, **m} for t, m in macro_data.items()]
    all_targets = list(stocks_data) + all_etfs_list

    for s in all_targets:
        ticker = s["ticker"]
        name = s["name"]
        cat = s["cat"]
        last = s["last"]
        active_price = s.get("active_price", last)
        pct_change = s.get("pct_change", 0.0)
        is_obs_only = ticker in {"JPM", "XOM", "WMT", "CAT", "NASA"}
        intraday_ready = s.get("intraday_entry_ready", False)
        is_pit = s.get("is_pit", False) and (not is_obs_only)
        is_stop = s.get("is_stop", False) and (not is_obs_only)
        is_tp = s.get("is_take_profit", False) and (not is_obs_only)
        shares = s.get("shares", 0.0)

        # 1. 🎯 5m 买点通知 (全池开放推送，与 Discord WATCHLIST_ENTRY_READY 同步，低波传统/行业基准观察池不弹窗)
        if intraday_ready and not is_stop and not is_obs_only:
            new_notifs.append({
                "id": f"5M_{ticker}_{date_tag}",
                "time": now_str[5:16],
                "category": "5m_entry",
                "cat_label": "🎯 5m买点",
                "level": "success",
                "ticker": ticker,
                "name": name,
                "cat": cat,
                "price": active_price,
                "pct_change": pct_change,
                "title": f"🎯 {ticker} ({name}) 触发 5分钟级买点共振",
                "desc": s.get("intraday_reason", "5m量比放量站稳VWAP，动能收阳确认"),
                "optimal_limit": s.get("optimal_limit", 0.0),
                "limit_note": s.get("limit_note", ""),
                "stop_loss": s.get("entry_stop_loss", 0.0),
                "batch1_shares": round(s.get("fractional_shares", 0.0) * 0.30, 2),
                "batch1_val": round(round(s.get("fractional_shares", 0.0) * 0.30, 2) * s.get("optimal_limit", active_price), 1),
                "options_summary": f"{s.get('pcr_sentiment','')} (张数PCR {s.get('pcr',0):.2f} / 资金 {s.get('dollar_pcr',0):.2f}) | 主力Call {s.get('top_call_info','--')}" if s.get("has_options") else ""
            })

        # 2. 💼 实盘持仓风控哨通知 (与微信 INTC/SOXL 及任意实盘持仓同步)
        if shares > 0:
            cost = s.get("cost", 0.0)
            stop_val = s.get("base_val", 0.0)
            trim1 = s.get("trim1", 0.0)
            trim1_shares = s.get("trim1_shares", 0.0)
            target = s.get("target", cost)
            buffer_pct = (active_price - stop_val) / stop_val * 100 if stop_val > 0 else 99.0
            pnl_pct = ((active_price - cost) / cost) * 100 if cost > 0 else 0.0

            p_level = "info"
            p_substate = "HOLD"
            p_title = f"💼 {ticker} 实盘持仓跟踪"
            p_desc = f"持仓 {shares:g} 股 · 成本 ${cost:.2f} ｜ 现价 ${active_price:.2f} ({pnl_pct:+.2f}%)"
            # 动态保本铁盾 (Breakeven Shield，基于历史最高价持久记忆)
            alert_history_temp = load_alert_history()
            pos_peaks_dict = alert_history_temp.setdefault("position_peaks", {})
            saved_peak = float(pos_peaks_dict.get(ticker, cost))
            today_high = float(s.get("high", active_price))
            peak_price = max(saved_peak, float(cost), float(active_price), today_high)
            pos_peaks_dict[ticker] = round(peak_price, 4)
            save_alert_history(alert_history_temp)
            max_pnl_pct = ((peak_price - cost) / cost) * 100 if cost > 0 else 0.0

            be_active = False
            be_stop = cost * 1.004 if ticker in ["SOXS", "SQQQ"] else cost * 1.005
            if ticker in ["SOXS", "SQQQ"]:
                if max_pnl_pct >= 4.0:
                    be_active = True
            else:
                # 对齐 V2.4/V3.1 动态保本铁盾激活门槛: 浮盈达到 +4.2% 或 2.0*ATR
                if max_pnl_pct >= 4.2 or (s.get("atr", 0) > 0 and peak_price >= cost + 2.0 * s["atr"]):
                    be_active = True
                lock_label = "保本盾"
                if be_active:
                    if max_pnl_pct >= 14.0:
                        be_stop = max(be_stop, round(cost * 1.060, 4))
                        lock_label = "阶梯锁利(+6.0%)"
                    elif max_pnl_pct >= 8.0:
                        be_stop = max(be_stop, round(cost * 1.025, 4))
                        lock_label = "阶梯锁利(+2.5%)"
            
            effective_stop = max(stop_val, be_stop) if be_active else stop_val
            buffer_pct = (active_price - effective_stop) / effective_stop * 100 if effective_stop > 0 else 99.0
            
            # 对冲标的专属逻辑
            if ticker in ["SOXS", "SQQQ"]:
                smh_rsi = 50.0
                if macro_data and "SMH" in macro_data:
                    smh_rsi = macro_data["SMH"].get("rsi", 50.0)
                ema5 = s.get("ema5", 0.0)
                soxs_gain = pct_change
                bias_ema5 = ((active_price - ema5) / ema5) * 100 if ema5 > 0 else 0.0
                is_climax_hedge = (smh_rsi <= 33.0) or (soxs_gain >= 7.0) or (bias_ema5 >= 6.0)
                is_break_ema5 = (ema5 > 0 and active_price < ema5)
                
                if be_active and active_price <= be_stop:
                    p_level = "danger"
                    p_substate = "BREAKEVEN"
                    p_title = f"🚨 {ticker} 触及动态保本止损线(${be_stop:.2f})！"
                    p_desc = f"历史最高曾达 ${peak_price:.2f} (+{max_pnl_pct:.1f}%)，现价回踩跌破保本线，立即平仓保本！"
                elif is_climax_hedge:
                    p_level = "success"
                    p_substate = "HEDGE_CLIMAX"
                    p_title = f"🟣 {ticker} 对冲恐慌极值（建议逢高收割）"
                    p_desc = f"现价 ${active_price:.2f} 浮盈 {pnl_pct:+.1f}%，半导体极度恐慌或脉冲拉升，果断落袋！"
                elif is_break_ema5:
                    p_level = "info"
                    p_substate = "HEDGE_EXIT"
                    p_title = f"⚪ {ticker} 跌破5日线(EMA5)，对冲行情结束"
                    p_desc = f"现价 ${active_price:.2f} 跌破 EMA5，建议全额清仓对冲盘！"
                elif effective_stop > 0 and active_price <= effective_stop:
                    p_level = "danger"
                    p_substate = "STOP"
                    p_title = f"🚨 {ticker} 击穿 ${effective_stop:.2f} 铁血硬止损线！"
                    p_desc = f"现价 ${active_price:.2f} 破位！持仓 {shares:g} 股，严格执行保命防守纪律！"
                elif effective_stop > 0 and buffer_pct <= 3.0:
                    p_level = "warning"
                    p_substate = "NEAR_STOP"
                    p_title = f"⚠️ {ticker} 紧迫风控预警：逼近防守线！"
                    p_desc = f"现价 ${active_price:.2f} 距防守线 ${effective_stop:.2f} 仅剩 ${active_price-effective_stop:.2f} ({buffer_pct:.1f}%) 缓冲！"
            else:
                fib_low = s.get("fib_low", cost)
                ma50 = s.get("ma50", 0.0)
                if cost > 0 and ma50 > 0 and cost < ma50:
                    res_level = fib_low * 0.99 if cost < fib_low * 0.97 else ma50 * 0.995
                    trim1_auto = max(res_level, cost * 1.045)
                else:
                    trim1_auto = cost + 1.5 * s.get("atr", 0.0) if s.get("atr", 0.0) > 0 else target
                dist_to_trim1 = ((trim1_auto - active_price) / trim1_auto) * 100 if trim1_auto > 0 else 99.0
                
                ema10 = s.get("ema10", s.get("ema5", 0.0))
                ema20 = s.get("ema20", ma50)
                rsi = s.get("rsi", 50.0)
                bias_ma50 = ((active_price - ma50) / ma50) * 100 if ma50 > 0 else 0.0
                is_climax_v31 = (rsi >= 82.0) or ((rsi >= 76.0 and bias_ma50 >= 20.0) and pnl_pct >= 10.0)

                is_trailing_exit = False
                trailing_title = ""
                trailing_desc = ""
                if pnl_pct >= 5.0 or max_pnl_pct >= 8.0:
                    is_crazy = (s.get("max_bias_5d", bias_ma50) >= 12.0) or (s.get("max_rsi_5d", rsi) >= 72.0) or (max_pnl_pct >= 12.0)
                    if is_crazy and ema10 > 0 and active_price < ema10:
                        is_trailing_exit = True
                        trailing_title = f"🟣 {ticker} 跌破疯牛线 EMA10(${ema10:.2f})移动止盈！"
                        trailing_desc = f"现价 ${active_price:.2f} 浮盈 {pnl_pct:+.1f}% 跌破 EMA10，果断全额锁利，锁定大波段利润！"
                    elif (not is_crazy) and ema20 > 0 and active_price < ema20 and active_price > ma50:
                        is_trailing_exit = True
                        trailing_title = f"🟣 {ticker} 跌破趋势线 EMA20(${ema20:.2f})移动止盈！"
                        trailing_desc = f"现价 ${active_price:.2f} 浮盈 {pnl_pct:+.1f}% 跌破 EMA20，主升浪趋缓，执行波段移动止盈！"

                _suppress_port_stop = bool(
                    float(pct_change or 0.0) <= -40.0
                    or any(
                        st.get("ticker") == ticker and should_suppress_stop_loss_alert(st)
                        for st in (stocks_data or [])
                    )
                )
                if not _suppress_port_stop and be_active and active_price <= be_stop:
                    p_level = "danger"
                    p_substate = "BREAKEVEN" if lock_label == "保本盾" else "TIERED_LOCK"
                    p_title = f"🚨 {ticker} 触及{lock_label}止损线(${be_stop:.2f})！"
                    p_desc = f"历史最高曾达 ${peak_price:.2f} (+{max_pnl_pct:.1f}%)，现价回踩跌破{lock_label}，立即兑现锁利！"
                elif not _suppress_port_stop and effective_stop > 0 and active_price <= effective_stop:
                    p_level = "danger"
                    p_substate = "STOP"
                    p_title = f"🚨 {ticker} 击穿 ${effective_stop:.2f} 铁血硬止损线！"
                    p_desc = f"现价 ${active_price:.2f} 破位！持仓 {shares:g} 股，严格执行保命防守纪律！"
                elif not _suppress_port_stop and effective_stop > 0 and buffer_pct <= 3.0:
                    p_level = "warning"
                    p_substate = "NEAR_STOP"
                    p_title = f"⚠️ {ticker} 紧迫风控预警：逼近防守线！"
                    p_desc = f"现价 ${active_price:.2f} 距防守线 ${effective_stop:.2f} 仅剩 ${active_price-effective_stop:.2f} ({buffer_pct:.1f}%) 缓冲！"
                elif is_climax_v31:
                    p_level = "success"
                    p_substate = "CLIMAX"
                    p_title = f"🎉 {ticker} 触发极度冲顶清仓止盈（RSI={rsi:.1f}）！"
                    p_desc = f"现价 ${active_price:.2f} 浮盈 {pnl_pct:+.1f}% 达到极端高潮顶，主力加速赶顶，建议全额清仓落袋！"
                elif is_trailing_exit:
                    p_level = "success"
                    p_substate = "TRAILING"
                    p_title = trailing_title
                    p_desc = trailing_desc
                elif cost < ma50 and trim1_auto > 0 and (active_price >= trim1_auto or dist_to_trim1 <= 1.0 or max_pnl_pct >= 6.0) and active_price > cost * 1.02:
                    p_level = "success"
                    p_substate = "TRIM1"
                    p_title = f"🟣 {ticker} 触及水下强阻 (${trim1_auto:.2f})！"
                    p_desc = f"现价 ${active_price:.2f} 浮盈 {pnl_pct:+.1f}% 遭遇强阻，建议逢高兑现50%锁定利润！"
                elif target > 0 and active_price >= target:
                    p_level = "success"
                    p_substate = "GOAL"
                    if target > cost:
                        p_title = f"🎉 {ticker} 达成 ${target:.2f} 阶段止盈目标！"
                        p_desc = f"现价 ${active_price:.2f} 达成盈利目标，建议按计划分批止盈锁定利润！"
                    else:
                        p_title = f"🎉 {ticker} 达成 ${target:.2f} 回本目标！"
                        p_desc = f"现价 ${active_price:.2f} 已回本，可依盘面决定是否落袋！"
                elif be_active:
                    p_desc += f" ｜ 🛡️ 已激活{lock_label}(底线锁定${be_stop:.2f})"

            new_notifs.append({
                "id": f"PORT_{ticker}_{p_substate}_{date_tag}",
                "time": now_str[5:16],
                "category": "portfolio",
                "cat_label": "💼 持仓风控",
                "level": p_level,
                "ticker": ticker,
                "name": name,
                "cat": cat,
                "price": active_price,
                "pct_change": pct_change,
                "title": p_title,
                "desc": p_desc,
                "cost": cost,
                "shares": shares,
                "stop_loss": stop_val,
                "trim1": trim1,
                "target": target
            })

        # 3. 🟢 黄金坑就位通知
        if is_pit and not is_stop and not s.get("is_crash_halt") and not s.get("is_cooling_halt"):
            new_notifs.append({
                "id": f"PIT_{ticker}_{date_tag}",
                "time": now_str[5:16],
                "category": "golden_pit",
                "cat_label": "🟢 黄金坑",
                "level": "success",
                "ticker": ticker,
                "name": name,
                "cat": cat,
                "price": active_price,
                "pct_change": pct_change,
                "title": f"🟢 {ticker} ({name}) 落入 60日斐波那契黄金坑",
                "desc": f"支撑带 ${s.get('fib_low',0):.2f}~${s.get('fib_high',0):.2f}，距坑底 {s.get('dist_to_pit',0):+.1f}%，14日RSI {s.get('rsi',0):.1f}",
                "optimal_limit": s.get("optimal_limit", 0.0),
                "stop_loss": s.get("entry_stop_loss", 0.0),
                "batch1_shares": round(s.get("fractional_shares", 0.0) * 0.30, 2)
            })

        # 4. 🔴 破位止损通知
        if is_stop and not ticker.endswith("ETF") and not should_suppress_stop_loss_alert(s):
            new_notifs.append({
                "id": f"STOP_{ticker}_{date_tag}",
                "time": now_str[5:16],
                "category": "stop_loss",
                "cat_label": "🔴 破位止损",
                "level": "danger",
                "ticker": ticker,
                "name": name,
                "cat": cat,
                "price": active_price,
                "pct_change": pct_change,
                "title": f"🔴 {ticker} ({name}) 跌破波段斐波那契下沿",
                "desc": f"偏离 MA50 {s.get('bias',0):+.1f}%，放量跌破坑底 ${s.get('fib_low',0):.2f}，空头宣泄期严禁左侧接刀！"
            })

        # 5. 🟣 冲顶减仓通知
        if is_tp:
            new_notifs.append({
                "id": f"TP_{ticker}_{date_tag}",
                "time": now_str[5:16],
                "category": "take_profit",
                "cat_label": "🟣 冲顶减仓",
                "level": "purple",
                "ticker": ticker,
                "name": name,
                "cat": cat,
                "price": active_price,
                "pct_change": pct_change,
                "title": f"🟣 {ticker} ({name}) 极度超买，触发高位减仓指令",
                "desc": f"偏离 MA50 {s.get('bias',0):+.1f}%，RSI {s.get('rsi',0):.1f}，进入极端冲顶兑现区，建议分批减半锁定利润！"
            })

    # 6. ⛅ 宏观气象通知
    cur_weather = weather_info.get("weather", "阴天防守")
    new_notifs.append({
        "id": f"WEATHER_{cur_weather}_{date_tag}",
        "time": now_str[5:16],
        "category": "macro_weather",
        "cat_label": "⛅ 宏观气象",
        "level": "info",
        "ticker": "SPY",
        "name": "美股大盘全景",
        "cat": "大盘气象",
        "price": macro_data.get("SPY", {}).get("last", 0.0),
        "pct_change": macro_data.get("SPY", {}).get("pct_change", 0.0),
        "title": f"⛅ 宏观大盘司令塔：【{cur_weather}】",
        "desc": f"{weather_info.get('desc', '')} ｜ 操盘军规：{weather_info.get('tactics', '')}"
    })

    # 合并去重：以 (category, ticker) 作为当天的实时状态主键，自动清理历史旧分钟产生的重复卡片
    combined = []
    seen_keys = set()
    for n in new_notifs:
        key = (n.get("category"), n.get("ticker"))
        combined.append(n)
        seen_keys.add(key)
    for n in existing_notifs:
        key = (n.get("category"), n.get("ticker"))
        if n.get("category") == "portfolio":
            continue
        if key not in seen_keys:
            # 过滤掉早于今天的旧分钟格式残留重复项
            combined.append(n)
            seen_keys.add(key)

    save_terminal_notifications(combined)
    return combined

# ==============================================================================
# 三、 历史 K 线获取与量化指标计算引擎
# ==============================================================================
def fetch_all_market_data(tickers, period="1y", retries=2):
    """并发批量抓取 1 年(约252交易日)历史日 K 线，支撑 MA200 牛熊线与 60日波段计算（XAUUSD 自动映射 GC=F 代理并稍后按现价校准）"""
    print(f"[*] 启动多线程底模引擎，抓取 {len(tickers)} 只标的历史形态 ({period})...")
    data_dict = {}
    proxy_map = {"XAUUSD": "GC=F"}
    dl_tickers = list(dict.fromkeys([proxy_map.get(t, t) for t in tickers]))
    try:
        raw_data = yf.download(dl_tickers, period=period, group_by="ticker", threads=True, progress=False)
        for t in tickers:
            try:
                dl_sym = proxy_map.get(t, t)
                sub = raw_data[dl_sym].dropna().copy() if hasattr(raw_data.columns, "levels") else raw_data.dropna().copy()
                if len(sub) >= 1:
                    data_dict[t] = sub
            except Exception:
                pass
    except Exception as e:
        print(f"[!] yfinance 批量下载异常: {e}，将启用单标的回退检索...")

    missing = [t for t in tickers if t not in data_dict or data_dict[t].empty]
    if missing:
        for t in missing:
            dl_sym = proxy_map.get(t, t)
            for attempt in range(1, retries + 1):
                try:
                    df = yf.Ticker(dl_sym).history(period=period, interval="1d")
                    if df is not None and len(df) >= 1:
                        data_dict[t] = df.copy()
                        break
                except Exception:
                    time.sleep(0.5)

    # 统一将所有历史 K 线 DatetimeIndex 转换为 tz-naive (规避 pandas 跨时区运算 TypeError)
    for t in data_dict:
        try:
            if hasattr(data_dict[t].index, "tz") and data_dict[t].index.tz is not None:
                data_dict[t].index = data_dict[t].index.tz_localize(None)
        except Exception:
            pass

    # 本地 5Y / 扩展日线缓存兜底：当 Yahoo 1y 接口对老股临时抽风截断（如 SNDK 仅返回 2 根日线 < 20 根）时，自动从本地 parquet 底模补齐完整历史并合并最新日线
    short_or_missing = [t for t in tickers if t not in data_dict or len(data_dict[t]) < 20]
    if short_or_missing:
        pq_frames = []
        for pq_name in ("ml_extended_panel.parquet", "ml_panel_5y.parquet"):
            pq_path = os.path.join(SCRATCH_DIR, "data_cache", pq_name)
            if os.path.exists(pq_path):
                try:
                    _pq = pd.read_parquet(pq_path)
                    if not _pq.empty and "Ticker" in _pq.columns:
                        _sub = _pq[_pq["Ticker"].isin(short_or_missing)]
                        if not _sub.empty:
                            pq_frames.append(_sub)
                except Exception:
                    pass
        if pq_frames:
            pq_all = pd.concat(pq_frames, ignore_index=True)
            req_cols = ["Open", "High", "Low", "Close", "Volume"]
            for t in short_or_missing:
                try:
                    t_rows = pq_all[pq_all["Ticker"] == t]
                    if t_rows.empty or not set(req_cols).issubset(t_rows.columns):
                        continue
                    sub_pq = t_rows.sort_values("Date").tail(260)[["Date"] + req_cols].copy()
                    sub_pq["Date"] = pd.to_datetime(sub_pq["Date"]).dt.tz_localize(None).dt.normalize()
                    sub_pq = sub_pq.set_index("Date")
                    if t in data_dict and not data_dict[t].empty and set(req_cols).issubset(data_dict[t].columns):
                        cur_df = data_dict[t][req_cols].copy()
                        cur_df.index = pd.to_datetime(cur_df.index).tz_localize(None).normalize()
                        max_cur_dt = cur_df.index.max()
                        if pd.notna(max_cur_dt):
                            sub_pq = sub_pq[sub_pq.index <= max_cur_dt].copy()
                        overlap_idx = sub_pq.index.intersection(cur_df.index)
                        if len(overlap_idx) > 0:
                            c_cur = pd.to_numeric(cur_df.loc[overlap_idx, "Close"], errors="coerce").dropna()
                            c_pq = pd.to_numeric(sub_pq.loc[overlap_idx, "Close"], errors="coerce").dropna()
                            common_ov = c_cur.index.intersection(c_pq.index)
                            if len(common_ov) > 0 and float(c_pq.loc[common_ov[-1]]) > 0:
                                raw_ratio = float(c_cur.loc[common_ov[-1]]) / float(c_pq.loc[common_ov[-1]])
                                if raw_ratio >= 1.67:
                                    sf = _match_split_factor_value(raw_ratio)
                                    if sf and sf > 1.0:
                                        for col_px in ("Open", "High", "Low", "Close"):
                                            sub_pq[col_px] = pd.to_numeric(sub_pq[col_px], errors="coerce") * sf
                                        sub_pq["Volume"] = pd.to_numeric(sub_pq["Volume"], errors="coerce") / sf
                                elif 0 < raw_ratio <= 0.60:
                                    sf = _match_split_factor_value(1.0 / raw_ratio)
                                    if sf and sf > 1.0:
                                        for col_px in ("Open", "High", "Low", "Close"):
                                            sub_pq[col_px] = pd.to_numeric(sub_pq[col_px], errors="coerce") / sf
                                        sub_pq["Volume"] = pd.to_numeric(sub_pq["Volume"], errors="coerce") * sf
                        merged_df = pd.concat([sub_pq, cur_df])
                    else:
                        merged_df = sub_pq
                    merged_df = merged_df[~merged_df.index.duplicated(keep="last")].sort_index()
                    if len(merged_df) > len(data_dict.get(t, [])):
                        data_dict[t] = merged_df
                except Exception:
                    pass

    # 阶段一规则 4：历史日线拆股/合股除权自检自愈 (无跳变 0 网络开销)
    for _tk_h in list(data_dict.keys()):
        try:
            _healed_df, _, _sp_meta = reconcile_live_quote_with_split_guard(
                _tk_h, data_dict[_tk_h], rq=None, allow_network_on_jump=True
            )
            if _healed_df is not None and len(_healed_df) > 0:
                data_dict[_tk_h] = _healed_df
            if _sp_meta.get("split_detected"):
                print(f"[+] 🔧 拆股除权自愈 ({_tk_h}): {_sp_meta.get('reason')} (修复 {_sp_meta.get('healed_rows', 0)} 行)")
        except Exception:
            pass

    print(f"[+] 历史形态抓取完成，成功获取 {len(data_dict)}/{len(tickers)} 只标的数据。\n")
    return data_dict

def compute_indicators(df):
    """向量化纯 Pandas 计算 MA200/SMA200、MA50、EMA20/10/5、14日 ATR/ATR%、14日 RSI/近3日最低RSI、20日均量及 60日滚动斐波那契黄金坑与右侧反转指标 (V2.4 对齐)"""
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    open_p = df["Open"] if "Open" in df.columns else close

    # 200 日牛熊分界线 & 50 日移动均线 & 偏离度 (SMA200 对齐 V2.4)
    df["MA200"] = close.rolling(200, min_periods=20).mean().bfill().fillna(close.rolling(50, min_periods=20).mean())
    df["SMA200"] = df["MA200"]
    df["MA50"] = close.rolling(50, min_periods=20).mean()
    df["Bias_MA50"] = ((close - df["MA50"]) / df["MA50"]) * 100

    # 20 日移动均线 MA20 & 偏离度
    df["MA20"] = close.rolling(20, min_periods=10).mean()
    df["Bias_MA20"] = ((close - df["MA20"]) / df["MA20"]) * 100

    # 20 日指数移动平均线 EMA20 (用于主升浪移动止损与跟踪止盈)
    df["EMA20"] = close.ewm(span=20, adjust=False).mean()
    df["EMA10"] = close.ewm(span=10, adjust=False).mean()
    df["EMA5"] = close.ewm(span=5, adjust=False).mean()

    # 14 日真实波动幅度 ATR 与 ATR% 波动率
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["ATR_14"] = tr.ewm(com=13, min_periods=14).mean()
    df["ATR_Pct"] = (df["ATR_14"] / close.replace(0, np.nan)) * 100.0

    # 14 日相对强弱指标 RSI 及近 3 日与近 5 日极值
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI_14"] = 100 - (100 / (1 + rs))
    df["Min_RSI_3d"] = df["RSI_14"].rolling(3, min_periods=1).min()
    df["Min_RSI_5d"] = df["RSI_14"].rolling(5, min_periods=1).min()
    df["Max_RSI_5d"] = df["RSI_14"].rolling(5, min_periods=1).max()
    df["Max_Bias_5d"] = df["Bias_MA50"].rolling(5, min_periods=1).max()

    # 成交量 20 日均线与量比
    df["VOL_MA20"] = volume.rolling(20, min_periods=5).mean()
    df["Vol_Ratio"] = volume / df["VOL_MA20"].replace(0, np.nan)

    # 60 日滚动最高最低与 0.5 / 0.618 斐波那契黄金坑及波段分位 Pos_60 / Fib_Pos
    high_60 = high.rolling(60, min_periods=20).max()
    low_60 = low.rolling(60, min_periods=20).min()
    diff_60 = high_60 - low_60
    fib_50 = high_60 - 0.5 * diff_60
    fib_618 = high_60 - 0.618 * diff_60
    df["Fib_50"] = fib_50
    df["Fib_618"] = fib_618
    df["Fib_Low"] = np.minimum(fib_50, fib_618)
    df["Fib_High"] = np.maximum(fib_50, fib_618)
    df["Fib_Pos"] = (close - df["Fib_Low"]) / (df["Fib_High"] - df["Fib_Low"] + 1e-6)
    df["Pos_60"] = (close - low_60) / diff_60.replace(0, np.nan)
    df["Ret_20d"] = ((close / close.shift(20)) - 1.0) * 100.0

    # 相对强弱 RET_60
    c_shift_60 = close.shift(60)
    df["RET_60"] = ((close - c_shift_60) / c_shift_60.replace(0, np.nan)) * 100.0
    df["RET_60"] = df["RET_60"].fillna(0.0)

    # 暴跌判定与 3日冷却期计算 (V3.2 升级版)
    daily_ret = ((close - prev_close) / prev_close.replace(0, np.nan)) * 100.0
    gap_down = ((open_p - prev_close) / prev_close.replace(0, np.nan)) * 100.0
    is_crash = (daily_ret <= -6.0) | (gap_down <= -4.5)

    # 保留 Crash_5d 保证向后兼容，新增 V3.2 核心指标
    df["Crash_5d"] = is_crash.rolling(5, min_periods=1).max().fillna(0).astype(bool)
    df["Crash_3d"] = is_crash.rolling(3, min_periods=1).max().fillna(0).astype(bool)
    df["Prev_Crash_3d"] = is_crash.shift(1).rolling(3, min_periods=1).max().fillna(0).astype(bool)

    # 右侧反转 K 线确认 (阳线实体 >= +0.5% 且 突破前一日高点 或 站上 EMA5)
    prev_high = high.shift(1)
    is_bullish_body = (close > open_p) & (((close - open_p) / open_p.replace(0, np.nan)) >= 0.005)
    df["Is_Right_Turn"] = is_bullish_body & ((close > prev_high) | (close > df["EMA5"]))
    df["Prev_Is_Right_Turn"] = df["Is_Right_Turn"].shift(1).fillna(0).astype(bool)

    # 记录近 3 日最近一次暴跌日的实体高度与收盘价 (严格同日配对，用于日内 50% 实体收复计算)
    crash_red_body = np.where(is_crash & (open_p > close), open_p - close, np.where(is_crash, (prev_close - close).clip(lower=0.0), 0.0))
    crash_body_series = pd.Series(np.where(is_crash, crash_red_body, np.nan), index=df.index)
    crash_close_series = pd.Series(np.where(is_crash, close, np.nan), index=df.index)
    df["Prev_Crash_Body"] = crash_body_series.shift(1).ffill(limit=2).fillna(0.0)
    df["Prev_Crash_Close"] = crash_close_series.shift(1).ffill(limit=2).fillna(close)

    return df

# ==============================================================================
# 四、 宏观气象与个股碎股信号判定
# ==============================================================================
def evaluate_market_weather(macro_dict, history_dict=None, stocks_lookup=None, vix_regime=None):
    """
    根据五大量化模型 + 全局真 VIX 恐慌中枢评定宏观气象与作战军规 (V3 深度升级版)：
    1. VIXY 三因子复合脉冲模型 (单日涨跌幅, MA20偏离度, 60日分位) + 真 ^VIX/^VIX3M/^VVIX/^TNX 中枢
    2. TLT 美债双向利率状态机 (估值顺风, 利率杀估值, 衰退避险, 中性震荡)
    3. 晴天做多一票否决权 (Veto) 机制 (高位急跌, 破MA20, 恐慌脉冲, VIX期限倒挂)
    4. SMH/IGV 相对强弱动态驱动风格主线军规
    5. 跨行业市场广度与普跌预警 (传统龙头 JPM/XOM/WMT/CAT/KO/LLY/DIA vs 科技 QQQ/SMH/NVDA)
    """
    qqqm_info = macro_dict.get("QQQ", {})
    smh_info = macro_dict.get("SMH", {})
    spy_info = macro_dict.get("SPY", {})
    vixy_info = macro_dict.get("VIXY", {})
    tlt_info = macro_dict.get("TLT", {})

    qqqm_bias = qqqm_info.get("bias", 0.0)
    qqqm_ma20_bias = qqqm_info.get("bias_ma20", 0.0)
    qqqm_pct = qqqm_info.get("pct_change", 0.0)
    qqqm_last = qqqm_info.get("last", 0.0)
    qqqm_ma20 = qqqm_info.get("ma20", 0.0)

    smh_bias = smh_info.get("bias", 0.0)
    smh_ma20_bias = smh_info.get("bias_ma20", 0.0)
    smh_pct = smh_info.get("pct_change", 0.0)
    smh_last = smh_info.get("last", 0.0)
    smh_ma20 = smh_info.get("ma20", 0.0)

    spy_bias = spy_info.get("bias", 0.0)

    vixy_last = vixy_info.get("last", 0.0)
    vixy_pct = vixy_info.get("pct_change", 0.0)
    vixy_ma20_bias = vixy_info.get("bias_ma20", 0.0)
    vixy_pos_60 = vixy_info.get("pos_60", 0.5) * 100.0 if vixy_info.get("pos_60") is not None else 50.0

    tlt_bias = tlt_info.get("bias", 0.0)

    # 1. 模型 1: VIXY 三因子复合脉冲模型
    is_vix_panic = bool((vixy_pct >= 7.5) or (vixy_ma20_bias >= 10.0) or (vixy_pos_60 >= 75.0))

    # 2. 模型 2: TLT 美债双向利率状态机
    if tlt_bias <= -2.0:
        tlt_state = "🔴 利率杀估值"
        tlt_color = "#ff5252"
        tlt_desc = "长端美债大跌，国债收益率飙升压制高估值科技"
    elif tlt_bias >= 1.0 and qqqm_bias < 0.0:
        tlt_state = "🟡 避险买盘"
        tlt_color = "#ffd54f"
        tlt_desc = "科技转弱，资金弃股投债流入避险资产"
    elif tlt_bias >= 0.0 and qqqm_bias >= 0.0:
        tlt_state = "🟢 估值顺风"
        tlt_color = "#00e676"
        tlt_desc = "股债双牛环境，宏观流动性充沛利好估值扩张"
    else:
        tlt_state = "⚪ 中性震荡"
        tlt_color = "#9ca3af"
        tlt_desc = "长端美债处于正常噪音区间，无剧烈流动性外溢"

    # 3. 模型 3: 晴天做多基准与一票否决权 (Veto)
    is_bull_base = (qqqm_bias >= 0.25) and (smh_bias >= 0.25)

    veto_reasons = []
    if is_vix_panic:
        veto_reasons.append("VIXY恐慌脉冲")
    if (qqqm_ma20 > 0 and qqqm_last < qqqm_ma20) and (smh_ma20 > 0 and smh_last < smh_ma20):
        veto_reasons.append("QQQ与SMH双破MA20")
    if qqqm_pct <= -1.8:
        veto_reasons.append(f"QQQ急杀{qqqm_pct:.1f}%")
    if smh_pct <= -2.8:
        veto_reasons.append(f"SMH大跌{smh_pct:.1f}%")

    if is_bull_base:
        if veto_reasons:
            weather = "阴天防守"
            icon = "⛅"
            color = "#29b6f6"
            veto_str = "、".join(veto_reasons)
            desc = f"【⚠️一票否决预警：{veto_str}】大盘均线虽处高位但遭遇短期剧烈承压，严禁盲目顶峰追高！"
            base_tactics = "严格控制总仓位，已持仓位保本止损，单股上限 25%(1.5%风险平价建仓)，耐心等待分歧消化。"
        else:
            weather = "晴天做多"
            icon = "☀️"
            color = "#00e676"
            desc = "纳指基准与半导体双双站上 MA50 生命线，风险偏好处于高位。"
            base_tactics = "顺势进攻核心科技与算力龙头，逢调整积极分批建仓优质标的。"
    elif qqqm_bias < -3.0 and smh_bias < -3.0:
        weather = "暴雨空仓"
        icon = "🌧️"
        color = "#ff1744"
        desc = "科技基准与半导体共振破位下行，系统性流动性挤压，严禁盲目抄底。"
        base_tactics = "严格执行个股硬止损，碎股进攻全面暂停，保留充沛现金与防守底仓。"
    else:
        weather = "阴天防守"
        icon = "⛅"
        color = "#29b6f6"
        desc = "大盘与核心芯片主线出现分化或均线争夺，市场进入结构性轮动。"
        base_tactics = "严格控制总仓位，单股上限 25%(1.5%风险平价建仓)，紧盯黄金坑分批防守。"

    # 4. 模型 4: SMH/IGV 相对强弱动态追加作战军规
    style_suffix = ""
    if history_dict and "SMH" in history_dict and "IGV" in history_dict:
        try:
            s_close = history_dict["SMH"]["Close"].copy()
            i_close = history_dict["IGV"]["Close"].copy()
            if hasattr(s_close.index, "tz") and s_close.index.tz is not None:
                s_close.index = s_close.index.tz_localize(None)
            if hasattr(i_close.index, "tz") and i_close.index.tz is not None:
                i_close.index = i_close.index.tz_localize(None)
            ratio_s = (s_close / i_close).dropna()
            if len(ratio_s) >= 5:
                cur_ratio = float(ratio_s.iloc[-1])
                ratio_ma20 = float(ratio_s.rolling(20, min_periods=5).mean().iloc[-1])
                if cur_ratio >= ratio_ma20:
                    style_suffix = " ｜ 🔥 风格主线：算力硬件领涨（优先聚焦芯片/光通信/电力）"
                else:
                    style_suffix = " ｜ 🔄 风格主线：硬件休整、软件应用占优（严禁追高芯片，关注软件与高低切）"
        except Exception:
            style_suffix = ""

    tactics = base_tactics + style_suffix

    # 4.5 跨行业市场广度与普跌预警 (传统龙头 vs 进攻科技)
    breadth_info = {}
    try:
        import ml_predictor_5y
        merged_live = dict(macro_dict or {})
        if isinstance(stocks_lookup, dict):
            merged_live.update(stocks_lookup)
        breadth_info = ml_predictor_5y.compute_cross_industry_breadth_alert(
            live_lookup=merged_live,
            history_dict=history_dict,
        )
    except Exception:
        breadth_info = {}

    if breadth_info.get("is_systemic_selloff"):
        desc += " ｜ 🚨 全市场系统性普跌预警（常规支撑S1/S2易失效·切勿盲目抄底）"
        tactics += " ｜ 🚨 全市场系统性普跌预警：传统龙头与科技同步下杀，常规支撑(S1/S2)极易失效，严禁盲目抄底接飞刀！"
    elif breadth_info.get("is_structural_rotation"):
        desc += " ｜ 🔄 科技结构性轮动（传统板块抗跌）"
        tactics += " ｜ 🔄 科技结构性轮动（传统板块抗跌）：资金向金融/能源/消费/工业切换，耐心等科技放量止跌"

    # 5. V3.1 旗舰轨道 2：Firstrade 零佣金 QQQ 闲置资金智能泊入与避险指示器
    qqq_info = macro_dict.get("QQQ", qqqm_info)
    qqq_last = qqq_info.get("last", qqqm_last)
    qqq_ma50 = qqq_info.get("ma50", 0.0)
    qqq_sma200 = qqq_info.get("sma200") or qqq_info.get("ma200", 0.0)

    is_rainstorm_env = (qqqm_bias < -3.0 and smh_bias < -3.0) or (qqq_ma50 > 0 and qqq_last < qqq_ma50 * 0.97)
    is_bear_defense_env = (qqq_sma200 > 0 and qqq_last < qqq_sma200)

    if is_rainstorm_env or is_bear_defense_env:
        qqq_parking_action = "🔴 清空避险"
        qqq_parking_desc = "大盘暴风雨/破防线，闲置资金 100% 留存美元现金，严禁泊入 QQQ"
        qqq_parking_color = "#ff5252"
    elif qqq_ma50 > 0 and qqq_last >= qqq_ma50 and not is_vix_panic:
        qqq_parking_action = "🟢 100% 满额泊入"
        qqq_parking_desc = "大盘多头安全期，闲置资金建议 100% 挂 MOO 泊入 QQQ 享大盘收益，有新买点时自动变现"
        qqq_parking_color = "#00e676"
    else:
        qqq_parking_action = "🟡 观望防守"
        qqq_parking_desc = "大盘处于 MA50 震荡拉锯期，暂缓新资金泊入 QQQ，保持轻仓防守"
        qqq_parking_color = "#ffd54f"

    # 阶段一核心：计算客观三态牛熊体制 (Market Regime Gate) 与全池 MA20 市场宽度
    regime_info = {
        "market_regime": "NEUTRAL_CHOP",
        "market_regime_cn": "🟡 震荡分化体制",
        "regime_score": 0.0,
        "breadth_ma20_pct": 50.0,
    }
    try:
        import ml_predictor_5y
        spy_ma20_bias_val = spy_bias
        qqq_ret_5d_val = 0.0
        qqq_down_5d_val = 0.0
        if history_dict:
            spy_df = history_dict.get("SPY")
            if spy_df is not None and len(spy_df) >= 20 and "Close" in spy_df.columns:
                spy_c = pd.to_numeric(spy_df["Close"], errors="coerce").dropna()
                if len(spy_c) >= 20:
                    spy_last_v = float(macro_dict.get("SPY", {}).get("last") or spy_c.iloc[-1])
                    spy_ma20_v = float(spy_c.iloc[-20:].mean())
                    if spy_ma20_v > 0:
                        spy_ma20_bias_val = ((spy_last_v - spy_ma20_v) / spy_ma20_v) * 100.0
            qqq_df = history_dict.get("QQQ")
            if qqq_df is not None and len(qqq_df) >= 6 and "Close" in qqq_df.columns:
                qqq_c = pd.to_numeric(qqq_df["Close"], errors="coerce").dropna()
                if len(qqq_c) >= 6:
                    qqq_last_v = float(macro_dict.get("QQQ", {}).get("last") or qqq_c.iloc[-1])
                    qqq_5d_base = float(qqq_c.iloc[-6])
                    if qqq_5d_base > 0:
                        qqq_ret_5d_val = ((qqq_last_v - qqq_5d_base) / qqq_5d_base) * 100.0
                    diffs = qqq_c.iloc[-6:].diff().dropna()
                    qqq_down_5d_val = float((diffs < 0).sum())

        exempt_breadth = {"SQQQ", "SOXS", "VIXY", "TLT", "GLD", "XAUUSD"}
        above_ma20 = 0
        valid_ma20 = 0
        seen_breadth = set()
        if stocks_lookup:
            for _tk, _sitem in stocks_lookup.items():
                if _tk in exempt_breadth:
                    continue
                _b20 = _sitem.get("bias_ma20")
                if _b20 is not None and not (isinstance(_b20, float) and math.isnan(_b20)):
                    valid_ma20 += 1
                    seen_breadth.add(_tk)
                    if float(_b20) > 0.0:
                        above_ma20 += 1
        if history_dict:
            for _tk, _hdf in history_dict.items():
                if _tk in exempt_breadth or _tk in seen_breadth:
                    continue
                if _hdf is not None and len(_hdf) >= 20 and "Close" in _hdf.columns:
                    _hc = pd.to_numeric(_hdf["Close"], errors="coerce").dropna()
                    if len(_hc) >= 20:
                        _m20 = float(_hc.iloc[-20:].mean())
                        _lc = float((macro_dict.get(_tk, {}) or {}).get("last") or _hc.iloc[-1])
                        if _m20 > 0:
                            valid_ma20 += 1
                            if _lc > _m20:
                                above_ma20 += 1
        breadth_ma20_pct_val = (above_ma20 / valid_ma20 * 100.0) if valid_ma20 > 0 else 50.0
        regime_info = ml_predictor_5y.compute_market_regime_state(
            qqq_bias_ma20=qqqm_ma20_bias,
            spy_bias_ma20=spy_ma20_bias_val,
            qqq_bias_ma50=qqqm_bias,
            smh_bias_ma50=smh_bias,
            breadth_ma20_pct=breadth_ma20_pct_val,
            vixy_pos_60=vixy_pos_60,
            qqq_ret_5d=qqq_ret_5d_val,
            qqq_down_days_5d=qqq_down_5d_val,
            vix_regime=vix_regime,
        )
    except Exception:
        pass

    _vr = vix_regime if isinstance(vix_regime, dict) else {}
    _vr_hard_brake = bool(_vr.get("vix_hard_brake") or regime_info.get("vix_hard_brake", False))
    _vr_bottom = bool(_vr.get("vix_bottom_signal") or regime_info.get("vix_bottom_signal", False))
    if _vr_hard_brake:
        _r_txt = f"{float(_vr['vix_term_ratio']):.2f}" if _vr.get("vix_term_ratio") is not None else "--"
        _v_txt = f"{float(_vr['vix_spot']):.1f}" if _vr.get("vix_spot") is not None else "--"
        desc += f" ｜ 🔴 真VIX期限倒挂刹车 (^VIX={_v_txt}, 比值={_r_txt})"
        tactics += " ｜ 🔴 机构恐慌倒挂踩踏期：严禁左侧接飞刀，常规多头胜率锁死 <=48%"
    elif _vr_bottom:
        desc += " ｜ 🟢 VIX恐慌出清·右侧抄底观察窗已开启"
        tactics += " ｜ 🟢 恐慌确认见顶回落：对站稳日内 VWAP 的非熔断优质标的平滑放宽右侧抄底权限"

    return {
        "weather": weather,
        "icon": icon,
        "color": color,
        "desc": desc,
        "tactics": tactics,
        "qqq_parking_action": qqq_parking_action,
        "qqq_parking_desc": qqq_parking_desc,
        "qqq_parking_color": qqq_parking_color,
        "qqqm_bias": qqqm_bias,
        "qqqm_ma20_bias": qqqm_ma20_bias,
        "qqqm_pct": qqqm_pct,
        "smh_bias": smh_bias,
        "smh_ma20_bias": smh_ma20_bias,
        "smh_pct": smh_pct,
        "spy_bias": spy_bias,
        "vixy_last": vixy_last,
        "vixy_pct": vixy_pct,
        "vixy_ma20_bias": vixy_ma20_bias,
        "vixy_pos_60": vixy_pos_60,
        "is_vix_panic": bool(is_vix_panic or _vr_hard_brake),
        "tlt_bias": tlt_bias,
        "tlt_state": tlt_state,
        "tlt_color": tlt_color,
        "tlt_desc": tlt_desc,
        "cross_industry_breadth": breadth_info,
        "market_regime": regime_info.get("market_regime", "NEUTRAL_CHOP"),
        "market_regime_cn": regime_info.get("market_regime_cn", "🟡 震荡分化体制"),
        "regime_score": regime_info.get("regime_score", 0.0),
        "breadth_ma20_pct": regime_info.get("breadth_ma20_pct", 50.0),
        "vix_regime": _vr,
        "vix_hard_brake": _vr_hard_brake,
        "vix_bottom_signal": _vr_bottom,
    }

def evaluate_stock_signal(
    ticker, last, ma50, bias, rsi, vol_ratio, fib_low, fib_high, meta,
    atr=0.0, weather_info=None, macro_data=None, ema20=0.0,
    high_bias_recently=False, prev_close=0.0, prev_ema20=0.0,
    prev_rsi=0.0, min_rsi_5d=50.0, pos_60=0.5, open_price=0.0,
    ema10=0.0, prev_ema10=0.0, ema5=0.0, prev_ema5=0.0,
    max_bias_5d=0.0, max_rsi_5d=50.0, max_pnl_pct=0.0,
    sma200=0.0, min_rsi_3d=50.0, atr_pct=3.0, fib_pos=0.5,
    crash_5d=False, is_right_turn=False, rs_60=0.0, low_price=None, rth_low=None
):
    """
    扫描标的作战信号 (V2.4 终极对齐版)：
    1. INTC 专属风控与通用标的分流
    2. 反向衰减 ETF (SQQQ, SOXS, VIXY) 专属对冲逻辑 (不参与黄金坑抄底)
    3. 通用破位：量价与K线方向确认 (放量杀跌真破位 vs 缩量回踩/底背离反包)
    4. 通用止盈与主升体系：冲顶滞涨减仓 + EMA20 移动止盈 + 🔵 多头加速主升浪
    5. 黄金坑右侧反转 (V2.4 严选准入铁律)：长线牛市 + 0.382~0.65 坑深 + ATR<=5.2% + 无暴雷 + 3日超卖 + 右侧反转K线
    """
    if last > fib_high:
        dist_to_pit = ((last - fib_high) / last) * 100
        pit_status = f"坑上 +{dist_to_pit:.2f}%"
    elif fib_low <= last <= fib_high:
        dist_to_pit = 0.0
        pit_status = "🟢 处于坑内"
    else:
        dist_to_pit = ((last - fib_low) / last) * 100
        pit_status = f"破坑 {dist_to_pit:.2f}%"

    # 对齐 V3.1 1.5% 风险平价配仓 (单股上限 25%，当日最低价 low_price - 0.5*ATR 结构止损)
    max_alloc = round(TOTAL_ACCOUNT_EQUITY * MAX_POSITION_PCT, 2) if "TOTAL_ACCOUNT_EQUITY" in globals() else 2500.0
    sig_low = low_price if (low_price is not None and low_price > 0) else (rth_low if (rth_low is not None and rth_low > 0) else last)
    struct_stop = max(min(sig_low - 0.5 * atr, last - 1.8 * atr), last * 0.932) if atr > 0 else last * 0.95
    stop_dist_pct = max((last - struct_stop) / last, 0.035) if last > 0 else 0.05
    risk_parity_alloc = min(max_alloc, (TOTAL_ACCOUNT_EQUITY * 0.015) / stop_dist_pct) if "TOTAL_ACCOUNT_EQUITY" in globals() else min(max_alloc, (2500.0 * 0.05) / stop_dist_pct)
    fractional_shares = risk_parity_alloc / last if last > 0 else 0.0

    signals = []
    tp_reasons = []
    atr_mult = (last - ma50) / atr if atr > 0 else 0.0
    is_deep_pullback = False
    is_climax_tp = False
    is_tp = False
    is_momentum = False

    cost = meta.get("cost", 0.0)
    shares = meta.get("shares", 0.0)
    pnl_pct = ((last - cost) / cost) * 100 if cost > 0 else 0.0

    # --------------------------------------------------------------------------
    # 分支 A: 反向/波动率衰减型对冲工具 (SQQQ, SOXS, VIXY) - 【左侧恐慌收割算法】
    # --------------------------------------------------------------------------
    if ticker in ["SQQQ", "SOXS", "VIXY"]:
        dist_to_pit = 0.0
        pit_status = "🛡️ 对冲工具(不计坑)"
        smh_rsi = 50.0
        if macro_data and "SMH" in macro_data:
            smh_rsi = macro_data["SMH"].get("rsi", 50.0)
        
        soxs_gain = ((last - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
        bias_ema5 = ((last - ema5) / ema5) * 100 if ema5 > 0 else 0.0

        # 1. 左侧恐慌极值收割 (SMH 极度超卖 RSI<=33，或单日脉冲>=+7.0%，或偏离自身EMA5>=+6.0%)
        is_climax_hedge = (smh_rsi <= 33.0) or (soxs_gain >= 7.0) or (bias_ema5 >= 6.0)
        # 2. 跌破自身 5日均线 (EMA5)，反向脉冲行情终结
        is_break_ema5 = (ema5 > 0 and last < ema5)

        if is_climax_hedge:
            signals.append("🟣 对冲恐慌极值(逢高分批收割)")
            tp_reasons.append(f"半导体恐慌极值(SMH RSI={smh_rsi:.1f},单日+{soxs_gain:.1f}%,偏离EMA5 {bias_ema5:+.1f}%)")
            is_tp = True
        elif is_break_ema5:
            signals.append("⚪ 跌破EMA5(清仓对冲盘)")
            tp_reasons.append(f"跌破EMA5生命线(${last:.2f} < EMA5 ${ema5:.2f})")
            is_tp = True
        elif bias > 5.0 and vol_ratio >= 1.2:
            signals.append("🛡️ 对冲防御激活")
        elif bias < -5.0:
            signals.append("⚪ 正常对冲衰减")
        else:
            signals.append("⚪ 观望对冲")

    # --------------------------------------------------------------------------
    # 分支 B: 全市场通用客观量化诊断 (含水下抄底、常规主升、高位疯牛)
    # --------------------------------------------------------------------------
    else:
        # 1. 破位止损【双轨制】：浅破看量防洗盘 + 深破无条件熔断 (严格区分持仓股 vs 观察股)
        has_pos = bool(cost > 0 and shares > 0)
        custom_stop = meta.get("base_val", 0.0)

        if has_pos:
            # 【已持仓实盘标的】（水下抄底单或主升浪单）
            # 极限止损严格以实盘买入成本、专属防守底线及单笔最大亏损为基准，杜绝被高位坑底误判！
            is_limit_break = (
                (custom_stop > 0 and last <= custom_stop)
                or (pnl_pct <= -7.0)
                or (atr > 0 and last <= cost - 2.0 * atr)
            )
            is_shallow_break = False
        else:
            # 【未持仓观察标的】
            is_limit_break = (
                (fib_low > 0 and last < fib_low * 0.93)
                or (atr_mult <= -2.8)
            )
            is_shallow_break = (last < fib_low * 0.97) or (atr_mult <= -2.2) or (bias < -14.0)

        if is_limit_break:
            signals.append("🔴 破位止损(极限风控)")
            is_price_break = True
        elif is_shallow_break:
            is_red_drop = (prev_close <= 0) or (last <= prev_close)
            if vol_ratio >= 1.20 and is_red_drop:
                signals.append("🔴 破位止损")
                is_price_break = True
            else:
                signals.append("🟠 深度回踩(观察企稳)")
                is_deep_pullback = True
                is_price_break = False
        else:
            is_price_break = False

        # 2. 止盈与多头主升体系
        is_tp = False
        is_stall_candle = (open_price > 0 and last < open_price) or (prev_close > 0 and last <= prev_close)

        # 规则 0: 水下抄底单 (cost < ma50) 反抽阻力减仓 (Trim_1 目标价防倒挂，对齐 V2.4)
        if cost > 0 and shares > 0 and cost < ma50:
            res_level = fib_low * 0.99 if cost < fib_low * 0.97 else ma50 * 0.995
            trim_target = max(res_level, cost * 1.045)
            pnl_high = max_pnl_pct if max_pnl_pct > 0 else pnl_pct
            if (last >= trim_target or pnl_high >= 6.0 or pnl_pct >= 6.0) and is_stall_candle and last > cost * 1.02:
                is_tp = True
                tp_reasons.append(f"水下反抽强阻(${trim_target:.2f},浮盈{pnl_pct:+.1f}%)")
                signals.append("🟣 触及强阻减仓(建议兑现50%锁利)")

        # 规则 1: 极度冲顶高潮 (RSI>=80 极端超买，或高位超买伴随阴线/滞涨确认)
        # 规则 1: 极度冲顶高潮 (V3.1 终极版：仅在 RSI>=82 或 RSI>=76且MA50偏离>=20%滞涨高潮顶才提示极度冲顶清仓，不再过早拦截主升浪)
        is_extreme_climax = (rsi >= 82.0) or ((rsi >= 76.0 and bias >= 20.0) and is_stall_candle and (pnl_pct >= 10.0 if shares > 0 else True))
        if not is_tp and is_extreme_climax:
            is_tp = True
            is_climax_tp = True
            signals.append("🎉 极度冲顶清仓止盈")
            tp_reasons.append(f"极度冲顶高潮(RSI={rsi:.1f},偏离{bias:+.1f}%)")

# 规则 2: (已对齐 V3.1 V30_RUNNER 模式，移除 +10% 乖离早退截胡，全面交由 EMA10/20 变速箱跟踪)

        # 规则 3: 移动止盈【EMA20 / EMA10 双速自适应变速箱】(融入近5日极值，防止破位当天降档漏报)
        if not is_tp:
            if max_bias_5d == 0.0:
                max_bias_5d = bias
            if max_rsi_5d == 50.0:
                max_rsi_5d = rsi
            if max_pnl_pct == 0.0 and has_pos:
                max_pnl_pct = meta.get("max_pnl_pct", pnl_pct)

            is_crazy_bull = (max_bias_5d >= 12.0) or (max_rsi_5d >= 72.0) or (has_pos and max_pnl_pct >= 12.0)
            if is_crazy_bull:
                # 高位疯牛档: 收紧保护绳，跟踪 EMA10
                is_break_ema10 = (
                    ema10 > 0
                    and last < ema10
                    and (prev_close >= prev_ema10 if (prev_close > 0 and prev_ema10 > 0) else True)
                    and last > ma50
                    and bias > 0.0
                )
                if is_break_ema10:
                    is_tp = True
                    tp_reasons.append(f"高位跌破EMA10(现价${last:.2f} < EMA10 ${ema10:.2f})")
                    signals.append("🟣 高位跌破EMA10(急速锁利)")
                elif last >= ema10:
                    signals.append("🔵 疯牛主升(设EMA10紧密保护)")
                    is_momentum = True
            else:
                # 常规主升档: (+5% <= Bias_MA50 < +12% 且 RSI < 72)
                is_break_ema20 = (
                    ema20 > 0
                    and last < ema20
                    and (prev_close >= prev_ema20 if (prev_close > 0 and prev_ema20 > 0) else True)
                    and last > ma50
                    and bias >= 5.0
                    and high_bias_recently
                )
                if is_break_ema20:
                    is_tp = True
                    tp_reasons.append(f"跌破EMA20移动止盈(现价${last:.2f} < EMA20 ${ema20:.2f})")
                    signals.append("🟣 跌破EMA20(移动止盈)")
                elif (bias >= 5.0 and rsi >= 55.0 and last >= ema20):
                    signals.append("🔵 多头加速(设EMA20移动保护)")
                    is_momentum = True

        if is_tp and not any(sig in signals for sig in ["🟣 跌破EMA20(移动止盈)", "🟣 高位跌破EMA10(急速锁利)", "🟣 触及强阻减仓(建议兑现50%锁利)"]):
            signals.append("🟣 冲顶减仓" if is_climax_tp else "🟣 止盈锁定")

        # 3. 黄金坑就位与右侧反转 V2.4 (对齐 V2.4 严选准入铁律)
        if not is_price_break and not is_tp:
            # V2.4 准入铁律判定
            # V3.1 准入铁律 (补齐 rs_60 >= -8.0 相对强弱门槛)
            cond_bull_trend = (sma200 <= 0 or last > sma200) and (sma200 <= 0 or ma50 >= sma200 * 0.97) and (rs_60 >= -8.0)
            cond_pit_depth = (-0.20 <= fib_pos <= 2.20)
            cond_pit_atr = (atr_pct <= 5.2)
            cond_no_crash = (not crash_5d)
            cond_pit_oversold = (min_rsi_3d <= 45.0) and (rsi <= 50.0)
            cond_right_turn = is_right_turn

            # V3.1 买点 1: 🟢 黄金坑右侧反转
            cond_golden_pit_v2 = (
                cond_bull_trend and cond_pit_depth and cond_pit_atr
                and cond_no_crash and cond_pit_oversold and cond_right_turn
            )

            # V3.1 买点 2: 🚀 极度超卖右侧反包 (min_rsi_3d <= 32.0, 放量反包 +1.5% 且 vol_ratio >= 1.15)
            is_reversal_body = (open_price <= 0 or last > open_price) and (prev_close <= 0 or last >= prev_close * 1.015)
            cond_climax_reversal = (
                cond_bull_trend and cond_no_crash and (min_rsi_3d <= 32.0)
                and is_reversal_body and (vol_ratio >= 1.15)
            )

            # 兼容宏观大盘熊市主跌段过滤
            is_bear_falling_knife = False
            if macro_data:
                qqqm_info = macro_data.get("QQQ", {})
                qqqm_last = qqqm_info.get("last", 1.0)
                qqqm_ma200 = qqqm_info.get("ma200", 0.0)
                qqqm_rsi = qqqm_info.get("rsi", 50.0)
                is_bear_falling_knife = (qqqm_ma200 > 0) and (qqqm_last < qqqm_ma200) and (qqqm_rsi > 35.0)

            # V3.1 QQQ MA50 大盘准入门禁 (QQQ < MA50 时冻结个股新开仓，彻底规避震荡期连环止损)
            is_qqq_below_ma50 = False
            if macro_data:
                qqq_info = macro_data.get("QQQ", macro_data.get("QQQ", {}))
                qqq_last = qqq_info.get("last", 1.0)
                qqq_ma50 = qqq_info.get("ma50", 0.0)
                if qqq_ma50 > 0 and qqq_last < qqq_ma50:
                    is_qqq_below_ma50 = True

            REMOVED_TICKERS = {'KO', 'PEP', 'MCD', 'NKE', 'V', 'INTC', 'WDC', 'HPE', 'MRNA', 'JPM', 'XOM', 'WMT', 'CAT', 'NASA'}
            if cond_golden_pit_v2 or cond_climax_reversal:
                if ticker in REMOVED_TICKERS:
                    signals.append("🟡 坑内观察(低波慢牛不作开仓)")
                elif is_bear_falling_knife:
                    signals.append("🟡 观望蓄势(熊市空头严禁接刀)")
                elif is_qqq_below_ma50:
                    signals.append("🟡 观望蓄势(大盘破MA50防御门禁)")
                else:
                    if cond_climax_reversal:
                        signals.append("🚀 极度超卖右侧反包")
                    else:
                        signals.append("🟢 黄金坑右侧反转")
            elif cond_bull_trend and cond_pit_depth and cond_pit_atr and cond_no_crash:
                if is_bear_falling_knife:
                    signals.append("🟡 观望蓄势(熊市空头严禁接刀)")
                elif rsi <= 50.0:
                    signals.append("🟡 黄金坑蓄势(等待右侧确认)")
                elif fib_low * 0.985 <= last <= fib_high * 1.015:
                    signals.append("🟡 坑内观察(防逆市阴跌)")

    # 4. 量能异动判定
    if vol_ratio >= 1.4 and "🟡 量能异动" not in signals and "放量" not in str(tp_reasons):
        signals.append("🟡 量能异动")

    if not signals:
        primary_signal = "🔵 多头推进" if last > ma50 else "⚪ 震荡蓄势"
    else:
        primary_signal = " · ".join(signals)

    return {
        "dist_to_pit": dist_to_pit,
        "pit_status": pit_status,
        "fractional_shares": fractional_shares,
        "signal": primary_signal,
        "tp_reason": " · ".join(tp_reasons) if tp_reasons else "",
        "atr_mult": atr_mult,
        "is_pit": ("🟢 黄金坑右侧反转" in primary_signal) or ("🟢 黄金坑就位" in primary_signal) or ("🚀 极度超卖右侧反包" in primary_signal),
        "is_momentum": is_momentum or ("🔵 多头加速" in primary_signal),
        "is_stop": "🔴 破位止损" in primary_signal or "🔴 触及硬止损" in primary_signal,
        "is_deep_pullback": is_deep_pullback or ("🟠 深度回踩" in primary_signal),
        "is_take_profit": is_tp or ("🟣" in primary_signal),
        "is_volume": "🟡 量能异动" in primary_signal,
        "struct_stop": round(struct_stop, 2),
        "stop_loss": round(struct_stop, 2),
        "low_price": round(sig_low, 2),
        "rth_low": round(sig_low, 2)
    }

# ==============================================================================
# 五、 页面 1：宏观大盘司令塔 (macro_radar.html · 10 只 ETF)
# ==============================================================================
def ui_display_price(item):
    """扩展时段优先显示真实盘前/盘后价，否则回退正规盘 last。"""
    try:
        if float(item.get("ext_price", 0) or 0) > 0 and float(item.get("active_price", 0) or 0) > 0:
            return float(item["active_price"])
        return float(item.get("last", 0) or 0)
    except Exception:
        return 0.0


def ui_display_pct(item):
    try:
        if float(item.get("ext_price", 0) or 0) > 0 and item.get("effective_pct") is not None:
            return float(item["effective_pct"])
        return float(item.get("pct_change", 0) or 0)
    except Exception:
        return 0.0


def sync_live_prices_to_predict_html(macro_data=None, stocks_data=None, session_code=""):
    """
    轻量级每分钟实时价格同步引擎 (<0.2s，无需重跑 LightGBM)：
    直接读取 data_cache/latest_ai_predictions.json 与 predict_radar_5y.html，
    将全部 70 只标的的右上角最新报价 (current_price)、timeCycleData 历史序列最后一根“今日节点”价格，
    以及未来 15 日推演路径的起点按最新现价实时对齐更新（保持原有 15 日涨跌幅波形比例不变，仅锚定最新现价）。
    同时保证 BTC-USD 未来 T+1~T+15 按 7x24 连续自然日历（含周六、周日）递增。
    """
    t0 = time.perf_counter()
    try:
        import json as _json
        import ml_predictor_5y

        live = {}
        _pairs = []
        if isinstance(macro_data, dict):
            _pairs.extend([(str(k), v) for k, v in macro_data.items() if isinstance(v, dict)])
        if isinstance(stocks_data, (list, tuple)):
            _pairs.extend([(str(v.get("ticker")), v) for v in stocks_data if isinstance(v, dict) and v.get("ticker")])
        for tk, item in _pairs:
            px_val = ui_display_price(item)
            if tk and px_val > 0:
                live[tk] = round(float(px_val), 2)

        json_path = os.path.join(SCRATCH_DIR, "data_cache", "latest_ai_predictions.json")
        html_path = os.path.join(SCRATCH_DIR, "predict_radar_5y.html")
        if not os.path.exists(html_path):
            return False

        ai_payload = None
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as jf:
                    ai_payload = _json.load(jf)
            except Exception:
                ai_payload = None

        s = open(html_path, "r", encoding="utf-8").read()
        key = "const timeCycleData = "
        i = s.find(key)
        if i < 0:
            return False
        j = i + len(key)
        html_tc_data, end = _json.JSONDecoder().raw_decode(s[j:])
        if not isinstance(html_tc_data, dict):
            return False

        by_tk = (ai_payload.get("by_ticker") if isinstance(ai_payload, dict) else None) or {}
        ml_predictor_5y.align_time_cycle_projections_with_live(
            tc_dict=html_tc_data,
            live_prices=live,
            by_ticker=by_tk,
            session_code=session_code,
        )

        if isinstance(ai_payload, dict):
            ai_payload["time_cycle_projections"] = html_tc_data
            for list_key in ("all_predictions", "table4", "top10", "top_long", "top_short"):
                arr = ai_payload.get(list_key)
                if isinstance(arr, list):
                    for row in arr:
                        if isinstance(row, dict) and row.get("ticker") in live:
                            row["latest_price"] = live[row["ticker"]]
                            row["current_price"] = live[row["ticker"]]
                            if row["ticker"] in html_tc_data:
                                tc_row = html_tc_data[row["ticker"]]
                                if tc_row.get("r2"):
                                    row["r2"] = tc_row["r2"]
                                for k_v46 in ("v46_regime_mode", "bear_inertia_active", "p15_t5_pct", "p15_t15_pct", "p85_t15_pct"):
                                    if tc_row.get(k_v46) is not None:
                                        row[k_v46] = tc_row[k_v46]
            with open(json_path, "w", encoding="utf-8") as jf:
                _json.dump(ai_payload, jf, ensure_ascii=False, indent=2)

        html_out = s[:j] + _json.dumps(html_tc_data, ensure_ascii=False) + s[j + end:]
        try:
            import jin10_news_sentinel as _jn_sync
            _b_start = "<!-- MACRO_CALENDAR_BANNER_START -->"
            _b_end = "<!-- MACRO_CALENDAR_BANNER_END -->"
            _i1 = html_out.find(_b_start)
            _i2 = html_out.find(_b_end, _i1) if _i1 >= 0 else -1
            if _i1 >= 0 and _i2 > _i1:
                _mnews_sync = _jn_sync.load_latest_market_news_cache(os.path.join(SCRATCH_DIR, "data_cache", "latest_market_news.json"))
                _new_banner = _jn_sync.render_macro_calendar_banner_html(_mnews_sync, variant="predict")
                html_out = html_out[:_i1] + _new_banner + html_out[_i2 + len(_b_end):]
        except Exception:
            pass
        with open(html_path, "w", encoding="utf-8") as hf:
            hf.write(html_out)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        print(f"⚡ [轻量实时同步] 已对齐 {len(html_tc_data)} 标的 predict_radar_5y.html 现价/今日节点/15日推演起点 (耗时 {elapsed_ms:.1f}ms)")
        return True
    except Exception as e:
        print(f"[!] 路径图轻量实时同步失败: {e}")
    return False


patch_predict_time_cycle_anchor = sync_live_prices_to_predict_html


def _snap_opt_float(val, digits: Optional[int] = 2) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        fv = float(val)
        if not math.isfinite(fv):
            return None
        return round(fv, digits) if digits is not None else fv
    except Exception:
        return None


def _coerce_et_datetime_for_snapshot(now_et=None, now_str: Optional[str] = None) -> datetime:
    et_tz = ZoneInfo("America/New_York") if ZoneInfo is not None else timezone(timedelta(hours=-5))
    if isinstance(now_et, datetime):
        return now_et.replace(tzinfo=et_tz) if now_et.tzinfo is None else now_et.astimezone(et_tz)
    if now_str:
        s = str(now_str).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(s[:19], fmt).replace(tzinfo=et_tz)
            except Exception:
                continue
    return datetime.now(et_tz)


def _compute_strategy_a_b_statuses(vix_regime: Dict[str, Any], session_code: str, cur_et_dt: datetime) -> Tuple[str, str]:
    vr = vix_regime if isinstance(vix_regime, dict) else {}
    vix_spot = _snap_opt_float(vr.get("vix_spot"), 2)
    vix_pct = _snap_opt_float(vr.get("vix_pct"), 2)
    ratio = _snap_opt_float(vr.get("vix_term_ratio"), 3)
    vix_peak = _snap_opt_float(vr.get("vix_10d_peak"), 2) or 0.0
    ratio_peak = _snap_opt_float(vr.get("vix_term_ratio_10d_peak"), 3) or 0.0
    vix_state = str(vr.get("vix_state") or "UNAVAILABLE").upper()
    hard_brake = bool(vr.get("vix_hard_brake", False))
    bottom_sig = bool(vr.get("vix_bottom_signal", False))
    bottom_preview = bool(vr.get("vix_bottom_premarket_preview", False))

    if vix_spot is None or vix_state == "UNAVAILABLE":
        return "⚪ VIX 数据暂缺", "⚪ VIX 数据暂缺"

    if hard_brake:
        strat_a = "🔴 倒挂硬刹车锁定"
    elif bottom_sig:
        strat_a = "🟢 抄底观察窗已激活"
    elif bottom_preview:
        strat_a = "🟡 盘前预热 · 待 09:30 确认"
    else:
        strat_a = "⚪ 未触发 (待恐慌出清)"

    hm = cur_et_dt.hour * 60 + cur_et_dt.minute
    in_b_window = (str(session_code).upper() == "REGULAR") and (cur_et_dt.weekday() < 5) and (9 * 60 + 45 <= hm <= 15 * 60)
    if hard_brake or vix_state == "BACKWARDATION_BRAKE" or (ratio is not None and ratio >= 1.00):
        strat_b = "🔴 倒挂期绝对禁多"
    elif not in_b_window:
        strat_b = "⏳ 时段锁 (仅美东 09:45~15:00 开放)"
    elif (vix_peak >= 24.0 or ratio_peak >= 1.00) and (vix_pct is not None and vix_pct <= -5.0):
        strat_b = "🟢 退潮窗口打开 (需站稳VWAP+5m收阳)"
    else:
        strat_b = "⚪ 待命 (需10日恐慌背景且当日^VIX≤-5%)"
    return strat_a, strat_b


def build_latest_radar_snapshot(
    macro_data=None,
    stocks_data=None,
    weather_info=None,
    vix_regime=None,
    cross_market_liquidity=None,
    intraday_forecasts=None,
    ai_predictions=None,
    session_code: str = "REGULAR",
    session_name: str = "",
    now_str: Optional[str] = None,
    now_et: Optional[datetime] = None,
    opex_shield: Optional[Dict[str, Any]] = None,
    daily_sr_anchors: Optional[Dict[str, Any]] = None,
    market_temperature: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建供 Discord 交互机器人零 API 压力只读消费的轻量全市场快照。"""
    sess = str(session_code or "REGULAR").strip().upper() or "REGULAR"
    sess_name_map = {
        "REGULAR": "常规盘中",
        "PRE_MARKET": "盘前交易",
        "POST_MARKET": "盘后交易",
        "OVERNIGHT": "美股夜盘",
        "WEEKEND": "周末休市",
    }
    sess_name_clean = str(session_name or "").strip() or sess_name_map.get(sess, sess)
    cur_et_dt = _coerce_et_datetime_for_snapshot(now_et=now_et, now_str=now_str)
    updated_at_et = str(now_str or cur_et_dt.strftime("%Y-%m-%d %H:%M:%S"))
    wi = weather_info if isinstance(weather_info, dict) else {}

    vr_raw = vix_regime if isinstance(vix_regime, dict) and vix_regime else (wi.get("vix_regime") if isinstance(wi.get("vix_regime"), dict) else {})
    strat_a, strat_b = _compute_strategy_a_b_statuses(vr_raw, sess, cur_et_dt)
    vr_out = {
        "as_of_date": vr_raw.get("as_of_date"),
        "updated_at": vr_raw.get("updated_at"),
        "vix_spot": _snap_opt_float(vr_raw.get("vix_spot"), 2),
        "vix_pct": _snap_opt_float(vr_raw.get("vix_pct"), 2),
        "vxn_spot": _snap_opt_float(vr_raw.get("vxn_spot"), 2),
        "vxn_pct": _snap_opt_float(vr_raw.get("vxn_pct"), 2),
        "vxn_vix_spread": _snap_opt_float(vr_raw.get("vxn_vix_spread"), 2),
        "tech_fear_label": str(vr_raw.get("tech_fear_label") or "⚪ 科技波动平稳"),
        "vix3m_spot": _snap_opt_float(vr_raw.get("vix3m_spot"), 2),
        "vix_term_ratio": _snap_opt_float(vr_raw.get("vix_term_ratio"), 3),
        "term_structure_state": str(vr_raw.get("term_structure_state") or "UNKNOWN"),
        "term_structure_cn": str(vr_raw.get("term_structure_cn") or "数据暂缺"),
        "vvix_spot": _snap_opt_float(vr_raw.get("vvix_spot"), 2),
        "tnx_yield": _snap_opt_float(vr_raw.get("tnx_yield"), 2),
        "vix_10d_peak": _snap_opt_float(vr_raw.get("vix_10d_peak"), 2),
        "vix_drawdown_from_peak_pct": _snap_opt_float(vr_raw.get("vix_drawdown_from_peak_pct"), 1),
        "vix_term_ratio_10d_peak": _snap_opt_float(vr_raw.get("vix_term_ratio_10d_peak"), 3),
        "vix_state": str(vr_raw.get("vix_state") or "UNAVAILABLE").upper(),
        "vix_hard_brake": bool(vr_raw.get("vix_hard_brake", False)),
        "raw_bottom_signal": bool(vr_raw.get("raw_bottom_signal", False)),
        "vix_bottom_signal": bool(vr_raw.get("vix_bottom_signal", False)),
        "vix_bottom_premarket_preview": bool(vr_raw.get("vix_bottom_premarket_preview", False)),
        "pill_text": str(vr_raw.get("pill_text") or "⚪ VIX 暂缺 · 常规门禁"),
        "summary_cn": str(vr_raw.get("summary_cn") or "波动率监测中"),
        "advice_cn": str(vr_raw.get("advice_cn") or ""),
        "degraded": bool(vr_raw.get("degraded", False)),
        "strategy_a_status": strat_a,
        "strategy_b_status": strat_b,
    }

    liq_raw = (
        cross_market_liquidity
        if isinstance(cross_market_liquidity, dict) and cross_market_liquidity
        else (wi.get("cross_market_liquidity") if isinstance(wi.get("cross_market_liquidity"), dict) else {})
    )
    liq_out = {
        "hyg_ief_ratio": _snap_opt_float(liq_raw.get("hyg_ief_ratio"), 4),
        "hyg_ief_1d_pct": _snap_opt_float(liq_raw.get("hyg_ief_1d_pct"), 2),
        "hyg_ief_5d_pct": _snap_opt_float(liq_raw.get("hyg_ief_5d_pct"), 2),
        "dxy_spot": _snap_opt_float(liq_raw.get("dxy_spot"), 2),
        "dxy_1d_pct": _snap_opt_float(liq_raw.get("dxy_1d_pct"), 2),
        "dxy_5d_pct": _snap_opt_float(liq_raw.get("dxy_5d_pct"), 2),
        "usdjpy_spot": _snap_opt_float(liq_raw.get("usdjpy_spot"), 2),
        "usdjpy_1d_pct": _snap_opt_float(liq_raw.get("usdjpy_1d_pct"), 2),
        "usdjpy_5d_pct": _snap_opt_float(liq_raw.get("usdjpy_5d_pct"), 2),
        "stress_level": str(liq_raw.get("stress_level") or "degraded"),
        "stress_flags": list(liq_raw.get("stress_flags") or []),
        "badge_cn": str(liq_raw.get("badge_cn") or "⚪ 跨市场流动性暂缺"),
        "summary_cn": str(liq_raw.get("summary_cn") or ""),
        "degraded": bool(liq_raw.get("degraded", False)),
    }

    if isinstance(opex_shield, dict) and opex_shield:
        opex_out = dict(opex_shield)
    else:
        try:
            import jin10_news_sentinel as _jn_opx
            opex_out = _jn_opx.compute_opex_shield_info(cur_et_dt.date())
        except Exception:
            opex_out = {"active": False, "timing": "NONE", "badge": "", "headline": "", "detail": ""}

    # 加载本地已生成的 AI 预测与盘中预测走廊（纯磁盘读取，0 网络）
    ai_doc = ai_predictions if isinstance(ai_predictions, dict) else {}
    if not ai_doc:
        _ai_path = os.path.join(SCRATCH_DIR, "data_cache", "latest_ai_predictions.json")
        if os.path.exists(_ai_path):
            try:
                with open(_ai_path, "r", encoding="utf-8") as _af:
                    ai_doc = json.load(_af) or {}
            except Exception:
                ai_doc = {}
    ai_by_tk: Dict[str, Dict[str, Any]] = {}
    if isinstance(ai_doc.get("by_ticker"), dict):
        for k, v in ai_doc["by_ticker"].items():
            if isinstance(v, dict):
                ai_by_tk[str(k).upper()] = v
    tc_by_tk: Dict[str, Dict[str, Any]] = {}
    if isinstance(ai_doc.get("time_cycle_projections"), dict):
        for k, v in ai_doc["time_cycle_projections"].items():
            if isinstance(v, dict):
                tc_by_tk[str(k).upper()] = v
    if isinstance(ai_doc.get("all_predictions"), list):
        for row in ai_doc["all_predictions"]:
            if isinstance(row, dict) and row.get("ticker"):
                tk_u = str(row["ticker"]).upper()
                merged = dict(row)
                merged.update(ai_by_tk.get(tk_u, {}))
                ai_by_tk[tk_u] = merged

    ip_doc = intraday_forecasts if isinstance(intraday_forecasts, dict) else {}
    if not ip_doc:
        _ip_path = os.path.join(SCRATCH_DIR, "data_cache", "intraday_path_forecasts.json")
        if os.path.exists(_ip_path):
            try:
                with open(_ip_path, "r", encoding="utf-8") as _ipf:
                    ip_doc = json.load(_ipf) or {}
            except Exception:
                ip_doc = {}
    ip_by_tk = ip_doc.get("by_ticker") if isinstance(ip_doc.get("by_ticker"), dict) else {}

    sr_anchors_map: Dict[str, Dict[str, Any]] = {}
    if isinstance(daily_sr_anchors, dict):
        inner = daily_sr_anchors.get("anchors") if isinstance(daily_sr_anchors.get("anchors"), dict) else daily_sr_anchors
        sr_anchors_map = {str(k).upper(): v for k, v in inner.items() if isinstance(v, dict)}
    elif ai_predictions is None:
        try:
            import sr_anchor_engine as _sr_snap_mod
            loaded_anc = _sr_snap_mod.load_daily_sr_anchors().get("anchors") or {}
            sr_anchors_map = {str(k).upper(): v for k, v in loaded_anc.items() if isinstance(v, dict)}
        except Exception:
            sr_anchors_map = {}

    # 统一收集全部已刷新的 ETF + 个股（确保不混入未监控指数）
    raw_items: Dict[str, Tuple[Dict[str, Any], bool]] = {}
    if isinstance(macro_data, dict):
        for tk_m, m_val in macro_data.items():
            if isinstance(m_val, dict):
                tk_u = str(tk_m).upper()
                raw_items[tk_u] = ({"ticker": tk_u, **m_val}, True)
    if isinstance(stocks_data, (list, tuple)):
        for s_val in stocks_data:
            if isinstance(s_val, dict) and s_val.get("ticker"):
                tk_u = str(s_val["ticker"]).upper()
                if tk_u not in raw_items:
                    raw_items[tk_u] = (dict(s_val), False)
    elif isinstance(stocks_data, dict):
        for tk_s, s_val in stocks_data.items():
            if isinstance(s_val, dict):
                tk_u = str(tk_s).upper()
                if tk_u not in raw_items:
                    raw_items[tk_u] = ({"ticker": tk_u, **s_val}, False)

    # Top 5 抗跌领涨真龙头榜
    try:
        from create_futu_terminal import compute_resilient_leaders_top5
        top5_raw = compute_resilient_leaders_top5(
            [v for v, is_etf in raw_items.values() if not is_etf],
            macro_data={k: v for k, (v, is_etf) in raw_items.items() if is_etf},
            session_code=sess,
            top_n=5,
        )
    except Exception:
        top5_raw = []

    top5_out = []
    for idx_ld, ld in enumerate(top5_raw or [], 1):
        tk_ld = str(ld.get("ticker") or "").upper()
        src_ld = raw_items.get(tk_ld, ({}, False))[0]
        if sess in ("POST_MARKET", "WEEKEND"):
            px_ld = _snap_opt_float(src_ld.get("last") or ld.get("rth_close") or src_ld.get("active_price"), 2)
        else:
            px_ld = _snap_opt_float(src_ld.get("active_price") or src_ld.get("last"), 2)
        top5_out.append({
            "rank": idx_ld,
            "ticker": tk_ld,
            "name": str(ld.get("name") or src_ld.get("name") or tk_ld),
            "cat": str(src_ld.get("cat") or ""),
            "price": px_ld,
            "rth_close": _snap_opt_float(src_ld.get("last") or ld.get("rth_close"), 2),
            "rth_pct": _snap_opt_float(src_ld.get("pct_change"), 2),
            "pct": _snap_opt_float(ld.get("pct"), 2),
            "excess_vs_spy": _snap_opt_float(ld.get("excess_vs_spy"), 2),
            "rs_20d": _snap_opt_float(ld.get("rs_20d"), 2),
            "above_vwap": bool(ld.get("above_vwap", False)),
            "vwap_tag": str(ld.get("vwap_tag") or ""),
        })

    corridor_label_map = {
        "ABOVE_P75": "突破上沿",
        "BELOW_P25": "跌破下沿",
        "IN_CORRIDOR": "贴合剧本",
    }

    tickers_out: Dict[str, Dict[str, Any]] = {}
    for tk_u, (src, is_etf_flag) in raw_items.items():
        ai_item = ai_by_tk.get(tk_u, {})
        tc_item = tc_by_tk.get(tk_u, {})
        meta_fallback = (MONITORED_TARGETS.get("ETFS", {}).get(tk_u) or MONITORED_TARGETS.get("STOCKS", {}).get(tk_u) or {})
        name = str(src.get("name") or meta_fallback.get("name") or tk_u)
        sub = str(src.get("sub") or meta_fallback.get("sub") or "")
        cat = str(src.get("cat") or meta_fallback.get("cat") or "")

        last_px = _snap_opt_float(src.get("last"), 2)
        ext_px = _snap_opt_float(src.get("ext_price"), 2)
        active_px = _snap_opt_float(src.get("active_price") or (ext_px if (ext_px and ext_px > 0) else last_px) or last_px, 2)
        pct_chg = _snap_opt_float(src.get("pct_change"), 2)
        ext_pct = _snap_opt_float(src.get("ext_pct"), 2)
        eff_pct = _snap_opt_float(
            src.get("effective_pct")
            if src.get("effective_pct") is not None
            else (ext_pct if (ext_px and ext_px > 0 and ext_pct is not None) else pct_chg),
            2,
        )
        sess_tag = str(src.get("session_tag") or ("盘中" if sess == "REGULAR" else sess_name_clean[:2]))

        vwap_val = _snap_opt_float(src.get("vwap"), 2)
        if vwap_val is None or vwap_val <= 0:
            vwap_val = last_px
        vwap_stale = (sess != "REGULAR")
        vwap_diff = (
            _snap_opt_float(((active_px - vwap_val) / vwap_val) * 100.0, 2)
            if (active_px and vwap_val and vwap_val > 0)
            else None
        )

        long_wr = _snap_opt_float(ai_item.get("calibrated_win_rate_pct"), 1)
        long_raw = _snap_opt_float(ai_item.get("model_raw_prob_pct") if ai_item.get("model_raw_prob_pct") is not None else ai_item.get("predicted_win_rate_pct"), 1)
        short_wr = _snap_opt_float(ai_item.get("calibrated_short_win_rate_pct"), 1)
        short_raw = _snap_opt_float(
            ai_item.get("model_raw_short_prob_pct")
            if ai_item.get("model_raw_short_prob_pct") is not None
            else ai_item.get("short_model_raw_prob_pct"),
            1,
        )
        ai_act = str(ai_item.get("action") or "")
        ai_short_act = str(ai_item.get("short_action") or "")
        if bool(ai_item.get("is_short_covering")) and ai_act:
            ai_act_disp = ai_act
        elif ai_short_act and (((short_wr or 0.0) > (long_wr or 0.0)) or ("🩸" in ai_short_act and not ai_act)):
            ai_act_disp = ai_short_act
        else:
            ai_act_disp = ai_act or ai_short_act

        sig_str = str(src.get("signal") or "")
        is_inv = bool(src.get("is_inverse_hedge") or tk_u in ("SQQQ", "SOXS", "VIXY"))
        is_ipo = bool(src.get("is_ipo_pending") or (float(src.get("ipo_price") or 0) > 0 and "IPO" in sig_str))
        is_crash = bool(src.get("is_crash_halt"))
        is_cooling = bool(src.get("is_cooling_halt"))
        is_broken_bear = bool(src.get("broken_bearish") or src.get("is_broken_bearish"))
        is_broken_reb = bool(src.get("broken_rebound_ready") or src.get("is_broken_rebound"))
        is_stop = bool(src.get("is_stop"))
        is_pit = bool(src.get("is_pit"))
        is_pit_watch = ("坑内观察" in sig_str)
        is_tp = bool(src.get("is_take_profit"))
        is_deep_pb = bool(src.get("is_deep_pullback"))
        is_bull = bool(src.get("is_momentum") or ("多头" in sig_str and not is_tp and not is_stop and not is_crash and not is_cooling))

        bear_kw = any(k in ai_act_disp for k in ("建议买入反向ETF对冲", "空头占优", "强力做空", "逢高做空"))
        is_bear_card = bool((not is_inv) and (not is_ipo) and (is_broken_bear or is_crash or is_cooling or is_stop or bear_kw))

        if is_ipo:
            stance, verdict, verdict_sub = "NEUTRAL", "🆕 IPO 新股观察期 · 先看少动", "上市不足 5 日，观察换手与 VWAP 承接。"
        elif is_crash:
            stance, verdict, verdict_sub = "BEAR", "🔴 暴跌风险期 · 严禁抄底接刀", "短线剧烈跳空或大跌，模型冻结买点等待日线企稳。"
        elif is_cooling:
            stance, verdict, verdict_sub = "BEAR", "🔴 大跌冷却期 · 暂缓买入", "近 3 日经历剧烈下跌，等待日线右侧企稳确认。"
        elif is_broken_bear:
            stance, verdict, verdict_sub = "BEAR", "🔴 空头占优 · 严禁左侧接刀", "股价压制于日内均价线与支撑带下方，未站稳前别急着抄底。"
        elif is_stop:
            stance, verdict, verdict_sub = "BEAR", "🔴 跌破支撑线 · 防守为主", "关键支撑位破位，严格风控不盲目补仓。"
        elif is_pit_watch:
            stance, verdict, verdict_sub = "NEUTRAL", "🟡 接近低吸区但板块偏弱 · 先观望", "行业 ETF 偏弱，暂不抢跑左侧接刀。"
        elif is_pit:
            stance, verdict, verdict_sub = "BULL", "🟢 低吸窗口打开 · 可按参考挂单", "回踩支撑区间且呈现止跌迹象，同步设好止损位。"
        elif is_broken_reb:
            stance, verdict, verdict_sub = "NEUTRAL", "🟡 破位后短线反抽 · 快进快出", "中期趋势仍弱，仅限小仓位盯紧短止损与压力 R1。"
        elif is_tp:
            stance, verdict, verdict_sub = "NEUTRAL", "🟣 高位震荡 / 跌破短均 · 注意分批止盈", "短期积累较大涨幅或跌破短均保护，建议落袋保护利润。"
        elif is_deep_pb:
            stance, verdict, verdict_sub = "NEUTRAL", "⚪ 深度回调 · 等待站稳均价线", "处于回调消化期，等待日内放量站稳 VWAP 再考虑低吸。"
        elif is_inv:
            stance, verdict, verdict_sub = "NEUTRAL", "🛡️ 反向避险工具 · 仅限短线对冲", "具备杠杆/展期损耗，仅适合大盘下跌时短线防守对冲。"
        elif is_bull:
            stance, verdict, verdict_sub = "BULL", "🟢 趋势偏强 · 顺势持有", "运行在中期均线上方，顺势持有为主，回踩不破再低吸。"
        else:
            stance, verdict, verdict_sub = "NEUTRAL", "⚪ 震荡整理 · 等待高胜率买点", "多空暂时胶着，控制仓位耐心等待明确买点。"

        if is_bear_card and stance != "BEAR":
            stance = "BEAR"
            if bear_kw:
                verdict = "🔴 空头占优 · 严禁左侧接刀"
                verdict_sub = str(ai_act_disp)

        # V4.4 静态支撑/阻力锚点 + 对称顶底互换状态 + 真实筹码/ATR区分 + 4H见底绿标压制
        anchor_row = sr_anchors_map.get(tk_u) if isinstance(sr_anchors_map, dict) else None
        is_sc_flag = bool(ai_item.get("is_short_covering", False))
        is_bk_flag = bool(ai_item.get("is_broken", False) or is_broken_bear or is_stop)
        if not (isinstance(anchor_row, dict) and anchor_row.get("anchor_s1")):
            fallback_s1 = _snap_opt_float(ai_item.get("anchor_s1") or ai_item.get("s1") or src.get("broken_s1") or src.get("entry_stop_loss"), 2)
            fallback_s2 = _snap_opt_float(ai_item.get("anchor_s2") or ai_item.get("s2") or (round(float(fallback_s1) * 0.96, 2) if fallback_s1 else None), 2)
            fallback_r1 = _snap_opt_float(ai_item.get("anchor_r1") or ai_item.get("r1") or src.get("broken_r1") or ai_item.get("target_profit_price"), 2)
            fallback_r2 = _snap_opt_float(ai_item.get("anchor_r2") or ai_item.get("r2") or (round(float(fallback_r1) * 1.04, 2) if fallback_r1 else None), 2)
            if fallback_s1 and fallback_r1:
                anchor_row = {
                    "anchor_s1": fallback_s1,
                    "anchor_s2": fallback_s2 or round(fallback_s1 * 0.96, 2),
                    "anchor_sl": _snap_opt_float(ai_item.get("anchor_sl") or ai_item.get("sl") or round((fallback_s2 or fallback_s1 * 0.96) * 0.985, 2), 2),
                    "anchor_r1": fallback_r1,
                    "anchor_r2": fallback_r2 or round(fallback_r1 * 1.04, 2),
                    "s1_source": str(ai_item.get("s1_source") or "ATR_ESTIMATE"),
                    "s2_source": str(ai_item.get("s2_source") or "ATR_ESTIMATE"),
                    "r1_source": str(ai_item.get("r1_source") or "ATR_ESTIMATE"),
                    "r2_source": str(ai_item.get("r2_source") or "ATR_ESTIMATE"),
                }

        sr_eval_out: Dict[str, Any] = {}
        if isinstance(anchor_row, dict) and anchor_row.get("anchor_s1") and anchor_row.get("anchor_r1"):
            try:
                import sr_anchor_engine as _sr_snap_eval
                sr_eval_out = _sr_snap_eval.evaluate_sr_breach_state(
                    price=active_px,
                    anchor=anchor_row,
                    is_halt=bool(is_crash or is_cooling),
                    is_short_covering=is_sc_flag,
                    is_broken=is_bk_flag,
                    bias_ma50=float(src.get("bias") or 0.0),
                    vwap=vwap_val,
                    base_script=str(ai_item.get("scenario_script") or ""),
                )
            except Exception:
                sr_eval_out = {}

        r1_val = _snap_opt_float(sr_eval_out.get("anchor_r1") or ai_item.get("r1") or src.get("broken_r1") or ai_item.get("target_profit_price"), 2)
        r2_val = _snap_opt_float(sr_eval_out.get("anchor_r2") or ai_item.get("r2"), 2)
        s1_val = _snap_opt_float(sr_eval_out.get("anchor_s1") or ai_item.get("s1") or src.get("broken_s1") or ai_item.get("short_target_profit_price") or src.get("entry_stop_loss"), 2)
        s2_val = _snap_opt_float(sr_eval_out.get("anchor_s2") or ai_item.get("s2"), 2)

        raw_rsi14 = _snap_opt_float(src.get("rsi"), 1)
        raw_bias50 = _snap_opt_float(src.get("bias"), 2)
        base_4h_div = ai_item.get("four_h_div")
        base_4h_tag = ai_item.get("four_h_tag")
        if not base_4h_div:
            if raw_rsi14 is not None and raw_bias50 is not None and raw_rsi14 < 32.0 and raw_bias50 < -10.0:
                base_4h_div, base_4h_tag = "🟢 4H 底背离拐点 (见底信号)", "div-bull"
            elif raw_rsi14 is not None and raw_bias50 is not None and raw_rsi14 > 68.0 and raw_bias50 > 10.0:
                base_4h_div, base_4h_tag = "🔴 4H 顶背离滞涨 (防回踩)", "div-bear"
            elif raw_rsi14 is not None and raw_bias50 is not None and raw_bias50 >= -4.0 and raw_rsi14 >= 45.0:
                base_4h_div, base_4h_tag = "⚡ 4H 均线多头共振", "div-trend"
            else:
                base_4h_div, base_4h_tag = "➖ 4H 均线震荡纠缠", "div-neutral"
        try:
            import sr_anchor_engine as _sr_snap_fh
            sup_fh_out = _sr_snap_fh.suppress_resonance_badge(
                four_h_div=base_4h_div,
                four_h_tag=base_4h_tag or "div-neutral",
                is_crash_halt=is_crash,
                is_cooling_halt=is_cooling,
                vix_regime=vr_out,
            )
        except Exception:
            sup_fh_out = {
                "four_h_div": str(base_4h_div),
                "four_h_tag": str(base_4h_tag or "div-neutral"),
                "resonance_badge_text": str(base_4h_div),
                "resonance_badge_tag": str(base_4h_tag or "div-neutral"),
                "resonance_suppressed": False,
            }

        ip_entry = ip_by_tk.get(tk_u) if isinstance(ip_by_tk.get(tk_u), dict) else {}
        div_obj = ip_entry.get("divergence") if isinstance(ip_entry.get("divergence"), dict) else {}
        div_status = str(div_obj.get("status") or "UNAVAILABLE").upper()
        if str(ip_entry.get("status") or "").lower() != "ok" and not div_obj:
            div_status = "UNAVAILABLE"
        slot_p25 = _snap_opt_float(div_obj.get("slot_p25"), 2)
        slot_p50 = _snap_opt_float(div_obj.get("slot_p50"), 2)
        slot_p75 = _snap_opt_float(div_obj.get("slot_p75"), 2)
        if slot_p50 is None and isinstance(ip_entry.get("p50_prices"), list) and ip_entry["p50_prices"]:
            idx_last = min(len(ip_entry["p50_prices"]) - 1, int(ip_entry.get("latest_slot") or (len(ip_entry["p50_prices"]) - 1)))
            slot_p25 = _snap_opt_float((ip_entry.get("p25_prices") or [None])[min(idx_last, len(ip_entry.get("p25_prices") or []) - 1)], 2)
            slot_p50 = _snap_opt_float(ip_entry["p50_prices"][idx_last], 2)
            slot_p75 = _snap_opt_float((ip_entry.get("p75_prices") or [None])[min(idx_last, len(ip_entry.get("p75_prices") or []) - 1)], 2)

        corridor_out = {
            "status": div_status,
            "status_cn": corridor_label_map.get(div_status, "暂无走廊"),
            "badge": str(div_obj.get("badge") or ip_entry.get("status_message") or ""),
            "pmh": _snap_opt_float(ip_entry.get("pmh"), 2),
            "pml": _snap_opt_float(ip_entry.get("pml"), 2),
            "p25": slot_p25,
            "p50": slot_p50,
            "p75": slot_p75,
            "pct_vs_p50": _snap_opt_float(div_obj.get("pct_vs_p50"), 2),
            "latest_time": str(div_obj.get("latest_time") or ""),
        }

        tickers_out[tk_u] = {
            "ticker": tk_u,
            "name": name,
            "sub": sub,
            "cat": cat,
            "is_etf": bool(is_etf_flag),
            "price": active_px,
            "last": last_px,
            "pct_change": pct_chg,
            "ext_price": ext_px if (ext_px and ext_px > 0) else None,
            "ext_pct": ext_pct if (ext_px and ext_px > 0) else None,
            "effective_pct": eff_pct,
            "session_tag": sess_tag,
            "vwap": vwap_val,
            "vwap_rth": vwap_val if not vwap_stale else None,
            "vwap_prev": vwap_val if vwap_stale else None,
            "vwap_stale": vwap_stale,
            "vwap_label": "上日参考 · 09:30重置" if vwap_stale else "当日日内",
            "vwap_diff_pct": vwap_diff,
            "has_5m_intraday": bool(src.get("has_5m_intraday", src.get("rsi_5m") is not None)),
            "rsi_14": _snap_opt_float(src.get("rsi"), 1),
            "rsi_5m": _snap_opt_float(src.get("rsi_5m"), 1),
            "rvol_5m": _snap_opt_float(src.get("rvol_5m"), 2),
            "rs_20d": _snap_opt_float(src.get("rs_20d"), 2),
            "bias_ma50": _snap_opt_float(src.get("bias"), 2),
            "ai_long_win_rate_pct": long_wr,
            "ai_long_raw_pct": long_raw,
            "ai_short_win_rate_pct": short_wr,
            "ai_short_raw_pct": short_raw,
            "ai_action": ai_act_disp,
            "ai_regime_capped": bool(
                vr_out.get("vix_hard_brake")
                or (
                    long_raw is not None
                    and long_wr is not None
                    and (long_raw - long_wr) >= 8.0
                    and str(wi.get("market_regime") or "").upper() == "BEAR_DEFENSE"
                )
            ),
            "is_inverse_hedge": is_inv,
            "is_ipo": is_ipo,
            "is_bear_defense_card": is_bear_card,
            "is_take_profit": is_tp,
            "is_crash_halt": is_crash,
            "is_cooling_halt": is_cooling,
            "is_broken_bearish": is_broken_bear,
            "is_broken_rebound": is_broken_reb,
            "is_stop": is_stop,
            "is_pit": is_pit,
            "is_pit_watch": is_pit_watch,
            "is_deep_pullback": is_deep_pb,
            "is_bull_trend": is_bull,
            "ema20": _snap_opt_float(src.get("ema20"), 2),
            "ma50": _snap_opt_float(src.get("ma50"), 2),
            "decision_stance": stance,
            "decision_verdict": verdict,
            "decision_sub": verdict_sub,
            "optimal_limit": _snap_opt_float(src.get("optimal_limit"), 2),
            "entry_stop_loss": _snap_opt_float(src.get("entry_stop_loss"), 2),
            "target_profit_price": _snap_opt_float(ai_item.get("target_profit_price"), 2),
            "r1": r1_val,
            "r2": r2_val,
            "s1": s1_val,
            "s2": s2_val,
            "anchor_s1": s1_val,
            "anchor_s2": s2_val,
            "anchor_sl": _snap_opt_float(sr_eval_out.get("anchor_sl") or ai_item.get("sl"), 2),
            "anchor_r1": r1_val,
            "anchor_r2": r2_val,
            "s1_source": str(sr_eval_out.get("s1_source") or ai_item.get("s1_source") or "ATR_ESTIMATE"),
            "s2_source": str(sr_eval_out.get("s2_source") or ai_item.get("s2_source") or "ATR_ESTIMATE"),
            "r1_source": str(sr_eval_out.get("r1_source") or ai_item.get("r1_source") or "ATR_ESTIMATE"),
            "r2_source": str(sr_eval_out.get("r2_source") or ai_item.get("r2_source") or "ATR_ESTIMATE"),
            "sr_breach_state": str(sr_eval_out.get("sr_breach_state") or "INTACT"),
            "sr_status_badge": str(sr_eval_out.get("sr_status_badge") or "今日原始支撑/阻力完好"),
            "scenario_script": str(sr_eval_out.get("scenario_script") or ai_item.get("scenario_script") or ""),
            "resonance_badge_text": str(sup_fh_out.get("resonance_badge_text") or base_4h_div),
            "resonance_badge_tag": str(sup_fh_out.get("resonance_badge_tag") or "div-neutral"),
            "resonance_suppressed": bool(sup_fh_out.get("resonance_suppressed", False)),
            "fib_low": _snap_opt_float(src.get("fib_low"), 2),
            "fib_high": _snap_opt_float(src.get("fib_high"), 2),
            "corridor": corridor_out,
            "v46_regime_mode": str(tc_item.get("v46_regime_mode") or ai_item.get("v46_regime_mode") or "NORMAL_BASE"),
            "bear_inertia_active": bool(
                tc_item.get("bear_inertia_active")
                if tc_item.get("bear_inertia_active") is not None
                else ai_item.get("bear_inertia_active", False)
            ),
            "p15_t5_pct": _snap_opt_float(
                tc_item.get("p15_t5_pct") if tc_item.get("p15_t5_pct") is not None else ai_item.get("p15_t5_pct"),
                1,
            ),
            "p15_t15_pct": _snap_opt_float(
                tc_item.get("p15_t15_pct") if tc_item.get("p15_t15_pct") is not None else ai_item.get("p15_t15_pct"),
                1,
            ),
            "p85_t15_pct": _snap_opt_float(
                tc_item.get("p85_t15_pct") if tc_item.get("p85_t15_pct") is not None else ai_item.get("p85_t15_pct"),
                1,
            ),
        }

    return {
        "schema_version": "v4.4-bot-snapshot",
        "updated_at_et": updated_at_et,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "session_code": sess,
        "session_name": sess_name_clean,
        "market_regime": str(wi.get("market_regime") or ai_doc.get("market_regime") or "NEUTRAL_CHOP"),
        "market_regime_cn": str(wi.get("market_regime_cn") or ai_doc.get("market_regime_cn") or ""),
        "weather": str(wi.get("weather") or "阴天防守"),
        "weather_desc": str(wi.get("desc") or ""),
        "breadth_ma20_pct": _snap_opt_float(wi.get("breadth_ma20_pct") if wi.get("breadth_ma20_pct") is not None else ai_doc.get("breadth_ma20_pct"), 1),
        "vix_regime": vr_out,
        "cross_market_liquidity": liq_out,
        "opex_shield": opex_out,
        "futures": (
            (market_temperature.get("futures") if isinstance(market_temperature, dict) else None)
            or (wi.get("futures") if isinstance(wi.get("futures"), dict) else {})
        ),
        "market_temperature": (
            market_temperature
            if isinstance(market_temperature, dict) and market_temperature
            else (wi.get("market_temperature") if isinstance(wi.get("market_temperature"), dict) else {})
        ),
        "resilient_leaders_top5": top5_out,
        "ticker_count": len(tickers_out),
        "tickers": tickers_out,
    }


def write_latest_radar_snapshot(snapshot: Dict[str, Any], snapshot_path: Optional[str] = None) -> str:
    """原子写入轻量只读全市场快照 (latest_radar_snapshot.json)，保护 Windows 并发读取零截断。"""
    target = os.path.abspath(snapshot_path or LATEST_RADAR_SNAPSHOT_FILE)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{target}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
        os.replace(tmp_path, target)
        return target
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise


def generate_macro_radar_html(
    macro_data,
    weather_info,
    history_dict,
    update_time_str="",
    has_realtime=False,
    cross_market_liquidity=None,
    market_temperature=None,
):
    """生成包含 14 只核心 ETF、股指期货先导哨、四维温度计与跨市场流动性监测的宏观大盘司令塔看板"""
    if not update_time_str:
        update_time_str = now_et_str("%Y-%m-%d %H:%M:%S")
    _vr = (weather_info or {}).get("vix_regime") or {}
    _mt_payload = (
        market_temperature
        if isinstance(market_temperature, dict) and market_temperature
        else ((weather_info or {}).get("market_temperature") or {})
    )
    try:
        import market_temperature_engine as _mte_html
        if not _mt_payload:
            _fut_fb = _mte_html.fetch_futures_snapshot(allow_network=False)
            _mt_payload = _mte_html.compute_market_temperature(
                macro_data=macro_data,
                stocks_data=None,
                vix_regime=_vr,
                cross_market_liquidity=cross_market_liquidity,
                futures_snapshot=_fut_fb,
                history_dict=history_dict,
            )
        futures_temp_section_html = _mte_html.render_macro_futures_and_temperature_html(_mt_payload)
    except Exception:
        futures_temp_section_html = ""
    _liq = cross_market_liquidity if isinstance(cross_market_liquidity, dict) else ((weather_info or {}).get("cross_market_liquidity") or {})
    if not _liq:
        try:
            import vix_regime_engine as _vre_liq
            _liq = _vre_liq.get_cross_market_liquidity_snapshot(allow_network=False)
        except Exception:
            _liq = {}

    def _fmt_liq_num(val, digits=2):
        try:
            if val is None or not math.isfinite(float(val)):
                return "--"
            return f"{float(val):.{digits}f}"
        except Exception:
            return "--"

    def _fmt_liq_5d(pct, bad_when="low"):
        try:
            if pct is None or not math.isfinite(float(pct)):
                return "5日 --", "var(--text-muted)"
            pv = float(pct)
            is_bad = (pv <= -1.0) if bad_when == "low" else (pv >= 1.2)
            col = "var(--color-down)" if is_bad else ("var(--color-up)" if abs(pv) < 0.8 else "var(--warning-gold)")
            return f"5日 {pv:+.2f}%", col
        except Exception:
            return "5日 --", "var(--text-muted)"

    _liq_level = str(_liq.get("stress_level") or "degraded")
    _liq_badge_cn = str(_liq.get("badge_cn") or "⚪ 跨市场流动性暂缺")
    _liq_summary_cn = str(_liq.get("summary_cn") or "跨市场指标监测中（异常静默降级）。")
    _liq_border_col = (
        "var(--color-down)" if _liq_level == "danger"
        else ("var(--warning-gold)" if _liq_level == "warn"
              else ("var(--color-up)" if _liq_level == "calm" else "var(--border-subtle)"))
    )
    _liq_hyg_ief_val = _fmt_liq_num(_liq.get("hyg_ief_ratio"), 4)
    _liq_hyg_5d, _liq_hyg_col = _fmt_liq_5d(_liq.get("hyg_ief_5d_pct"), bad_when="low")
    _liq_dxy_val = _fmt_liq_num(_liq.get("dxy_spot"), 2)
    _liq_dxy_5d, _liq_dxy_col = _fmt_liq_5d(_liq.get("dxy_5d_pct"), bad_when="high")
    _liq_jpy_val = _fmt_liq_num(_liq.get("usdjpy_spot"), 2)
    _liq_jpy_5d, _liq_jpy_col = _fmt_liq_5d(_liq.get("usdjpy_5d_pct"), bad_when="low")
    try:
        import vix_regime_engine as _vre_m
        vix_css, vix_pill_html, vix_js = _vre_m.render_vix_pill_widget_parts(_vr, popover_align="right")
    except Exception:
        vix_css, vix_pill_html, vix_js = "", "", ""
    _vr_spot = _vr.get("vix_spot") if isinstance(_vr, dict) else None
    _vr_vxn = _vr.get("vxn_spot") if isinstance(_vr, dict) else None
    _vr_spread = _vr.get("vxn_vix_spread") if isinstance(_vr, dict) else None
    _vr_vix3m = _vr.get("vix3m_spot") if isinstance(_vr, dict) else None
    _vr_ratio = _vr.get("vix_term_ratio") if isinstance(_vr, dict) else None
    _vr_vvix = _vr.get("vvix_spot") if isinstance(_vr, dict) else None
    _vr_tnx = _vr.get("tnx_yield") if isinstance(_vr, dict) else None
    _vr_ratio_color = "var(--color-down)" if (_vr_ratio is not None and float(_vr_ratio) >= 1.0) else ("var(--warning-gold)" if (_vr_ratio is not None and float(_vr_ratio) >= 0.92) else "var(--color-up)")
    _vr_val_str = (
        f"{float(_vr_spot):.2f} / {float(_vr_vxn):.2f}"
        if (_vr_spot is not None and _vr_vxn is not None)
        else (f"{float(_vr_spot):.2f}" if _vr_spot is not None else f"${weather_info['vixy_last']:.2f}")
    )
    _vr_lbl_str = "真 ^VIX / 纳指 ^VXN" if (_vr_spot is not None and _vr_vxn is not None) else ("真 ^VIX / 期限比值" if _vr_spot is not None else "VIXY 恐慌指数")
    _vr_sub_str = (
        f"3M {float(_vr_vix3m):.1f} · 比值 {float(_vr_ratio):.2f} · 溢价 {float(_vr_spread):+.1f}"
        if (_vr_spot is not None and _vr_vix3m is not None and _vr_ratio is not None and _vr_spread is not None)
        else (
            f"3M {float(_vr_vix3m):.1f} · 比值 {float(_vr_ratio):.2f} · 10Y {float(_vr_tnx):.2f}%"
            if (_vr_spot is not None and _vr_vix3m is not None and _vr_ratio is not None and _vr_tnx is not None)
            else (
                f"3M {float(_vr_vix3m):.1f} · 比值 {float(_vr_ratio):.2f}"
                if (_vr_spot is not None and _vr_vix3m is not None and _vr_ratio is not None)
                else f"MA20: {weather_info.get('vixy_ma20_bias', 0.0):+.1f}% | 60d: {weather_info.get('vixy_pos_60', 50.0):.0f}%"
            )
        )
    )
    _vvix_box_val = f"{float(_vr_vvix):.1f}" if _vr_vvix is not None else "--"
    _tnx_box_val = f"{float(_vr_tnx):.2f}%" if _vr_tnx is not None else f"{weather_info['tlt_bias']:+.2f}%"

    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            "<b>核心资产 180 日累计收益率走势 (%)</b>",
            "<b>核心基准 ETF 均线偏离度对比 (MA50 Bias %)</b>",
            "<b>半导体 vs 软件 SaaS 相对强弱 (SMH / IGV)</b>",
            "<b>恐慌指数 (VIXY) · 黄金 (GLD) · 美债 (TLT) 偏离 MA20 水温 (%)</b>"
        ),
        vertical_spacing=0.12,
        horizontal_spacing=0.08
    )

    # 1. 核心资产 180 日收益走势 (开启顶部图例 + 曲线末端直接标注代码与收益率)
    palette = {"QQQ": "#00e676", "SPY": "#29b6f6", "DIA": "#4fc3f7", "SMH": "#ff9100", "IWM": "#ab47bc", "TLT": "#ffd54f", "GLD": "#ffb300"}
    for t, color in palette.items():
        if t in history_dict and len(history_dict[t]) > 0:
            series = history_dict[t]["Close"]
            norm_ret = ((series / series.iloc[0]) - 1.0) * 100
            name = MONITORED_TARGETS["ETFS"].get(t, {}).get("name", t)
            latest_ret = float(norm_ret.iloc[-1])
            fig.add_trace(go.Scatter(
                x=series.index, y=norm_ret, mode="lines",
                name=f"{t} ({name}) {latest_ret:+.1f}%",
                line=dict(color=color, width=1.8)
            ), row=1, col=1)
            # 在每条曲线最右侧末端直接标注 代码 + 收益率%
            fig.add_trace(go.Scatter(
                x=[series.index[-1]], y=[latest_ret],
                mode="text",
                text=[f" <b>{t} {latest_ret:+.1f}%</b>"],
                textposition="middle right",
                textfont=dict(color=color, size=10),
                showlegend=False,
                hoverinfo="skip"
            ), row=1, col=1)

    # 2. 核心基准 ETF MA50 偏离度对比 (剔除杠杆 ETF: KORU, SOXL, SOXS, SQQQ；加 cliponaxis=False 与 1.35x 边距防截断)
    leveraged_exclude = {"KORU", "SOXL", "SOXS", "SQQQ"}
    clean_macro = {t: v for t, v in macro_data.items() if t not in leveraged_exclude}
    tickers_sorted = sorted(clean_macro.keys(), key=lambda x: clean_macro[x]["bias"])
    biases = [clean_macro[t]["bias"] for t in tickers_sorted]
    bar_colors = ["#00e676" if b >= 0 else "#ff5252" for b in biases]
    fig.add_trace(go.Bar(
        y=tickers_sorted,
        x=biases,
        orientation="h",
        marker_color=bar_colors,
        text=[f"{b:+.2f}%" for b in biases],
        textposition="outside",
        cliponaxis=False,
        name="MA50偏离度"
    ), row=1, col=2)
    fig.add_vline(x=0, line_dash="dash", line_color="rgba(255,255,255,0.4)", row=1, col=2)
    
    min_bias = min(biases) if biases else -5.0
    max_bias = max(biases) if biases else 5.0
    left_x = min_bias * 1.35 if min_bias < 0 else -1.5
    right_x = max_bias * 1.35 if max_bias > 0 else 1.5
    fig.update_xaxes(range=[left_x, right_x], row=1, col=2)

    # 3. SMH / IGV 相对强度与 20日均线 (防范时区不匹配与 NaN 空值)
    if "SMH" in history_dict and "IGV" in history_dict:
        try:
            s_close = history_dict["SMH"]["Close"].copy()
            i_close = history_dict["IGV"]["Close"].copy()
            if hasattr(s_close.index, "tz") and s_close.index.tz is not None:
                s_close.index = s_close.index.tz_localize(None)
            if hasattr(i_close.index, "tz") and i_close.index.tz is not None:
                i_close.index = i_close.index.tz_localize(None)
            ratio = (s_close / i_close).dropna()
            if len(ratio) >= 5:
                ratio_ma20 = ratio.rolling(20, min_periods=5).mean()
                cur_r = float(ratio.iloc[-1])
                cur_rma20 = float(ratio_ma20.iloc[-1])
                r_state = "硬件占优" if cur_r >= cur_rma20 else "软件占优"
                fig.add_trace(go.Scatter(x=ratio.index, y=ratio, mode="lines", line=dict(color="#00e5ff", width=1.5), name=f"SMH/IGV 比率 ({cur_r:.2f} · {r_state})"), row=2, col=1)
                fig.add_trace(go.Scatter(x=ratio.index, y=ratio_ma20, mode="lines", line=dict(color="#ffa726", width=1.2, dash="dot"), name=f"20日比率均线 ({cur_rma20:.2f})"), row=2, col=1)
        except Exception as e:
            print(f"[!] SMH/IGV 比率图表绘制跳过: {e}")

    # 4. 恐慌指数 (VIXY) · 黄金 (GLD) · 美债 (TLT) 偏离各自 MA20 水温 (%)
    risk_off_palette = [
        ("VIXY", "#ff1744", "VIXY 恐慌"),
        ("GLD", "#ffb300", "GLD 黄金"),
        ("TLT", "#ffd54f", "TLT 美债")
    ]
    for sym, clr, lbl in risk_off_palette:
        if sym in history_dict and len(history_dict[sym]) >= 20:
            c = history_dict[sym]["Close"]
            ma20 = c.rolling(20, min_periods=5).mean()
            bias20 = ((c - ma20) / ma20) * 100
            latest_b20 = float(bias20.iloc[-1])
            fig.add_trace(go.Scatter(
                x=bias20.index, y=bias20,
                mode="lines",
                line=dict(color=clr, width=1.6),
                name=f"{lbl} ({latest_b20:+.1f}%)"
            ), row=2, col=2)
    fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.3)", row=2, col=2)

    fig.update_layout(
        template="plotly_dark",
        plot_bgcolor="#131722",
        paper_bgcolor="#131722",
        height=720,
        margin=dict(l=50, r=40, t=75, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="center", x=0.5, font=dict(size=10))
    )
    chart_div = fig.to_html(full_html=False, include_plotlyjs="cdn")

    # 构建表格
    rows_html = ""
    for ticker, info in macro_data.items():
        bias_style = "color: var(--color-up);" if info["bias"] >= 0 else "color: var(--color-down);"
        rsi_style = "color: var(--color-down); font-weight:bold;" if info["rsi"] < 30 else ("color: var(--color-up);" if info["rsi"] > 70 else "color: var(--text-sub);")
        status_badge = f"<span class='badge' style='background:{info['badge_color']};'>{info['status']}</span>"
        pct_color = "var(--color-up)" if ui_display_pct(info) >= 0 else "var(--color-down)"
        rows_html += f"""
        <tr>
            <td class="tk-cell" style="font-weight: bold;">{ticker}</td>
            <td>{info['name']}</td>
            <td><span class="category-tag">{info['cat']}</span></td>
            <td><b>${ui_display_price(info):.2f}</b> <span style="font-size: 11px; color: {pct_color};">({ui_display_pct(info):+.2f}%)</span></td>
            <td>${info['ma50']:.2f}</td>
            <td style="{bias_style} font-weight: bold;">{info['bias']:+.2f}%</td>
            <td>${info['atr']:.2f}</td>
            <td style="{rsi_style}">{info['rsi']:.1f}</td>
            <td>{status_badge}</td>
            <td style="font-size: 12px; color: var(--text-muted);">{info['sub']}</td>
        </tr>
        """

    live_badge_html = """
    <span class="live-pill"><span class="live-dot"></span>Finnhub 秒级实时已激活 (Live)</span>
    """ if has_realtime else """
    <span class="category-tag" style="color: var(--warning-gold); border-color: rgba(245,158,11,0.35);">yfinance 底模</span>
    """

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="180">
    <meta name="theme-color" id="metaThemeColor" content="#0B0F19">
    <script>
        (function() {{
            try {{
                var savedTheme = localStorage.getItem("futu_theme");
                var t = (savedTheme === "light") ? "light" : "dark";
                document.documentElement.setAttribute("data-theme", t);
            }} catch (e) {{
                document.documentElement.setAttribute("data-theme", "dark");
            }}
        }})();
    </script>
    <title>宏观大盘司令塔 · 全局水温看板 ({len(MONITORED_TARGETS["ETFS"])}只核心ETF · 全网{len(MONITORED_TARGETS["ETFS"]) + len(MONITORED_TARGETS["STOCKS"])}只标的)</title>
    <style>
        :root,
        [data-theme="dark"] {{
            --bg-page: #0B0F19;
            --bg-card: #121826;
            --bg-elevated: #1A2234;
            --border-subtle: rgba(255, 255, 255, 0.08);
            --border-strong: rgba(255, 255, 255, 0.16);
            --text-main: #F3F4F6;
            --text-sub: #CBD5E1;
            --text-muted: #94A3B8;
            --color-up: #10B981;
            --color-down: #EF4444;
            --color-accent: #38BDF8;
            --color-purple: #A78BFA;
            --warning-gold: #FFD54F;
            --shadow-card: 0 2px 12px rgba(0, 0, 0, 0.28);
        }}
        [data-theme="light"] {{
            --bg-page: #F4F6F9;
            --bg-card: #FFFFFF;
            --bg-elevated: #F1F5F9;
            --border-subtle: #E2E8F0;
            --border-strong: #CBD5E1;
            --text-main: #0F172A;
            --text-sub: #334155;
            --text-muted: #64748B;
            --color-up: #059669;
            --color-down: #DC2626;
            --color-accent: #0284C7;
            --color-purple: #7C3AED;
            --warning-gold: #B45309;
            --shadow-card: 0 1px 4px rgba(15, 23, 42, 0.07);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            background-color: var(--bg-page);
            color: var(--text-sub);
            font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            margin: 0;
            padding: 16px 22px 32px;
            transition: background-color 0.2s ease, color 0.2s ease;
        }}
        .suite-nav {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 8px 14px;
            margin-bottom: 14px;
            flex-wrap: wrap;
            gap: 10px;
            box-shadow: var(--shadow-card);
        }}
        .suite-nav-links {{
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }}
        .suite-link {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 5px 11px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-sub);
            text-decoration: none;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            transition: all 0.16s ease;
            white-space: nowrap;
        }}
        .suite-link:hover {{
            background: rgba(56, 189, 248, 0.14);
            border-color: var(--color-accent);
            color: var(--text-main);
        }}
        .suite-link.active {{
            background: rgba(56, 189, 248, 0.18);
            border-color: var(--color-accent);
            color: var(--color-accent);
            font-weight: 700;
        }}
        .suite-link-home {{
            background: rgba(255, 105, 0, 0.14) !important;
            border-color: rgba(255, 105, 0, 0.45) !important;
            color: #FF6900 !important;
            font-weight: 800 !important;
        }}
        .suite-meta {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            font-size: 12px;
            color: var(--text-muted);
        }}
        .theme-toggle-btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
            height: 29px;
            padding: 0 10px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            color: var(--text-main);
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
        }}
        .theme-toggle-btn:hover {{ border-color: var(--color-accent); }}

        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            border-bottom: 1px solid var(--border-subtle);
            padding-bottom: 12px;
            margin-bottom: 18px;
        }}
        .header h1 {{ margin: 0; font-size: 22px; color: var(--text-main); }}
        .tk-cell {{ color: var(--warning-gold); }}

        .live-pill {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(16, 185, 129, 0.14);
            border: 1px solid rgba(16, 185, 129, 0.4);
            color: var(--color-up);
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
        }}
        .live-dot {{
            width: 7px;
            height: 7px;
            background: var(--color-up);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--color-up);
        }}

        .weather-card {{
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.95));
            border: 1px solid {weather_info['color']};
            box-shadow: var(--shadow-card);
            border-radius: 14px;
            padding: 20px 24px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
        }}
        [data-theme="light"] .weather-card {{
            background: var(--bg-card) !important;
        }}
        .weather-main {{ display: flex; align-items: center; gap: 18px; }}
        .weather-icon {{ font-size: 50px; line-height: 1; }}
        .weather-title {{ font-size: 24px; font-weight: 800; color: {weather_info['color']}; margin-bottom: 6px; }}
        .weather-desc {{ font-size: 13.5px; color: var(--text-muted); max-width: 650px; line-height: 1.5; }}
        .weather-stats {{ display: flex; gap: 12px; flex-wrap: wrap; }}
        .stat-box {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 10px 14px;
            text-align: center;
            min-width: 120px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}
        .stat-label {{ font-size: 11px; color: var(--text-muted); margin-bottom: 4px; white-space: nowrap; }}
        .stat-value {{ font-size: 17px; font-weight: 700; line-height: 1.2; color: var(--text-main); }}
        .stat-sub {{ font-size: 11px; margin-top: 4px; color: var(--text-muted); white-space: nowrap; }}

        .chart-section {{
            background: var(--bg-card);
            border-radius: 14px;
            border: 1px solid var(--border-subtle);
            padding: 16px;
            margin-bottom: 20px;
            box-shadow: var(--shadow-card);
        }}
        .table-card {{
            background: var(--bg-card);
            border-radius: 14px;
            border: 1px solid var(--border-subtle);
            padding: 18px;
            overflow-x: auto;
            box-shadow: var(--shadow-card);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}
        th {{
            background: var(--bg-elevated);
            color: var(--text-muted);
            font-weight: 600;
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-subtle);
        }}
        td {{
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-subtle);
            color: var(--text-sub);
        }}
        tr:hover td {{ background: var(--bg-elevated); }}
        .category-tag {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            color: var(--text-sub);
        }}
        .badge {{
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            color: #fff;
        }}
        [data-theme="light"] #siteDisclaimerBanner {{
            background: #FFFBEB !important;
            border-color: #FCD34D !important;
            color: #92400E !important;
        }}
        [data-theme="light"] #siteDisclaimerBanner strong {{
            color: #B45309 !important;
        }}
        @media (max-width: 768px) {{
            body {{ padding: 10px; }}
            .suite-nav {{ padding: 8px 10px; }}
            .suite-nav-links {{ width: 100%; display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }}
            .suite-link {{ justify-content: center; text-align: center; padding: 6px 8px; font-size: 12px; }}
            .header h1 {{ font-size: 19px; }}
            .weather-card {{ flex-direction: column; align-items: flex-start; gap: 16px; padding: 16px; }}
            .weather-stats {{ width: 100%; display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }}
            .stat-box {{ min-width: 0; }}
            .liquidity-grid {{ grid-template-columns: 1fr !important; }}
        }}
        .liquidity-card {{
            background: var(--bg-card);
            border: 1px solid {_liq_border_col};
            border-radius: 14px;
            padding: 16px 20px;
            margin-bottom: 20px;
            box-shadow: var(--shadow-card);
        }}
        .liquidity-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 12px;
        }}
        .liquidity-title {{
            font-size: 15px;
            font-weight: 800;
            color: var(--text-main);
        }}
        .liquidity-badge {{
            font-size: 12px;
            font-weight: 700;
            padding: 3px 10px;
            border-radius: 999px;
            background: var(--bg-elevated);
            border: 1px solid {_liq_border_col};
            color: {_liq_border_col if _liq_level != 'degraded' else 'var(--text-muted)'};
        }}
        .liquidity-grid {{
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 12px;
            margin-bottom: 10px;
        }}
        .liq-item {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 11px 14px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .liq-lbl {{ font-size: 11.5px; color: var(--text-muted); font-weight: 600; }}
        .liq-val-row {{ display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap; }}
        .liq-val {{ font-size: 18px; font-weight: 800; color: var(--text-main); font-variant-numeric: tabular-nums; }}
        .liq-chg {{ font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }}
        .liq-hint {{ font-size: 11px; color: var(--text-muted); }}
        .liquidity-summary {{
            font-size: 12.5px;
            line-height: 1.5;
            color: var(--text-sub);
            background: var(--bg-elevated);
            border-radius: 8px;
            padding: 8px 12px;
        }}
        {vix_css}
    </style>
</head>
<body>
    <nav class="suite-nav">
        <div class="suite-nav-links">
            <a href="futu_pro_terminal.html" class="suite-link suite-link-home">🐮 看盘主终端</a>
            <a href="stocks_radar.html" class="suite-link">🎯 核心个股雷达</a>
            <a href="macro_radar.html" class="suite-link active">🏛️ 宏观司令塔</a>
            <a href="backtest_report_5y.html" class="suite-link">🛡️ 5Y回测</a>
            <a href="predict_radar_5y.html" class="suite-link">⚡ AI 15日推演</a>
        </div>
        <div class="suite-meta">
            <span>⏱️ 自动刷新: 每3分钟</span>
            <span>最近更新: {update_time_str}</span>
            {live_badge_html}
            {vix_pill_html}
            <button type="button" id="btnThemeToggle" class="theme-toggle-btn" onclick="toggleTheme()" title="切换浅色模式" aria-label="切换浅色或深色模式">
                <span id="themeToggleIcon">☀️</span>
                <span class="theme-toggle-text" id="themeToggleLabel">浅色</span>
            </button>
        </div>
    </nav>

    <div class="header">
        <div>
            <h1>🏛️ 宏观大盘司令塔 · 全局水温看板</h1>
            <div style="font-size: 12px; color: var(--text-muted); margin-top: 6px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                <span>全网 {len(MONITORED_TARGETS["ETFS"]) + len(MONITORED_TARGETS["STOCKS"])} 只标的监控 ({len(MONITORED_TARGETS["ETFS"])} 只 ETF + {len(MONITORED_TARGETS["STOCKS"])} 只核心个股) | 大盘基准: QQQ / SPY / DIA / IWM | 行业风向: SMH / IGV | 避险: TLT / GLD / VIXY</span>
                <span style="color:var(--warning-gold); background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.32); padding:2px 8px; border-radius:4px; font-weight:600;">⚠️ 数据仅供参考·不构成投资建议</span>
            </div>
        </div>
    </div>

    <!-- 市场气象大卡片 -->
    <div class="weather-card">
        <div class="weather-main">
            <div class="weather-icon">{weather_info['icon']}</div>
            <div>
                <div class="weather-title">【{weather_info['weather']}】模式</div>
                <div class="weather-desc"><b>风向研判：</b>{weather_info['desc']}</div>
                <div class="weather-desc" style="color: var(--color-accent); margin-top: 4px;"><b>作战军规：</b>{weather_info['tactics']}</div>
            </div>
        </div>
        <div class="weather-stats">
            <div class="stat-box">
                <div class="stat-label">QQQ 偏离 MA50</div>
                <div class="stat-value" style="color: {'var(--color-up)' if weather_info['qqqm_bias']>=0 else 'var(--color-down)'};">{weather_info['qqqm_bias']:+.2f}%</div>
                <div class="stat-sub">MA20: {weather_info.get('qqqm_ma20_bias', 0.0):+.1f}% | 今日: {weather_info.get('qqqm_pct', 0.0):+.2f}%</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">SMH 偏离 MA50</div>
                <div class="stat-value" style="color: {'var(--color-up)' if weather_info['smh_bias']>=0 else 'var(--color-down)'};">{weather_info['smh_bias']:+.2f}%</div>
                <div class="stat-sub">MA20: {weather_info.get('smh_ma20_bias', 0.0):+.1f}% | 今日: {weather_info.get('smh_pct', 0.0):+.2f}%</div>
            </div>
            <div class="stat-box" style="border-color: {'var(--color-down)' if weather_info.get('is_vix_panic') else 'var(--border-subtle)'};">
                <div class="stat-label">{_vr_lbl_str}</div>
                <div class="stat-value" style="color: {_vr_ratio_color if _vr_spot is not None else ('var(--color-down)' if weather_info.get('is_vix_panic') else 'var(--warning-gold)')};">{_vr_val_str}</div>
                <div class="stat-sub" style="color: {'var(--color-down)' if weather_info.get('is_vix_panic') else 'var(--text-muted)'};">{_vr_sub_str}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">^VVIX / 10Y美债 (^TNX)</div>
                <div class="stat-value" style="color: {weather_info.get('tlt_color', 'var(--text-muted)')}; font-size: 15px;">{_vvix_box_val} / {_tnx_box_val}</div>
                <div class="stat-sub">{weather_info.get('tlt_state', '⚪ 中性震荡')} · TLT {weather_info['tlt_bias']:+.2f}%</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">跨行业市场广度</div>
                <div class="stat-value" style="color: {(weather_info.get('cross_industry_breadth') or {}).get('color', 'var(--text-muted)')}; font-size: 14px;">{(weather_info.get('cross_industry_breadth') or {}).get('short_badge', '🛡️ 广度均衡')}</div>
                <div class="stat-sub">传统 {(weather_info.get('cross_industry_breadth') or {}).get('trad_mean_chg', 0.0):+.2f}% vs 科技 {(weather_info.get('cross_industry_breadth') or {}).get('tech_mean_chg', 0.0):+.2f}%</div>
            </div>
        </div>
    </div>

    {futures_temp_section_html}


    <!-- 跨市场流动性压力卡片 (独立宏观监控，不混入 79 只可交易标的池) -->
    <section id="crossMarketLiquidityCard" class="liquidity-card">
        <div class="liquidity-head">
            <div class="liquidity-title">🌐 跨市场流动性压力监控 (信用利差 · 美元 · 日元套息)</div>
            <span class="liquidity-badge">{_liq_badge_cn}</span>
        </div>
        <div class="liquidity-grid">
            <div class="liq-item">
                <div class="liq-lbl">高收益债信用利差比值 (HYG / IEF)</div>
                <div class="liq-val-row">
                    <span class="liq-val">{_liq_hyg_ief_val}</span>
                    <span class="liq-chg" style="color: {_liq_hyg_col};">{_liq_hyg_5d}</span>
                </div>
                <div class="liq-hint">比值走弱代表信用利差走阔 / 债市开始抽血</div>
            </div>
            <div class="liq-item">
                <div class="liq-lbl">美元指数 (DXY · DX-Y.NYB)</div>
                <div class="liq-val-row">
                    <span class="liq-val">{_liq_dxy_val}</span>
                    <span class="liq-chg" style="color: {_liq_dxy_col};">{_liq_dxy_5d}</span>
                </div>
                <div class="liq-hint">美元急升代表全球美元流动性边际收紧</div>
            </div>
            <div class="liq-item">
                <div class="liq-lbl">美元兑日元 (USD/JPY · JPY=X)</div>
                <div class="liq-val-row">
                    <span class="liq-val">{_liq_jpy_val}</span>
                    <span class="liq-chg" style="color: {_liq_jpy_col};">{_liq_jpy_5d}</span>
                </div>
                <div class="liq-hint">急跌（日元急升）代表套息交易 Carry Trade 去杠杆风险</div>
            </div>
        </div>
        <div class="liquidity-summary">💡 <b>宏观流动解读：</b>{_liq_summary_cn}</div>
    </section>

    <!-- 综合量化图表 -->
    <div class="chart-section">
        {chart_div}
    </div>

    <!-- 宏观水温监控表 -->
    <div class="table-card">
        <h3 style="margin-top: 0; margin-bottom: 14px; font-size: 16px; color: var(--text-main);">📊 宏观资产数字化水温总表 ({len(macro_data)} 只 ETF · 实时行情)</h3>
        <table>
            <thead>
                <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>分类</th>
                    <th>最新成交 (实时)</th>
                    <th>MA50</th>
                    <th>偏离 MA50</th>
                    <th>14日 ATR</th>
                    <th>14日 RSI</th>
                    <th>水温判定</th>
                    <th>战术定位</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    <footer id="siteDisclaimerBanner" style="margin: 24px 0 8px; padding: 12px 18px; background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.32); border-radius: 10px; color: #fde68a; font-size: 12px; line-height: 1.6; text-align: center;">
        ⚠️ <strong style="color: #fbbf24;">免责声明</strong>：本看板所有行情数据、量化指标与 AI 历史统计推演均为程序客观计算结果，仅作个人学习研究与看盘辅助参考，<strong style="color: #fbbf24;">不构成任何投资或买卖建议</strong>。美股及杠杆产品波动剧烈，请独立决策并严控风险。
    </footer>
    <script>
        function getCurrentTheme() {{
            const attr = document.documentElement.getAttribute('data-theme');
            if (attr === 'light' || attr === 'dark') return attr;
            try {{
                return localStorage.getItem('futu_theme') === 'light' ? 'light' : 'dark';
            }} catch (e) {{ return 'dark'; }}
        }}
        function updateThemeToggleUI(theme) {{
            const isLight = (theme === 'light');
            const icon = document.getElementById('themeToggleIcon');
            const label = document.getElementById('themeToggleLabel');
            const btn = document.getElementById('btnThemeToggle');
            const meta = document.getElementById('metaThemeColor');
            if (icon) icon.innerText = isLight ? '🌙' : '☀️';
            if (label) label.innerText = isLight ? '深色' : '浅色';
            if (btn) btn.title = isLight ? '切换到深色模式' : '切换到浅色模式';
            if (meta) meta.setAttribute('content', isLight ? '#F4F6F9' : '#0B0F19');
        }}
        function applyPlotlyTheme(theme) {{
            if (typeof Plotly === 'undefined' || !Plotly.relayout) return;
            const isLight = (theme === 'light');
            const layoutUpd = {{
                paper_bgcolor: isLight ? '#FFFFFF' : '#121826',
                plot_bgcolor: isLight ? '#FFFFFF' : '#121826',
                'font.color': isLight ? '#0F172A' : '#CBD5E1'
            }};
            document.querySelectorAll('.plotly-graph-div').forEach(function(gd) {{
                try {{ Plotly.relayout(gd, layoutUpd); }} catch (e) {{}}
            }});
        }}
        function applyTheme(theme) {{
            const nextTheme = (theme === 'light') ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', nextTheme);
            try {{ localStorage.setItem('futu_theme', nextTheme); }} catch (e) {{}}
            updateThemeToggleUI(nextTheme);
            applyPlotlyTheme(nextTheme);
        }}
        function toggleTheme() {{
            applyTheme(getCurrentTheme() === 'dark' ? 'light' : 'dark');
        }}
        window.addEventListener('storage', function(ev) {{
            if (ev && ev.key === 'futu_theme' && ev.newValue) applyTheme(ev.newValue);
        }});
        document.addEventListener('DOMContentLoaded', function() {{
            const t = getCurrentTheme();
            updateThemeToggleUI(t);
            setTimeout(function() {{ applyPlotlyTheme(t); }}, 150);
        }});
        {vix_js}
    </script>
</body>
</html>
"""
    with open(MACRO_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"✅ 宏观大盘司令塔已生成: {MACRO_HTML_FILE}")

# ==============================================================================
# 六、 页面 2：核心个股碎股雷达 (stocks_radar.html · 27 只个股)
# ==============================================================================
def generate_stocks_radar_html(stocks_data, history_dict, update_time_str="", has_realtime=False, vix_regime=None):
    """生成包含 27 只核心科技个股的碎股雷达与黄金坑矩阵"""
    if not update_time_str:
        update_time_str = now_et_str("%Y-%m-%d %H:%M:%S")
    try:
        import vix_regime_engine as _vre_s
        vix_css, vix_pill_html, vix_js = _vre_s.render_vix_pill_widget_parts(vix_regime, popover_align="right")
    except Exception:
        vix_css, vix_pill_html, vix_js = "", "", ""

    # 2. 绘制 27 只个股“黄金坑距离 vs RSI”散点作战矩阵
    fig_matrix = go.Figure()
    for item in stocks_data:
        t = item["ticker"]
        x_val = item["dist_to_pit"]
        y_val = item["rsi"]
        size_val = max(10, min(34, item["atr"] * 3.2))

        if item.get("is_take_profit"):
            color = "#d946ef"
        elif item["is_pit"]:
            color = "#00e676"
        elif item["is_volume"]:
            color = "#ffd600"
        elif item["is_stop"]:
            color = "#ff1744"
        elif item.get("is_deep_pullback"):
            color = "#ff9100"
        else:
            color = "#29b6f6"
        fig_matrix.add_trace(go.Scatter(
            x=[x_val],
            y=[y_val],
            mode="markers+text",
            name=t,
            text=[f"<b>{t}</b> (${ui_display_price(item):.2f})"],
            textposition="top center",
            marker=dict(size=[size_val], color=color, opacity=0.85, line=dict(color="#ffffff", width=1.5)),
            hoverinfo="text",
            hovertext=f"<b>{t} ({item['name']})</b><br>赛道: {item['cat']}<br>现价: ${ui_display_price(item):.2f} ({ui_display_pct(item):+.2f}%)<br>距黄金坑: {x_val:+.2f}%<br>RSI(14): {y_val:.1f}<br>14日ATR: ${item['atr']:.2f}<br>信号: {item['signal']}"
        ))

    fig_matrix.add_vline(x=0, line_dash="dash", line_color="#00e676", annotation_text="黄金坑临界带 (X=0%)", annotation_position="top left")
    fig_matrix.add_hline(y=70, line_dash="dot", line_color="#d946ef", annotation_text="超买止盈警戒带 (RSI=70)", annotation_position="top right")
    fig_matrix.add_hline(y=35, line_dash="dot", line_color="#4fc3f7", annotation_text="超卖安全区 (RSI=35)", annotation_position="bottom right")
    fig_matrix.add_hline(y=50, line_dash="dot", line_color="rgba(255,255,255,0.3)", annotation_text="强弱分水岭 (RSI=50)", annotation_position="bottom left")

    fig_matrix.update_layout(
        template="plotly_dark",
        plot_bgcolor="#131722",
        paper_bgcolor="#131722",
        title=f"<b>{len(stocks_data)} 只核心个股作战雷达矩阵 (X: 距黄金坑百分比 vs Y: RSI 强弱 | 气泡大小: ATR 波动度)</b>",
        xaxis_title="距黄金坑距离 (%) [负值=跌破坑底, 0=坑内就位, 正值=坑上待回调]",
        yaxis_title="14日 RSI 相对强弱",
        height=520,
        margin=dict(l=50, r=40, t=60, b=50),
        showlegend=False
    )
    matrix_div = fig_matrix.to_html(full_html=False, include_plotlyjs="cdn")

    # 构建个股表格
    def _position_display(r):
        last = max(float(r.get("last", 0.0) or 0.0), 0.01)
        atr_pct = (float(r.get("atr", 0.0) or 0.0) / last) * 100.0
        sig = str(r.get("signal", "") or "")
        if r.get("is_stop") or any(k in sig for k in ("破位", "暴跌", "熔断", "止损")):
            return "0%", "严禁接刀", "#ff5252"
        if r.get("is_pit") or any(k in sig for k in ("黄金坑", "日内买点", "右侧反转", "极度超卖", "分批低吸")):
            pct = 25 if atr_pct <= 2.5 else 22 if atr_pct <= 4.0 else 19 if atr_pct <= 6.0 else 15
            return f"{pct}%", "分批低吸", "#00e676"
        if r.get("is_momentum") or any(k in sig for k in ("多头推进", "坑上", "量能异动", "顺势", "等回踩")):
            pct = 15 if atr_pct <= 2.5 else 13 if atr_pct <= 4.0 else 11 if atr_pct <= 6.0 else 10
            return f"{pct}%", "顺势/等回踩", "#64b5f6"
        return "0%", "观望等待", "#90a4ae"

    rows_html = ""
    for r in stocks_data:
        bias_style = "color: #00e676;" if r["bias"] >= 0 else "color: #ff5252;"
        dist_style = "color: #00e676; font-weight:bold;" if r["dist_to_pit"] <= 0 else "color: #90caf9;"
        pct_color = "#00e676" if r.get("pct_change", 0) >= 0 else "#ff5252"
        pos_pct, pos_note, pos_color = _position_display(r)
        
        if "🔴" in r["signal"]:
            sig_badge = f"<span class='badge' style='background:#b71c1c;'>{r['signal']}</span>"
        elif "🟣" in r["signal"]:
            sig_badge = f"<span class='badge' style='background:#8e24aa;'>{r['signal']}</span>"
        elif "🟢" in r["signal"]:
            sig_badge = f"<span class='badge' style='background:#1b5e20;'>{r['signal']}</span>"
        elif "🟡" in r["signal"]:
            sig_badge = f"<span class='badge' style='background:#f57f17; color:#000;'>{r['signal']}</span>"
        elif "🟠" in r["signal"]:
            sig_badge = f"<span class='badge' style='background:#e65100; color:#fff;'>{r['signal']}</span>"
        else:
            sig_badge = f"<span class='badge' style='background:#263238;'>{r['signal']}</span>"

        rows_html += f"""
        <tr>
            <td class="tk-cell" style="font-weight: bold;">{r['ticker']}</td>
            <td>{r['name']}</td>
            <td><span class="category-tag">{r['cat']}</span></td>
            <td><b>${ui_display_price(r):.2f}</b> <span style="font-size: 11px; color: {pct_color};">({ui_display_pct(r):+.2f}%)</span></td>
            <td>${r['ma50']:.2f}</td>
            <td style="{bias_style} font-weight: bold;">{r['bias']:+.2f}%</td>
            <td>${r['atr']:.2f}</td>
            <td>{r['rsi']:.1f}</td>
            <td style="font-size: 12px; color: var(--color-accent);">${r['fib_low']:.2f} ~ ${r['fib_high']:.2f}</td>
            <td style="{dist_style}">{r['pit_status']}</td>
            <td style="color: {pos_color}; font-weight: bold;">{pos_pct}<div style="font-size:10px; color:#90a4ae;">{pos_note}</div></td>
            <td>{sig_badge}</td>
        </tr>
        """

    # INTC 数据已在顶部统一定义抽取

    live_badge_html = """
    <span class="live-pill"><span class="live-dot"></span>Finnhub 秒级实时已激活 (Live)</span>
    """ if has_realtime else """
    <span class="category-tag" style="color: var(--warning-gold); border-color: rgba(245,158,11,0.35);">yfinance 底模</span>
    """

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="180">
    <meta name="theme-color" id="metaThemeColor" content="#0B0F19">
    <script>
        (function() {{
            try {{
                var savedTheme = localStorage.getItem("futu_theme");
                var t = (savedTheme === "light") ? "light" : "dark";
                document.documentElement.setAttribute("data-theme", t);
            }} catch (e) {{
                document.documentElement.setAttribute("data-theme", "dark");
            }}
        }})();
    </script>
    <title>核心个股碎股雷达 · 黄金坑作战与进攻看板 ({len(stocks_data)}只标的 · 全网{len(MONITORED_TARGETS["ETFS"]) + len(MONITORED_TARGETS["STOCKS"])}只标的)</title>
    <style>
        :root,
        [data-theme="dark"] {{
            --bg-page: #0B0F19;
            --bg-card: #121826;
            --bg-elevated: #1A2234;
            --border-subtle: rgba(255, 255, 255, 0.08);
            --border-strong: rgba(255, 255, 255, 0.16);
            --text-main: #F3F4F6;
            --text-sub: #CBD5E1;
            --text-muted: #94A3B8;
            --color-up: #10B981;
            --color-down: #EF4444;
            --color-accent: #38BDF8;
            --warning-gold: #FFD54F;
            --shadow-card: 0 2px 12px rgba(0, 0, 0, 0.28);
        }}
        [data-theme="light"] {{
            --bg-page: #F4F6F9;
            --bg-card: #FFFFFF;
            --bg-elevated: #F1F5F9;
            --border-subtle: #E2E8F0;
            --border-strong: #CBD5E1;
            --text-main: #0F172A;
            --text-sub: #334155;
            --text-muted: #64748B;
            --color-up: #059669;
            --color-down: #DC2626;
            --color-accent: #0284C7;
            --warning-gold: #B45309;
            --shadow-card: 0 1px 4px rgba(15, 23, 42, 0.07);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            background-color: var(--bg-page);
            color: var(--text-sub);
            font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            margin: 0;
            padding: 16px 22px 32px;
            transition: background-color 0.2s ease, color 0.2s ease;
        }}
        .suite-nav {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 8px 14px;
            margin-bottom: 14px;
            flex-wrap: wrap;
            gap: 10px;
            box-shadow: var(--shadow-card);
        }}
        .suite-nav-links {{
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }}
        .suite-link {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 5px 11px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-sub);
            text-decoration: none;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            transition: all 0.16s ease;
            white-space: nowrap;
        }}
        .suite-link:hover {{
            background: rgba(56, 189, 248, 0.14);
            border-color: var(--color-accent);
            color: var(--text-main);
        }}
        .suite-link.active {{
            background: rgba(56, 189, 248, 0.18);
            border-color: var(--color-accent);
            color: var(--color-accent);
            font-weight: 700;
        }}
        .suite-link-home {{
            background: rgba(255, 105, 0, 0.14) !important;
            border-color: rgba(255, 105, 0, 0.45) !important;
            color: #FF6900 !important;
            font-weight: 800 !important;
        }}
        .suite-meta {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            font-size: 12px;
            color: var(--text-muted);
        }}
        .theme-toggle-btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
            height: 29px;
            padding: 0 10px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-elevated);
            color: var(--text-main);
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
        }}
        .theme-toggle-btn:hover {{ border-color: var(--color-accent); }}

        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            border-bottom: 1px solid var(--border-subtle);
            padding-bottom: 12px;
            margin-bottom: 18px;
        }}
        .header h1 {{ margin: 0; font-size: 22px; color: var(--text-main); }}
        .tk-cell {{ color: var(--warning-gold); }}

        .live-pill {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(16, 185, 129, 0.14);
            border: 1px solid rgba(16, 185, 129, 0.4);
            color: var(--color-up);
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
        }}
        .live-dot {{
            width: 7px;
            height: 7px;
            background: var(--color-up);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--color-up);
        }}

        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
        }}
        .card {{
            background: var(--bg-card);
            border-radius: 12px;
            border: 1px solid var(--border-subtle);
            padding: 16px 20px;
            box-shadow: var(--shadow-card);
        }}
        .card-stop {{
            border-left: 4px solid #ef4444;
        }}
        .card-title {{ font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }}
        .card-value {{ font-size: 22px; font-weight: 800; color: var(--text-main); margin-bottom: 4px; }}
        .card-sub {{ font-size: 12px; color: var(--text-muted); }}

        .chart-box {{
            background: var(--bg-card);
            border-radius: 14px;
            border: 1px solid var(--border-subtle);
            padding: 16px;
            margin-bottom: 20px;
            overflow-x: auto;
            box-shadow: var(--shadow-card);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}
        th {{
            background: var(--bg-elevated);
            color: var(--text-muted);
            font-weight: 600;
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-subtle);
        }}
        td {{
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-subtle);
            color: var(--text-sub);
        }}
        tr:hover td {{ background: var(--bg-elevated); }}
        .category-tag {{
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            color: var(--text-sub);
        }}
        .badge {{
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            color: #fff;
            display: inline-block;
        }}
        [data-theme="light"] #siteDisclaimerBanner {{
            background: #FFFBEB !important;
            border-color: #FCD34D !important;
            color: #92400E !important;
        }}
        [data-theme="light"] #siteDisclaimerBanner strong {{
            color: #B45309 !important;
        }}
        @media (max-width: 768px) {{
            body {{ padding: 10px; }}
            .suite-nav {{ padding: 8px 10px; }}
            .suite-nav-links {{ width: 100%; display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }}
            .suite-link {{ justify-content: center; text-align: center; padding: 6px 8px; font-size: 12px; }}
            .header h1 {{ font-size: 19px; }}
        }}
        {vix_css}
    </style>
</head>
<body>
    <nav class="suite-nav">
        <div class="suite-nav-links">
            <a href="futu_pro_terminal.html" class="suite-link suite-link-home">🐮 看盘主终端</a>
            <a href="stocks_radar.html" class="suite-link active">🎯 核心个股雷达</a>
            <a href="macro_radar.html" class="suite-link">🏛️ 宏观司令塔</a>
            <a href="backtest_report_5y.html" class="suite-link">🛡️ 5Y回测</a>
            <a href="predict_radar_5y.html" class="suite-link">⚡ AI 15日推演</a>
        </div>
        <div class="suite-meta">
            <span>⏱️ 自动刷新: 每3分钟</span>
            <span>最近更新: {update_time_str}</span>
            {live_badge_html}
            {vix_pill_html}
            <button type="button" id="btnThemeToggle" class="theme-toggle-btn" onclick="toggleTheme()" title="切换浅色模式" aria-label="切换浅色或深色模式">
                <span id="themeToggleIcon">☀️</span>
                <span class="theme-toggle-text" id="themeToggleLabel">浅色</span>
            </button>
        </div>
    </nav>

    <div class="header">
        <div>
            <h1>🎯 核心个股碎股雷达 · {len(stocks_data)}只标的实战看板 (全网 {len(MONITORED_TARGETS["ETFS"]) + len(MONITORED_TARGETS["STOCKS"])} 只标的 · {len(MONITORED_TARGETS["ETFS"])} ETF + {len(MONITORED_TARGETS["STOCKS"])} 个股)</h1>
            <div style="font-size: 12px; color: var(--text-muted); margin-top: 6px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                <span>覆盖赛道: 芯片算力 / 商业AI / 新算力云 / AI能源核电 / 液冷服务器 / 光通信 / 太空商业 / 存储闪存 / 物理AI安全 / 经典蓝筹</span>
                <span style="color:var(--warning-gold); background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.32); padding:2px 8px; border-radius:4px; font-weight:600;">⚠️ 数据仅供参考·不构成投资建议</span>
            </div>
        </div>
    </div>

    <!-- 顶部概览看板 -->
    <div class="summary-grid">
        <div class="card">
            <div class="card-title">🟢 黄金坑就位与进攻机会</div>
            <div class="card-value" style="color: var(--color-up);">{sum(1 for s in stocks_data if s.get('is_pit') and not s.get('is_crash_halt') and not s.get('is_cooling_halt') and not s.get('is_inverse_hedge'))} <span style="font-size: 14px; color: var(--text-muted);">/ {len(stocks_data)} 只</span></div>
            <div class="card-sub">现价进入 0.5 ~ 0.618 斐波那契回撤低吸防守带且 RSI &lt; 52</div>
        </div>
        <div class="card">
            <div class="card-title">💼 单股仓位配置标准 (动态比例制)</div>
            <div class="card-value" style="color: var(--color-accent);">25.0% <span style="font-size: 14px; color: var(--text-muted);">(1.5%风险平价)</span></div>
            <div class="card-sub">单股上限 25% (1.5%风险预算一次性建仓)，纪律执行防追高，拒绝情绪化重仓</div>
        </div>
        <div class="card card-stop">
            <div class="card-title">🔴 破位止损与风控警戒</div>
            <div class="card-value" style="color: var(--color-down);">{sum(1 for s in stocks_data if s.get('is_stop'))} <span style="font-size: 14px; color: var(--text-muted);">/ {len(stocks_data)} 只</span></div>
            <div class="card-sub">跌破坑底 3% 或 MA50 偏离 &lt;-12%，坚决风控防守，拒绝逆势抄底</div>
        </div>
    </div>

    <!-- 黄金坑散点作战矩阵 -->
    <div class="chart-box">
        {matrix_div}
    </div>

    <!-- 碎股作战行动表 -->
    <div class="chart-box">
        <h3 style="margin-top: 0; margin-bottom: 14px; font-size: 16px; color: var(--text-main);">📋 核心个股碎股作战行动表 ({len(stocks_data)}只标的 · 按距黄金坑距离升序)</h3>
        <table>
            <thead>
                <tr>
                    <th>代码</th>
                    <th>标的名称</th>
                    <th>赛道分类</th>
                    <th>最新成交 (实时)</th>
                    <th>MA50</th>
                    <th>偏离 MA50</th>
                    <th>14日 ATR</th>
                    <th>14日 RSI</th>
                    <th>黄金坑区间 (0.5~0.618)</th>
                    <th>距黄金坑位置</th>
                    <th>建议建仓仓位 (纯百分比)</th>
                    <th>作战行动信号</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    <footer id="siteDisclaimerBanner" style="margin: 24px 0 8px; padding: 12px 18px; background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.32); border-radius: 10px; color: #fde68a; font-size: 12px; line-height: 1.6; text-align: center;">
        ⚠️ <strong style="color: #fbbf24;">免责声明</strong>：本看板所有行情数据、量化指标与 AI 历史统计推演均为程序客观计算结果，仅作个人学习研究与看盘辅助参考，<strong style="color: #fbbf24;">不构成任何投资或买卖建议</strong>。美股及杠杆产品波动剧烈，请独立决策并严控风险。
    </footer>
    <script>
        function getCurrentTheme() {{
            const attr = document.documentElement.getAttribute('data-theme');
            if (attr === 'light' || attr === 'dark') return attr;
            try {{
                return localStorage.getItem('futu_theme') === 'light' ? 'light' : 'dark';
            }} catch (e) {{ return 'dark'; }}
        }}
        function updateThemeToggleUI(theme) {{
            const isLight = (theme === 'light');
            const icon = document.getElementById('themeToggleIcon');
            const label = document.getElementById('themeToggleLabel');
            const btn = document.getElementById('btnThemeToggle');
            const meta = document.getElementById('metaThemeColor');
            if (icon) icon.innerText = isLight ? '🌙' : '☀️';
            if (label) label.innerText = isLight ? '深色' : '浅色';
            if (btn) btn.title = isLight ? '切换到深色模式' : '切换到浅色模式';
            if (meta) meta.setAttribute('content', isLight ? '#F4F6F9' : '#0B0F19');
        }}
        function applyPlotlyTheme(theme) {{
            if (typeof Plotly === 'undefined' || !Plotly.relayout) return;
            const isLight = (theme === 'light');
            const layoutUpd = {{
                paper_bgcolor: isLight ? '#FFFFFF' : '#121826',
                plot_bgcolor: isLight ? '#FFFFFF' : '#121826',
                'font.color': isLight ? '#0F172A' : '#CBD5E1'
            }};
            document.querySelectorAll('.plotly-graph-div').forEach(function(gd) {{
                try {{ Plotly.relayout(gd, layoutUpd); }} catch (e) {{}}
            }});
        }}
        function applyTheme(theme) {{
            const nextTheme = (theme === 'light') ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', nextTheme);
            try {{ localStorage.setItem('futu_theme', nextTheme); }} catch (e) {{}}
            updateThemeToggleUI(nextTheme);
            applyPlotlyTheme(nextTheme);
        }}
        function toggleTheme() {{
            applyTheme(getCurrentTheme() === 'dark' ? 'light' : 'dark');
        }}
        window.addEventListener('storage', function(ev) {{
            if (ev && ev.key === 'futu_theme' && ev.newValue) applyTheme(ev.newValue);
        }});
        document.addEventListener('DOMContentLoaded', function() {{
            const t = getCurrentTheme();
            updateThemeToggleUI(t);
            setTimeout(function() {{ applyPlotlyTheme(t); }}, 150);
        }});
        {vix_js}
    </script>
</body>
</html>
"""
    with open(STOCKS_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"✅ 核心个股碎股雷达已生成: {STOCKS_HTML_FILE}")

# ==============================================================================
# 七、 控制台 Rich 终端彩色表格输出
# ==============================================================================
def display_terminal_tables(macro_data, stocks_data, weather_info, has_realtime=False):
    """在终端输出规整的 12 只 ETF 水温表与个股按黄金坑升序行动表"""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich import box

        console = Console(width=145)
        realtime_tag = " [bold green](🟢 Finnhub 秒级实时行情已激活)[/bold green]" if has_realtime else " [yellow](yfinance 底模)[/yellow]"

        # 1. 宏观气象 Panel
        weather_text = (
            f"[bold {weather_info['color']}]{weather_info['icon']} 宏观气象模式：【{weather_info['weather']}】[/bold {weather_info['color']}]\n"
            f"[bold cyan]研判说明：[/bold cyan]{weather_info['desc']}\n"
            f"[bold yellow]作战军规：[/bold yellow]{weather_info['tactics']}\n"
            f"[dim]大盘偏离：QQQ ({weather_info['qqqm_bias']:+.2f}%) | SMH ({weather_info['smh_bias']:+.2f}%) | VIXY (${weather_info['vixy_last']:.2f} · MA20:{weather_info.get('vixy_ma20_bias',0.0):+.1f}%) | TLT ({weather_info.get('tlt_state','中性')} / {weather_info['tlt_bias']:+.2f}%)[/dim]"
        )
        console.print(Panel(weather_text, title=f"[bold white]🏛️ 宏观大盘司令塔 · 气象研判卡片{realtime_tag}[/bold white]", box=box.ROUNDED))

        # 2. 12 只 ETF 水温表
        macro_table = Table(title=f"📊 宏观大盘水温监控表 ({len(macro_data)} 只核心 ETF ·{realtime_tag})", box=box.ROUNDED, header_style="bold cyan")
        macro_table.add_column("代码", justify="center", style="bold yellow", no_wrap=True)
        macro_table.add_column("名称", justify="left", no_wrap=True)
        macro_table.add_column("分类", justify="center", no_wrap=True)
        macro_table.add_column("最新价(实时)", justify="right", no_wrap=True)
        macro_table.add_column("MA50", justify="right", no_wrap=True)
        macro_table.add_column("MA50偏离度", justify="right", no_wrap=True)
        macro_table.add_column("14日ATR", justify="right", no_wrap=True)
        macro_table.add_column("RSI(14)", justify="right", no_wrap=True)
        macro_table.add_column("水温评级", justify="center", no_wrap=True)
        macro_table.add_column("战术定位", justify="left")

        for t, m in macro_data.items():
            b_style = "green" if m["bias"] >= 0 else "red"
            pct_style = "green" if m.get("pct_change", 0) >= 0 else "red"
            macro_table.add_row(
                t,
                m["name"],
                m["cat"],
                f"${m['last']:.2f} [{pct_style}]({m.get('pct_change', 0):+.2f}%)[/{pct_style}]",
                f"${m['ma50']:.2f}",
                f"[{b_style}]{m['bias']:+.2f}%[/{b_style}]",
                f"${m['atr']:.2f}",
                f"{m['rsi']:.1f}",
                m["status"],
                m["sub"]
            )
        console.print(macro_table)
        print("\n")

        # 3. 个股作战表
        stocks_table = Table(title=f"🎯 核心个股碎股雷达 · 作战行动表 ({len(stocks_data)}只标的 · 按距黄金坑升序 ·{realtime_tag})", box=box.ROUNDED, header_style="bold green")
        stocks_table.add_column("代码", justify="center", style="bold yellow", no_wrap=True)
        stocks_table.add_column("标的名称", justify="left", no_wrap=True)
        stocks_table.add_column("赛道", justify="center", no_wrap=True)
        stocks_table.add_column("最新价(实时)", justify="right", no_wrap=True)
        stocks_table.add_column("MA50", justify="right", no_wrap=True)
        stocks_table.add_column("偏离度", justify="right", no_wrap=True)
        stocks_table.add_column("14日ATR", justify="right", no_wrap=True)
        stocks_table.add_column("RSI", justify="right", no_wrap=True)
        stocks_table.add_column("黄金坑支撑带", justify="center", no_wrap=True)
        stocks_table.add_column("距黄金坑", justify="right", no_wrap=True)
        stocks_table.add_column("建议仓位比例", justify="right", style="bold cyan", no_wrap=True)
        stocks_table.add_column("作战信号", justify="center", no_wrap=True)

        for s in stocks_data:
            b_style = "green" if s["bias"] >= 0 else "red"
            pct_style = "green" if s.get("pct_change", 0) >= 0 else "red"
            dist_style = "bold green" if s["dist_to_pit"] <= 0 else "blue"
            sig_style = "bold red" if "破位" in s["signal"] or "跌破" in s["signal"] else ("bold green" if "黄金坑" in s["signal"] or "盈利" in s["signal"] else ("bold yellow" if "量能" in s["signal"] or "预警" in s["signal"] else "white"))
            ext_str = f" [magenta]· {s['session_tag']}${s['ext_price']:.2f}({s['ext_pct']:+.2f}%)[/magenta]" if s.get("session_tag") and s.get("ext_price") else ""

            stocks_table.add_row(
                s["ticker"],
                s["name"],
                s["cat"],
                f"${s['last']:.2f} [{pct_style}]({s.get('pct_change', 0):+.2f}%)[/{pct_style}]{ext_str}",
                f"${s['ma50']:.2f}",
                f"[{b_style}]{s['bias']:+.2f}%[/{b_style}]",
                f"${s['atr']:.2f}",
                f"{s['rsi']:.1f}",
                f"${s['fib_low']:.2f}~${s['fib_high']:.2f}",
                f"[{dist_style}]{s['pit_status']}[/{dist_style}]",
                "上限 25% (1.5%风险平价)",
                f"[{sig_style}]{s['signal']}[/{sig_style}]"
            )
        console.print(stocks_table)

    except Exception as e:
        print(f"[!] Rich 控制台排版回退: {e}")

# ==============================================================================
# 八、 主循环与全量数据流水线 (全量标的池架构)
# ==============================================================================
def run_radar_cycle(is_first_run=False, round_idx=1):
    """单轮执行全量标的数据抓取、实时穿透、技术指标重算与三份看板覆盖写入"""
    start_star_api_server()
    try:
        import zoneinfo
        local_tz = zoneinfo.ZoneInfo("Asia/Seoul")
    except Exception:
        from datetime import timezone, timedelta
        local_tz = timezone(timedelta(hours=9))
    now_str = now_et_str("%Y-%m-%d %H:%M:%S")

    # 实盘持仓功能已移除：不再加载或合并 user_portfolio.json
    user_portfolio = {}

    all_etfs = list(MONITORED_TARGETS["ETFS"].keys())
    all_stocks = list(MONITORED_TARGETS["STOCKS"].keys())
    all_tickers = all_etfs + all_stocks

    # 1. 抓取 1 年(约252交易日)历史日 K 线底模
    history_dict = fetch_all_market_data(all_tickers, period="1y")

    # 1.5 判断当前美东交易时段 (常规盘中 / 盘前 / 盘后 / 夜盘)
    session_code, session_name = get_us_market_session()
    print(f"[*] 🕒 当前美东市场时段状态: 【{session_name}】")

    # 2. 并发秒级拉取 Finnhub 实时报价 (max_workers=2, 稳态间隔防 429)
    # 免费额度 60次/分。全标的 79 只 (14 ETF + 65 股票)，BTC-USD 与 XAUUSD 走免费直连，DIA 等 17 只低波蓝筹/宽基走 Yahoo 5m + V7 实时穿透，留足 60 个 Finnhub 名额保障 ANT 等重点高弹性标的
    DEFENSIVE_11_TICKERS = {"DIA", "KO", "PEP", "MCD", "AXP", "V", "LLY", "MRNA", "NKE", "HPE", "FPS", "JPM", "XOM", "WMT", "CAT", "NASA", "GLW"}
    finnhub_tickers = [tk for tk in all_tickers if tk not in ("BTC-USD", "XAUUSD") and (tk not in DEFENSIVE_11_TICKERS or (user_portfolio and tk in user_portfolio))][:60]
    realtime_quotes = fetch_finnhub_quotes_batch(finnhub_tickers, FINNHUB_API_KEY) if FINNHUB_API_KEY else {}
    if "BTC-USD" in all_tickers:
        _btc_q = fetch_yahoo_crypto_quote("BTC-USD")
        if _btc_q:
            realtime_quotes["BTC-USD"] = _btc_q
    if "XAUUSD" in all_tickers:
        _xau_q = fetch_xauusd_spot_quote()
        if _xau_q and _xau_q.get("last", 0) > 0:
            realtime_quotes["XAUUSD"] = _xau_q
    if "ANT" in all_tickers and ("ANT" not in realtime_quotes or float(realtime_quotes["ANT"].get("last", 0) or 0) <= 0):
        _ant_q = fetch_tradingview_equity_quote("ANT", exchange="NASDAQ")
        if _ant_q and _ant_q.get("last", 0) > 0:
            realtime_quotes["ANT"] = _ant_q

    # 2.5 批量拉取 5 分钟级日内指标 (VWAP / 5m RSI / 5m RVOL) 与盘前盘后最新成交价
    print(f"[*] 📊 正在同步 {len(all_tickers)} 只标的【5分钟级 VWAP/RSI/量比 日内择时指标 & 盘前盘后最新报价】...")
    intraday_map = fetch_intraday_5m_and_ext_data(all_tickers)
    if session_code == "OVERNIGHT":
        overnight_quotes = fetch_yahoo_overnight_quotes(all_tickers)
        if overnight_quotes:
            print(f"[+] 夜盘真实成交 {len(overnight_quotes)} 只，其余保持正规盘收盘")
        else:
            print("[*] 夜盘没有 overnightMarketPrice，主价格保持正规盘收盘，盘后价只作定格标注")
        for tk, info in overnight_quotes.items():
            slot = intraday_map.setdefault(tk, {})
            slot["ext_price"] = info["price"]
            if info.get("time"):
                slot["ext_time"] = info["time"]
            slot["price_kind"] = "overnight"
    _apply_xauusd_spot_basis(history_dict, intraday_map, realtime_quotes)
    if intraday_map:
        print(f"[+] ✅ 成功计算 {len(intraday_map)}/{len(all_tickers)} 只标的 5 分钟分时指标与盘前盘后行情！")

    # 2.6 同步未来 45 天个股财报日历 (12h 缓存防刷)
    earnings_map = fetch_earnings_calendar_map(all_stocks, FINNHUB_API_KEY)
    risk_cnt = sum(1 for v in earnings_map.values() if v.get("is_earnings_risk"))
    print(f"[+] 📅 财报避雷日历同步完成：未来 5 天内共有 {risk_cnt} 只个股面临财报披露窗口。")

    # 2.7 同步全市场 58 只标的【机构期权多空异动、权利金大单与预期波动区间】(免额度·30m本地缓存)
    spot_map = {}
    for tk in all_tickers:
        if tk in realtime_quotes and realtime_quotes[tk].get("last", 0) > 0:
            spot_map[tk] = float(realtime_quotes[tk]["last"])
        elif tk in intraday_map and intraday_map[tk].get("ext_price", 0) > 0:
            spot_map[tk] = float(intraday_map[tk]["ext_price"])
        elif tk in history_dict and len(history_dict[tk]) > 0:
            spot_map[tk] = float(history_dict[tk]["Close"].iloc[-1])
    options_map = fetch_options_anomaly_map(all_tickers, spot_map=spot_map)

    # 2.8 全局真 VIX 恐慌中枢 (^VIX / ^VXN / ^VIX3M / ^VVIX / ^TNX，独立于 79 交易池之外，3 分钟缓存节流 + 单指数容错)
    vix_regime = {}
    try:
        import vix_regime_engine
        vix_regime = vix_regime_engine.get_vix_regime_snapshot(
            force_refresh=("--once" in sys.argv),
            session_code=session_code,
        )
        print(
            f"[+] ⚡ 全局真 VIX 中枢: ^VIX={vix_regime.get('vix_spot')} | ^VXN={vix_regime.get('vxn_spot')} | "
            f"VXN-VIX={vix_regime.get('vxn_vix_spread')} | ^VIX3M={vix_regime.get('vix3m_spot')} | "
            f"期限比值={vix_regime.get('vix_term_ratio')} | ^VVIX={vix_regime.get('vvix_spot')} | "
            f"^TNX={vix_regime.get('tnx_yield')}% | 科技判定={vix_regime.get('tech_fear_label')} | 状态={vix_regime.get('pill_text')}"
        )
    except Exception as _vre_err:
        print(f"  [!] 全局 VIX 恐慌中枢降级跳过: {_vre_err}")

    # 2.9 宏观跨市场流动性监控 (HYG/IEF · DX-Y.NYB · JPY=X，严格独立于 79 只可交易标的池之外)
    cross_market_liquidity = {}
    try:
        import vix_regime_engine as _vre_liq_c
        cross_market_liquidity = _vre_liq_c.get_cross_market_liquidity_snapshot(force_refresh=("--once" in sys.argv))
        print(
            f"[+] 🌐 跨市场流动性: HYG/IEF={cross_market_liquidity.get('hyg_ief_ratio')} ({cross_market_liquidity.get('hyg_ief_5d_pct')}%) | "
            f"DXY={cross_market_liquidity.get('dxy_spot')} ({cross_market_liquidity.get('dxy_5d_pct')}%) | "
            f"USDJPY={cross_market_liquidity.get('usdjpy_spot')} ({cross_market_liquidity.get('usdjpy_5d_pct')}%) | "
            f"判定={cross_market_liquidity.get('badge_cn')}"
        )
    except Exception as _liq_err:
        print(f"  [!] 跨市场流动性监控降级跳过: {_liq_err}")
    print("")

    has_realtime = len(realtime_quotes) > 0 or len(intraday_map) > 0

    # 预计算 QQQ 20日基准收益率用于计算全市场个股 RS 相对强弱度 (Alpha)
    qqqm_ret_20d = 0.0
    if "QQQ" in history_dict and len(history_dict["QQQ"]) >= 21:
        q_last = realtime_quotes["QQQ"]["last"] if "QQQ" in realtime_quotes else float(history_dict["QQQ"]["Close"].iloc[-1])
        q_prev20 = float(history_dict["QQQ"]["Close"].iloc[-21])
        if q_prev20 > 0:
            qqqm_ret_20d = ((q_last - q_prev20) / q_prev20) * 100.0

    # 3. 处理宏观 14 只 ETF 数据 (含 SOXX / SOXL，严格分离正股收盘价与盘前盘后价)
    macro_data = {}
    for ticker, meta in MONITORED_TARGETS["ETFS"].items():
        if ticker in history_dict and len(history_dict[ticker]) >= 20:
            _rq_raw = realtime_quotes.get(ticker)
            history_dict[ticker], _rq_healed, _etf_split_meta = reconcile_live_quote_with_split_guard(
                ticker, history_dict[ticker], rq=_rq_raw, allow_network_on_jump=True
            )
            if _rq_healed is not None and ticker in realtime_quotes:
                realtime_quotes[ticker] = _rq_healed
            if ticker in realtime_quotes:
                rq = realtime_quotes[ticker]
                last = rq["last"]
                change = rq["change"]
                pct_change = rq["pct_change"]
                is_realtime = True
                history_dict[ticker].loc[history_dict[ticker].index[-1], "Close"] = last
                if rq.get("high", 0) > 0:
                    history_dict[ticker].loc[history_dict[ticker].index[-1], "High"] = max(float(history_dict[ticker]["High"].iloc[-1]), rq["high"])
                if rq.get("low", 0) > 0:
                    history_dict[ticker].loc[history_dict[ticker].index[-1], "Low"] = min(float(history_dict[ticker]["Low"].iloc[-1]), rq["low"])
            else:
                df_raw = history_dict[ticker]
                rth_c = intraday_map.get(ticker, {}).get("rth_close", 0.0)
                rth_date = intraday_map.get(ticker, {}).get("rth_date")
                last_daily_date = str(df_raw.index[-1].date()) if hasattr(df_raw.index[-1], "date") else str(df_raw.index[-1])[:10]
                if rth_c > 0:
                    if (rth_date and last_daily_date == rth_date) or (session_code in ["POST_MARKET", "OVERNIGHT", "WEEKEND", "PRE_MARKET"] and len(df_raw) >= 2):
                        prev_c = float(df_raw["Close"].iloc[-2]) if len(df_raw) >= 2 else float(df_raw["Close"].iloc[-1])
                        last = float(df_raw["Close"].iloc[-1]) if session_code in ["POST_MARKET", "OVERNIGHT", "WEEKEND"] else float(rth_c)
                    else:
                        prev_c = float(df_raw["Close"].iloc[-1])
                        last = float(rth_c)
                    change = last - prev_c
                    pct_change = (change / prev_c) * 100 if prev_c > 0 else 0.0
                    is_realtime = True
                    history_dict[ticker].loc[history_dict[ticker].index[-1], "Close"] = last
                else:
                    last = float(df_raw["Close"].iloc[-1])
                    change = float(df_raw["Close"].diff().iloc[-1]) if len(df_raw) >= 2 else 0.0
                    pct_change = (change / float(df_raw["Close"].iloc[-2])) * 100 if len(df_raw) >= 2 else 0.0
                    is_realtime = False

            # 提取盘前/盘后独立价格与 5m 日内数据
            intra_info = intraday_map.get(ticker, {})
            if _etf_split_meta.get("split_detected") and float(_etf_split_meta.get("split_factor") or 1.0) > 1.0:
                _scale_intra_split_fields(intra_info, last, float(_etf_split_meta["split_factor"]), is_forward_split=True)
            ext_price = 0.0
            ext_change = 0.0
            ext_pct = 0.0
            session_tag = ""
            active_price = last
            if session_code in ["PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND"] and intra_info.get("ext_price", 0) > 0:
                ep = intra_info["ext_price"]
                _adj_last, _adj_ep, _ext_chg, _ext_pct_g, _ext_healed, _ext_reason = reconcile_extended_quote_with_split_guard(
                    ticker, last, ep, df_hist=history_dict[ticker], split_meta=_etf_split_meta, intra_info=intra_info
                )
                if _ext_healed:
                    print(f"[+] 🔧 盘前/盘后拆股对齐 ({ticker}): {_ext_reason}")
                    if _adj_last > 0 and _adj_last != last:
                        last = _adj_last
                        active_price = _adj_last
                        history_dict[ticker].loc[history_dict[ticker].index[-1], "Close"] = last
                if _adj_ep > 0 and last > 0 and abs(_adj_ep - last) >= 0.005:
                    ext_price = _adj_ep
                    ext_change = _ext_chg
                    ext_pct = _ext_pct_g
                    active_price = _adj_ep
                    if session_code == "PRE_MARKET":
                        session_tag = "盘前"
                    elif session_code == "POST_MARKET":
                        session_tag = "盘后"
                    elif session_code == "OVERNIGHT":
                        session_tag = "夜盘"
                    else:
                        session_tag = "休市"
            effective_pct = pct_change
            overnight_pct = 0.0
            if ticker != "XAUUSD" and session_code in ["PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND"]:
                # 扩展时段只看“最近一次正规盘收盘后”的纯变动，绝不叠加前一日白天涨幅（24h XAUUSD 现货除外）
                if ext_price > 0:
                    effective_pct = ext_pct
                    overnight_pct = ext_pct
                else:
                    effective_pct = 0.0
            if session_code == "OVERNIGHT" and ext_price > 0 and session_tag == "夜盘" and intra_info.get("price_kind") != "overnight":
                try:
                    _ext_dt = datetime.fromisoformat(str(intra_info.get("ext_time", "")))
                    if _ext_dt.hour < 20 or (_ext_dt.hour == 20 and _ext_dt.minute == 0):
                        session_tag = "盘后定格"
                except Exception:
                    session_tag = "盘后定格"
            if session_tag == "盘后定格":
                active_price = last
                effective_pct = 0.0
                overnight_pct = 0.0

            df = compute_indicators(history_dict[ticker])
            history_dict[ticker] = df
            ma200 = float(df["MA200"].iloc[-1]) if "MA200" in df else float(df["MA50"].iloc[-1])
            ma50 = float(df["MA50"].iloc[-1])
            ma20 = float(df["MA20"].iloc[-1]) if "MA20" in df else float(df["Close"].rolling(20, min_periods=10).mean().iloc[-1])
            ema20 = float(df["EMA20"].iloc[-1]) if "EMA20" in df else float(df["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
            atr = float(df["ATR_14"].iloc[-1])
            rsi = float(df["RSI_14"].iloc[-1])
            bias = ((last - ma50) / ma50) * 100
            bias_ma20 = ((last - ma20) / ma20) * 100
            ret_20d = float(df["Ret_20d"].iloc[-1]) if "Ret_20d" in df and not np.isnan(df["Ret_20d"].iloc[-1]) else 0.0
            rs_20d = ret_20d - qqqm_ret_20d

            fib_low = float(df["Fib_Low"].iloc[-1])
            fib_high = float(df["Fib_High"].iloc[-1])
            vol_ratio = float(df["Vol_Ratio"].iloc[-1])
            high_bias_recently = bool((df["Bias_MA50"].iloc[max(0, len(df)-11):-1] >= 8.0).any()) if "Bias_MA50" in df and len(df) >= 2 else False
            prev_rsi = float(df["RSI_14"].iloc[-2]) if len(df) >= 2 else rsi
            min_rsi_5d = float(df["Min_RSI_5d"].iloc[-1]) if "Min_RSI_5d" in df else rsi
            pos_60 = float(df["Pos_60"].iloc[-1]) if "Pos_60" in df and not np.isnan(df["Pos_60"].iloc[-1]) else 0.5
            open_price = float(df["Open"].iloc[-1]) if "Open" in df else last
            prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else last
            prev_ema20 = float(df["EMA20"].iloc[-2]) if ("EMA20" in df and len(df) >= 2) else ema20
            # 盘前盘后价格联动：以真实成交有效价重新计算 bias 与 dist_to_pit，绝不顶着日盘收盘价的温和假象
            effective_price = active_price if (active_price > 0 and ext_price is not None and ext_price > 0) else last
            effective_bias = round(((effective_price - ma50) / ma50) * 100, 2) if ma50 > 0 else 0.0
            effective_bias_ma20 = round(((effective_price - ma20) / ma20) * 100, 2) if ma20 > 0 else 0.0
            bias = effective_bias
            bias_ma20 = effective_bias_ma20

            ema10 = float(df["EMA10"].iloc[-1]) if "EMA10" in df else ema20
            prev_ema10 = float(df["EMA10"].iloc[-2]) if ("EMA10" in df and len(df) >= 2) else ema10
            ema5 = float(df["EMA5"].iloc[-1]) if "EMA5" in df else ema20
            prev_ema5 = float(df["EMA5"].iloc[-2]) if ("EMA5" in df and len(df) >= 2) else ema5
            max_bias_5d = float(df["Max_Bias_5d"].iloc[-1]) if "Max_Bias_5d" in df and not np.isnan(df["Max_Bias_5d"].iloc[-1]) else effective_bias
            max_rsi_5d = float(df["Max_RSI_5d"].iloc[-1]) if "Max_RSI_5d" in df and not np.isnan(df["Max_RSI_5d"].iloc[-1]) else rsi

            sma200 = float(df["SMA200"].iloc[-1]) if "SMA200" in df else (float(df["MA200"].iloc[-1]) if "MA200" in df else ma50)
            min_rsi_3d = float(df["Min_RSI_3d"].iloc[-1]) if "Min_RSI_3d" in df and not np.isnan(df["Min_RSI_3d"].iloc[-1]) else min_rsi_5d
            atr_pct = float(df["ATR_Pct"].iloc[-1]) if "ATR_Pct" in df and not np.isnan(df["ATR_Pct"].iloc[-1]) else (((atr / effective_price) * 100.0) if effective_price > 0 else 3.0)
            fib_pos = float(df["Fib_Pos"].iloc[-1]) if "Fib_Pos" in df and not np.isnan(df["Fib_Pos"].iloc[-1]) else 0.5
            crash_5d = bool(df["Crash_5d"].iloc[-1]) if "Crash_5d" in df else False
            crash_3d = bool(df["Crash_3d"].iloc[-1]) if "Crash_3d" in df else False
            prev_crash_3d = bool(df["Prev_Crash_3d"].iloc[-1]) if "Prev_Crash_3d" in df else False
            is_right_turn = bool(df["Is_Right_Turn"].iloc[-1]) if "Is_Right_Turn" in df else False
            prev_is_right_turn = bool(df["Prev_Is_Right_Turn"].iloc[-1]) if "Prev_Is_Right_Turn" in df else False
            prev_crash_body = float(df["Prev_Crash_Body"].iloc[-1]) if "Prev_Crash_Body" in df and not np.isnan(df["Prev_Crash_Body"].iloc[-1]) else 0.0
            prev_crash_close = float(df["Prev_Crash_Close"].iloc[-1]) if "Prev_Crash_Close" in df and not np.isnan(df["Prev_Crash_Close"].iloc[-1]) else 0.0
            rs_60 = float(df["RET_60"].iloc[-1]) if "RET_60" in df and not np.isnan(df["RET_60"].iloc[-1]) else 0.0
            rth_high = float(intra_info.get("rth_high", 0.0)) if intra_info.get("rth_high", 0.0) > 0 else (float(df["High"].iloc[-1]) if "High" in df else effective_price)

            eval_res = evaluate_stock_signal(
                ticker, effective_price, ma50, effective_bias, rsi, vol_ratio, fib_low, fib_high, meta,
                atr=atr, weather_info=None, macro_data=None, ema20=ema20,
                high_bias_recently=high_bias_recently, prev_close=prev_close, prev_ema20=prev_ema20,
                prev_rsi=prev_rsi, min_rsi_5d=min_rsi_5d, pos_60=pos_60, open_price=open_price,
                ema10=ema10, prev_ema10=prev_ema10, ema5=ema5, prev_ema5=prev_ema5,
                max_bias_5d=max_bias_5d, max_rsi_5d=max_rsi_5d, max_pnl_pct=meta.get("max_pnl_pct", 0.0),
                sma200=sma200, min_rsi_3d=min_rsi_3d, atr_pct=atr_pct, fib_pos=fib_pos,
                crash_5d=crash_5d, is_right_turn=is_right_turn, rs_60=rs_60, low_price=float(df['Low'].iloc[-1]) if 'Low' in df else effective_price, rth_low=intra_info.get('rth_low', effective_price)
            )
            intra_eval = evaluate_intraday_entry(
                active_price, fib_low, fib_high, ma50,
                eval_res["is_pit"], eval_res["is_stop"], eval_res["dist_to_pit"], intra_info,
                earnings_info=earnings_map.get(ticker), rs_20d=rs_20d, atr=atr, daily_rsi=rsi,
                options_info=options_map.get(ticker),
                ai_s1=(load_ai_levels_map().get(ticker) or {}).get("s1"),
                ai_r1=(load_ai_levels_map().get(ticker) or {}).get("r1"),
                ai_cal_win_rate=(load_ai_levels_map().get(ticker) or {}).get("cal_win_rate"),
                pct_change=pct_change, ext_pct=ext_pct,
                rth_high=rth_high, prev_crash_3d=prev_crash_3d, prev_is_right_turn=prev_is_right_turn,
                ema5=ema5, prev_crash_body=prev_crash_body, prev_crash_close=prev_crash_close,
                session_code=session_code, ticker=ticker, crash_3d=crash_3d
            )

            if bias > 0:
                if eval_res["is_take_profit"]:
                    status = "🟣 极度超买"
                    badge_color = "#8e24aa"
                else:
                    status = "多头回踩" if rsi < 50 else "强势多头"
                    badge_color = "#1b5e20"
            else:
                if eval_res["is_stop"]:
                    status = "🔴 破位承压"
                    badge_color = "#b71c1c"
                elif eval_res["is_pit"]:
                    status = "🟢 黄金坑回踩"
                    badge_color = "#2e7d32"
                else:
                    status = "超跌蓄势" if rsi < 35 else "震荡承压"
                    badge_color = "#b71c1c" if bias < -5 else "#e65100"

            macro_data[ticker] = {
                "name": meta["name"],
                "sub": meta["sub"],
                "cat": meta["cat"],
                "cost": meta.get("cost", 0.0),
                "shares": meta.get("shares", 0.0),
                "trim1": meta.get("trim1", 0.0),
                "trim1_shares": meta.get("trim1_shares", 0.0),
                "trim2": meta.get("trim2", 0.0),
                "base_val": meta.get("base_val", 0.0),
                "target": meta.get("target", 0.0),
                "last": last,
                "change": change,
                "pct_change": pct_change,
                "active_price": active_price,
                "ext_price": ext_price,
                "ext_change": ext_change,
                "ext_pct": ext_pct,
                "effective_pct": effective_pct,
                "overnight_pct": overnight_pct,
                "session_tag": session_tag,
                "ma200": ma200,
                "sma200": sma200,
                "ma50": ma50,
                "ma20": ma20,
                "ema20": ema20,
                "ema10": ema10,
                "ema5": ema5,
                "max_bias_5d": max_bias_5d,
                "max_rsi_5d": max_rsi_5d,
                "bias": bias,
                "bias_ma20": bias_ma20,
                "pos_60": pos_60,
                "atr": atr,
                "rsi": rsi,
                "fib_low": fib_low,
                "fib_high": fib_high,
                "vol_ratio": vol_ratio,
                "status": status,
                "badge_color": badge_color,
                "is_realtime": is_realtime,
                **eval_res,
                **intra_eval,
                **options_map.get(ticker, {})
            }

    weather_info = evaluate_market_weather(macro_data, history_dict=history_dict, vix_regime=vix_regime)
    if isinstance(weather_info, dict):
        weather_info["cross_market_liquidity"] = cross_market_liquidity

    # 4. 处理全量个股数据并计算碎股、日线信号与 5m 日内建仓信号
    stocks_list = []
    for ticker, meta in MONITORED_TARGETS["STOCKS"].items():
        if ticker in history_dict and len(history_dict[ticker]) >= 20:
            _rq_raw = realtime_quotes.get(ticker)
            history_dict[ticker], _rq_healed, split_meta = reconcile_live_quote_with_split_guard(
                ticker, history_dict[ticker], rq=_rq_raw, allow_network_on_jump=True
            )
            if _rq_healed is not None and ticker in realtime_quotes:
                realtime_quotes[ticker] = _rq_healed
            if split_meta.get("split_detected"):
                print(f"[+] 🔧 实盘拆股自愈 ({ticker}): {split_meta.get('reason')}")
            if ticker in realtime_quotes:
                rq = realtime_quotes[ticker]
                last = rq["last"]
                change = rq["change"]
                pct_change = rq["pct_change"]
                is_realtime = True
                history_dict[ticker].loc[history_dict[ticker].index[-1], "Close"] = last
            else:
                df_raw = history_dict[ticker]
                rth_c = intraday_map.get(ticker, {}).get("rth_close", 0.0)
                rth_date = intraday_map.get(ticker, {}).get("rth_date")
                last_daily_date = str(df_raw.index[-1].date()) if hasattr(df_raw.index[-1], "date") else str(df_raw.index[-1])[:10]
                if rth_c > 0:
                    if (rth_date and last_daily_date == rth_date) or (session_code in ["POST_MARKET", "OVERNIGHT", "WEEKEND", "PRE_MARKET"] and len(df_raw) >= 2):
                        prev_c = float(df_raw["Close"].iloc[-2]) if len(df_raw) >= 2 else float(df_raw["Close"].iloc[-1])
                        last = float(df_raw["Close"].iloc[-1]) if session_code in ["POST_MARKET", "OVERNIGHT", "WEEKEND"] else float(rth_c)
                    else:
                        prev_c = float(df_raw["Close"].iloc[-1])
                        last = float(rth_c)
                    change = last - prev_c
                    pct_change = (change / prev_c) * 100 if prev_c > 0 else 0.0
                    is_realtime = True
                    history_dict[ticker].loc[history_dict[ticker].index[-1], "Close"] = last
                else:
                    last = float(df_raw["Close"].iloc[-1])
                    change = float(df_raw["Close"].diff().iloc[-1]) if len(df_raw) >= 2 else 0.0
                    pct_change = (change / float(df_raw["Close"].iloc[-2])) * 100 if len(df_raw) >= 2 else 0.0
                    is_realtime = False

            # 提取盘前/盘后独立价格与 5m 日内指标 (不篡改日线 Close，保证 MA50/RSI/日阳线纯净)
            intra_info = intraday_map.get(ticker, {})
            if split_meta.get("split_detected") and float(split_meta.get("split_factor") or 1.0) > 1.0:
                _scale_intra_split_fields(intra_info, last, float(split_meta["split_factor"]), is_forward_split=True)
            ext_price = 0.0
            ext_change = 0.0
            ext_pct = 0.0
            session_tag = ""
            active_price = last
            if session_code in ["PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND"] and intra_info.get("ext_price", 0) > 0:
                ep = intra_info["ext_price"]
                _adj_last, _adj_ep, _ext_chg, _ext_pct_g, _ext_healed, _ext_reason = reconcile_extended_quote_with_split_guard(
                    ticker, last, ep, df_hist=history_dict[ticker], split_meta=split_meta, intra_info=intra_info
                )
                if _ext_healed:
                    print(f"[+] 🔧 盘前/盘后拆股对齐 ({ticker}): {_ext_reason}")
                    if _adj_last > 0 and _adj_last != last:
                        last = _adj_last
                        active_price = _adj_last
                        history_dict[ticker].loc[history_dict[ticker].index[-1], "Close"] = last
                if _adj_ep > 0 and last > 0 and abs(_adj_ep - last) >= 0.005:
                    ext_price = _adj_ep
                    ext_change = _ext_chg
                    ext_pct = _ext_pct_g
                    active_price = _adj_ep
                    if session_code == "PRE_MARKET":
                        session_tag = "盘前"
                    elif session_code == "POST_MARKET":
                        session_tag = "盘后"
                    elif session_code == "OVERNIGHT":
                        session_tag = "夜盘"
                    else:
                        session_tag = "休市"
            effective_pct = pct_change
            overnight_pct = 0.0
            if ticker != "XAUUSD" and session_code in ["PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND"]:
                # 扩展时段只看“最近一次正规盘收盘后”的纯变动，绝不叠加前一日白天涨幅（24h XAUUSD 现货除外）
                if ext_price > 0:
                    effective_pct = ext_pct
                    overnight_pct = ext_pct
                else:
                    effective_pct = 0.0
            if session_code == "OVERNIGHT" and ext_price > 0 and session_tag == "夜盘" and intra_info.get("price_kind") != "overnight":
                try:
                    _ext_dt = datetime.fromisoformat(str(intra_info.get("ext_time", "")))
                    if _ext_dt.hour < 20 or (_ext_dt.hour == 20 and _ext_dt.minute == 0):
                        session_tag = "盘后定格"
                except Exception:
                    session_tag = "盘后定格"
            if session_tag == "盘后定格":
                active_price = last
                effective_pct = 0.0
                overnight_pct = 0.0

            df = compute_indicators(history_dict[ticker])
            history_dict[ticker] = df
            ma50 = float(df["MA50"].iloc[-1])
            ema20 = float(df["EMA20"].iloc[-1]) if "EMA20" in df else float(df["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
            high_bias_recently = bool((df["Bias_MA50"].iloc[max(0, len(df)-11):-1] >= 8.0).any()) if "Bias_MA50" in df and len(df) >= 2 else False
            atr = float(df["ATR_14"].iloc[-1])
            rsi = float(df["RSI_14"].iloc[-1])
            prev_rsi = float(df["RSI_14"].iloc[-2]) if len(df) >= 2 else rsi
            min_rsi_5d = float(df["Min_RSI_5d"].iloc[-1]) if "Min_RSI_5d" in df else rsi
            vol_ratio = float(df["Vol_Ratio"].iloc[-1])
            fib_low = float(df["Fib_Low"].iloc[-1])
            fib_high = float(df["Fib_High"].iloc[-1])
            pos_60 = float(df["Pos_60"].iloc[-1]) if "Pos_60" in df and not np.isnan(df["Pos_60"].iloc[-1]) else 0.5
            open_price = float(df["Open"].iloc[-1]) if "Open" in df else last
            ret_20d = float(df["Ret_20d"].iloc[-1]) if "Ret_20d" in df and not np.isnan(df["Ret_20d"].iloc[-1]) else 0.0
            rs_20d = ret_20d - qqqm_ret_20d

            prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else last
            prev_ema20 = float(df["EMA20"].iloc[-2]) if ("EMA20" in df and len(df) >= 2) else ema20
            # 盘前盘后价格联动：以真实成交有效价重新计算 bias 与 dist_to_pit，绝不顶着日盘收盘价的温和假象
            effective_price = active_price if (active_price > 0 and ext_price is not None and ext_price > 0) else last
            effective_bias = round(((effective_price - ma50) / ma50) * 100, 2) if ma50 > 0 else 0.0
            bias = effective_bias

            ema10 = float(df["EMA10"].iloc[-1]) if "EMA10" in df else ema20
            prev_ema10 = float(df["EMA10"].iloc[-2]) if ("EMA10" in df and len(df) >= 2) else ema10
            ema5 = float(df["EMA5"].iloc[-1]) if "EMA5" in df else ema20
            prev_ema5 = float(df["EMA5"].iloc[-2]) if ("EMA5" in df and len(df) >= 2) else ema5
            max_bias_5d = float(df["Max_Bias_5d"].iloc[-1]) if "Max_Bias_5d" in df and not np.isnan(df["Max_Bias_5d"].iloc[-1]) else effective_bias
            max_rsi_5d = float(df["Max_RSI_5d"].iloc[-1]) if "Max_RSI_5d" in df and not np.isnan(df["Max_RSI_5d"].iloc[-1]) else rsi

            sma200 = float(df["SMA200"].iloc[-1]) if "SMA200" in df else (float(df["MA200"].iloc[-1]) if "MA200" in df else ma50)
            min_rsi_3d = float(df["Min_RSI_3d"].iloc[-1]) if "Min_RSI_3d" in df and not np.isnan(df["Min_RSI_3d"].iloc[-1]) else min_rsi_5d
            atr_pct = float(df["ATR_Pct"].iloc[-1]) if "ATR_Pct" in df and not np.isnan(df["ATR_Pct"].iloc[-1]) else (((atr / effective_price) * 100.0) if effective_price > 0 else 3.0)
            fib_pos = float(df["Fib_Pos"].iloc[-1]) if "Fib_Pos" in df and not np.isnan(df["Fib_Pos"].iloc[-1]) else 0.5
            crash_5d = bool(df["Crash_5d"].iloc[-1]) if "Crash_5d" in df else False
            crash_3d = bool(df["Crash_3d"].iloc[-1]) if "Crash_3d" in df else False
            prev_crash_3d = bool(df["Prev_Crash_3d"].iloc[-1]) if "Prev_Crash_3d" in df else False
            is_right_turn = bool(df["Is_Right_Turn"].iloc[-1]) if "Is_Right_Turn" in df else False
            prev_is_right_turn = bool(df["Prev_Is_Right_Turn"].iloc[-1]) if "Prev_Is_Right_Turn" in df else False
            prev_crash_body = float(df["Prev_Crash_Body"].iloc[-1]) if "Prev_Crash_Body" in df and not np.isnan(df["Prev_Crash_Body"].iloc[-1]) else 0.0
            prev_crash_close = float(df["Prev_Crash_Close"].iloc[-1]) if "Prev_Crash_Close" in df and not np.isnan(df["Prev_Crash_Close"].iloc[-1]) else 0.0
            rs_60 = float(df["RET_60"].iloc[-1]) if "RET_60" in df and not np.isnan(df["RET_60"].iloc[-1]) else 0.0
            rth_high = float(intra_info.get("rth_high", 0.0)) if intra_info.get("rth_high", 0.0) > 0 else (float(df["High"].iloc[-1]) if "High" in df else effective_price)

            eval_res = evaluate_stock_signal(
                ticker, effective_price, ma50, effective_bias, rsi, vol_ratio, fib_low, fib_high, meta,
                atr=atr, weather_info=weather_info, macro_data=macro_data, ema20=ema20,
                high_bias_recently=high_bias_recently, prev_close=prev_close, prev_ema20=prev_ema20,
                prev_rsi=prev_rsi, min_rsi_5d=min_rsi_5d, pos_60=pos_60, open_price=open_price,
                ema10=ema10, prev_ema10=prev_ema10, ema5=ema5, prev_ema5=prev_ema5,
                max_bias_5d=max_bias_5d, max_rsi_5d=max_rsi_5d, max_pnl_pct=meta.get("max_pnl_pct", 0.0),
                sma200=sma200, min_rsi_3d=min_rsi_3d, atr_pct=atr_pct, fib_pos=fib_pos,
                crash_5d=crash_5d, is_right_turn=is_right_turn, rs_60=rs_60, low_price=float(df['Low'].iloc[-1]) if 'Low' in df else effective_price, rth_low=intra_info.get('rth_low', effective_price)
            )
            _ai_s1_n, _ai_r1_n, _ai_cwr_n = _normalize_ai_levels_for_price(
                load_ai_levels_map().get(ticker), active_price, split_meta
            )
            intra_eval = evaluate_intraday_entry(
                active_price, fib_low, fib_high, ma50,
                eval_res["is_pit"], eval_res["is_stop"], eval_res["dist_to_pit"], intra_info,
                earnings_info=earnings_map.get(ticker), rs_20d=rs_20d, atr=atr, daily_rsi=rsi,
                options_info=options_map.get(ticker),
                ai_s1=_ai_s1_n,
                ai_r1=_ai_r1_n,
                ai_cal_win_rate=_ai_cwr_n,
                pct_change=pct_change, ext_pct=ext_pct,
                rth_high=rth_high, prev_crash_3d=prev_crash_3d, prev_is_right_turn=prev_is_right_turn,
                ema5=ema5, prev_crash_body=prev_crash_body, prev_crash_close=prev_crash_close,
                session_code=session_code, ticker=ticker, crash_3d=crash_3d
            )

            _stock_bias_ma20 = _calc_bias_ma20(history_dict[ticker], active_price)
            stocks_list.append({
                "ticker": ticker,
                "name": meta["name"],
                "sub": meta["sub"],
                "cat": meta["cat"],
                "cost": meta.get("cost", 0.0),
                "shares": meta.get("shares", 0.0),
                "trim1": meta.get("trim1", 0.0),
                "trim1_shares": meta.get("trim1_shares", 0.0),
                "trim2": meta.get("trim2", 0.0),
                "base_val": meta.get("base_val", 0.0),
                "target": meta.get("target", 0.0),
                "max_alloc": 0.0,
                "last": last,
                "change": change,
                "pct_change": pct_change,
                "active_price": active_price,
                "ext_price": ext_price,
                "ext_change": ext_change,
                "ext_pct": ext_pct,
                "effective_pct": effective_pct,
                "overnight_pct": overnight_pct,
                "ma50": ma50,
                "bias_ma20": _stock_bias_ma20,
                "split_detected": bool(split_meta.get("split_detected", False)),
                "split_factor": float(split_meta.get("split_factor", 1.0) or 1.0),
                "split_suspect_unhealed": bool(split_meta.get("split_suspect_unhealed", False)),
                "ema20": ema20,
                "ema10": ema10,
                "ema5": ema5,
                "max_bias_5d": max_bias_5d,
                "max_rsi_5d": max_rsi_5d,
                "bias": bias,
                "atr": atr,
                "rsi": rsi,
                "vol_ratio": vol_ratio,
                "fib_low": fib_low,
                "fib_high": fib_high,
                "is_realtime": is_realtime,
                "session_tag": session_tag,
                **eval_res,
                **intra_eval,
                **options_map.get(ticker, {})
            })
        else:
            # yfinance 历史不足 20 日或次新上市/首日 IPO 暂未开盘时，优雅生成安全底模确保不中断不丢失
            df_short = history_dict.get(ticker)
            intra_fb = intraday_map.get(ticker, {})
            rq = realtime_quotes.get(ticker, {})
            ipo_price = float(meta.get("ipo_price", 0.0) or 0.0)
            last = 0.0
            change = 0.0
            pct_change = 0.0
            is_realtime = False
            is_ipo_pending = False
            if rq and rq.get("last", 0) > 0:
                last = float(rq["last"])
                change = float(rq.get("change", 0.0) or 0.0)
                pct_change = float(rq.get("pct_change", 0.0) or 0.0)
                is_realtime = True
            elif df_short is not None and not df_short.empty:
                last = float(df_short["Close"].iloc[-1])
                if len(df_short) >= 2:
                    prev_c = float(df_short["Close"].iloc[-2])
                    change = last - prev_c
                    pct_change = (change / prev_c) * 100.0 if prev_c > 0 else 0.0
                is_realtime = bool(intra_fb.get("rth_close", 0) > 0)
            elif intra_fb.get("ext_price", 0) > 0:
                last = float(intra_fb["ext_price"])
                is_realtime = True
            elif intra_fb.get("rth_close", 0) > 0:
                last = float(intra_fb["rth_close"])
                is_realtime = True
            elif ipo_price > 0:
                last = ipo_price
                is_ipo_pending = True

            if last > 0 and ipo_price > 0 and (df_short is None or len(df_short) < 2) and not is_ipo_pending and change == 0.0 and pct_change == 0.0:
                change = round(last - ipo_price, 2)
                pct_change = round((change / ipo_price) * 100.0, 2)

            if last > 0:
                raw_ep = float(intra_fb.get("ext_price", 0.0) or 0.0)
                ext_price = 0.0
                ext_change = 0.0
                ext_pct = 0.0
                session_tag = ""
                if session_code in ["PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND"] and raw_ep > 0:
                    _adj_last, _adj_ep, _ext_chg, _ext_pct_g, _ext_healed, _ = reconcile_extended_quote_with_split_guard(
                        ticker, last, raw_ep, df_hist=df_short, intra_info=intra_fb
                    )
                    if _ext_healed and _adj_last > 0 and _adj_last != last:
                        last = _adj_last
                    if _adj_ep > 0 and last > 0 and abs(_adj_ep - last) >= 0.005:
                        ext_price = _adj_ep
                        ext_change = _ext_chg
                        ext_pct = _ext_pct_g
                        session_tag = "盘前" if session_code == "PRE_MARKET" else ("盘后" if session_code == "POST_MARKET" else ("夜盘" if session_code == "OVERNIGHT" else "休市"))
                active_price = ext_price if ext_price > 0 else last
                ma50 = float(df_short["Close"].mean()) if (df_short is not None and not df_short.empty) else last
                ema20 = ma50
                bias = ((active_price - ma50) / ma50) * 100 if ma50 > 0 else 0.0
                atr = last * 0.03
                rsi = 50.0
                vol_ratio = 1.0
                fib_low = float(df_short["Low"].min()) if (df_short is not None and not df_short.empty) else last * 0.92
                fib_high = float(df_short["High"].max()) if (df_short is not None and not df_short.empty) else last * 0.98
                eval_res = evaluate_stock_signal(ticker, active_price, ma50, bias, rsi, vol_ratio, fib_low, fib_high, meta, atr=atr, weather_info=weather_info, macro_data=macro_data, ema20=ema20, high_bias_recently=False)
                is_ipo_stage = is_ipo_pending or (ipo_price > 0 and (df_short is None or len(df_short) < 5))
                if is_ipo_stage:
                    eval_res.update({
                        "signal": f"🆕 IPO待开盘 (${ipo_price:.2f})" if is_ipo_pending else f"🆕 IPO首日观察 (${ipo_price:.2f})",
                        "pit_status": "🆕 IPO发行期 · 待积累K线",
                        "is_pit": False,
                        "is_stop": False,
                        "is_take_profit": False,
                        "is_deep_pullback": False,
                        "is_crash_halt": False,
                        "is_cooling_halt": False,
                        "dist_to_pit": 999.0,
                        "optimal_limit": round(ipo_price, 2),
                        "limit_note": f"IPO 发行参考价 ${ipo_price:.2f}（前5日仅实时盯盘）",
                        "intraday_entry_ready": False,
                        "intraday_status_text": "🆕 IPO价格发现期 (待积累5m/日线)",
                        "intraday_reason": f"新股 IPO 发行参考价 ${ipo_price:.2f}。上市前 5 个交易日缺历史均线与斐波那契支撑，雷达实时追踪成交报价，暂不触发量化买点或破位警报。",
                        "entry_stop_loss": 0.0,
                        "is_ipo_pending": is_ipo_pending,
                        "ipo_price": ipo_price,
                    })
                stocks_list.append({
                    "ticker": ticker,
                    "name": meta["name"],
                    "sub": meta["sub"],
                    "cat": meta["cat"],
                    "cost": meta.get("cost", 0.0),
                    "shares": meta.get("shares", 0.0),
                    "base_val": meta.get("base_val", 0.0),
                    "target": meta.get("target", 0.0),
                    "max_alloc": 0.0,
                    "last": last,
                    "change": change,
                    "pct_change": pct_change,
                    "active_price": active_price,
                    "ext_price": ext_price,
                    "ext_change": ext_change,
                    "ext_pct": ext_pct,
                    "session_tag": session_tag,
                    "effective_pct": ext_pct if ext_price > 0 else (0.0 if (not is_ipo_stage and session_code in ["PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND"]) else pct_change),
                    "ma50": ma50,
                    "ema20": ema20,
                    "bias": bias,
                    "atr": atr,
                    "rsi": rsi,
                    "vol_ratio": vol_ratio,
                    "has_5m_intraday": False,
                    "rsi_5m": None,
                    "rvol_5m": None,
                    "rs_20d": None,
                    "fib_low": fib_low,
                    "fib_high": fib_high,
                    "is_realtime": is_realtime,
                    "is_ipo_pending": is_ipo_pending,
                    "ipo_price": ipo_price,
                    **eval_res,
                    **options_map.get(ticker, {})
                })

    # 按距离黄金坑升序排序
    stocks_sorted = sorted(stocks_list, key=lambda x: x["dist_to_pit"])

    # 使用全量个股 + 宏观 ETF 重新完整核算跨行业市场广度与普跌预警
    stocks_lookup = {s["ticker"]: s for s in stocks_sorted if isinstance(s, dict) and s.get("ticker")}
    weather_info = evaluate_market_weather(macro_data, history_dict=history_dict, stocks_lookup=stocks_lookup, vix_regime=vix_regime)
    if isinstance(weather_info, dict):
        weather_info["cross_market_liquidity"] = cross_market_liquidity

    # 5. 终端打印规整 Rich 表格
    display_terminal_tables(macro_data, stocks_sorted, weather_info, has_realtime=has_realtime)

    # 6. 生成三大交互式 HTML 看板 (静默覆盖写入，且具备空数据自动跳过保护)
    if not stocks_sorted or not macro_data:
        print("[!] ⚠️ 警告: 本轮抓取标的数异常为空 (疑似网络瞬时断连)，保留上一轮有效看板数据，跳过覆盖写入以防页面白屏。")
        return

    # 6.9 静默零未来函数自动回填未完成实盘推送信号账本 (Dry-Run / --no-alerts 下严禁修改生产账本)
    if not _external_alerts_disabled():
        try:
            import signal_ledger as _sl_bf
            _all_live_lk = _build_quote_lookup_map(macro_data=macro_data, stocks_data=stocks_sorted)
            _bf_res = _sl_bf.backfill_open_signals(
                live_quotes=_all_live_lk,
                history_dict=history_dict,
                intraday_map=intraday_map,
            )
            if _bf_res.get("updated", 0) > 0:
                print(f"[+] 📒 实盘信号账本自动回填: 更新 {_bf_res['updated']} 笔 / 完结 {_bf_res['completed']} 笔")
        except Exception as _sl_bf_err:
            print(f"  [!] 📒 信号账本自动回填降级跳过: {_sl_bf_err}")

    # 7.0 聚合脱敏新闻面、宏观日历防插针状态与个股资讯缓存 (data_cache/latest_market_news.json, 严格timeout=3s且异常静默降级)
    try:
        import jin10_news_sentinel as _jn_agg
        _all_monitored_tickers = list(MONITORED_TARGETS.get("ETFS", {}).keys()) + list(MONITORED_TARGETS.get("STOCKS", {}).keys())
        _jn_agg.build_latest_market_news_cache(
            tickers=_all_monitored_tickers,
            cache_path=os.path.join(SCRATCH_DIR, "data_cache", "latest_market_news.json"),
            timeout=3,
            max_wall_seconds=6.0,
            refresh_all=("--once" in sys.argv),
            finnhub_api_key=FINNHUB_API_KEY,
        )
    except Exception as _mn_err:
        print(f"  [!] 📰 消息面缓存聚合静默降级: {_mn_err}")

    # 7.05 V5.0 股指期货先导哨 (NQ=F / ES=F) + 大盘/日内泡沫与抄底四维温度计 (严格纯美股口径，0 污染 79 标的池)
    _market_temperature = {}
    try:
        import market_temperature_engine as _mte_run
        _fut_snap = _mte_run.fetch_futures_snapshot(session_code=session_code)
        _market_temperature = _mte_run.compute_market_temperature(
            macro_data=macro_data,
            stocks_data=stocks_sorted,
            vix_regime=vix_regime,
            cross_market_liquidity=cross_market_liquidity,
            futures_snapshot=_fut_snap,
            session_code=session_code,
            history_dict=history_dict,
            intraday_map=intraday_map,
        )
        if isinstance(weather_info, dict):
            weather_info["futures"] = _market_temperature.get("futures") or {}
            weather_info["market_temperature"] = _market_temperature
        _nq_i = (_market_temperature.get("futures") or {}).get("NQ=F") or {}
        _es_i = (_market_temperature.get("futures") or {}).get("ES=F") or {}
        print(
            f"[+] 🌡️ V5.0 期货与温度计: NQ={_nq_i.get('last', '--')} ({_nq_i.get('pct_chg', '--')}%) | "
            f"ES={_es_i.get('last', '--')} ({_es_i.get('pct_chg', '--')}%) | "
            f"泡沫 {_market_temperature.get('macro_bubble_score')}/{_market_temperature.get('intraday_bubble_score')} | "
            f"抄底 {_market_temperature.get('macro_bottom_score')}/{_market_temperature.get('intraday_bottom_score')} "
            f"[锁定={_market_temperature.get('knife_catch_locked')}]"
        )
    except Exception as _mte_err:
        print(f"  [!] 🌡️ V5.0 期货与温度计静默降级: {_mte_err}")

    generate_macro_radar_html(
        macro_data,
        weather_info,
        history_dict,
        update_time_str=now_str,
        has_realtime=has_realtime,
        cross_market_liquidity=cross_market_liquidity,
        market_temperature=_market_temperature,
    )
    generate_stocks_radar_html(stocks_sorted, history_dict, update_time_str=now_str, has_realtime=has_realtime, vix_regime=vix_regime)
    
    target_watchlist = load_target_watchlist()
    terminal_notifications = generate_and_sync_notifications(macro_data, stocks_sorted, weather_info, now_str, session_code=session_code)

    # 阶段二：更新盘前走廊与 09:45 ET 实时背离纠偏快照 (盘中 0 次全量重下 60d 底模)
    _ip_res = None
    try:
        import intraday_path_predictor
        _ip_tickers = list(MONITORED_TARGETS.get("ETFS", {}).keys()) + list(MONITORED_TARGETS.get("STOCKS", {}).keys())
        _ip_res = intraday_path_predictor.update_intraday_path_forecasts(
            tickers=_ip_tickers,
            intraday_map=intraday_map,
            history_dict=history_dict,
            realtime_quotes=realtime_quotes,
            market_regime=(weather_info or {}).get("market_regime", "NEUTRAL_CHOP"),
        )
        _ip_cov = _ip_res.get("coverage", {})
        print(f"[+] 📈 今日盘中走廊更新 ({_ip_res.get('forecast_date')}): 有效 {_ip_cov.get('ok_count', 0)}/{_ip_cov.get('total_count', 0)} 只 | 样本不足 {_ip_cov.get('insufficient_history_count', 0)} | 7x24 {_ip_cov.get('continuous_24h_count', 0)}")
    except Exception as _ip_err:
        print(f"  [!] 盘中预测走廊静默降级: {_ip_err}")

    # 6.94 锁定当日 S1/S2/R1/R2 静态筹码锚点 (data_cache/daily_sr_anchors.json · 盘中不随现价漂移)
    _sr_anchors_map = {}
    try:
        import sr_anchor_engine as _sr_eng
        _sr_date_et = _sr_eng.resolve_trading_date_et(session_code=session_code)
        _sr_batch_items = []
        _seen_sr = set()
        for _tk_sr, _it_sr in [(str(k), v) for k, v in (macro_data or {}).items() if isinstance(v, dict)] + [(str(s.get("ticker")), s) for s in (stocks_sorted or []) if isinstance(s, dict) and s.get("ticker")]:
            _tk_u = str(_tk_sr).upper()
            if not _tk_u or _tk_u in _seen_sr:
                continue
            _seen_sr.add(_tk_u)
            _df_h = history_dict.get(_tk_u) if isinstance(history_dict, dict) else None
            _opt_sr = options_map.get(_tk_u) if isinstance(options_map, dict) else {}
            _last_sr = float(_it_sr.get("last") or 0.0)
            _chg_sr = float(_it_sr.get("change") or 0.0)
            _prev_c_fb = (_last_sr - _chg_sr) if (_last_sr > 0 and (_last_sr - _chg_sr) > 0 and abs(_chg_sr) > 0) else _last_sr
            _atr_sr = float(_it_sr.get("atr") or 0.0)
            _atr_pct_sr = ((_atr_sr / _last_sr) * 100.0) if (_atr_sr > 0 and _last_sr > 0) else 2.5
            _sr_batch_items.append({
                "ticker": _tk_u,
                "prev_close": None if (_df_h is not None and not _df_h.empty and len(_df_h) >= 10) else _prev_c_fb,
                "atr_pct": _atr_pct_sr,
                "df_history": _df_h,
                "put_wall": (_opt_sr or {}).get("put_wall"),
                "call_wall": (_opt_sr or {}).get("call_wall"),
                "split_detected": bool(_it_sr.get("split_detected", False)),
            })
        _sr_anchors_map = _sr_eng.update_daily_sr_anchors_batch(_sr_batch_items, date_et=_sr_date_et)
        print(f"[+] ⚓ 当日静态支撑/阻力锚点已锁定 ({_sr_date_et}): {len(_sr_anchors_map)} 只标的")
    except Exception as _sr_err:
        print(f"  [!] ⚓ 当日静态 S/R 锚点锁定降级跳过: {_sr_err}")

    try:
        import importlib, create_futu_terminal
        importlib.reload(create_futu_terminal)
        _build_fn = create_futu_terminal.build_futu_terminal_html
    except Exception:
        _build_fn = build_futu_terminal_html
    futu_html = _build_fn(stocks_sorted, macro_data, weather_info, update_time_str=now_str, has_realtime=has_realtime, target_watchlist=target_watchlist, terminal_notifications=terminal_notifications, session_code=session_code, session_name=session_name, sync_side_effects=True, daily_sr_anchors=_sr_anchors_map)
    with open(FUTU_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(futu_html)
    index_html_path = os.path.join(SCRATCH_DIR, "index.html")
    try:
        with open(index_html_path, "w", encoding="utf-8") as f:
            f.write(futu_html)
    except Exception:
        pass
    print(f"✅ 富途牛牛专业全周期看盘终端已生成: {FUTU_HTML_FILE}")
    sync_live_prices_to_predict_html(macro_data, stocks_sorted, session_code)

    # 6.95 原子写入轻量只读快照 data_cache/latest_radar_snapshot.json 供独立 Discord Bot 秒查 (零额外 API)
    try:
        _snap_obj = build_latest_radar_snapshot(
            macro_data=macro_data,
            stocks_data=stocks_sorted,
            weather_info=weather_info,
            vix_regime=vix_regime,
            cross_market_liquidity=cross_market_liquidity,
            intraday_forecasts=_ip_res,
            session_code=session_code,
            session_name=session_name,
            now_str=now_str,
            daily_sr_anchors=_sr_anchors_map,
            market_temperature=_market_temperature,
        )
        _snap_written = write_latest_radar_snapshot(_snap_obj)
        print(f"[+] 📦 轻量只读快照已更新: {_snap_written} ({_snap_obj.get('ticker_count', 0)} 只标的)")
    except Exception as _snap_err:
        print(f"  [!] 📦 轻量只读快照落盘降级跳过: {_snap_err}")

    if not _external_alerts_disabled():
        # 7. 智能去重微信预警推送 (PushPlus 微信 Markdown 卡片通道)
        dispatch_wechat_radar_alerts(macro_data, stocks_sorted, weather_info, now_str)

        # 7.5 美股五大梯队量化哨兵与板块巡检 (Discord Webhook 富文本通道)
        dispatch_discord_sentinel_async(macro_data, stocks_sorted, weather_info, now_str)

        # 7.55 金十数据宏观红字快讯与重磅财经日历哨兵 (独立守护线程 · 3s超时熔断)
        dispatch_jin10_sentinel_async()

        # 7.6 专属防御因子背离百分比频道推送 (每次雷达刷新后专属播报)
        df_alert_push = None
        try:
            import threading
            live_lookup_df = {s["ticker"]: s for s in stocks_sorted if isinstance(s, dict) and "ticker" in s}
            live_lookup_df.update(macro_data or {})
            df_alert_push = weather_info.get("defensive_flight") if (weather_info and isinstance(weather_info, dict)) else None
            if not df_alert_push:
                import ml_predictor_5y
                df_alert_push = ml_predictor_5y.compute_defensive_flight_alert(live_lookup=live_lookup_df, session_code=session_code)
            # 夜盘防刷屏：仅 Score 变动 >=0.10%p 或核心成分变动 >=0.5% 才发送
            if should_send_defensive_flight_alert(df_alert_push, live_lookup_df, session_code=session_code):
                if send_discord_defensive_flight_alert(df_alert_push, live_lookup_df, now_str):
                    record_defensive_flight_sent(df_alert_push, live_lookup_df, session_code=session_code)
            else:
                print("  [*] 防御因子无显著变化（<0.10%p 且核心成分<0.5%），跳过 Discord 推送")
        except Exception as df_err:
            print(f"  [!] 💬 防御因子背离推送启动异常: {df_err}")

        # 7.7 VIX/VXN 恐慌中枢状态切换 & 科技极端去杠杆预警
        try:
            dispatch_vix_regime_discord_alerts(
                vix_regime=vix_regime,
                weather_info=weather_info,
                def_flight=df_alert_push,
                now_str=now_str,
                session_code=session_code,
                macro_data=macro_data,
                stocks_data=stocks_sorted,
            )
        except Exception as _vx_err:
            print(f"  [!] 💬 VIX/VXN 恐慌状态 Discord 推送异常: {_vx_err}")

        # 7.8 盘中预测走廊背离预警 (仅限 SPY/QQQ/SOXL，且严格锁定 09:45~15:50 ET)
        try:
            dispatch_intraday_corridor_divergence_alerts(
                intraday_forecasts=_ip_res,
                macro_data=macro_data,
                stocks_data=stocks_sorted,
                weather_info=weather_info,
                session_code=session_code,
            )
        except Exception as _ip_al_err:
            print(f"  [!] 💬 盘中走廊背离 Discord 推送异常: {_ip_al_err}")

        # 7.9 策略 B：⚡ VIX 盘中真退潮 · 右侧窄止损博反抽 (严格倒挂禁令 + VIX<=-5% + 09:45~15:00 ET)
        try:
            dispatch_vix_intraday_rebound_alerts(
                vix_regime=vix_regime,
                macro_data=macro_data,
                stocks_data=stocks_sorted,
                intraday_map=intraday_map,
                weather_info=weather_info,
                session_code=session_code,
            )
        except Exception as _vr_reb_err:
            print(f"  [!] 💬 VIX 盘中真退潮博反抽 Discord 推送异常: {_vr_reb_err}")

        # 7.10 每周六实盘信号复盘周报 (美东周五 18:30 ET ~ 周六 12:00 ET，跨设备每周唯一)
        try:
            import signal_ledger as _sl_wk
            _sl_wk.dispatch_weekly_recap_discord(device_tag=_shared_device_tag())
        except Exception as _wk_err:
            print(f"  [!] 📒 Discord 每周实盘复盘周报推送异常: {_wk_err}")
    else:
        print("  [*] [Dry-Run Guard] 已屏蔽全部外部推送 (RADAR_DISABLE_EXTERNAL_ALERTS / --no-alerts)")

    # 8. 更新本地同步锁时间戳
    update_last_sync_time()

    macro_path = MACRO_HTML_FILE
    stocks_path = STOCKS_HTML_FILE
    futu_path = FUTU_HTML_FILE

    if is_first_run:
        print("\n" + "=" * 80)
        print(f"📊 [首次启动] 正在浏览器中开启看盘终端与量化雷达...")
        print(f"  -> 🐮 富途全周期终端: {futu_path}")
        print(f"  -> 🎯 核心个股碎股雷达: {stocks_path}")
        print(f"  -> 🏛️ 宏观大盘司令塔: {macro_path}")
        print("  -> 提示: 网页内置 <meta http-equiv='refresh' content='180'> 自刷新机制，后续轮次仅静默覆盖文件，无需重复弹窗！")
        print("=" * 80 + "\n")
        if not NO_BROWSER:
            try:
                webbrowser.open(f"file://{futu_path}")
            except Exception as e:
                print(f"[!] browser open failed: {e}")
    else:
        print(f"[*] [第 {round_idx} 轮] 三份看板与富途终端 HTML 已覆盖写入更新完毕 (已重置 180s 同步锁)。")

# ==============================================================================
# 九、 主入口与智能同步锁调度引擎 (3分钟自动轮询 + 手动刷新无缝重置)
# ==============================================================================

def _latest_closed_us_day_et():
    """返回最近一个已经收盘的美股交易日（周末/节假日自动回退）。"""
    try:
        import ml_predictor_5y
        return ml_predictor_5y._get_latest_expected_trading_day()
    except Exception:
        try:
            from zoneinfo import ZoneInfo
            et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            et = datetime.now(timezone(timedelta(hours=-5)))
        d = et.date()
        hm = et.hour * 60 + et.minute
        if d.weekday() < 5 and hm >= 16 * 60 + 5:
            return d
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d


def _prediction_last_history_day():
    try:
        p = os.path.join(SCRATCH_DIR, "data_cache", "latest_ai_predictions.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        cyc = d.get("time_cycle_projections") or {}
        days = []
        for item in cyc.values():
            hist = (item or {}).get("history") or []
            if hist:
                days.append(str(hist[-1].get("date", ""))[:10])
        return max(days) if days else ""
    except Exception:
        return ""


def _page_last_history_day():
    try:
        p = os.path.join(SCRATCH_DIR, "predict_radar_5y.html")
        s = open(p, encoding="utf-8", errors="replace").read()
        key = "const timeCycleData = "
        i = s.find(key)
        if i < 0:
            return ""
        d, _ = json.JSONDecoder().raw_decode(s[i + len(key):])
        days = []
        for item in (d or {}).values():
            hist = (item or {}).get("history") or []
            if hist:
                days.append(str(hist[-1].get("date", ""))[:10])
        return max(days) if days else ""
    except Exception:
        return ""


def _btc_history_status():
    """检查 BTC-USD 在 predict_radar_5y.html 中是否已包含周末自然日及最新 UTC 日线。"""
    try:
        p = os.path.join(SCRATCH_DIR, "predict_radar_5y.html")
        s = open(p, encoding="utf-8", errors="replace").read()
        key = "const timeCycleData = "
        i = s.find(key)
        if i < 0:
            return "", False
        d, _ = json.JSONDecoder().raw_decode(s[i + len(key):])
        btc = (d or {}).get("BTC-USD") or {}
        hist = btc.get("history") or []
        if not hist:
            return "", False
        dates = [str(h.get("date", ""))[:10] for h in hist if h.get("date")]
        has_wknd = any(datetime.strptime(dt_s, "%Y-%m-%d").weekday() >= 5 for dt_s in dates if len(dt_s) == 10)
        return (dates[-1] if dates else ""), has_wknd
    except Exception:
        return "", False


def maybe_refresh_daily_ml_panel():
    """每当美股收盘 (16:05 ET) 或 BTC-USD 跨越 UTC 00:00 产生新的自然日 K 线时，自动执行 ml_predictor_5y.py --refresh。"""
    try:
        expected = _latest_closed_us_day_et()
        exp_s = expected.strftime("%Y-%m-%d")
        exp_btc_utc = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
        state_path = os.path.join(SCRATCH_DIR, "data_cache", ".last_daily_ml_refresh")
        marker_path = os.path.join(SCRATCH_DIR, "data_cache", ".ml_panel_max_date")
        btc_marker_path = os.path.join(SCRATCH_DIR, "data_cache", ".ml_btc_utc_date")
        os.makedirs(os.path.dirname(state_path), exist_ok=True)

        marker = ""
        try:
            marker = open(marker_path, encoding="utf-8").read().strip()[:10]
        except Exception:
            pass
        btc_marker = ""
        try:
            btc_marker = open(btc_marker_path, encoding="utf-8").read().strip()[:10]
        except Exception:
            pass
        pred_day = _prediction_last_history_day()
        page_day = _page_last_history_day()
        btc_last_day, btc_has_wknd = _btc_history_status()
        # 只要美股收盘日线落后，或 BTC-USD 跨越 UTC 00:00 产生新自然日 K 线 / 缺少周末日线，就触发全量重算
        us_ok = bool((marker and marker >= exp_s) and (pred_day and pred_day >= exp_s) and (page_day and page_day >= exp_s))
        btc_ok = bool((btc_marker and btc_marker >= exp_btc_utc) and (btc_last_day and btc_last_day >= exp_btc_utc) and btc_has_wknd)
        if us_ok and btc_ok:
            return

        state = {}
        if os.path.exists(state_path):
            try:
                raw = open(state_path, encoding="utf-8").read().strip()
                state = json.loads(raw) if raw.startswith("{") else ({"success_day": raw} if raw else {})
            except Exception:
                state = {}
        last_attempt = float(state.get("last_attempt_epoch") or 0)
        if time.time() - last_attempt < 600:
            return
        state["last_attempt_epoch"] = time.time()
        state.pop("success_day", None)
        with open(state_path, "w", encoding="utf-8") as sf:
            json.dump(state, sf)

        print(f"[*] 收盘/UTC日切全量重算触发：美股期望 {exp_s} (marker={marker or '无'}) | BTC UTC期望 {exp_btc_utc} (btc_marker={btc_marker or '无'}, wknd={btc_has_wknd})")
        proc = subprocess.run(
            [sys.executable, "ml_predictor_5y.py", "--refresh"],
            cwd=SCRATCH_DIR,
            check=False,
            timeout=1800,
            creationflags=WIN_NO_WINDOW
        )
        marker2 = ""
        try:
            marker2 = open(marker_path, encoding="utf-8").read().strip()[:10]
        except Exception:
            pass
        pred2 = _prediction_last_history_day()
        page2 = _page_last_history_day()
        if proc.returncode == 0 and (marker2 >= exp_s or pred2 >= exp_s or page2 >= exp_s):
            state["success_day"] = exp_s
            state["btc_success_utc_day"] = exp_btc_utc
            with open(state_path, "w", encoding="utf-8") as sf:
                json.dump(state, sf)
            print(f"[+] 收盘/UTC日切全量重算已完成：美股 {exp_s} / BTC UTC {exp_btc_utc}（AI {pred2} / 页面 {page2}）")
        else:
            print(f"[!] 收盘日线补刷未完成 {exp_s}（exit={proc.returncode}, AI={pred2}, 页面={page2}），10分钟后重试")
    except Exception as _e:
        print(f"[!] 收盘日线自动补刷异常: {_e}")


def _other_device_is_active():
    """Skip a round if origin/main has a <50s live commit from the other device."""
    try:
        subprocess.run(["git", "fetch", "origin", "main", "--quiet"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
        res = subprocess.run(["git", "log", "-1", "--format=%ct|%s", "origin/main"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, creationflags=WIN_NO_WINDOW)
        raw = (res.stdout or "").strip()
        if "|" not in raw:
            return False
        ts_s, subject = raw.split("|", 1)
        if time.time() - float(ts_s) >= 50:
            return False
        android_submit = "小米平板实盘同步" in subject
        pc_submit = ("实盘同步" in subject) and not android_submit
        return (pc_submit and ANDROID_MODE) or (android_submit and not ANDROID_MODE)
    except Exception:
        return False


def main():
    # 检查是否为本地常驻自选盯盘 API 守护子进程 (--star-server-daemon)
    if "--star-server-daemon" in sys.argv:
        try:
            lan_ip = get_local_lan_ip()
            print(f"[*] 📱 移动端/局域网看盘守护服务已启动: http://{lan_ip}:8765 (同一Wi-Fi下手机直接打开)")
            print(f"[*] 💻 电脑端本地看盘地址: http://127.0.0.1:8765")
            server = HTTPServer(('0.0.0.0', 8765), WatchlistApiHandler)
            server.serve_forever()
        except Exception:
            pass
        sys.exit(0)

    # 检查是否为微信推送测试模式 (--test-wechat)
    if "--test-wechat" in sys.argv:
        print("\n" + "=" * 80)
        print("📱 [测试] 正在向微信发送 PushPlus 预警测试卡片...")
        print("=" * 80)
        test_content = (
            "### 🎉 美股量化雷达 · 微信通知连接成功！\n"
            "- **监控标的**：55 只标的 (12 只 ETF + 43 只核心个股)\n"
            "- **监控策略**：🟢黄金坑就位、🔴破位止损、🟣止盈卖出、🎯INTC专属防线\n"
            "- **防刷机制**：已开启 4 小时同状态冷却去重\n"
            "- **轮询调度**：3 分钟后台静默巡检\n\n"
            "*(当雷达捕获关键买入或风控信号时，将自动推送卡片至微信)*"
        )
        ok = send_pushplus_wechat_alert("【美股量化雷达】微信推送测试成功", test_content)
        if ok:
            print("[+] ✅ 微信测试消息发送成功，请在手机微信中查收！\n")
        else:
            print("[!] ❌ 微信测试消息发送失败，请检查网络或 Token。\n")
        sys.exit(0)

    # 检查是否为批处理脚本手动单次触发模式 (--once)
    if "--once" in sys.argv:
        print("\n" + "=" * 80)
        print("⚡ [手动触发] 执行单次即时全市场数据同步...")
        print("=" * 80)
        run_radar_cycle(is_first_run=False, round_idx=1)
        print("\n[+] ✅ 手动单次同步完成，已更新 .radar_last_sync 锁文件，后台守护进程倒计时已自动同步重置！")
        sys.exit(0)

    # 检查是否为云端/守护进程长轮询极速模式 (--loop-daemon)
    if "--loop-daemon" in sys.argv:
        duration_min = 180
        interval_sec = 60
        for i, arg in enumerate(sys.argv):
            if arg == "--duration-minutes" and i + 1 < len(sys.argv):
                try:
                    duration_min = int(sys.argv[i + 1])
                except Exception:
                    pass
            elif arg == "--interval-seconds" and i + 1 < len(sys.argv):
                try:
                    interval_sec = max(30, int(sys.argv[i + 1]))
                except Exception:
                    pass
        if duration_min <= 0:
            print(f"\n🚀 启动全天候无上限极速长轮询守护引擎 (每 {interval_sec} 秒轮询一次 · 24/7 永久驻留)")
        else:
            print(f"\n🚀 启动云端 {interval_sec} 秒极速长轮询守护引擎 (计划持续运行 {duration_min} 分钟)")
        start_time = time.time()
        end_time = start_time + duration_min * 60
        round_idx = 1
        while (duration_min <= 0) or (time.time() < end_time):
            c_start = time.time()
            now_ts = now_et_str("%Y-%m-%d %H:%M:%S")

            # 🛡️ 云端 Actions 互斥检测：如果由 GITHUB_ACTIONS 运行，检查是否有本地开发者的最新提交 (<15分钟)
            if os.environ.get("GITHUB_ACTIONS") == "true":
                try:
                    subprocess.run(["git", "fetch", "origin", "main", "--quiet"], check=False, creationflags=WIN_NO_WINDOW)
                    res = subprocess.run(["git", "log", "-1", "--format=%ae|%ct", "origin/main"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, creationflags=WIN_NO_WINDOW)
                    if res.returncode == 0 and res.stdout.strip():
                        parts = res.stdout.strip().split("|", 1)
                        if len(parts) == 2:
                            author_email, c_time_str = parts
                            diff_sec = time.time() - float(c_time_str)
                            if author_email != "actions@github.com" and diff_sec < 900:
                                print(f"[!] 🛡️ 检测到本地客户端活跃提交 ({author_email}, {int(diff_sec)}s 前)，云端 Actions 优雅退出！")
                                sys.exit(0)
                except Exception:
                    pass

            print(f"\n" + "-" * 75)
            print(f"⏱️ [{now_ts}] [第 {round_idx} 轮] 执行全市场 {interval_sec} 秒极速同步与期权扫描...")
            print("-" * 75)
            try:
                if _SHARED_ALERTS:
                    if ANDROID_MODE:
                        shared_alert_state.pull_shared(SCRATCH_DIR)
                        if shared_alert_state.should_standby("ANDROID"):
                            print("[Standby] PC 主机在线值班中，本轮仅同步看板并待命")
                            round_idx += 1
                            time.sleep(15)
                            continue
                    shared_alert_state.heartbeat(_shared_device_tag())
                if _other_device_is_active():
                    print("[*] another device submitted live data within 50s; skip this round")
                    round_idx += 1
                    time.sleep(15)
                    continue
                maybe_refresh_daily_ml_panel()
                run_radar_cycle(is_first_run=False, round_idx=round_idx)
            except Exception as e:
                print(f"[!] ⚠️ 守护进程自动拦截异常并继续运行: {e}")
            # 周末休市：本地刷新照常，git commit/push 降频为每30分钟一次
            try:
                _wk_session, _ = get_us_market_session()
                _weekend_quiet = (_wk_session == "WEEKEND")
            except Exception:
                _weekend_quiet = False
            if _weekend_quiet:
                _last_push_ts = globals().get("_LAST_GIT_PUSH_TS", 0.0)
                if time.time() - _last_push_ts < 1800:
                    try:
                        import shutil as _wk_shutil
                        if os.path.exists("futu_pro_terminal.html"):
                            _wk_shutil.copyfile("futu_pro_terminal.html", "index.html")
                    except Exception:
                        pass
                    print("[*] 周末休市：本地看板已刷新，Git commit/push 按30分钟降频，跳过本轮推送")
                    round_idx += 1
                    time.sleep(15.0)
                    continue
            # 云端同步持久化至 Git 仓库，令手机端 GitHub Pages 秒级保持最新
            try:
                import shutil
                if os.path.exists("futu_pro_terminal.html"):
                    shutil.copyfile("futu_pro_terminal.html", "index.html")
                
                # 配置带 PAT 的远程地址，确保触发 GitHub Pages 自动部署，规避 GITHUB_TOKEN 的防递归拦截
                gh_token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
                if gh_token:
                    auth_url = f"https://x-access-token:{gh_token}@github.com/2827173579/us-market-radar.git"
                    subprocess.run(["git", "remote", "set-url", "origin", auth_url], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
                
                # 🛡️ 精准守门员机制：在准备执行 git add / commit / push 之前的那一刻，
                # 调用 GitHub API 检查是否还有 in_progress 或 queued 的 Pages workflow。
                # 若上一轮已 completed，则 0 秒延迟立即放行推送！
                # 若上一轮还差最后十几秒没跑完，则每 8 秒轮询等待其变为 completed (最多 75 秒超时兜底)
                try:
                    pat_match = gh_token
                    if not pat_match:
                        rem_out = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True, creationflags=WIN_NO_WINDOW)
                        if "ghp_" in rem_out:
                            pat_match = "ghp_" + rem_out.split("ghp_")[1].split("@")[0]
                    if pat_match:
                        check_url = "https://api.github.com/repos/2827173579/us-market-radar/actions/runs?per_page=1"
                        req_chk = urllib.request.Request(check_url, headers={"Authorization": f"Bearer {pat_match}", "User-Agent": "TradeRadar-Gatekeeper"})
                        wait_start = time.time()
                        waited_any = False
                        while time.time() - wait_start < 240:
                            with urllib.request.urlopen(req_chk, timeout=10) as c_resp:
                                c_data = json.loads(c_resp.read().decode("utf-8"))
                            runs = c_data.get("workflow_runs", [])
                            if runs and runs[0].get("status") in ["in_progress", "queued"]:
                                waited_any = True
                                print(f"[*] ⏳ 云端上一轮 Pages 正在部署中 (Run {runs[0].get('id')} / {runs[0].get('status')})，守门员等待其就绪以防踩踏 (已等 {int(time.time()-wait_start)}s)...")
                                time.sleep(8)
                            else:
                                if waited_any:
                                    print(f"[+] 🎯 云端上一轮 Pages 部署顺利就绪 (等待 {int(time.time()-wait_start)}s)，立即放行当前推送！")
                                else:
                                    print(f"[+] ⚡ 云端已处于空闲就绪状态 (0 秒等待)，直接放行推送！")
                                break
                except Exception as wait_err:
                    pass

                subprocess.run(
                    ["git", "add", "index.html", "futu_pro_terminal.html", "predict_radar_5y.html", "data_cache/latest_ai_predictions.json", "data_cache/live_prediction_scorecard.json", "data_cache/intraday_path_forecasts.json", "data_cache/latest_market_news.json", "data_cache/shared_alert_dedupe.json", ".terminal_notifications.json", ".options_cache.json", ".radar_alert_history.json", "macro_radar.html", "stocks_radar.html", ".gitignore"],
                    check=False, creationflags=WIN_NO_WINDOW)
                subprocess.run(
                    ["git", "commit", "-m", f"⚡ [{'小米平板实盘同步' if ANDROID_MODE else '实盘同步'}] 第 {round_idx} 轮极速刷新 ({now_ts})"],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW
                )
                _head_before = subprocess.run(["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=WIN_NO_WINDOW).stdout.strip()
                subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
                _head_after = subprocess.run(["git", "rev-parse", "HEAD"], check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=WIN_NO_WINDOW).stdout.strip()
                if _head_before and _head_after and _head_before != _head_after:
                    _changed = subprocess.run(["git", "diff", "--name-only", _head_before, _head_after], check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=WIN_NO_WINDOW).stdout
                    if any(_f.endswith(".py") for _f in _changed.splitlines()):
                        print("[*] 检测到 git pull 更新了 .py 源码，立即热重启守护进程以加载新代码与 .env ...", flush=True)
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                push_res = subprocess.run(["git", "push", "origin", "HEAD:main"], check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=WIN_NO_WINDOW)
                if push_res.returncode == 0:
                    print(f"[+] ✅ 成功推送第 {round_idx} 轮数据至 GitHub Pages (PAT 触发部署成功)！")
                    globals()["_LAST_GIT_PUSH_TS"] = time.time()
                else:
                    print(f"[*] Git 推送反馈: {push_res.stderr.strip()[:100]}")
            except Exception as e:
                print(f"[!] Git 同步异常: {e}")
            round_idx += 1
            cost = time.time() - c_start
            # 🚀 流水线并行 (Pipeline Parallelism)：
            # 严禁在 push 后长休眠！仅休眠 15 秒短呼吸，立即开始下一轮本地扫描 (耗时约 70s)。
            # 本地计算 (15s + 70s = 85s) 与云端部署 (75~90s) 完美重叠并行，
            # 结合推送前精准守门员机制，实现 90 秒一轮极限高频稳定更新！
            sleep_time = 15.0
            print(f"[*] 本轮扫描与同步耗时 {cost:.1f}s，流水线休眠 {sleep_time:.1f}s 立即启动下一轮计算...")
            time.sleep(sleep_time)
        print("\n✅ 云端守护计划周期已圆满完成。")
        sys.exit(0)

    total_etfs = len(MONITORED_TARGETS['ETFS'])
    total_stocks = len(MONITORED_TARGETS['STOCKS'])
    total_targets = total_etfs + total_stocks
    print(f"🚀 启动 Integrated Market Radar 智能轮询守护引擎 ({total_targets} 标的全量架构)")
    print(f"监控标的: {total_etfs} 只宏观/行业 ETF + {total_stocks} 只核心个股 (共 {total_targets} 只标的) | 自动轮询: 60 秒 (1分钟极速)")
    print(f"频控保护: workers=4, jitter=0.02~0.06s, 智能 429 降级保护")
    print(f"同步锁机制: 实时监听 .radar_last_sync，手动刷新批处理自动重置倒计时，彻底杜绝冲突")
    print("提示: 按 Ctrl+C 可随时安全退出。")
    print("=" * 80 + "\n")

    round_count = 1

    # 首次启动：先检查距离上一次刷新是否已满 60 秒，或直接执行第一轮
    _initial_standby = False
    if ANDROID_MODE and _SHARED_ALERTS:
        shared_alert_state.pull_shared(SCRATCH_DIR)
        _initial_standby = shared_alert_state.should_standby("ANDROID")
    last_sync = get_last_sync_time()
    elapsed = time.time() - last_sync
    if _initial_standby:
        print("[Standby] PC 主机在线，完整引擎首轮仅待命")
    elif elapsed >= SYNC_INTERVAL or last_sync == 0.0:
        start_ts = now_et_str("%Y-%m-%d %H:%M:%S")
        print(f"\n" + "-" * 75)
        print(f"⏱️ [{start_ts}] 正在执行第 {round_count} 轮全市场数据同步与指标扫描...")
        print("-" * 75)
        run_radar_cycle(is_first_run=True, round_idx=round_count)
        round_count += 1
    else:
        remaining = int(SYNC_INTERVAL - elapsed)
        print(f"[*] 检测到最近一次同步发生在 {int(elapsed)} 秒前，守护进程将在 {remaining} 秒后执行下一轮同步。")

    try:
        while True:
            # 每秒轻量探测一次本地同步锁
            time.sleep(1)
            last_sync = get_last_sync_time()
            elapsed = time.time() - last_sync

            if elapsed >= SYNC_INTERVAL:
                start_ts = now_et_str("%Y-%m-%d %H:%M:%S")
                print(f"\n" + "-" * 75)
                print(f"⏱️ [{start_ts}] 达到 180 秒调度周期，正在执行第 {round_count} 轮数据同步...")
                print("-" * 75)

                run_radar_cycle(is_first_run=False, round_idx=round_count)

                end_ts = now_et_str("%Y-%m-%d %H:%M:%S")
                print(f"\n[{end_ts}] ✅ 第 {round_count} 轮数据更新完成！")
                print(f"⏳ 守护引擎进入 180 秒 (3分钟) 休眠倒计时...")

                round_count += 1

    except KeyboardInterrupt:
        print("\n\n" + "=" * 80)
        print("[!] 收到中断信号 (Ctrl+C)，Integrated Market Radar 轮询守护引擎已安全退出。")
        print("=" * 80 + "\n")
        sys.exit(0)

if __name__ == "__main__":
    main()
