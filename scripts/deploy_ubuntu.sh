#!/usr/bin/env bash
set -Eeuo pipefail

# XinQing single-server deployment helper for Ubuntu 24.04.
# Run from the project root as a user with sudo access:
#   bash deploy_ubuntu.sh --domain xinqing.example.com

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAIN="_"
INSTALL_DIR="$PROJECT_DIR"
SERVICE_USER="${SUDO_USER:-$USER}"

NODE_MAJOR="20"
DISABLE_RAG="0"
SKIP_APT="0"
ENABLE_NGINX="1"
ENABLE_SYSTEMD="1"

usage() {
  cat <<'EOF'
Usage: sudo bash deploy_ubuntu.sh [options]

Options:
  --domain NAME       Public domain (default: _)
  --install-dir PATH  Project directory (default: current directory)
  --user NAME         Linux user that owns/runs the services
  --disable-rag       Start LangGraph without loading the RAG collections
  --skip-apt          Do not install apt packages
  --no-nginx          Do not write/enable the nginx site
  --no-systemd        Do not write/enable systemd units
  -h, --help          Show this help
EOF
}

log() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { echo "部署失败: $*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --domain) DOMAIN="${2:?missing value for --domain}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:?missing value for --install-dir}"; shift 2 ;;
    --user) SERVICE_USER="${2:?missing value for --user}"; shift 2 ;;
    --disable-rag) DISABLE_RAG="1"; shift ;;
    --skip-apt) SKIP_APT="1"; shift ;;
    --no-nginx) ENABLE_NGINX="0"; shift ;;
    --no-systemd) ENABLE_SYSTEMD="0"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; fail "未知参数: $1" ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || fail "此脚本只能在 Ubuntu/Linux 上运行"
[[ -f "$INSTALL_DIR/requirements.txt" ]] || fail "找不到 $INSTALL_DIR/requirements.txt"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "Linux 用户不存在: $SERVICE_USER"

if [[ "$EUID" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

if [[ "$SKIP_APT" != "1" ]]; then
  log "安装系统依赖"
  $SUDO apt-get update
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential curl nginx
fi

if ! command -v uv >/dev/null 2>&1; then
  log "安装 uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v node >/dev/null 2>&1; then
  log "安装 Node.js ${NODE_MAJOR}.x"
  curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | $SUDO -E bash -
  $SUDO apt-get install -y nodejs
fi

log "准备项目目录"
$SUDO mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs"
$SUDO chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

VENV="$INSTALL_DIR/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  log "创建 Python 虚拟环境 (uv venv)"
  uv venv "$VENV"
fi

log "安装 Python 依赖"
uv pip install --python "$VENV/bin/python" -r "$INSTALL_DIR/requirements.txt"

if [[ ! -d "$INSTALL_DIR/web/live2d_demo/node_modules" ]]; then
  log "安装 Live2D 前端依赖"
  (cd "$INSTALL_DIR/web/live2d_demo" && npm install)
fi

if [[ -d "$INSTALL_DIR/CubismSdkForWeb-5-r.5/Samples/TypeScript/Demo" && \
      ! -d "$INSTALL_DIR/CubismSdkForWeb-5-r.5/Samples/TypeScript/Demo/node_modules" ]]; then
  log "安装 Cubism Demo 依赖"
  (cd "$INSTALL_DIR/CubismSdkForWeb-5-r.5/Samples/TypeScript/Demo" && npm install)
fi

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  log "创建 .env 模板"
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
  echo "已创建 $INSTALL_DIR/.env，请先填写 DEEPSEEK_API_KEY、管理员密码和 SMTP 配置。"
else
  log "保留已有 .env，不覆盖"
fi

if [[ "$DISABLE_RAG" == "1" ]]; then
  if ! grep -q '^XINQING_DISABLE_RAG=' "$INSTALL_DIR/.env"; then
    printf '\nXINQING_DISABLE_RAG=1\n' >> "$INSTALL_DIR/.env"
  else
    sed -i 's/^XINQING_DISABLE_RAG=.*/XINQING_DISABLE_RAG=1/' "$INSTALL_DIR/.env"
  fi
fi

log "发布前端静态文件"
rm -rf "$INSTALL_DIR/dist"
mkdir -p "$INSTALL_DIR/dist/live2d_demo"
cp -a "$INSTALL_DIR/web/live2d_demo/." "$INSTALL_DIR/dist/live2d_demo/"
if [[ ! -f "$INSTALL_DIR/dist/live2d_demo/index.html" ]]; then
  fail "前端发布失败：找不到 dist/live2d_demo/index.html"
fi

if [[ "$ENABLE_SYSTEMD" == "1" ]]; then
  log "生成 systemd 服务"
  for service in data alert langgraph; do
    case "$service" in
      data) description="XinQing Data API"; command="$VENV/bin/python $INSTALL_DIR/src/xinqing/data_service.py"; after="network.target" ;;
      alert) description="XinQing Alert API"; command="$VENV/bin/python $INSTALL_DIR/src/xinqing/alert.py"; after="network.target xinqing-data.service" ;;
      langgraph) description="XinQing LangGraph"; command="$VENV/bin/langgraph dev --host 127.0.0.1 --port 2024"; after="network.target xinqing-data.service xinqing-alert.service" ;;
    esac
    $SUDO tee "/etc/systemd/system/xinqing-${service}.service" >/dev/null <<EOF
[Unit]
Description=$description
After=$after

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1
ExecStart=$command
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  done
  if [[ -f "$INSTALL_DIR/CubismSdkForWeb-5-r.5/Samples/TypeScript/Demo/package.json" ]]; then
    $SUDO tee /etc/systemd/system/xinqing-cubism.service >/dev/null <<EOF
[Unit]
Description=XinQing Cubism Live2D Demo
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR/CubismSdkForWeb-5-r.5/Samples/TypeScript/Demo
ExecStart=/usr/bin/npx vite --host 127.0.0.1 --port 8084
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  fi
  $SUDO systemctl daemon-reload
  if [[ -f "$INSTALL_DIR/CubismSdkForWeb-5-r.5/Samples/TypeScript/Demo/package.json" ]]; then
    $SUDO systemctl enable --now xinqing-cubism
  fi
  $SUDO systemctl enable --now xinqing-data xinqing-alert xinqing-langgraph
fi

if [[ "$ENABLE_NGINX" == "1" ]]; then
  log "生成 Nginx 配置"
  $SUDO tee "/etc/nginx/sites-available/xinqing" >/dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 10m;

    location / {
        root $INSTALL_DIR/dist;
        try_files \$uri \$uri/ /live2d_demo/index.html;
    }
    location /langgraph-api/ {
        proxy_pass http://127.0.0.1:2024/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
    location /data-api/ {
        proxy_pass http://127.0.0.1:8001/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    location /alert-api/ {
        proxy_pass http://127.0.0.1:5000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    location /live2d-sdk/ {
        proxy_pass http://127.0.0.1:8084/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
    }
}
EOF
  $SUDO ln -sfn /etc/nginx/sites-available/xinqing /etc/nginx/sites-enabled/xinqing
  $SUDO rm -f /etc/nginx/sites-enabled/default
  $SUDO nginx -t
  $SUDO systemctl reload nginx
fi

cat <<EOF

部署基础服务已完成。

项目目录: $INSTALL_DIR
访问地址: http://$DOMAIN/

下一步：
1. 编辑 $INSTALL_DIR/.env，填写 API Key、管理员密码、SMTP 配置。
2. 修改 web/live2d_demo/live2d-adapter.js，把 SDK 地址改成 /live2d-sdk/。
3. 将 Cubism Demo 作为 127.0.0.1:8084 服务运行，并在其 postMessage 校验中加入正式域名。
4. 配置 HTTPS：sudo certbot --nginx -d $DOMAIN
5. 查看日志：journalctl -u xinqing-langgraph -f
EOF
