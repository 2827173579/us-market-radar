#!/usr/bin/env bash
# ==============================================================================
# us-market-radar · Ubuntu 22.04/24.04 VPS 一键初始化脚本
# ==============================================================================
# 功能：
#   1) 系统依赖安装（git / python3 / venv / pip / tzdata 等）
#   2) 2GB Swap 挂载（含 /etc/fstab 持久化 + vm.swappiness=10）
#   3) 代码拉取 / 更新（GitHub 仓库）
#   4) Python 虚拟环境 + requirements.txt 依赖
#   5) systemd 常驻守护服务（崩溃自动拉起、开机自启）
#   6) 生成 /opt/us-market-radar/update_server.sh（服务器端手动更新脚本）
#
# 使用方式（在 Ubuntu VPS 上，以有 sudo 权限的普通用户执行）：
#   sudo bash docs/vps_deploy/setup_server.sh
#
# 可选覆盖参数（环境变量形式）：
#   sudo REPO_URL=https://github.com/2827173579/us-market-radar.git \
#        APP_DIR=/opt/us-market-radar \
#        INTERVAL=120 TZ_NAME=Asia/Seoul bash docs/vps_deploy/setup_server.sh
#
# ⚠️ 切换到 VPS 前必须先完成（见脚本末尾提示）：
#   ① 停止旧电脑上的 integrated_market_radar.py 守护进程；
#   ② 禁用 GitHub Actions 的 "US Market Radar 24-7 Engine" workflow，
#      防止 Actions 与 VPS 同时 commit/push 撞车（Pages 部署 workflow 请保留）。
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# 可配置项（环境变量优先）
# ------------------------------------------------------------------------------
APP_DIR="${APP_DIR:-/opt/us-market-radar}"
REPO_URL="${REPO_URL:-https://github.com/2827173579/us-market-radar.git}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-us-market-radar}"
RUN_USER="${RUN_USER:-${SUDO_USER:-}}"
INTERVAL="${INTERVAL:-120}"              # 轮询间隔秒（VPS 建议 120s，避免限流）
DURATION="${DURATION:-0}"                # 0 = 24/7 无上限长轮询
TZ_NAME="${TZ_NAME:-Asia/Seoul}"
SWAP_SIZE_MB="${SWAP_SIZE_MB:-2048}"     # 2GB Swap
VENV_DIR="$APP_DIR/.venv"
ENV_FILE="$APP_DIR/.env"
INSTALL_ML="${INSTALL_ML:-0}"            # 1 = 额外安装 lightgbm/scikit-learn

# ------------------------------------------------------------------------------
# 颜色与日志
# ------------------------------------------------------------------------------
C_RED='\033[0;31m'; C_GRN='\033[0;32m'; C_YEL='\033[1;33m'; C_BLU='\033[0;36m'; C_NC='\033[0m'
info() { echo -e "${C_BLU}[*]${C_NC} $*"; }
ok()   { echo -e "${C_GRN}[+]${C_NC} $*"; }
warn() { echo -e "${C_YEL}[!]${C_NC} $*"; }
die()  { echo -e "${C_RED}[x]${C_NC} $*" >&2; exit 1; }

as_user() {
    if [ "$(id -u)" -eq 0 ]; then
        sudo -u "$RUN_USER" "$@"
    else
        "$@"
    fi
}

# ------------------------------------------------------------------------------
# 0. 前置检查
# ------------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    die "请使用 sudo 运行：sudo bash docs/vps_deploy/setup_server.sh"
fi

if [ -z "$RUN_USER" ]; then
    if id -u ubuntu >/dev/null 2>&1; then RUN_USER="ubuntu"; else RUN_USER="root"; fi
fi
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    die "运行用户 $RUN_USER 不存在，请用 RUN_USER=xxx 指定一个已存在用户"
fi
RUN_GROUP="$(id -gn "$RUN_USER")"

info "部署配置："
echo "    APP_DIR      = $APP_DIR"
echo "    REPO_URL     = $REPO_URL"
echo "    BRANCH       = $BRANCH"
echo "    SERVICE_NAME = $SERVICE_NAME"
echo "    RUN_USER     = $RUN_USER ($RUN_GROUP)"
echo "    INTERVAL     = ${INTERVAL}s / DURATION = ${DURATION} (0=永久)"
echo "    TZ           = $TZ_NAME"
echo "    SWAP         = ${SWAP_SIZE_MB} MB"

# ------------------------------------------------------------------------------
# 1. 系统依赖
# ------------------------------------------------------------------------------
info "1/8 安装系统依赖 ..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    git curl ca-certificates tzdata sudo \
    python3 python3-venv python3-pip \
    rsync tar
ok "系统依赖安装完成"

# ------------------------------------------------------------------------------
# 2. Swap（2GB）
# ------------------------------------------------------------------------------
info "2/8 配置 Swap（${SWAP_SIZE_MB} MB）..."
if swapon --show=NAME --noheadings 2>/dev/null | grep -qx "/swapfile"; then
    ok "Swap 已存在，跳过"
else
    if fallocate -l "${SWAP_SIZE_MB}M" /swapfile 2>/dev/null; then
        :
    else
        warn "fallocate 失败，改用 dd 创建（较慢）..."
        dd if=/dev/zero of=/swapfile bs=1M count="$SWAP_SIZE_MB" status=none
    fi
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' > /etc/sysctl.d/99-radar-swap.conf
    sysctl --system >/dev/null 2>&1 || true
    ok "Swap 创建完成：$(free -h | awk '/Swap/{print $2}')"
fi

# ------------------------------------------------------------------------------
# 3. 时区
# ------------------------------------------------------------------------------
info "3/8 设置时区：$TZ_NAME ..."
timedatectl set-timezone "$TZ_NAME" 2>/dev/null || warn "时区设置失败（可忽略）"
ok "当前时区：$(timedatectl show -p Timezone --value 2>/dev/null || date +%Z)"

# ------------------------------------------------------------------------------
# 4. 代码拉取 / 更新
# ------------------------------------------------------------------------------
info "4/8 准备代码目录：$APP_DIR ..."
install -d -m 0755 -o "$RUN_USER" -g "$RUN_GROUP" "$APP_DIR"

if [ -d "$APP_DIR/.git" ]; then
    info "检测到已有 git 仓库，执行 fetch + pull --rebase ..."
    as_user git -C "$APP_DIR" config --get user.name  >/dev/null 2>&1 || as_user git -C "$APP_DIR" config user.name  "vps-radar"
    as_user git -C "$APP_DIR" config --get user.email >/dev/null 2>&1 || as_user git -C "$APP_DIR" config user.email "vps-radar@localhost"
    as_user git -C "$APP_DIR" fetch origin "$BRANCH" --quiet || warn "git fetch 失败（继续使用本地代码）"
    as_user git -C "$APP_DIR" pull --rebase --autostash origin "$BRANCH" || warn "git pull 失败（继续使用本地代码）"
elif [ -z "$(ls -A "$APP_DIR" 2>/dev/null)" ]; then
    info "目录为空，克隆仓库 ..."
    as_user git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$APP_DIR"
    as_user git -C "$APP_DIR" config user.name  "vps-radar"
    as_user git -C "$APP_DIR" config user.email "vps-radar@localhost"
else
    warn "目录非空且不是 git 仓库：跳过拉取，直接使用已有代码（适合先上传代码再初始化）"
    as_user git -C "$APP_DIR" init -q 2>/dev/null || true
    as_user git -C "$APP_DIR" config user.name  "vps-radar"
    as_user git -C "$APP_DIR" config user.email "vps-radar@localhost"
fi
ok "代码目录就绪"

# ------------------------------------------------------------------------------
# 5. Python 虚拟环境与依赖
# ------------------------------------------------------------------------------
info "5/8 创建 Python 虚拟环境并安装依赖（首次约 1~3 分钟）..."
if [ ! -x "$VENV_DIR/bin/python" ]; then
    as_user python3 -m venv "$VENV_DIR"
fi
as_user "$VENV_DIR/bin/python" -m pip install --upgrade pip wheel --quiet
as_user "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
# pyarrow 用于 data_cache/*.parquet 读写（项目缓存格式）
as_user "$VENV_DIR/bin/pip" install pyarrow
if [ "$INSTALL_ML" = "1" ]; then
    info "额外安装 ML 依赖：lightgbm / scikit-learn ..."
    as_user "$VENV_DIR/bin/pip" install lightgbm scikit-learn
fi
ok "Python 依赖安装完成：$("$VENV_DIR/bin/python" --version)"

# ------------------------------------------------------------------------------
# 6. .env 配置
# ------------------------------------------------------------------------------
info "6/8 检查 .env 配置 ..."
if [ ! -f "$ENV_FILE" ]; then
    cp "$APP_DIR/.env.example" "$ENV_FILE"
    chown "$RUN_USER:$RUN_GROUP" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    warn "已从 .env.example 生成 $ENV_FILE（占位符）"
    warn "请填入真实 FINNHUB_API_KEY / PUSHPLUS_TOKEN / DISCORD_* / GH_TOKEN 后重启服务："
    warn "    sudo -u $RUN_USER nano $ENV_FILE && sudo systemctl restart $SERVICE_NAME"
else
    chown "$RUN_USER:$RUN_GROUP" "$ENV_FILE" || true
    chmod 600 "$ENV_FILE" || true
    ok ".env 已存在（权限已修正为 600）"
fi

# ------------------------------------------------------------------------------
# 7. systemd 守护服务
# ------------------------------------------------------------------------------
info "7/8 配置 systemd 服务：$SERVICE_NAME ..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=US Market Radar 24-7 Daemon (integrated_market_radar.py --loop-daemon)
Documentation=https://github.com/2827173579/us-market-radar
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${APP_DIR}
Environment=TZ=${TZ_NAME}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
EnvironmentFile=-${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -u ${APP_DIR}/integrated_market_radar.py --loop-daemon --duration-minutes ${DURATION} --interval-seconds ${INTERVAL}
Restart=always
RestartSec=15
TimeoutStopSec=45
KillMode=mixed
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
systemctl restart "$SERVICE_NAME"
sleep 4

if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "systemd 服务已启动：$SERVICE_NAME"
else
    warn "服务未处于 active 状态，请查看日志：journalctl -u $SERVICE_NAME -n 80 --no-pager"
fi

# 服务器端手动更新脚本（供运维/远程调用）
cat > "$APP_DIR/update_server.sh" <<'EOS'
#!/usr/bin/env bash
# 服务器端手动更新：git pull --rebase + 重启守护服务
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"
git fetch origin main --quiet
git pull --rebase --autostash origin main
sudo systemctl restart us-market-radar
sleep 3
sudo systemctl --no-pager --lines=20 status us-market-radar || true
EOS
chmod 755 "$APP_DIR/update_server.sh"

# ------------------------------------------------------------------------------
# 8. 完成提示
# ------------------------------------------------------------------------------
info "8/8 初始化完成"
echo ""
echo "=================================================================================="
echo " ✅ us-market-radar VPS 部署完成"
echo "=================================================================================="
echo "  服务状态 : systemctl status $SERVICE_NAME"
echo "  实时日志 : journalctl -u $SERVICE_NAME -f"
echo "  手动重启 : sudo systemctl restart $SERVICE_NAME"
echo "  停止服务 : sudo systemctl stop $SERVICE_NAME"
echo "  代码更新 : sudo bash $APP_DIR/update_server.sh   （或本地运行 一键更新云服务器.bat）"
echo "  看盘端口 : 8765（守护进程内置 HTTP API；公网访问请自行加 nginx/HTTPS/认证，勿裸奔）"
echo ""
echo " ⚠️ 切换前检查清单（重要）："
echo "   ① 旧电脑守护进程已停止（任务管理器确认无 python integrated_market_radar.py）；"
echo "   ② GitHub Actions 的 'US Market Radar 24-7 Engine' 已 Disable；"
echo "   ③ .env 中 GH_TOKEN 已填入有本仓库 contents:write 权限的 PAT；"
echo "   ④ 本服务启动成功后，观察 2~3 轮日志确认数据抓取与 git push 均正常。"
echo ""
echo "  禁用 Actions 的命令行方式（需本机安装 gh 并登录）："
echo "    gh workflow disable \"US Market Radar 24-7 Engine\" --repo 2827173579/us-market-radar"
echo "  或网页：GitHub → Actions → US Market Radar 24-7 Engine → 右侧 ... → Disable workflow"
echo "  （请勿关闭 pages-build-deployment，博客/看板 GitHub Pages 仍需它部署）"
echo "=================================================================================="
