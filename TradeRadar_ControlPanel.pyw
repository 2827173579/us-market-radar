#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TradeRadar_ControlPanel.pyw - 美股量化雷达与 Discord 哨兵可视化控制中心
- 支持一键启动/停止后台 60 秒轮询守护与 Web 看盘服务
- 支持手动即时刷新与 Discord / 微信预警镜像测试
- 支持开机自启动一键安装与解除
- 支持一键打包新电脑完整无缝迁移包
"""

import os
import sys
import time
import json
import socket
import threading
import subprocess
import webbrowser
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# 确保脚本工作目录为当前项目目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

# 确保 Windows 终端编码为 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

STARTUP_FOLDER = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
STARTUP_VBS_PATH = os.path.join(STARTUP_FOLDER, "TradeRadar_AutoStart.vbs")


def get_local_lan_ip():
    """获取本机局域网 Wi-Fi IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def is_radar_port_listening(port=8765):
    """检测 8765 看盘与 API 端口是否处于监听状态"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except Exception:
        return False


def get_running_radar_pids():
    """精确获取正在运行 integrated_market_radar.py 的进程 PID 列表"""
    pids = []
    try:
        cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like \'*integrated_market_radar.py*\' } | Select-Object -ExpandProperty ProcessId"'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
    except Exception:
        pass
    return pids


def is_autostart_enabled():
    """检查开机自启动脚本是否已安装在系统启动目录"""
    return os.path.exists(STARTUP_VBS_PATH)


class TradeRadarControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("⚡ 美股全周期量化雷达 · 本地控制中心 (Trade Radar Control Panel)")
        self.root.geometry("680 x 620".replace(" ", ""))
        self.root.minsize(640, 580)
        self.root.configure(bg="#0d1117")

        # 样式配置
        self.setup_styles()

        # 构建 UI
        self.build_ui()

        # 启动状态刷新定时器 (每 2.5 秒探测一次后台进程)
        self.refresh_status_loop()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#0d1117")
        style.configure("Card.TFrame", background="#161b22", relief="flat")
        style.configure("Header.TLabel", background="#0d1117", foreground="#f0f6fc", font=("Segoe UI", 14, "bold"))
        style.configure("Sub.TLabel", background="#0d1117", foreground="#8b949e", font=("Segoe UI", 9))
        style.configure("Status.TLabel", background="#161b22", foreground="#58a6ff", font=("Segoe UI", 10, "bold"))
        style.configure("Detail.TLabel", background="#161b22", foreground="#c9d1d9", font=("Segoe UI", 9))

    def build_ui(self):
        # 1. 顶部 Header
        header_frame = ttk.Frame(self.root, padding="16 12 16 8")
        header_frame.pack(fill="x")

        ttk.Label(header_frame, text="⚡ 美股量化雷达与 Discord 哨兵 · 本地控制台", style="Header.TLabel").pack(anchor="w")
        ttk.Label(header_frame, text="全周期量化终端 · 60s极速实盘轮询 · 每日4次定时哨兵 · 双通道预警镜像", style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        # 2. 状态卡片
        self.card = ttk.Frame(self.root, style="Card.TFrame", padding="14")
        self.card.pack(fill="x", padx=16, pady=6)

        status_row = tk.Frame(self.card, bg="#161b22")
        status_row.pack(fill="x")

        self.lbl_status_badge = tk.Label(status_row, text="● 检测中...", font=("Segoe UI", 11, "bold"), fg="#8b949e", bg="#161b22")
        self.lbl_status_badge.pack(side="left")

        self.lbl_pid_info = tk.Label(status_row, text="", font=("Consolas", 9), fg="#8b949e", bg="#161b22")
        self.lbl_pid_info.pack(side="right")

        self.lbl_info = tk.Label(
            self.card,
            text="本地看盘: http://127.0.0.1:8765  |  局域网: http://--:8765\n开机自启动状态: 检测中...",
            font=("Segoe UI", 9),
            fg="#8b949e",
            bg="#161b22",
            justify="left"
        )
        self.lbl_info.pack(anchor="w", pady=(8, 0))

        # 3. 核心控制按钮网格
        btn_frame = tk.Frame(self.root, bg="#0d1117", padx=16, pady=8)
        btn_frame.pack(fill="x")

        btn_grid = [
            ("🟢 启动后台轮询守护", self.on_start_daemon, "#238636", "#ffffff", "在后台静默运行 60s 轮询与看盘服务 (不弹黑框)"),
            ("🔴 停止所有后台进程", self.on_stop_daemon, "#da3633", "#ffffff", "一键安全终止所有后台轮询进程，释放 8765 端口"),
            ("🔄 手动即时刷新一次", self.on_scan_once, "#1f6feb", "#ffffff", "立即抓取最新全市场报价并刷新看板 (一次性)"),
            ("📢 发送预警镜像测试", self.on_test_alert, "#8957e5", "#ffffff", "向微信与 Discord 镜像频道发送一条即时测试卡片"),
            ("🛡️ 立即发送量化哨兵", self.on_test_sentinel, "#d29922", "#ffffff", "向 Discord 发送五大梯队共振巡检战报"),
            ("🌐 打开富途看盘网页", self.on_open_browser, "#30363d", "#f0f6fc", "用默认浏览器打开富途牛牛专业全周期看盘终端"),
        ]

        for idx, (text, cmd, bg_c, fg_c, tip) in enumerate(btn_grid):
            r, c = divmod(idx, 2)
            btn = tk.Button(
                btn_frame,
                text=text,
                command=cmd,
                bg=bg_c,
                fg=fg_c,
                activebackground=bg_c,
                activeforeground=fg_c,
                relief="flat",
                font=("Segoe UI", 9, "bold"),
                cursor="hand2",
                pady=7
            )
            btn.grid(row=r, column=c, padx=6, pady=5, sticky="ew")
            btn_frame.columnconfigure(c, weight=1)

        # 4. 辅助工具栏 (开机自启与迁移打包)
        tool_frame = tk.Frame(self.root, bg="#0d1117", padx=16, pady=4)
        tool_frame.pack(fill="x")

        self.btn_autostart = tk.Button(
            tool_frame,
            text="🚀 设置开机自启",
            command=self.on_toggle_autostart,
            bg="#21262d",
            fg="#c9d1d9",
            relief="groove",
            font=("Segoe UI", 8),
            cursor="hand2",
            padx=10,
            pady=4
        )
        self.btn_autostart.pack(side="left", padx=(0, 8))

        self.btn_pack = tk.Button(
            tool_frame,
            text="📦 生成新电脑迁移全量包",
            command=self.on_create_migration_zip,
            bg="#21262d",
            fg="#58a6ff",
            relief="groove",
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            padx=10,
            pady=4
        )
        self.btn_pack.pack(side="left")

        btn_clear_log = tk.Button(
            tool_frame,
            text="🧹 清屏日志",
            command=self.clear_logs,
            bg="#21262d",
            fg="#8b949e",
            relief="groove",
            font=("Segoe UI", 8),
            cursor="hand2",
            padx=8,
            pady=4
        )
        btn_clear_log.pack(side="right")

        # 5. 底部实时运行日志监视器
        log_frame = tk.Frame(self.root, bg="#0d1117", padx=16, pady=(4, 12))
        log_frame.pack(fill="both", expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            bg="#010409",
            fg="#7ee787",
            insertbackground="#ffffff",
            font=("Consolas", 9),
            relief="flat",
            wrap="word",
            padx=8,
            pady=8
        )
        self.log_text.pack(fill="both", expand=True)

        self.append_log("⚡ 控制中心已就绪，当前项目路径: " + PROJECT_DIR)

    def append_log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {msg}\n")
        self.log_text.see("end")

    def clear_logs(self):
        self.log_text.delete("1.0", "end")

    def refresh_status(self):
        """定期刷新后台进程探测结果与状态展示"""
        is_listening = is_radar_port_listening(8765)
        pids = get_running_radar_pids()
        lan_ip = get_local_lan_ip()
        autostart = is_autostart_enabled()

        if is_listening or pids:
            self.lbl_status_badge.config(text="● 守护进程运行中 (ACTIVE)", fg="#3fb950")
            pid_str = f"PID: {', '.join(map(str, pids))}" if pids else "端口 8765 监听中"
            self.lbl_pid_info.config(text=pid_str, fg="#3fb950")
        else:
            self.lbl_status_badge.config(text="● 守护进程已停止 (STOPPED)", fg="#f85149")
            self.lbl_pid_info.config(text="未运行", fg="#8b949e")

        auto_desc = "已开启 (开机即在后台静默轮询)" if autostart else "未开启 (需手动在此点击启动)"
        self.lbl_info.config(
            text=f"本地看盘: http://127.0.0.1:8765  |  局域网: http://{lan_ip}:8765\n开机自启动状态: {auto_desc}",
            fg="#c9d1d9"
        )
        self.btn_autostart.config(text="❌ 取消开机自启" if autostart else "🚀 设置开机自启")

    def refresh_status_loop(self):
        self.refresh_status()
        self.root.after(2500, self.refresh_status_loop)

    def on_start_daemon(self):
        """启动后台轮询守护进程"""
        pids = get_running_radar_pids()
        if pids or is_radar_port_listening(8765):
            messagebox.showinfo("提示", "后台轮询守护已经在运行中，无需重复启动！")
            return

        def _worker():
            self.append_log("正在启动后台轮询守护进程...")
            try:
                creation_flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
                log_file = os.path.join(PROJECT_DIR, ".radar_daemon.log")
                cmd = [sys.executable, "-u", "integrated_market_radar.py", "--loop-daemon", "--interval-seconds", "60", "--duration-minutes", "0"]
                with open(log_file, "a", encoding="utf-8") as lf:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=PROJECT_DIR,
                        stdout=lf,
                        stderr=lf,
                        creationflags=creation_flags,
                        close_fds=True
                    )
                self.append_log(f"✅ 后台轮询已启动，PID: {proc.pid}")
                self.append_log(f"📱 局域网看盘地址: http://{get_local_lan_ip()}:8765")
            except Exception as e:
                self.append_log(f"❌ 启动失败: {e}")
            self.refresh_status()

        threading.Thread(target=_worker, daemon=True).start()

    def on_stop_daemon(self):
        """一键终止所有后台轮询守护进程"""
        def _worker():
            self.append_log("正在安全终止后台轮询进程...")
            pids = get_running_radar_pids()
            if not pids and not is_radar_port_listening(8765):
                self.append_log("ℹ️ 当前没有运行中的雷达进程。")
                return

            try:
                cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like \'*integrated_market_radar.py*\' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"'
                subprocess.run(cmd, shell=True, timeout=8)
                time.sleep(1)
                self.append_log("🛑 所有后台雷达进程已全部终止，端口 8765 已释放。")
            except Exception as e:
                self.append_log(f"❌ 终止异常: {e}")
            self.refresh_status()

        threading.Thread(target=_worker, daemon=True).start()

    def on_scan_once(self):
        """手动运行一次完整扫描"""
        def _worker():
            self.append_log("🚀 启动单次即时市场扫描与看板渲染...")
            try:
                cmd = [sys.executable, "-u", "integrated_market_radar.py", "--once"]
                proc = subprocess.Popen(
                    cmd,
                    cwd=PROJECT_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace"
                )
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        self.append_log(line)
                proc.wait()
                self.append_log("✅ 单次即时市场扫描已执行完成！")
            except Exception as e:
                self.append_log(f"❌ 扫描异常: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def on_test_alert(self):
        """发送测试预警消息到微信与 Discord 镜像频道"""
        def _worker():
            self.append_log("📢 正在向微信与 Discord 预警镜像频道发送测试卡片...")
            try:
                import integrated_market_radar as imr
                title = "【美股量化雷达】🔔 控制中心即时连通性测试"
                body = (
                    "> 🎯 **控制中心测试指令**：双通道即时通信正常！\n"
                    "> • **微信 ClawBot / 服务号**：已连接\n"
                    "> • **Discord 预警镜像频道**：已同步\n"
                    "> • **测试发起时间**：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                ok = imr.send_pushplus_wechat_alert(title, body)
                if ok:
                    self.append_log("✅ 测试预警卡片已成功发送！请查看微信与 Discord 预警频道。")
                else:
                    self.append_log("⚠️ 测试卡片发送返回未完全确认，请检查网络或 .env 配置。")
            except Exception as e:
                self.append_log(f"❌ 发送测试异常: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def on_test_sentinel(self):
        """立即运行量化哨兵并推送到 Discord"""
        def _worker():
            self.append_log("🛡️ 正在运行五大梯队量化哨兵扫描并推送到 Discord...")
            try:
                cmd = [sys.executable, "-u", "discord_sentinel.py", "--once"]
                proc = subprocess.Popen(
                    cmd,
                    cwd=PROJECT_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace"
                )
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        self.append_log(line)
                proc.wait()
                self.append_log("✅ 量化哨兵巡检卡片已成功推送至 Discord！")
            except Exception as e:
                self.append_log(f"❌ 哨兵执行异常: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def on_open_browser(self):
        """用默认浏览器打开富途全周期量化终端"""
        url = "http://127.0.0.1:8765"
        self.append_log(f"🌐 正在打开浏览器看盘: {url}")
        webbrowser.open(url)

    def on_toggle_autostart(self):
        """安装或卸载开机自启动"""
        if is_autostart_enabled():
            try:
                os.remove(STARTUP_VBS_PATH)
                self.append_log("🗑️ 已取消开机自启动设置。")
                messagebox.showinfo("成功", "已取消开机自启动！今后开机不会自动拉起雷达后台。")
            except Exception as e:
                messagebox.showerror("错误", f"取消失败: {e}")
        else:
            try:
                vbs_content = f'''Dim ws, fso, wmi, colProcesses, proc, isRunning
Set ws = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set wmi = GetObject("winmgmts:\\\\.\\root\\cimv2")

isRunning = False
Set colProcesses = wmi.ExecQuery("Select CommandLine from Win32_Process Where Name = 'python.exe' or Name = 'pythonw.exe'")
For Each proc in colProcesses
    If Not IsNull(proc.CommandLine) Then
        If InStr(proc.CommandLine, "integrated_market_radar.py") > 0 And InStr(proc.CommandLine, "--loop-daemon") > 0 Then
            isRunning = True
            Exit For
        End If
    End If
Next

If Not isRunning Then
    Dim radarDir, logFile, cmd
    radarDir = "{PROJECT_DIR}"
    logFile = radarDir & "\\.radar_daemon.log"
    ws.CurrentDirectory = radarDir
    cmd = "cmd.exe /c python -u integrated_market_radar.py --loop-daemon --interval-seconds 60 --duration-minutes 0 > """ & logFile & """ 2>&1"
    ws.Run cmd, 0, False
End If
'''
                os.makedirs(STARTUP_FOLDER, exist_ok=True)
                with open(STARTUP_VBS_PATH, "w", encoding="gbk", errors="replace") as f:
                    f.write(vbs_content)
                self.append_log("🚀 开机自启动设置成功！已写入: " + STARTUP_VBS_PATH)
                messagebox.showinfo("成功", "开机自启动设置成功！\n每次电脑开机时，系统将在后台自动静默拉起 60 秒极速轮询与 Discord 哨兵。")
            except Exception as e:
                messagebox.showerror("错误", f"设置开机自启失败: {e}")

        self.refresh_status()

    def on_create_migration_zip(self):
        """打包生成新电脑无缝迁移全量 ZIP 包"""
        def _worker():
            self.append_log("📦 正在打包新电脑无缝迁移全量压缩包...")
            try:
                import zipfile
                desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
                zip_filename = f"TradeRadar_Migration_Package_{datetime.now().strftime('%Y%m%d')}.zip"
                zip_path = os.path.join(desktop, zip_filename)

                exclude_dirs = {".git", ".agents", "__pycache__", "build", "dist"}
                exclude_files = {".DS_Store", "Thumbs.db"}

                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, dirs, files in os.walk(PROJECT_DIR):
                        dirs[:] = [d for d in dirs if d not in exclude_dirs]
                        for f in files:
                            if f in exclude_files or f.endswith(".pyc"):
                                continue
                            abs_f = os.path.join(root, f)
                            rel_f = os.path.relpath(abs_f, PROJECT_DIR)
                            zf.write(abs_f, arcname=os.path.join("us-market-radar", rel_f))

                self.append_log(f"🎉 迁移全量包打包成功！已保存至桌面:")
                self.append_log(f"   👉 {zip_path}")
                messagebox.showinfo(
                    "打包成功",
                    f"新电脑全量迁移压缩包已生成在您的桌面！\n\n文件: {zip_filename}\n\n您只需将此压缩包拷入U盘或发至新电脑解压，按照里面的《新电脑无缝迁移指南》即可一键开跑！"
                )
            except Exception as e:
                self.append_log(f"❌ 打包失败: {e}")

        threading.Thread(target=_worker, daemon=True).start()


def main():
    root = tk.Tk()
    app = TradeRadarControlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
