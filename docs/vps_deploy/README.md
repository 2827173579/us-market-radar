# VPS 部署脚本（备用）

> ⚠️ 当前生产环境仍使用 **GitHub Actions 云端轮询 + GitHub Pages**，本目录脚本仅作为**日后迁移海外 Ubuntu VPS 的备用方案**，尚未启用。
> 切换前请先阅读 `AGENTS.md` §5.2 的《切换到海外 Ubuntu VPS 的安全步骤》，务必先停旧电脑守护进程并禁用 Actions 雷达 workflow，避免双写冲突。

## 文件说明

| 文件 | 用途 |
|---|---|
| `setup_server.sh` | Ubuntu 22.04/24.04 VPS 一键初始化：2GB Swap、系统依赖、Python venv、代码拉取、systemd 常驻服务（`us-market-radar.service`，崩溃自动拉起、开机自启）。 |
| `一键更新云服务器.bat` | Windows 本地 → VPS 代码热更新：tar 打包（排除 `.git/.env/.venv/研究目录`）→ scp 上传 → ssh 解压 → `systemctl restart`。不会覆盖服务器 `.env`。 |

## 基本用法

```bash
# 1) 在 VPS 上（有 sudo 权限的普通用户）
git clone https://github.com/2827173579/us-market-radar.git
cd us-market-radar
sudo bash docs/vps_deploy/setup_server.sh

# 2) 按提示编辑 /opt/us-market-radar/.env（FINNHUB_API_KEY / DISCORD_* / GH_TOKEN 等）
sudo -u ubuntu nano /opt/us-market-radar/.env
sudo systemctl restart us-market-radar

# 3) 观察日志
journalctl -u us-market-radar -f
```

```bat
:: 4) 本地 Windows 热更新：先修改 docs\vps_deploy\一键更新云服务器.bat 顶部 5 个配置，
::    然后双击运行（需要 Windows OpenSSH 客户端 + 内置 tar）
```

## 前置条件清单

- [ ] 旧电脑守护进程已停止；
- [ ] GitHub Actions `US Market Radar 24-7 Engine` 已 Disable（保留 `pages-build-deployment`）；
- [ ] VPS 上 `.env` 已填 `GH_TOKEN`（有本仓库 contents:write 权限的 PAT）；
- [ ] VPS 网络能访问 Finnhub / yfinance / Discord / PushPlus；
- [ ] （可选）如需公网访问 8765 看盘 API，请自行加 nginx + HTTPS + 认证，切勿裸奔。
