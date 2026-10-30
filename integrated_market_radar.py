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
import subprocess
import os
import time
import random
from datetime import datetime
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import webbrowser
import numpy as np
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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
        "QQQM": {"name": "纳指100微型", "sub": "科技大盘Beta", "cat": "大盘基准"},
        "SPY":  {"name": "标普500", "sub": "美股全市场基石", "cat": "大盘基准"},
        "DIA":  {"name": "道琼斯蓝筹", "sub": "美国30大工业蓝筹基石", "cat": "大盘基准"},
        "IWM":  {"name": "罗素2000", "sub": "小盘流动性温度计", "cat": "小盘弹性"},
        "SMH":  {"name": "半导体ETF", "sub": "硬科技与周期先导", "cat": "半导体"},
        "SOXX": {"name": "费城半导体ETF", "sub": "iShares核心半导体指数基准", "cat": "半导体"},
        "SOXL": {"name": "3倍做多半导体", "sub": "费半三倍正向杠杆·实盘底仓跟踪", "cat": "正向杠杆", "cost": 108.2109, "shares": 12.22594, "trim1": 94.50, "trim1_shares": 4.22594, "trim2": 102.50, "base_val": 82.09, "target": 108.21},
        "KORU": {"name": "3倍做多韩国", "sub": "韩国股市3倍正向杠杆·三星海力士先导", "cat": "正向杠杆"},
        "IGV":  {"name": "软件科技ETF", "sub": "SaaS与AI企业级应用", "cat": "云与软件"},
        "TLT":  {"name": "20年+美债", "sub": "长端利率与避险情绪", "cat": "利率债券"},
        "GLD":  {"name": "黄金现货ETF", "sub": "抗通胀与终极地缘避险", "cat": "大宗商品"},
        "QQQI": {"name": "纳指高收益增强", "sub": "备兑看涨防守金流", "cat": "防守现金流"},
        "VIXY": {"name": "短期恐慌指数", "sub": "尾部黑天鹅对冲工具", "cat": "黑天鹅对冲"},
        "SQQQ": {"name": "3倍做空纳指", "sub": "纳指100三倍反向对冲", "cat": "反向对冲"},
        "SOXS": {"name": "3倍做空半导体", "sub": "费半半导体三倍反向对冲", "cat": "反向对冲"},
        "IBIT": {"name": "比特币现货ETF", "sub": "贝莱德现货ETF·数字黄金与机构杠杆", "cat": "数字资产"}
    },
    "STOCKS": {
        # 1. 底层算力与半导体设备
        "NVDA":  {"name": "NVIDIA", "sub": "全球算力总龙头", "cat": "芯片算力", "cost": 0.0, "shares": 0.0, "base_val": 130.0},
        "TSM":   {"name": "台积电", "sub": "全球先进制程代工绝对垄断", "cat": "晶圆代工", "cost": 0.0, "shares": 0.0, "base_val": 185.0},
        "AMD":   {"name": "AMD", "sub": "算力双雄与x86挑战者", "cat": "芯片算力", "cost": 0.0, "shares": 0.0, "base_val": 155.0},
        "AVGO":  {"name": "博通", "sub": "网络交换与ASIC定制芯片之王", "cat": "通信芯片", "cost": 0.0, "shares": 0.0, "base_val": 170.0},
        "ASML":  {"name": "阿斯麦", "sub": "光刻机绝对霸主", "cat": "制造设备", "cost": 0.0, "shares": 0.0, "base_val": 820.0},
        "AMAT":  {"name": "应用材料", "sub": "半导体薄膜沉积与设备龙头", "cat": "制造设备", "cost": 0.0, "shares": 0.0, "base_val": 210.0},
        
        # 2. 商业化巨头与企业级AI
        "MSFT":  {"name": "微软", "sub": "企业软件与Copilot商业化", "cat": "云与软件", "cost": 0.0, "shares": 0.0, "base_val": 420.0},
        "GOOGL": {"name": "Alphabet", "sub": "全栈AI生态与搜索基本盘", "cat": "生态平台", "cost": 0.0, "shares": 0.0, "base_val": 170.0},
        "META":  {"name": "Meta", "sub": "开源Llama与最强广告变现", "cat": "社交AI", "cost": 0.0, "shares": 0.0, "base_val": 560.0},
        "AAPL":  {"name": "苹果", "sub": "端侧AI设备与消费电子闭环", "cat": "终端硬件", "cost": 0.0, "shares": 0.0, "base_val": 225.0},
        "AMZN":  {"name": "亚马逊", "sub": "AWS算力底座与电商飞轮", "cat": "云与电商", "cost": 0.0, "shares": 0.0, "base_val": 185.0},
        "PLTR":  {"name": "Palantir", "sub": "AIP企业级与军工落地标杆", "cat": "企业AI", "cost": 0.0, "shares": 0.0, "base_val": 42.0},
        
        # 3. 新算力租赁 (Neo-Cloud)
        "CRWV":  {"name": "CoreWeave", "sub": "纯血GPU算力租赁高弹性领头羊", "cat": "算力租赁", "cost": 0.0, "shares": 0.0, "base_val": 95.0},
        "NBIS":  {"name": "Nebius", "sub": "欧洲与全球新兴AI云服务商", "cat": "算力租赁", "cost": 0.0, "shares": 0.0, "base_val": 28.0},
        
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
        
        # 6. 太空商业与低轨卫星
        "SPCX":  {"name": "SpaceX", "sub": "星链与可重复火箭先驱", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 147.0},
        "ASTS":  {"name": "AST SpaceMobile", "sub": "全球手机直连卫星宽带网络", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 26.0},
        "RKLB":  {"name": "Rocket Lab", "sub": "中小型火箭发射与卫星制造", "cat": "太空商业", "cost": 0.0, "shares": 0.0, "base_val": 19.5},
        
        # 7. 价值蓝筹与核心风控仓位
        "KO":    {"name": "可口可乐", "sub": "全球消费防御与抗通胀现金奶牛", "cat": "价值蓝筹", "cost": 0.0, "shares": 0.0, "base_val": 68.0},
        "AXP":   {"name": "美国运通", "sub": "高端消费信贷与抗衰退金融底座", "cat": "金融蓝筹", "cost": 0.0, "shares": 0.0, "base_val": 270.0},
        "INTC":  {"name": "Intel", "sub": "IDM2.0转型·实盘底仓跟踪", "cat": "芯片算力", "cost": 95.53429, "shares": 7.0, "trim1": 91.80, "trim1_shares": 5.0, "base_val": 88.0, "target": 95.53},

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

        # 13. 企业级算力云与资本载体
        "ORCL":  {"name": "甲骨文", "sub": "OCI云基础设施与企业大模型底座", "cat": "云与软件", "cost": 0.0, "shares": 0.0, "base_val": 110.0},
        "CCXI":  {"name": "Churchill XI", "sub": "科技并购与成长资本载体", "cat": "资本运作", "cost": 0.0, "shares": 0.0, "base_val": 10.0},

        # 14. 医药健康、消费龙头与全球支付网络
        "LLY":   {"name": "礼来", "sub": "全球GLP-1减肥药与制药万亿新贵", "cat": "生物医药", "cost": 0.0, "shares": 0.0, "base_val": 780.0},
        "MRNA":  {"name": "Moderna", "sub": "mRNA疫苗与肿瘤前沿疗法先锋", "cat": "生物医药", "cost": 0.0, "shares": 0.0, "base_val": 45.0},
        "MCD":   {"name": "麦当劳", "sub": "全球快餐连锁与抗周期现金流巨无霸", "cat": "价值蓝筹", "cost": 0.0, "shares": 0.0, "base_val": 280.0},
        "NKE":   {"name": "耐克", "sub": "全球运动鞋服总龙头·去库存与品牌复苏", "cat": "可选消费", "cost": 0.0, "shares": 0.0, "base_val": 70.0},
        "V":     {"name": "Visa", "sub": "全球卡组织支付网络垄断寡头", "cat": "金融科技", "cost": 0.0, "shares": 0.0, "base_val": 290.0},
        "PEP":   {"name": "百事可乐", "sub": "食品饮料防守抗通胀消费龙头", "cat": "价值蓝筹", "cost": 0.0, "shares": 0.0, "base_val": 160.0}
    }
}

# ==============================================================================
# 二、 Finnhub 频控与并发抓取引擎 (Jitter + 429 智能降级)
# ==============================================================================
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY") or "d9be4q1r01qmk4glemdgd9be4q1r01qmk4gleme0"

# PushPlus 微信推送配置与去重缓存
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN") or "eca6f1f8c3494843aa7f1170e34fd2b6"
PUSHPLUS_CHANNEL = os.environ.get("PUSHPLUS_CHANNEL") or "clawbot"

# ------------------------------------------------------------------------------
# 🎯 账户资金与风控配置 (V3.1 旗舰纯比例制 · 单股上限 25% · 1.5%风险平价一次性建仓)
# ------------------------------------------------------------------------------
TOTAL_ACCOUNT_EQUITY = 10000.0      # 参考基准账户总资产 ($10,000)
MAX_POSITION_PCT = 0.25            # 单股最大配置上限 25% (V3.1 旗舰标准)
MAX_ALLOC_PER_STOCK = round(TOTAL_ACCOUNT_EQUITY * MAX_POSITION_PCT, 2)  # $2,500.00
BATCH1_PCT = 1.00                  # V3.1 1.5% 账户风险平价一次性建仓到位 (100%到位，不再 3-3-4 碎步)
SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_HTML_FILE = os.path.join(SCRATCH_DIR, "macro_radar.html")
STOCKS_HTML_FILE = os.path.join(SCRATCH_DIR, "stocks_radar.html")
FUTU_HTML_FILE = os.path.join(SCRATCH_DIR, "futu_pro_terminal.html")
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
    """读取用户自定义独立实盘持仓配置 (user_portfolio.json)"""
    try:
        if os.path.exists(USER_PORTFOLIO_FILE):
            with open(USER_PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        print(f"[!] 读取用户持仓文件异常: {e}")
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
            # 1. 移动端/局域网 Web 静态服务 (主页与 PWA Manifest)
            if parsed.path in ["/", "/index.html", "/futu_pro_terminal.html"]:
                html_path = os.path.join(SCRATCH_DIR, "futu_pro_terminal.html")
                if os.path.exists(html_path):
                    with open(html_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "*")
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

def fetch_single_finnhub_quote(symbol, api_key):
    """单标的通过 Finnhub REST API 获取秒级报价 (加 0.30~0.40s 控速与 429 自动避让保护)"""
    time.sleep(random.uniform(0.30, 0.40))
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={api_key}"
    for attempt in range(2):
        try:
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
                    time.sleep(2.0)
                    continue
                print(f"[!] [Finnhub 频控预警] {symbol} 触发 429 限流保护，该标的将自动使用 yfinance 底模数据。")
            else:
                pass
        except Exception:
            pass
    return symbol, None

def fetch_finnhub_quotes_batch(tickers, api_key):
    """限额并发抓取 (max_workers=2)，严控每分钟调用在 60 次安全红线内"""
    if not api_key:
        return {}
    results = {}
    print(f"[*] 🚀 启动 Finnhub 稳态频控引擎 (workers=2, pace=0.35s)，平稳抓取 {len(tickers)} 只标的实时报价...")
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

def get_us_market_session():
    """判断当前美东时间交易时段"""
    try:
        import zoneinfo
        ny_tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        from datetime import timezone, timedelta
        ny_tz = timezone(timedelta(hours=-4))
    now_ny = datetime.now(ny_tz)
    weekday = now_ny.weekday()
    hour = now_ny.hour + now_ny.minute / 60.0

    if weekday >= 5:
        return "WEEKEND", "☕ 周末休市"
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
            from datetime import timezone, timedelta
            ny_tz = timezone(timedelta(hours=-4))

        df = yf.download(tickers, period="2d", interval="5m", prepost=True, progress=False)
        if df is None or df.empty or "Close" not in df:
            return results

        for t in tickers:
            try:
                if t not in df["Close"]:
                    continue
                c = df["Close"][t].dropna()
                if len(c) < 5:
                    continue
                o = df["Open"][t].reindex(c.index).fillna(c)
                h = df["High"][t].reindex(c.index).fillna(c)
                l = df["Low"][t].reindex(c.index).fillna(c)
                v = df["Volume"][t].reindex(c.index).fillna(0)

                ext_price = float(c.iloc[-1])
                ny_idx = c.index.tz_convert(ny_tz)
                ny_hr = ny_idx.hour + ny_idx.minute / 60.0
                last_date = ny_idx[-1].date()

                # 过滤出最新交易日正股常规时段 (09:30 ~ 16:00 EDT) 用于纯净 VWAP 与日高低点计算
                rth_mask = (ny_idx.date == last_date) & (ny_hr >= 9.5) & (ny_hr < 16.0)
                if not rth_mask.any():
                    # 若当前处于盘前 (当日尚未开盘)，则回溯前一个正股交易日计算基准 VWAP
                    unique_dates = sorted(list(set(ny_idx.date)))
                    prev_date = unique_dates[-2] if len(unique_dates) >= 2 else last_date
                    rth_mask = (ny_idx.date == prev_date) & (ny_hr >= 9.5) & (ny_hr < 16.0)

                if rth_mask.any():
                    tp_rth = (h[rth_mask] + l[rth_mask] + c[rth_mask]) / 3.0
                    vol_rth = v[rth_mask]
                    vwap = float((tp_rth * vol_rth).sum() / vol_rth.sum()) if vol_rth.sum() > 0 else float(c[rth_mask].mean())
                    rth_low = float(l[rth_mask].min())
                    rth_high = float(h[rth_mask].max())
                    rth_close = float(c[rth_mask].iloc[-1])
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
                    rth_close = float(c.iloc[-1])
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

                results[t] = {
                    "ext_price": ext_price,
                    "vwap": round(vwap, 2),
                    "rsi_5m": round(rsi_5m, 1),
                    "prev_rsi_5m": round(prev_rsi_5m, 1),
                    "min_rsi_5m_30m": round(min_rsi_5m_30m, 1),
                    "rvol_5m": round(rvol_5m, 2),
                    "is_5m_green": is_5m_green,
                    "is_5m_higher_low": is_5m_higher_low,
                    "rth_low": round(rth_low, 2),
                    "rth_high": round(rth_high, 2),
                    "rth_close": round(rth_close, 4)
                }
            except Exception:
                continue
    except Exception as e:
        print(f"[!] 5分钟日内与盘前盘后数据计算回退: {e}")
    return results

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
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        cal = json.loads(resp.read().decode()).get("earningsCalendar", [])
                        return [(x.get("symbol"), x.get("date"), x.get("hour", "")) for x in cal if x.get("symbol") in ticker_set and x.get("date")]
                except Exception:
                    return []

            with ThreadPoolExecutor(max_workers=3) as ex:
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
                if c_data.get("schema_version") == 3 and (now_ts - c_data.get("updated_at", 0) < 1800):
                    cached_map = c_data.get("data", {})
    except Exception:
        pass

    needed = [t for t in focus_tickers if t not in cached_map]
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
                if spot > 0:
                    dir_calls = calls[(calls["strike"] >= spot * 0.92) & (calls["strike"] <= spot * 1.40)] if not calls.empty else calls
                    dir_puts = puts[(puts["strike"] >= spot * 0.65) & (puts["strike"] <= spot * 1.08)] if not puts.empty else puts
                    c_dollar = float(dir_calls["dollar_val"].sum()) if not dir_calls.empty else float(calls["dollar_val"].sum())
                    p_dollar = float(dir_puts["dollar_val"].sum()) if not dir_puts.empty else float(puts["dollar_val"].sum())
                else:
                    c_dollar = float(calls["dollar_val"].sum()) if not calls.empty else 0.0
                    p_dollar = float(puts["dollar_val"].sum()) if not puts.empty else 0.0

                if c_vol <= 0 and p_vol <= 0:
                    return t, {"has_options": False}

                pcr = round(p_vol / c_vol, 2) if c_vol > 0 else 1.0
                dollar_pcr = round(p_dollar / c_dollar, 2) if c_dollar > 0 else pcr
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
                    if eff_pcr <= 0.50:
                        pcr_sentiment = "🔥 机构极度看涨"
                        pcr_color = "#00e676"
                    elif eff_pcr <= 0.80:
                        pcr_sentiment = "🟢 资金偏多看涨"
                        pcr_color = "#4ade80"
                    elif eff_pcr >= 1.30:
                        pcr_sentiment = "🔴 资金偏空对冲"
                        pcr_color = "#ff5252"
                    elif eff_pcr >= 1.05:
                        pcr_sentiment = "🟠 机构谨慎防跌"
                        pcr_color = "#ffa726"
                    else:
                        pcr_sentiment = "🟡 多空博弈均衡"
                        pcr_color = "#ffd700"

                # 2. 按真实方向性权利金成交额 (Directional Dollar Premium) 筛选主力大单：
                #    - 过滤 <$0.05 末日彩票废约
                #    - 过滤深度实值 (Deep ITM) 机构换仓合成头寸，Call 聚焦平值与上方阻力墙 (>=0.96*spot)，Put 聚焦平值与下方支撑墙 (<=1.04*spot)
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
                        if opt_type == "Call":
                            otm_band = sub[(sub["strike"] >= spot * 0.96) & (sub["strike"] <= spot * 1.38)]
                        else:
                            otm_band = sub[(sub["strike"] >= spot * 0.68) & (sub["strike"] <= spot * 1.04)]
                        if not otm_band.empty:
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
                json.dump({"schema_version": 3, "updated_at": now_ts, "data": cached_map}, f, ensure_ascii=False, indent=2)
            valid_opt_cnt = sum(1 for v in cached_map.values() if v.get("has_options"))
            print(f"[+] ⚡ 机构期权异动同步完成: 成功解析并缓存 {valid_opt_cnt}/{len(cached_map)} 只标的期权主力资金流。")
        except Exception as e:
            print(f"[!] 期权缓存写入异常: {e}")

    return cached_map

def evaluate_intraday_entry(active_price, fib_low, fib_high, ma50, is_pit, is_stop, dist_to_pit, intra_info, earnings_info=None, rs_20d=0.0, atr=0.0, daily_rsi=50.0, options_info=None, pct_change=0.0, ext_pct=None):
    """
    真正的 5 分钟级日内量化建仓评估器：
    1. 科学测算建议挂单价 optimal_limit 与防守止损线 entry_stop_loss (严禁止损线高于买入价倒挂)
    2. 结合日线黄金坑位置 + 5m RSI 超卖底背离 + 日内 VWAP 均价线 + 5m 相对量比 RVOL + 财报日历避雷，判定是否触发日内买点
    3. 加装【极端跳空/暴跌防手痒熔断闸】：单日急跌或盘后跳空破位时，强制冻结挂单与底仓推荐
    """
    vwap = intra_info.get("vwap", 0.0) or active_price
    rsi_5m = intra_info.get("rsi_5m", 50.0)
    prev_rsi_5m = intra_info.get("prev_rsi_5m", 50.0)
    min_rsi_30m = intra_info.get("min_rsi_5m_30m", 50.0)
    rvol_5m = intra_info.get("rvol_5m", 1.0)
    is_5m_green = intra_info.get("is_5m_green", True)
    is_higher_low = intra_info.get("is_5m_higher_low", True)
    rth_low = intra_info.get("rth_low", active_price)
    e_info = earnings_info or {}
    days_to_e = e_info.get("days_to_earnings")
    is_e_risk = e_info.get("is_earnings_risk", False)
    e_tag = e_info.get("earnings_tag", "")

    # 0. 极端跳空/暴跌防手痒熔断闸判定 (严格限定为当日/盘后急杀，不误伤已筑底股票)
    safe_ext = ext_pct if ext_pct is not None else 0.0
    is_crash_halt = (
        (pct_change <= -5.0) or
        (ext_pct is not None and ext_pct <= -4.5) or
        ((pct_change + safe_ext) <= -5.5) or
        (fib_low > 0 and active_price < fib_low * 0.92 and (pct_change <= -2.5 or (ext_pct is not None and ext_pct <= -2.5)))
    )

    if is_crash_halt:
        return {
            "optimal_limit": 0.0,
            "limit_note": "单日/盘后剧烈跳空破位，严禁首日左侧挂单接飞刀",
            "entry_stop_loss": 0.0,
            "stop_loss_pct": 0.0,
            "intraday_entry_ready": False,
            "intraday_status_text": "⛔ 暂停挂单 (暴跌宣泄期)",
            "intraday_reason": f"标的触发暴跌熔断(日盘 {pct_change:+.2f}%, 盘后 {safe_ext:+.2f}%)，单日/盘后剧烈跳空破位，严禁首日左侧挂单接飞刀(需等待3日宣泄企稳)",
            "is_crash_halt": True,
            "vwap": round(vwap, 2),
            "rsi_5m": round(rsi_5m, 1),
            "rvol_5m": round(rvol_5m, 2),
            "rs_20d": round(rs_20d, 2),
            **e_info
        }

    # 1. 科学挂单买入价测算 (V3.1 T+1 开盘买入标准：当 is_pit 确认右侧买点时，使用开盘限价上限，杜绝踏空大牛股)
    rth_close = intra_info.get("rth_close", active_price) or active_price
    if is_pit:
        # V3.1 标准开盘保护限价上限：开盘前取昨收 +2.0% 防追高，盘中则取现价 +0.5%
        session_is_rth = False
        optimal_limit = round(rth_close * 1.020, 2)
        limit_note = "V3.1右侧确认·9:30开盘限价上限(昨收+2.0%防追高,低于昨收*0.985作废)"
    else:
        pit_mid_low = round(fib_low + 0.382 * (fib_high - fib_low), 2) if fib_high > fib_low else active_price
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

    intraday_entry_ready = False
    if is_stop:
        intraday_status_text = "🔴 破位风控·严禁建仓"
        intraday_reason = f"股价已放量击穿黄金坑底防线(${fib_low:.2f})，空头宣泄期严禁左侧接刀"
    elif dist_to_pit < -2.0 and not is_pit:
        if cond_deep_sync:
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
        # V3.1 右侧买点优先处理：只要未触发暴跌熔断且盘前涨跌幅在正常执行区间内，直接标记就绪
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
        if cond_oversold_turn:
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

    if is_e_risk and days_to_e is not None:
        intraday_reason += f" ｜ {e_tag}，谨防财报跳空盲盒(建议减半仓位或等财报落地)"

    o_info = options_info or {}
    if o_info.get("has_options"):
        o_sent = o_info.get("pcr_sentiment", "")
        o_call = o_info.get("top_call_info", "")
        o_put = o_info.get("top_put_info", "")
        if intraday_entry_ready:
            if o_info.get("pcr", 1.0) <= 0.80 or o_info.get("dollar_pcr", 1.0) <= 0.80:
                intraday_reason += f" ｜ ⚡期权多头共振({o_sent}, 主力Call {o_call})"
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
        "vwap": round(vwap, 2),
        "rsi_5m": round(rsi_5m, 1),
        "rvol_5m": round(rvol_5m, 2),
        "rs_20d": round(rs_20d, 2),
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

def send_pushplus_wechat_alert(title, markdown_content, channel=None):
    """通过 PushPlus API 向微信推送 Markdown 卡片消息 (专线直达 ClawBot)"""
    if not PUSHPLUS_TOKEN or PUSHPLUS_TOKEN == "YOUR_TOKEN":
        return False
    
    # 采用毫秒级极速 HTTPS 接口 (0.2s 极速响应，避免 http 超时)
    url = "https://www.pushplus.plus/send"
    target_channel = channel or globals().get("PUSHPLUS_CHANNEL", "clawbot")
    payload = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": markdown_content,
        "template": "markdown"
    }
    if target_channel:
        payload["channel"] = target_channel

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if data.get("code") == 200:
                print(f"  [+] 📱 微信[{target_channel}]卡片推送成功: {title}")
                return True
            else:
                print(f"  [!] 📱 微信推送返回错误: {data.get('msg')} (数据: {data.get('data')})")
                # 若专向 channel 异常，自动回退到默认账户通道二次重试
                if target_channel:
                    del payload["channel"]
                    req_fb = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req_fb, timeout=10) as resp_fb:
                        data_fb = json.loads(resp_fb.read().decode("utf-8", errors="replace"))
                        if data_fb.get("code") == 200:
                            print(f"  [+] 📱 微信[默认回退通道]推送成功: {title}")
                            return True
    except Exception as e:
        print(f"  [!] 📱 微信推送网络异常: {e}")
    return False

def dispatch_wechat_radar_alerts(macro_data, stocks_data, weather_info, now_str):
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

    # 1. 扫描个股及已加入自选盯盘或实盘持仓的 ETF (如 SOXX / SOXL / QQQM) 重大异动
    starred_etfs = [{"ticker": t, **m} for t, m in macro_data.items() if t in target_watchlist or m.get("shares", 0) > 0]
    for s in list(stocks_data) + starred_etfs:
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
        rsi_5m = s.get("rsi_5m", 50.0)
        rvol_5m = s.get("rvol_5m", 1.0)
        rs_20d = s.get("rs_20d", 0.0)
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
        is_pit = s.get("is_pit", False)
        is_stop = s.get("is_stop", False)
        is_etf = ticker in macro_data

        # 当前核心技术状态 (自选盯盘+5m买点共振、黄金坑、技术止损、冲顶止盈；持仓股一视同仁，绝不被清零覆盖)
        current_state = "NORMAL"
        if is_stop and not is_etf:
            current_state = "STOP_LOSS"
        elif is_watched and intraday_ready and not is_stop:
            current_state = "WATCHLIST_ENTRY_READY"
        elif is_pit and not is_etf:
            current_state = "GOLDEN_PIT"
        elif s.get("is_take_profit") and not is_etf:
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

        # 更新技术历史状态
        if current_state != "NORMAL":
            targets_history[ticker] = {
                "state": current_state,
                "last_notified_at": current_time if should_alert else last_notified_at
            }
        else:
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
            elif current_state == "GOLDEN_PIT":
                pit_alerts.append({
                    "ticker": ticker, "name": name, "cat": cat, "last": last,
                    "bias": bias, "rsi": rsi, "fib_low": fib_low, "fib_high": fib_high,
                    "fractional": fractional, "pit_status": pit_status,
                    "stop_loss": stop_loss, "rth_low": s.get("rth_low", last * 0.985),
                    "optimal_limit": s.get("optimal_limit", round(last * 1.020, 2))
                })
            elif current_state == "STOP_LOSS":
                if bias < -15.0 and s.get("vol_ratio", 1.0) >= 1.5:
                    stop_alerts.append({
                        "ticker": ticker, "name": name, "cat": cat, "last": last,
                        "bias": bias, "rsi": rsi, "fib_low": fib_low, "pit_status": pit_status
                    })
            elif current_state == "TAKE_PROFIT":
                tp_alerts.append({
                    "ticker": ticker, "name": name, "cat": cat, "last": last,
                    "bias": bias, "rsi": rsi, "reason": s.get("tp_reason", "")
                })

        # 2. 实盘持仓专属风控底线监测 (硬止损、动态保本、水下强阻与对冲收割)
        if s.get("shares", 0) > 0:
            cost = s.get("cost", 95.53)
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
                targets_history[p_key] = {
                    "state": p_state,
                    "last_notified_at": current_time if p_alert else p_last_notified
                }
                if p_alert:
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
                targets_history[p_key] = {
                    "state": "PORT_NORMAL",
                    "last_notified_at": 0.0
                }

    # 2. 宏观气象突变检查
    current_weather = weather_info.get("weather", "晴天做多")
    prev_weather = history.get("weather", "")
    weather_changed = (prev_weather and prev_weather != current_weather)
    history["weather"] = current_weather

    # 保存最新状态
    save_alert_history(history)

    # 3. 组织并发送微信通知 (若无新增异动则静默)
    total_alerts = len(watchlist_entry_alerts) + len(pit_alerts) + len(stop_alerts) + len(tp_alerts) + len(intc_alerts)
    if total_alerts == 0 and not weather_changed:
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
    if intc_alerts:
        p_tickers = "/".join([a["ticker"] for a in intc_alerts])
        title_parts.append(f"💼持仓风控:{p_tickers}")
    if weather_changed:
        title_parts.append(f"⛅气象变动:{current_weather}")

    msg_title = f"【美股量化雷达】{' · '.join(title_parts)}"

    md = [
        f"### 🔔 美股量化雷达 ｜ {current_weather}",
        f"> ⏱️ **时间**：`{now_str[5:16]}` ｜ **大盘**：{weather_info.get('desc', '')[:28]}",
        ""
    ]

    if watchlist_entry_alerts:
        md.append("### 🎯 ⭐自选盯盘 · 5m买点触发")
        for a in watchlist_entry_alerts:
            ext_str = f" 🌙{a['session_tag']}:`${a['ext_price']:.2f}`" if a.get("session_tag") and a.get("ext_price") else ""
            e_str = f" ｜ 📅{a['earnings_tag']}" if a.get("earnings_tag") else ""
            opt_tag = f" ｜ ⚡**{a.get('pcr_sentiment','期权均衡')}** (资金PCR `{a.get('dollar_pcr',0):.2f}`)" if a.get("has_options") else ""

            md.append(f"> ### 🚀 **{a['ticker']}** {a['name']} ({a['cat']}){e_str}")
            md.append(f"> **现价**：`${a['last']:.2f}` (`{a['pct_change']:+.2f}%`){ext_str}{opt_tag}")
            md.append(f"> ")
            md.append(f"> 📋 **V3.1 操盘指令 (单股上限 25% · 1.5%风险平价)**：")
            md.append(f"> • **建议挂单**：**`${a['optimal_limit']:.2f}`** ({a['limit_note']})")
            md.append(f"> • **建议仓位**：**`{a.get('fractional_shares', a['batch1_shares']):.2f} 股`** (约 `${(a.get('fractional_shares', a['batch1_shares']) * a['last']):.0f}` · 1.5%风险平价一次性建仓)")
            md.append(f"> • **防守止损**：**`${a['stop_loss']:.2f}`** (结构止损单)")
            md.append(f"> ")
            
            clean_reason = a['intraday_reason'].split(" ｜ ⚡")[0]
            call_put_brief = f"主力Call `{a.get('top_call_info','--')}`" if a.get("has_options") and a.get("top_call_info") != "--" else ""
            logic_line = clean_reason
            if call_put_brief:
                logic_line += f" ｜ {call_put_brief}"
            md.append(f"> 💡 **触发逻辑**：{logic_line}")
            md.append("---")
        md.append("")

    if intc_alerts:
        md.append("### 💼 实盘持仓止盈与风控跟踪")
        for a in intc_alerts:
            t_sym = a["ticker"]
            cost = a.get("cost", 95.53)
            shares = a.get("shares", 7.0)
            trim1 = a.get("trim1", 91.80)
            trim1_s = a.get("trim1_shares", 5.0)
            stop_val = a.get("stop_val", 88.00)
            target = a.get("target", cost)
            st = a["state"]
            last_p = a['last']
            pnl_pct = ((last_p - cost) / cost) * 100 if cost > 0 else 0.0

            if st.endswith("_CRITICAL_STOP"):
                md.append(f"> 🚨 **{t_sym} 跌破铁血硬止损 ${stop_val:.2f}！**")
                md.append(f"> 现价 **`${last_p:.2f}`** (`{pnl_pct:+.2f}%`) ｜ 持仓 `{shares:g} 股` (成本 `${cost:.2f}`)")
                md.append(f"> ⚠️ **纪律指令**：触发保命止损，请立即执行减仓避险！")
            elif st.endswith("_BREAKEVEN_STOP"):
                peak_p = a.get("peak_price", last_p)
                max_gain = a.get("max_pnl_pct", 0.0)
                md.append(f"> 🚨 **{t_sym} 触及动态保本止损线 (${stop_val:.2f})！**")
                md.append(f"> 现价 **`${last_p:.2f}`** 回落跌破保本底线，历史最高曾冲至 `${peak_p:.2f}` (最高浮盈 `+{max_gain:.1f}%`)！")
                md.append(f"> 🛡️ **纪律指令**：立即执行平仓保本，绝不让盈利单变亏损！")
            elif st.endswith("_TIERED_LOCK_STOP"):
                peak_p = a.get("peak_price", last_p)
                max_gain = a.get("max_pnl_pct", 0.0)
                be_stop = a.get("be_stop", last_p)
                lock_pct = "+6.0%" if max_gain >= 14.0 else "+2.5%"
                md.append(f"> 🛡️ **{t_sym} 触及阶梯锁利保护线 (${be_stop:.2f})！**")
                md.append(f"> 现价 **`${last_p:.2f}`** 回踩触发阶梯保护，最高曾冲至 `${peak_p:.2f}` (最高浮盈 `+{max_gain:.1f}%`)！")
                md.append(f"> 💰 **锁利纪律**：当前锁定收益至少 `{lock_pct}`，建议落袋为安，保护胜利果实！")
            elif st.endswith("_CLIMAX_EXIT"):
                peak_p = a.get("peak_price", last_p)
                md.append(f"> 🎉 **{t_sym} 触发极度冲顶清仓止盈！**")
                md.append(f"> 现价 **`${last_p:.2f}`** (浮盈 `{pnl_pct:+.1f}%`)，技术面进入极端超买赶顶高潮，主力加速拉升！")
                md.append(f"> 🚀 **止盈纪律**：按 V3.1 规则执行全额止盈清仓，锁定超级大顶利润！")
            elif st.endswith("_EMA10_CRAZY_EXIT"):
                peak_p = a.get("peak_price", last_p)
                md.append(f"> 🟣 **{t_sym} 跌破疯牛生命线 EMA10 移动止盈！**")
                md.append(f"> 现价 **`${last_p:.2f}`** (浮盈 `{pnl_pct:+.1f}%`) 跌破 10日线，最高曾达 `${peak_p:.2f}`！")
                md.append(f"> 🛡️ **止盈纪律**：疯牛推进动能减速，建议全额或减大半仓落袋，锁定大波段利润！")
            elif st.endswith("_EMA20_TREND_EXIT"):
                peak_p = a.get("peak_price", last_p)
                md.append(f"> 🟣 **{t_sym} 跌破趋势生命线 EMA20 移动止盈！**")
                md.append(f"> 现价 **`${last_p:.2f}`** (浮盈 `{pnl_pct:+.1f}%`) 跌破 20日线，最高曾达 `${peak_p:.2f}`！")
                md.append(f"> 🛡️ **止盈纪律**：主升浪斜率趋缓跌破趋势线，按规则执行波段移动止盈！")
            elif st.endswith("_NEAR_STOP"):
                buffer = last_p - stop_val
                buffer_pct = buffer / stop_val * 100
                md.append(f"> ⚠️ **{t_sym} 逼近防守线预警！**")
                md.append(f"> 现价 **`${last_p:.2f}`** ｜ 防守线 **`${stop_val:.2f}`** (仅剩缓冲 **`${buffer:.2f}`** / `{buffer_pct:.1f}%`)")
                md.append(f"> 🛡️ **防御纪律**：准备防守，若跌破 `${stop_val:.2f}` 坚决减仓！")
            elif st.endswith("_TRIM1_RESISTANCE"):
                md.append(f"> 🟣 **{t_sym} 触及水下强阻压力位！**")
                md.append(f"> 现价 **`${last_p:.2f}`** (`{pnl_pct:+.2f}%`) 逼近 MA50/黄金坑反抽强阻，建议逢高减半锁定胜局！")
            elif st.endswith("_HEDGE_CLIMAX"):
                md.append(f"> 🟣 **{t_sym} 对冲恐慌极值，建议左侧逢高分批收割！**")
                md.append(f"> 现价 **`${last_p:.2f}`** (`{pnl_pct:+.2f}%`)，半导体出现极度恐慌或脉冲拉升，果断兑现对冲利润！")
            elif st.endswith("_HEDGE_EXIT"):
                md.append(f"> ⚪ **{t_sym} 跌破5日线(EMA5)，对冲波段结束！**")
                md.append(f"> 现价 **`${last_p:.2f}`** 跌破 EMA5，建议全额清仓对冲盘回笼资金！")
            elif st.endswith("_GOAL_ACHIEVED"):
                if target > cost:
                    md.append(f"> 🎉 **{t_sym} 达成盈利止盈目标 (${target:.2f})！**")
                    md.append(f"> 现价 **`${last_p:.2f}`** (浮盈 `{pnl_pct:+.2f}%`)，按计划分批落袋锁定利润！")
                else:
                    md.append(f"> 🎉 **{t_sym} 达成成本目标 (${target:.2f})！**")
                    md.append(f"> 现价 **`${last_p:.2f}`** 已回本，可根据盘面决定是否落袋或继续持有！")
            elif st.endswith("_NEAR_GOAL"):
                if target > cost:
                    md.append(f"> 🎯 **{t_sym} 逼近盈利止盈目标**：现价 `${last_p:.2f}`，距离止盈位 `${target:.2f}` 仅剩 3%！")
                else:
                    md.append(f"> 🚀 **{t_sym} 逼近保本线**：现价 `${last_p:.2f}`，距离成本 `${target:.2f}` 仅剩 3%！")
            md.append("---")
        md.append("")

    if pit_alerts:
        md.append("### 🟢 V3.1 黄金坑买点就绪 (Firstrade 9:30 开盘委托单)")
        for a in pit_alerts:
            t_last = a['last']
            t_frac = a.get('fractional', 0.0)
            t_alloc_val = t_frac * t_last
            t_alloc_pct = (t_alloc_val / TOTAL_ACCOUNT_EQUITY) * 100 if TOTAL_ACCOUNT_EQUITY > 0 else 25.0
            t_buy_limit = t_last * 1.020
            t_cancel_line = max(a.get('rth_low', t_last * 0.985), t_last * 0.985)
            t_stop_loss = a.get('stop_loss', round(t_last * 0.932, 2))

            md.append(f"> ### 🚀 **{a['ticker']}** {a['name']} ({a['cat']})")
            md.append(f"> • 现价 **`${t_last:.2f}`** ｜ 偏离度 `{a['bias']:+.1f}%` ｜ 14日RSI `{a['rsi']:.1f}`")
            md.append(f"> • 黄金坑区间：`${a['fib_low']:.2f} ~ ${a['fib_high']:.2f}` ({a['pit_status']})")
            md.append(f"> ")
            md.append(f"> 📋 **Firstrade 9:30 开盘执行指令 (V3.1 标准)**：")
            md.append(f"> 1️⃣ **9:30开盘买入上限价 (Limit)**：**`${t_buy_limit:.2f}`** (开盘高于此价放弃不追)")
            md.append(f"> 2️⃣ **隔夜低开作废底线**：**`${t_cancel_line:.2f}`** (开盘跌破此价撤单不买)")
            md.append(f"> 3️⃣ **V3.1 风险平价建议仓位**：约 **`${t_alloc_val:.0f}`** (`{t_frac:.2f} 股` · 占总资金 `{t_alloc_pct:.1f}%` · 1.5%风险平价一次性建仓)")
            md.append(f"> 4️⃣ **GTC 铁血结构止损单**：**`${t_stop_loss:.2f}`**")
            md.append("---")
        md.append("")

    if stop_alerts:
        md.append("### 🔴 破位止损 (风控防御警报)")
        for a in stop_alerts:
            md.append(f"> 🚨 **{a['ticker']}** ({a['name']}) 现价 **`${a['last']:.2f}`** (偏离度 `{a['bias']:+.1f}%`)")
            md.append(f"> • 跌破斐波那契下沿 **`${a['fib_low']:.2f}`**，进入空头宣泄期，严禁左侧接刀！")
            md.append("---")
        md.append("")

    if tp_alerts:
        md.append("### 🟣 冲顶减仓 / 阶梯止盈")
        for a in tp_alerts:
            md.append(f"> 💰 **{a['ticker']}** ({a['name']}) 现价 **`${a['last']:.2f}`** (偏离度 `{a['bias']:+.1f}%` · RSI `{a['rsi']:.1f}`)")
            md.append(f"> • 触发原因：`{a['reason']}`，建议分批减半锁定利润！")
            md.append("---")
        md.append("")

    md.append("📱 *查看全量 58 标的高清走势请打开【富途专业看盘终端】*")

    send_pushplus_wechat_alert(msg_title, "\n".join(md))

def generate_and_sync_notifications(macro_data, stocks_data, weather_info, now_str):
    """
    生成与微信通知 100% 同步的结构化分类消息中心数据，并持久化到 .terminal_notifications.json
    采用【日期+类型+标的+子状态】作为唯一 ID，同日同状态原地更新最新价格，杜绝每3分钟重复堆叠与误报响铃。
    """
    target_watchlist = load_target_watchlist()
    existing_notifs = load_terminal_notifications()
    new_notifs = []
    date_tag = now_str[:10]

    starred_etfs = [{"ticker": t, **m} for t, m in macro_data.items() if t in target_watchlist or m.get("shares", 0) > 0]
    all_targets = list(stocks_data) + starred_etfs

    for s in all_targets:
        ticker = s["ticker"]
        name = s["name"]
        cat = s["cat"]
        last = s["last"]
        active_price = s.get("active_price", last)
        pct_change = s.get("pct_change", 0.0)
        is_watched = ticker in target_watchlist
        intraday_ready = s.get("intraday_entry_ready", False)
        is_pit = s.get("is_pit", False)
        is_stop = s.get("is_stop", False)
        is_tp = s.get("is_take_profit", False)
        shares = s.get("shares", 0.0)

        # 1. 🎯 5m 买点通知 (与微信 WATCHLIST_ENTRY_READY 同步)
        if is_watched and intraday_ready and not is_stop:
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

                if be_active and active_price <= be_stop:
                    p_level = "danger"
                    p_substate = "BREAKEVEN" if lock_label == "保本盾" else "TIERED_LOCK"
                    p_title = f"🚨 {ticker} 触及{lock_label}止损线(${be_stop:.2f})！"
                    p_desc = f"历史最高曾达 ${peak_price:.2f} (+{max_pnl_pct:.1f}%)，现价回踩跌破{lock_label}，立即兑现锁利！"
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
        if is_pit and not is_stop:
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
        if is_stop and not ticker.endswith("ETF"):
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
    """并发批量抓取 1 年(约252交易日)历史日 K 线，支撑 MA200 牛熊线与 60日波段计算"""
    print(f"[*] 启动多线程底模引擎，抓取 {len(tickers)} 只标的历史形态 ({period})...")
    data_dict = {}
    try:
        raw_data = yf.download(tickers, period=period, group_by="ticker", threads=True, progress=False)
        for t in tickers:
            try:
                sub = raw_data[t].dropna() if hasattr(raw_data.columns, "levels") else raw_data.dropna()
                if len(sub) >= 20:
                    data_dict[t] = sub
            except Exception:
                pass
    except Exception as e:
        print(f"[!] yfinance 批量下载异常: {e}，将启用单标的回退检索...")

    missing = [t for t in tickers if t not in data_dict or data_dict[t].empty]
    if missing:
        for t in missing:
            for attempt in range(1, retries + 1):
                try:
                    df = yf.Ticker(t).history(period=period, interval="1d")
                    if df is not None and len(df) >= 20:
                        data_dict[t] = df
                        break
                except Exception:
                    time.sleep(0.5)

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

    # 暴雷冷却 (过去 5 天单日跌幅 <= -6.0% 或 跳空缺口 <= -4.5%)
    daily_ret = ((close - prev_close) / prev_close.replace(0, np.nan)) * 100.0
    gap_down = ((open_p - prev_close) / prev_close.replace(0, np.nan)) * 100.0
    is_crash = (daily_ret <= -6.0) | (gap_down <= -4.5)
    df["Crash_5d"] = is_crash.rolling(5, min_periods=1).max().fillna(0).astype(bool)

    # 右侧反转 K 线确认 (阳线实体 >= +0.5% 且 突破前一日高点 或 站上 EMA5)
    prev_high = high.shift(1)
    is_bullish_body = (close > open_p) & (((close - open_p) / open_p.replace(0, np.nan)) >= 0.005)
    df["Is_Right_Turn"] = is_bullish_body & ((close > prev_high) | (close > df["EMA5"]))

    return df

# ==============================================================================
# 四、 宏观气象与个股碎股信号判定
# ==============================================================================
def evaluate_market_weather(macro_dict, history_dict=None):
    """
    根据四大量化模型评定宏观气象与作战军规 (V3 深度升级版)：
    1. VIXY 三因子复合脉冲模型 (单日涨跌幅, MA20偏离度, 60日分位)
    2. TLT 美债双向利率状态机 (估值顺风, 利率杀估值, 衰退避险, 中性震荡)
    3. 晴天做多一票否决权 (Veto) 机制 (高位急跌, 破MA20, 恐慌脉冲)
    4. SMH/IGV 相对强弱动态驱动风格主线军规
    """
    qqqm_info = macro_dict.get("QQQM", {})
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
        veto_reasons.append("QQQM与SMH双破MA20")
    if qqqm_pct <= -1.8:
        veto_reasons.append(f"QQQM急杀{qqqm_pct:.1f}%")
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
            s_close = history_dict["SMH"]["Close"]
            i_close = history_dict["IGV"]["Close"]
            ratio_s = s_close / i_close
            cur_ratio = float(ratio_s.iloc[-1])
            ratio_ma20 = float(ratio_s.rolling(20, min_periods=5).mean().iloc[-1])
            if cur_ratio >= ratio_ma20:
                style_suffix = " ｜ 🔥 风格主线：算力硬件领涨（优先聚焦芯片/光通信/电力）"
            else:
                style_suffix = " ｜ 🔄 风格主线：硬件休整、软件应用占优（严禁追高芯片，关注软件与高低切）"
        except Exception:
            style_suffix = ""

    tactics = base_tactics + style_suffix

    # 5. V3.1 旗舰轨道 2：Firstrade 零佣金 QQQ 闲置资金智能泊入与避险指示器
    qqq_info = macro_dict.get("QQQ", qqqm_info)
    qqq_last = qqq_info.get("last", qqqm_last)
    qqq_ma50 = qqq_info.get("ma50", 0.0)
    qqq_sma200 = qqq_info.get("sma200") or qqq_info.get("ma200", 0.0)

    is_rainstorm_env = (qqqm_bias < -3.0 and smh_bias < -3.0) or (qqq_ma50 > 0 and qqq_last < qqq_ma50 * 0.97)
    is_bear_defense_env = (qqq_sma200 > 0 and qqq_last < qqq_sma200)

    if is_rainstorm_env or is_bear_defense_env:
        qqq_parking_action = "🔴 清空避险"
        qqq_parking_desc = "大盘暴风雨/破防线，Firstrade 闲置资金 100% 留存美元现金，严禁泊入 QQQ"
        qqq_parking_color = "#ff5252"
    elif qqq_ma50 > 0 and qqq_last >= qqq_ma50 and not is_vix_panic:
        qqq_parking_action = "🟢 100% 满额泊入"
        qqq_parking_desc = "大盘多头安全期，Firstrade 闲置资金建议 100% 挂 MOO 泊入 QQQ 享大盘收益，有新买点时自动变现"
        qqq_parking_color = "#00e676"
    else:
        qqq_parking_action = "🟡 观望防守"
        qqq_parking_desc = "大盘处于 MA50 震荡拉锯期，暂缓新资金泊入 QQQ，保持轻仓防守"
        qqq_parking_color = "#ffd54f"

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
        "is_vix_panic": is_vix_panic,
        "tlt_bias": tlt_bias,
        "tlt_state": tlt_state,
        "tlt_color": tlt_color,
        "tlt_desc": tlt_desc
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
                qqqm_info = macro_data.get("QQQM", {})
                qqqm_last = qqqm_info.get("last", 1.0)
                qqqm_ma200 = qqqm_info.get("ma200", 0.0)
                qqqm_rsi = qqqm_info.get("rsi", 50.0)
                is_bear_falling_knife = (qqqm_ma200 > 0) and (qqqm_last < qqqm_ma200) and (qqqm_rsi > 35.0)

            # V3.1 QQQ MA50 大盘准入门禁 (QQQ < MA50 时冻结个股新开仓，彻底规避震荡期连环止损)
            is_qqq_below_ma50 = False
            if macro_data:
                qqq_info = macro_data.get("QQQ", macro_data.get("QQQM", {}))
                qqq_last = qqq_info.get("last", 1.0)
                qqq_ma50 = qqq_info.get("ma50", 0.0)
                if qqq_ma50 > 0 and qqq_last < qqq_ma50:
                    is_qqq_below_ma50 = True

            REMOVED_TICKERS = {'KO', 'PEP', 'MCD', 'NKE', 'V', 'INTC', 'WDC', 'HPE', 'MRNA'}
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
        "is_volume": "🟡 量能异动" in primary_signal
    }

# ==============================================================================
# 五、 页面 1：宏观大盘司令塔 (macro_radar.html · 10 只 ETF)
# ==============================================================================
def generate_macro_radar_html(macro_data, weather_info, history_dict, update_time_str="", has_realtime=False):
    """生成包含 12 只核心 ETF 的宏观大盘司令塔看板"""
    if not update_time_str:
        update_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
    palette = {"QQQM": "#00e676", "SPY": "#29b6f6", "DIA": "#4fc3f7", "SMH": "#ff9100", "IWM": "#ab47bc", "TLT": "#ffd54f", "GLD": "#ffb300"}
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

    # 3. SMH / IGV 相对强度与 20日均线
    if "SMH" in history_dict and "IGV" in history_dict:
        ratio = history_dict["SMH"]["Close"] / history_dict["IGV"]["Close"]
        ratio_ma20 = ratio.rolling(20, min_periods=5).mean()
        cur_r = float(ratio.iloc[-1])
        cur_rma20 = float(ratio_ma20.iloc[-1])
        r_state = "硬件占优" if cur_r >= cur_rma20 else "软件占优"
        fig.add_trace(go.Scatter(x=ratio.index, y=ratio, mode="lines", line=dict(color="#00e5ff", width=1.5), name=f"SMH/IGV 比率 ({cur_r:.2f} · {r_state})"), row=2, col=1)
        fig.add_trace(go.Scatter(x=ratio.index, y=ratio_ma20, mode="lines", line=dict(color="#ffa726", width=1.2, dash="dot"), name=f"20日比率均线 ({cur_rma20:.2f})"), row=2, col=1)

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
        bias_style = "color: #00e676;" if info["bias"] >= 0 else "color: #ff5252;"
        rsi_style = "color: #ff5252; font-weight:bold;" if info["rsi"] < 30 else ("color: #00e676;" if info["rsi"] > 70 else "color: #e0e0e0;")
        status_badge = f"<span class='badge' style='background:{info['badge_color']};'>{info['status']}</span>"
        pct_color = "#00e676" if info.get("pct_change", 0) >= 0 else "#ff5252"
        rows_html += f"""
        <tr>
            <td style="font-weight: bold; color: #ffd54f;">{ticker}</td>
            <td>{info['name']}</td>
            <td><span class="category-tag">{info['cat']}</span></td>
            <td><b>${info['last']:.2f}</b> <span style="font-size: 11px; color: {pct_color};">({info.get('pct_change', 0):+.2f}%)</span></td>
            <td>${info['ma50']:.2f}</td>
            <td style="{bias_style} font-weight: bold;">{info['bias']:+.2f}%</td>
            <td>${info['atr']:.2f}</td>
            <td style="{rsi_style}">{info['rsi']:.1f}</td>
            <td>{status_badge}</td>
            <td style="font-size: 12px; color: #aaa;">{info['sub']}</td>
        </tr>
        """

    live_badge_html = """
    <span class="live-pill"><span class="live-dot"></span>Finnhub 秒级实时已激活 (Live)</span>
    """ if has_realtime else """
    <span class="category-tag" style="color: #ffd54f; border-color: rgba(255,213,79,0.3);">yfinance 底模</span>
    """

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="180">
    <title>宏观大盘司令塔 · 全局水温看板 (12只核心ETF)</title>
    <style>
        body {{
            background-color: #0b0e14;
            color: #d1d5db;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            margin: 0;
            padding: 24px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        .header h1 {{ margin: 0; font-size: 24px; color: #f3f4f6; }}
        .header-links a {{
            color: #60a5fa;
            text-decoration: none;
            background: rgba(96, 165, 250, 0.12);
            padding: 8px 16px;
            border-radius: 8px;
            border: 1px solid rgba(96, 165, 250, 0.3);
            font-size: 14px;
            font-weight: 500;
            transition: all 0.2s;
        }}
        .header-links a:hover {{ background: rgba(96, 165, 250, 0.25); }}
        
        .live-pill {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(0, 230, 118, 0.15);
            border: 1px solid rgba(0, 230, 118, 0.45);
            color: #00e676;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
        }}
        .live-dot {{
            width: 7px;
            height: 7px;
            background: #00e676;
            border-radius: 50%;
            box-shadow: 0 0 8px #00e676;
        }}

        .weather-card {{
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.95));
            border: 1px solid {weather_info['color']};
            box-shadow: 0 0 25px rgba(41, 182, 246, 0.15);
            border-radius: 14px;
            padding: 24px 28px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .weather-main {{ display: flex; align-items: center; gap: 20px; }}
        .weather-icon {{ font-size: 56px; line-height: 1; }}
        .weather-title {{ font-size: 26px; font-weight: 800; color: {weather_info['color']}; margin-bottom: 6px; }}
        .weather-desc {{ font-size: 14px; color: #9ca3af; max-width: 650px; line-height: 1.5; }}
        .weather-stats {{ display: flex; gap: 18px; }}
        .stat-box {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            padding: 10px 14px;
            text-align: center;
            min-width: 125px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}
        .stat-label {{ font-size: 11px; color: #9ca3af; margin-bottom: 4px; white-space: nowrap; }}
        .stat-value {{ font-size: 17px; font-weight: 700; line-height: 1.2; }}
        .stat-sub {{ font-size: 11px; margin-top: 4px; color: #9ca3af; white-space: nowrap; }}
        
        .chart-section {{
            background: #131722;
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 16px;
            margin-bottom: 24px;
        }}
        .table-card {{
            background: #131722;
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 20px;
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}
        th {{
            background: rgba(255, 255, 255, 0.03);
            color: #9ca3af;
            font-weight: 600;
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }}
        td {{
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }}
        tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}
        .category-tag {{
            background: rgba(255, 255, 255, 0.08);
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            color: #cbd5e1;
        }}
        .badge {{
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            color: #fff;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🏛️ 宏观大盘司令塔 · 全局水温看板</h1>
            <div style="font-size: 12px; color: #6b7280; margin-top: 6px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                <span>大盘基准: QQQM / SPY / DIA / IWM | 行业风向: SMH / IGV | 避险流动性: TLT / GLD / QQQI / VIXY</span>
                <span style="color:#60a5fa;">⏱️ 自动刷新: 每3分钟 (180s)</span>
                <span>最近更新: {update_time_str}</span>
                {live_badge_html}
            </div>
        </div>
        <div class="header-links" style="display: flex; gap: 8px;">
            <a href="futu_pro_terminal.html" target="_blank" style="background: rgba(255,105,0,0.15); border: 1px solid rgba(255,105,0,0.4); color: #ff6900;">🐮 富途全周期看盘</a>
            <a href="stocks_radar.html" target="_blank">🎯 核心个股雷达 (27只)</a>
        </div>
    </div>

    <!-- 市场气象大卡片 -->
    <div class="weather-card">
        <div class="weather-main">
            <div class="weather-icon">{weather_info['icon']}</div>
            <div>
                <div class="weather-title">【{weather_info['weather']}】模式</div>
                <div class="weather-desc"><b>风向研判：</b>{weather_info['desc']}</div>
                <div class="weather-desc" style="color: #60a5fa; margin-top: 4px;"><b>作战军规：</b>{weather_info['tactics']}</div>
            </div>
        </div>
        <div class="weather-stats">
            <div class="stat-box">
                <div class="stat-label">QQQM 偏离 MA50</div>
                <div class="stat-value" style="color: {'#00e676' if weather_info['qqqm_bias']>=0 else '#ff5252'};">{weather_info['qqqm_bias']:+.2f}%</div>
                <div class="stat-sub">MA20: {weather_info.get('qqqm_ma20_bias', 0.0):+.1f}% | 今日: {weather_info.get('qqqm_pct', 0.0):+.2f}%</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">SMH 偏离 MA50</div>
                <div class="stat-value" style="color: {'#00e676' if weather_info['smh_bias']>=0 else '#ff5252'};">{weather_info['smh_bias']:+.2f}%</div>
                <div class="stat-sub">MA20: {weather_info.get('smh_ma20_bias', 0.0):+.1f}% | 今日: {weather_info.get('smh_pct', 0.0):+.2f}%</div>
            </div>
            <div class="stat-box" style="border-color: {'#ff1744' if weather_info.get('is_vix_panic') else 'rgba(255, 255, 255, 0.08)'};">
                <div class="stat-label">VIXY 恐慌指数</div>
                <div class="stat-value" style="color: {'#ff1744' if weather_info.get('is_vix_panic') else '#ffd54f'};">${weather_info['vixy_last']:.2f}</div>
                <div class="stat-sub" style="color: {'#ff5252' if weather_info.get('is_vix_panic') else '#9ca3af'};">MA20: {weather_info.get('vixy_ma20_bias', 0.0):+.1f}% | 60d: {weather_info.get('vixy_pos_60', 50.0):.0f}%</div>
            </div>
            <div class="stat-box" style="border-color: {weather_info.get('tlt_color', '#9ca3af')}44;">
                <div class="stat-label">美债利率定性</div>
                <div class="stat-value" style="color: {weather_info.get('tlt_color', '#9ca3af')}; font-size: 15px;">{weather_info.get('tlt_state', '⚪ 中性震荡')}</div>
                <div class="stat-sub">TLT偏离: {weather_info['tlt_bias']:+.2f}%</div>
            </div>
        </div>
    </div>

    <!-- 综合量化图表 -->
    <div class="chart-section">
        {chart_div}
    </div>

    <!-- 宏观水温监控表 -->
    <div class="table-card">
        <h3 style="margin-top: 0; margin-bottom: 16px; font-size: 16px; color: #e2e8f0;">📊 宏观资产数字化水温总表 (10 只 ETF · 实时行情)</h3>
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
</body>
</html>
"""
    with open(MACRO_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"✅ 宏观大盘司令塔已生成: {MACRO_HTML_FILE}")

# ==============================================================================
# 六、 页面 2：核心个股碎股雷达 (stocks_radar.html · 27 只个股)
# ==============================================================================
def generate_stocks_radar_html(stocks_data, history_dict, update_time_str="", has_realtime=False):
    """生成包含 27 只核心科技个股的碎股雷达与黄金坑矩阵"""
    if not update_time_str:
        update_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # INTC 数据提取与风控参数
    intc_info = next((s for s in stocks_data if s.get("ticker") == "INTC"), stocks_data[0] if stocks_data else {})
    intc_last = intc_info["last"]
    intc_cost = intc_info.get("cost", 95.0)
    intc_shares = intc_info.get("shares", 10.0)
    intc_stop = intc_info.get("base_val", 88.0)
    intc_target = intc_info.get("target", 95.50)
    intc_val = intc_last * intc_shares
    intc_pnl = ((intc_last - intc_cost) / intc_cost) * 100 if intc_cost > 0 else 0.0
    intc_pnl_color = "#00e676" if intc_pnl >= 0 else "#ff5252"
    intc_pnl_sub_color = "#4ade80" if intc_pnl >= 0 else "#f87171"
    intc_card_border = "#00e676" if intc_pnl >= 0 else "#ff5252"
    intc_card_bg = f"linear-gradient(135deg, rgba({'0, 230, 118' if intc_pnl >= 0 else '255, 82, 82'}, 0.08), #131722)"

    # 1. 绘制 INTC 专属 K 线
    intc_div = ""
    if "INTC" in history_dict and len(history_dict["INTC"]) >= 20:
        intc_df = history_dict["INTC"]
        fib_low = intc_df["Fib_Low"].iloc[-1]
        fib_high = intc_df["Fib_High"].iloc[-1]

        fig_intc = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.04,
            row_heights=[0.72, 0.28],
            subplot_titles=(
                f"<b>INTC 核心持仓风控与解套监测图</b> (现价: ${intc_last:.2f} | 成本基线: ${intc_cost:.2f} | 止损防线: ${intc_stop:.2f})",
                "<b>成交量 (Volume)</b>"
            )
        )
        fig_intc.add_trace(go.Candlestick(x=intc_df.index, open=intc_df["Open"], high=intc_df["High"], low=intc_df["Low"], close=intc_df["Close"], name="INTC K线", increasing_line_color="#26a69a", decreasing_line_color="#ef5350"), row=1, col=1)
        fig_intc.add_trace(go.Scatter(x=intc_df.index, y=intc_df["MA50"], line=dict(color="#ffa726", width=1.8), name="MA50 均线"), row=1, col=1)

        # 成本线与底估基线
        fig_intc.add_hline(y=intc_cost, line_dash="dash", line_color="#00e676", line_width=2, annotation_text=f"🎯 持仓成本 ${intc_cost:.2f} ({intc_shares:g}股)", annotation_position="top right", row=1, col=1)
        fig_intc.add_hline(y=intc_stop, line_dash="solid", line_color="#ff1744", line_width=2, annotation_text=f"🛡️ 风控止损线 ${intc_stop:.2f}", annotation_position="bottom right", row=1, col=1)

        # 黄金坑防守带
        fig_intc.add_hrect(y0=fib_low, y1=fib_high, fillcolor="rgba(33, 150, 243, 0.18)", line_width=1, line_color="rgba(33, 150, 243, 0.5)", annotation_text=f"0.5 - 0.618 斐波那契黄金坑 (${fib_low:.2f} ~ ${fib_high:.2f})", annotation_position="bottom left", row=1, col=1)

        vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(intc_df["Close"], intc_df["Open"])]
        fig_intc.add_trace(go.Bar(x=intc_df.index, y=intc_df["Volume"], marker_color=vol_colors, name="成交量"), row=2, col=1)

        fig_intc.update_layout(
            template="plotly_dark",
            plot_bgcolor="#131722",
            paper_bgcolor="#131722",
            xaxis_rangeslider_visible=False,
            height=460,
            margin=dict(l=50, r=40, t=50, b=30),
            hovermode="x unified"
        )
        intc_div = fig_intc.to_html(full_html=False, include_plotlyjs="cdn")

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
            text=[f"<b>{t}</b> (${item['last']:.2f})"],
            textposition="top center",
            marker=dict(size=[size_val], color=color, opacity=0.85, line=dict(color="#ffffff", width=1.5)),
            hoverinfo="text",
            hovertext=f"<b>{t} ({item['name']})</b><br>赛道: {item['cat']}<br>现价: ${item['last']:.2f} ({item.get('pct_change',0):+.2f}%)<br>距黄金坑: {x_val:+.2f}%<br>RSI(14): {y_val:.1f}<br>14日ATR: ${item['atr']:.2f}<br>碎股数: {item['fractional_shares']:.2f}股<br>信号: {item['signal']}"
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
    rows_html = ""
    for r in stocks_data:
        bias_style = "color: #00e676;" if r["bias"] >= 0 else "color: #ff5252;"
        dist_style = "color: #00e676; font-weight:bold;" if r["dist_to_pit"] <= 0 else "color: #90caf9;"
        pct_color = "#00e676" if r.get("pct_change", 0) >= 0 else "#ff5252"
        
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
            <td style="font-weight: bold; color: #ffd54f;">{r['ticker']}</td>
            <td>{r['name']}</td>
            <td><span class="category-tag">{r['cat']}</span></td>
            <td><b>${r['last']:.2f}</b> <span style="font-size: 11px; color: {pct_color};">({r.get('pct_change', 0):+.2f}%)</span></td>
            <td>${r['ma50']:.2f}</td>
            <td style="{bias_style} font-weight: bold;">{r['bias']:+.2f}%</td>
            <td>${r['atr']:.2f}</td>
            <td>{r['rsi']:.1f}</td>
            <td style="font-size: 12px; color: #64b5f6;">${r['fib_low']:.2f} ~ ${r['fib_high']:.2f}</td>
            <td style="{dist_style}">{r['pit_status']}</td>
            <td style="color: #00e5ff; font-weight: bold;">{r['fractional_shares']:.2f} 股 <span style="font-size:10px; color:#888;">({min(25.0, (r['fractional_shares'] * r['last'] / TOTAL_ACCOUNT_EQUITY) * 100):.1f}%)</span></td>
            <td>{sig_badge}</td>
        </tr>
        """

    # INTC 数据已在顶部统一定义抽取

    live_badge_html = """
    <span class="live-pill"><span class="live-dot"></span>Finnhub 秒级实时已激活 (Live)</span>
    """ if has_realtime else """
    <span class="category-tag" style="color: #ffd54f; border-color: rgba(255,213,79,0.3);">yfinance 底模</span>
    """

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="180">
    <title>核心个股碎股雷达 · 黄金坑作战与进攻看板 ({len(stocks_data)}只标的)</title>
    <style>
        body {{
            background-color: #0b0e14;
            color: #d1d5db;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            margin: 0;
            padding: 24px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        .header h1 {{ margin: 0; font-size: 24px; color: #f3f4f6; }}
        .header-links a {{
            color: #60a5fa;
            text-decoration: none;
            background: rgba(96, 165, 250, 0.12);
            padding: 8px 16px;
            border-radius: 8px;
            border: 1px solid rgba(96, 165, 250, 0.3);
            font-size: 14px;
            font-weight: 500;
            transition: all 0.2s;
        }}
        .header-links a:hover {{ background: rgba(96, 165, 250, 0.25); }}

        .live-pill {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(0, 230, 118, 0.15);
            border: 1px solid rgba(0, 230, 118, 0.45);
            color: #00e676;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
        }}
        .live-dot {{
            width: 7px;
            height: 7px;
            background: #00e676;
            border-radius: 50%;
            box-shadow: 0 0 8px #00e676;
        }}

        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .card {{
            background: #131722;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 18px 22px;
        }}
        .card-intc {{
            border-left: 4px solid #00e676;
            background: linear-gradient(135deg, rgba(0, 230, 118, 0.08), #131722);
        }}
        .card-title {{ font-size: 13px; color: #9ca3af; margin-bottom: 6px; }}
        .card-value {{ font-size: 22px; font-weight: 800; color: #f3f4f6; margin-bottom: 4px; }}
        .card-sub {{ font-size: 12px; color: #6b7280; }}

        .chart-box {{
            background: #131722;
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 16px;
            margin-bottom: 24px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}
        th {{
            background: rgba(255, 255, 255, 0.03);
            color: #9ca3af;
            font-weight: 600;
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }}
        td {{
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }}
        tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}
        .category-tag {{
            background: rgba(255, 255, 255, 0.08);
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            color: #cbd5e1;
        }}
        .badge {{
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            color: #fff;
            display: inline-block;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🎯 核心个股碎股雷达 · {len(stocks_data)}只标的实战看板</h1>
            <div style="font-size: 12px; color: #6b7280; margin-top: 6px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                <span>覆盖赛道: 芯片算力 / 商业AI / 新算力云 / AI能源核电 / 液冷服务器 / 光通信 / 太空商业 / 存储闪存 / 物理AI安全 / 经典蓝筹</span>
                <span style="color:#60a5fa;">⏱️ 自动刷新: 每3分钟 (180s)</span>
                <span>最近更新: {update_time_str}</span>
                {live_badge_html}
            </div>
        </div>
        <div class="header-links" style="display: flex; gap: 8px;">
            <a href="futu_pro_terminal.html" target="_blank" style="background: rgba(255,105,0,0.15); border: 1px solid rgba(255,105,0,0.4); color: #ff6900;">🐮 富途全周期看盘</a>
            <a href="macro_radar.html" target="_blank">🏛️ 宏观司令塔</a>
        </div>
    </div>

    <!-- 顶部概览看板 -->
    <div class="summary-grid">
        <div class="card card-intc" style="border-left: 4px solid {intc_card_border}; background: {intc_card_bg};">
            <div class="card-title">🛡️ INTC 核心持仓风控哨 (成本 ${intc_cost:.2f} · {intc_shares:g}股)</div>
            <div class="card-value" style="color: {intc_pnl_color};">${intc_last:.2f} <span style="font-size: 14px; font-weight: normal; color: {intc_pnl_sub_color};">({intc_pnl:+.2f}%)</span></div>
            <div class="card-sub">当前持仓市值: <b>${intc_val:.2f}</b> | 风控止损线: <b>${intc_stop:.2f}</b> (距止损 ${(intc_last - intc_stop):+.2f})</div>
        </div>
        <div class="card">
            <div class="card-title">🟢 黄金坑就位与进攻机会</div>
            <div class="card-value" style="color: #00e676;">{sum(1 for s in stocks_data if s['is_pit'])} <span style="font-size: 14px; color: #888;">/ {len(stocks_data)} 只</span></div>
            <div class="card-sub">现价进入 0.5 ~ 0.618 斐波那契回撤低吸防守带且 RSI < 52</div>
        </div>
        <div class="card">
            <div class="card-title">💼 单股仓位配置标准 (动态比例制)</div>
            <div class="card-value" style="color: #00e5ff;">25.0% <span style="font-size: 14px; color: #888;">(1.5%风险平价)</span></div>
            <div class="card-sub">单股上限 25% (1.5%风险平价一次性建仓)，纪律执行防追高，拒绝情绪化重仓</div>
        </div>
        <div class="card" style="border-left: 4px solid #ff5252; background: linear-gradient(135deg, rgba(255, 82, 82, 0.08), #131722);">
            <div class="card-title">🔴 破位止损与风控警戒</div>
            <div class="card-value" style="color: #ff5252;">{sum(1 for s in stocks_data if s.get('is_stop'))} <span style="font-size: 14px; color: #888;">/ {len(stocks_data)} 只</span></div>
            <div class="card-sub">跌破坑底 3% 或 MA50 偏离 <-12%，坚决风控防守，拒绝逆势抄底</div>
        </div>
    </div>

    <!-- INTC 作战防线图 -->
    <div class="chart-box">
        {intc_div}
    </div>

    <!-- 黄金坑散点作战矩阵 -->
    <div class="chart-box">
        {matrix_div}
    </div>

    <!-- 碎股作战行动表 -->
    <div class="chart-box">
        <h3 style="margin-top: 0; margin-bottom: 16px; font-size: 16px; color: #e2e8f0;">📋 核心个股碎股作战行动表 ({len(stocks_data)}只标的 · 按距黄金坑距离升序)</h3>
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
                    <th>建议建仓仓位 (单股25%上限)</th>
                    <th>作战行动信号</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
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
            f"[dim]大盘偏离：QQQM ({weather_info['qqqm_bias']:+.2f}%) | SMH ({weather_info['smh_bias']:+.2f}%) | VIXY (${weather_info['vixy_last']:.2f} · MA20:{weather_info.get('vixy_ma20_bias',0.0):+.1f}%) | TLT ({weather_info.get('tlt_state','中性')} / {weather_info['tlt_bias']:+.2f}%)[/dim]"
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
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 0. 载入用户自主管理实盘持仓配置 (user_portfolio.json) 并无缝动态合并
    user_portfolio = load_user_portfolio()
    if os.path.exists(USER_PORTFOLIO_FILE):
        # 先清空内置默认持仓股数，确保用户在弹窗中删除/清仓某只股票后能真正清零，不死灰复燃
        for group_key in ("ETFS", "STOCKS"):
            for tk_meta in MONITORED_TARGETS[group_key].values():
                tk_meta["shares"] = 0.0
                tk_meta["trim1"] = 0.0
                tk_meta["trim1_shares"] = 0.0
                tk_meta["trim2"] = 0.0
    if user_portfolio:
        for t_sym, p_info in user_portfolio.items():
            t_sym = t_sym.upper()
            shares = float(p_info.get("shares", 0.0))
            cost = float(p_info.get("cost", 0.0))
            target = float(p_info.get("target", cost if cost > 0 else 0.0))
            stop_val = float(p_info.get("base_val", p_info.get("stop_val", 0.0)))
            trim1 = float(p_info.get("trim1", 0.0))
            trim1_shares = float(p_info.get("trim1_shares", 0.0))
            trim2 = float(p_info.get("trim2", 0.0))

            if t_sym in MONITORED_TARGETS["ETFS"]:
                tgt = MONITORED_TARGETS["ETFS"][t_sym]
            elif t_sym in MONITORED_TARGETS["STOCKS"]:
                tgt = MONITORED_TARGETS["STOCKS"][t_sym]
            else:
                tgt = {
                    "name": p_info.get("name", t_sym),
                    "sub": p_info.get("note", "实盘持仓"),
                    "cat": p_info.get("cat", "自选持仓"),
                    
                }
                MONITORED_TARGETS["STOCKS"][t_sym] = tgt

            tgt["shares"] = shares
            tgt["cost"] = cost
            tgt["target"] = target
            if stop_val > 0:
                tgt["base_val"] = stop_val
            tgt["trim1"] = trim1
            tgt["trim1_shares"] = trim1_shares
            tgt["trim2"] = trim2

    all_etfs = list(MONITORED_TARGETS["ETFS"].keys())
    all_stocks = list(MONITORED_TARGETS["STOCKS"].keys())
    all_tickers = all_etfs + all_stocks

    # 1. 抓取 1 年(约252交易日)历史日 K 线底模
    history_dict = fetch_all_market_data(all_tickers, period="1y")

    # 1.5 判断当前美东交易时段 (常规盘中 / 盘前 / 盘后 / 夜盘)
    session_code, session_name = get_us_market_session()
    print(f"[*] 🕒 当前美东市场时段状态: 【{session_name}】")

    # 2. 并发秒级拉取 Finnhub 实时报价 (max_workers=2, 稳态间隔防 429)
    # 免费额度 60次/分。全标的 64 只，必须 100% 优先保障 15 只宏观大盘基准 ETF (QQQM/SMH/SPY等决定气象与黄金坑闸门) + 43 只核心科技股 = 58 只走 Finnhub；其余 6 只低波防守股走 Yahoo 5m RTH 实时穿透
    _defensive_6 = {"KO", "PEP", "MCD", "AXP", "V", "CCXI"}
    finnhub_tickers = [tk for tk in all_tickers if tk not in _defensive_6 or (user_portfolio and tk in user_portfolio)]
    realtime_quotes = fetch_finnhub_quotes_batch(finnhub_tickers, FINNHUB_API_KEY) if FINNHUB_API_KEY else {}

    # 2.5 批量拉取 5 分钟级日内指标 (VWAP / 5m RSI / 5m RVOL) 与盘前盘后最新成交价
    print(f"[*] 📊 正在同步 {len(all_tickers)} 只标的【5分钟级 VWAP/RSI/量比 日内择时指标 & 盘前盘后最新报价】...")
    intraday_map = fetch_intraday_5m_and_ext_data(all_tickers)
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
    print("")

    has_realtime = len(realtime_quotes) > 0 or len(intraday_map) > 0

    # 预计算 QQQM 20日基准收益率用于计算全市场个股 RS 相对强弱度 (Alpha)
    qqqm_ret_20d = 0.0
    if "QQQM" in history_dict and len(history_dict["QQQM"]) >= 21:
        q_last = realtime_quotes["QQQM"]["last"] if "QQQM" in realtime_quotes else float(history_dict["QQQM"]["Close"].iloc[-1])
        q_prev20 = float(history_dict["QQQM"]["Close"].iloc[-21])
        if q_prev20 > 0:
            qqqm_ret_20d = ((q_last - q_prev20) / q_prev20) * 100.0

    # 3. 处理宏观 14 只 ETF 数据 (含 SOXX / SOXL，严格分离正股收盘价与盘前盘后价)
    macro_data = {}
    for ticker, meta in MONITORED_TARGETS["ETFS"].items():
        if ticker in history_dict and len(history_dict[ticker]) >= 20:
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
                if rth_c > 0:
                    last = float(rth_c)
                    prev_c = float(df_raw["Close"].iloc[-1]) if abs(rth_c - float(df_raw["Close"].iloc[-1])) > 0.01 else (float(df_raw["Close"].iloc[-2]) if len(df_raw) >= 2 else last)
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
            ext_price = 0.0
            ext_change = 0.0
            ext_pct = 0.0
            session_tag = ""
            active_price = last
            if session_code in ["PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND"] and intra_info.get("ext_price", 0) > 0:
                ep = intra_info["ext_price"]
                if last > 0 and abs(ep - last) >= 0.005:
                    ext_price = ep
                    ext_change = ep - last
                    ext_pct = (ext_change / last) * 100
                    active_price = ep
                    if session_code == "PRE_MARKET":
                        session_tag = "盘前"
                    elif session_code == "POST_MARKET":
                        session_tag = "盘后"
                    elif session_code == "OVERNIGHT":
                        session_tag = "夜盘"
                    else:
                        session_tag = "休市"

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
            is_right_turn = bool(df["Is_Right_Turn"].iloc[-1]) if "Is_Right_Turn" in df else False
            rs_60 = float(df["RET_60"].iloc[-1]) if "RET_60" in df and not np.isnan(df["RET_60"].iloc[-1]) else 0.0

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
                pct_change=pct_change, ext_pct=ext_pct
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
                "session_tag": session_tag,
                "ma200": ma200,
                "sma200": sma200,
                "ma50": ma50,
                "ma20": ma20,
                "ema20": ema20,
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

    weather_info = evaluate_market_weather(macro_data, history_dict=history_dict)

    # 4. 处理全量个股数据并计算碎股、日线信号与 5m 日内建仓信号
    stocks_list = []
    for ticker, meta in MONITORED_TARGETS["STOCKS"].items():
        if ticker in history_dict and len(history_dict[ticker]) >= 20:
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
                if rth_c > 0:
                    last = float(rth_c)
                    prev_c = float(df_raw["Close"].iloc[-1]) if abs(rth_c - float(df_raw["Close"].iloc[-1])) > 0.01 else (float(df_raw["Close"].iloc[-2]) if len(df_raw) >= 2 else last)
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
            ext_price = 0.0
            ext_change = 0.0
            ext_pct = 0.0
            session_tag = ""
            active_price = last
            if session_code in ["PRE_MARKET", "POST_MARKET", "OVERNIGHT", "WEEKEND"] and intra_info.get("ext_price", 0) > 0:
                ep = intra_info["ext_price"]
                if last > 0 and abs(ep - last) >= 0.005:
                    ext_price = ep
                    ext_change = ep - last
                    ext_pct = (ext_change / last) * 100
                    active_price = ep
                    if session_code == "PRE_MARKET":
                        session_tag = "盘前"
                    elif session_code == "POST_MARKET":
                        session_tag = "盘后"
                    elif session_code == "OVERNIGHT":
                        session_tag = "夜盘"
                    else:
                        session_tag = "休市"

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
            is_right_turn = bool(df["Is_Right_Turn"].iloc[-1]) if "Is_Right_Turn" in df else False
            rs_60 = float(df["RET_60"].iloc[-1]) if "RET_60" in df and not np.isnan(df["RET_60"].iloc[-1]) else 0.0

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
            intra_eval = evaluate_intraday_entry(
                active_price, fib_low, fib_high, ma50,
                eval_res["is_pit"], eval_res["is_stop"], eval_res["dist_to_pit"], intra_info,
                earnings_info=earnings_map.get(ticker), rs_20d=rs_20d, atr=atr, daily_rsi=rsi,
                options_info=options_map.get(ticker),
                pct_change=pct_change, ext_pct=ext_pct
            )

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
                "target": meta.get("target", 95.53),
                "max_alloc": 0.0,
                "last": last,
                "change": change,
                "pct_change": pct_change,
                "active_price": active_price,
                "ext_price": ext_price,
                "ext_change": ext_change,
                "ext_pct": ext_pct,
                "ma50": ma50,
                "ema20": ema20,
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
            # yfinance 历史不足时，若有 Finnhub 实时数据则优雅生成安全底模，确保标的不丢失
            if ticker in realtime_quotes:
                rq = realtime_quotes[ticker]
                last = rq["last"]
                change = rq["change"]
                pct_change = rq["pct_change"]
                ma50 = meta.get("base_val", last)
                ema20 = ma50
                bias = ((last - ma50) / ma50) * 100 if ma50 > 0 else 0.0
                atr = last * 0.03
                rsi = 50.0
                vol_ratio = 1.0
                fib_low = last * 0.92
                fib_high = last * 0.98
                is_realtime = True
                eval_res = evaluate_stock_signal(ticker, last, ma50, bias, rsi, vol_ratio, fib_low, fib_high, meta, atr=atr, weather_info=weather_info, macro_data=macro_data, ema20=ema20, high_bias_recently=False)
                stocks_list.append({
                    "ticker": ticker,
                    "name": meta["name"],
                    "sub": meta["sub"],
                    "cat": meta["cat"],
                    "cost": meta.get("cost", 0.0),
                    "shares": meta.get("shares", 0.0),
                    "base_val": meta.get("base_val", 0.0),
                    "target": meta.get("target", 95.50),
                    "max_alloc": 0.0,
                    "last": last,
                    "change": change,
                    "pct_change": pct_change,
                    "ma50": ma50,
                    "ema20": ema20,
                    "bias": bias,
                    "atr": atr,
                    "rsi": rsi,
                    "vol_ratio": vol_ratio,
                    "fib_low": fib_low,
                    "fib_high": fib_high,
                    "is_realtime": is_realtime,
                    **eval_res
                })

    # 按距离黄金坑升序排序
    stocks_sorted = sorted(stocks_list, key=lambda x: x["dist_to_pit"])

    # 5. 终端打印规整 Rich 表格
    display_terminal_tables(macro_data, stocks_sorted, weather_info, has_realtime=has_realtime)

    # 6. 生成三大交互式 HTML 看板 (静默覆盖写入，且具备空数据自动跳过保护)
    if not stocks_sorted or not macro_data:
        print("[!] ⚠️ 警告: 本轮抓取标的数异常为空 (疑似网络瞬时断连)，保留上一轮有效看板数据，跳过覆盖写入以防页面白屏。")
        return

    generate_macro_radar_html(macro_data, weather_info, history_dict, update_time_str=now_str, has_realtime=has_realtime)
    generate_stocks_radar_html(stocks_sorted, history_dict, update_time_str=now_str, has_realtime=has_realtime)
    
    target_watchlist = load_target_watchlist()
    terminal_notifications = generate_and_sync_notifications(macro_data, stocks_sorted, weather_info, now_str)
    futu_html = build_futu_terminal_html(stocks_sorted, macro_data, weather_info, update_time_str=now_str, has_realtime=has_realtime, target_watchlist=target_watchlist, user_portfolio=user_portfolio, terminal_notifications=terminal_notifications, session_code=session_code, session_name=session_name)
    with open(FUTU_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(futu_html)
    index_html_path = os.path.join(SCRATCH_DIR, "index.html")
    try:
        with open(index_html_path, "w", encoding="utf-8") as f:
            f.write(futu_html)
    except Exception:
        pass
    print(f"✅ 富途牛牛专业全周期看盘终端已生成: {FUTU_HTML_FILE}")

    # 7. 智能去重微信预警推送 (PushPlus 微信 Markdown 卡片通道)
    dispatch_wechat_radar_alerts(macro_data, stocks_sorted, weather_info, now_str)

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
        try:
            webbrowser.open(f"file://{futu_path}")
        except Exception as e:
            print(f"[!] 自动唤起浏览器提示: {e}")
    else:
        print(f"[*] [第 {round_idx} 轮] 三份看板与富途终端 HTML 已覆盖写入更新完毕 (已重置 180s 同步锁)。")

# ==============================================================================
# 九、 主入口与智能同步锁调度引擎 (3分钟自动轮询 + 手动刷新无缝重置)
# ==============================================================================
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
        print(f"\n🚀 启动云端 {interval_sec} 秒极速长轮询守护引擎 (计划持续运行 {duration_min} 分钟)")
        start_time = time.time()
        end_time = start_time + duration_min * 60
        round_idx = 1
        while time.time() < end_time:
            c_start = time.time()
            now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n" + "-" * 75)
            print(f"⏱️ [{now_ts}] [第 {round_idx} 轮] 执行全市场 {interval_sec} 秒极速同步与期权扫描...")
            print("-" * 75)
            try:
                run_radar_cycle(is_first_run=False, round_idx=round_idx)
            except Exception as e:
                print(f"[!] ⚠️ 守护进程自动拦截异常并继续运行: {e}")
            # 云端同步持久化至 Git 仓库，令手机端 GitHub Pages 秒级保持最新
            try:
                import shutil
                if os.path.exists("futu_pro_terminal.html"):
                    shutil.copyfile("futu_pro_terminal.html", "index.html")
                
                # 配置带 PAT 的远程地址，确保触发 GitHub Pages 自动部署，规避 GITHUB_TOKEN 的防递归拦截
                gh_token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
                if gh_token:
                    auth_url = f"https://x-access-token:{gh_token}@github.com/2827173579/us-market-radar.git"
                    subprocess.run(["git", "remote", "set-url", "origin", auth_url], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(
                    ["git", "add", "index.html", "futu_pro_terminal.html", ".terminal_notifications.json", ".options_cache.json", ".radar_alert_history.json", "macro_radar.html", "stocks_radar.html"],
                    check=False
                )
                subprocess.run(
                    ["git", "commit", "-m", f"⚡ [实盘同步] 第 {round_idx} 轮极速刷新 ({now_ts})"],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                push_res = subprocess.run(["git", "push", "origin", "HEAD:main"], check=False, capture_output=True, text=True)
                if push_res.returncode == 0:
                    print(f"[+] ✅ 成功推送第 {round_idx} 轮数据至 GitHub Pages (PAT 触发部署成功)！")
                else:
                    print(f"[*] Git 推送反馈: {push_res.stderr.strip()[:100]}")
            except Exception as e:
                print(f"[!] Git 同步异常: {e}")
            round_idx += 1
            cost = time.time() - c_start
            sleep_time = max(2.0, float(interval_sec) - cost)
            print(f"[*] 本轮扫描耗时 {cost:.1f}s，休眠 {sleep_time:.1f}s 后进入下一轮...")
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
    last_sync = get_last_sync_time()
    elapsed = time.time() - last_sync
    if elapsed >= SYNC_INTERVAL or last_sync == 0.0:
        start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n" + "-" * 75)
                print(f"⏱️ [{start_ts}] 达到 180 秒调度周期，正在执行第 {round_count} 轮数据同步...")
                print("-" * 75)

                run_radar_cycle(is_first_run=False, round_idx=round_count)

                end_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
