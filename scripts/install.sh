#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# easy-tdx 本地安装脚本
#
# 用法:
#   ./scripts/install.sh           # 完整安装（Python 库 + Web 依赖 + 前端构建）
#   ./scripts/install.sh --dev     # 开发模式安装（可编辑 + dev 工具链 + 前端）
#   ./scripts/install.sh --no-ui   # 跳过前端构建，仅安装 Python 包
#
# 前置条件:
#   - Python >= 3.10（python3 --version 检查）
#   - Node.js >= 18 + npm（仅 --no-ui 时可跳过）
#
# 安装完成后启动 Web 服务:
#   easy-tdx serve --host 0.0.0.0 --port 8000
# ---------------------------------------------------------------------------

set -euo pipefail

# --- 解析参数 ---------------------------------------------------------------

INSTALL_MODE="full"
BUILD_UI=true
VENV_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)
      INSTALL_MODE="dev"
      shift
      ;;
    --no-ui)
      BUILD_UI=false
      shift
      ;;
    --venv)
      VENV_DIR="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "错误: 未知参数 '$1'（用 --help 查看用法）" >&2
      exit 1
      ;;
  esac
done

# --- 路径与颜色 -------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -t 1 ]]; then
  GREEN='\033[0;32m'
  YELLOW='\033[0;33m'
  RED='\033[0;31m'
  CYAN='\033[0;36m'
  NC='\033[0m'
else
  GREEN='' YELLOW='' RED='' CYAN='' NC=''
fi

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# --- 前置检查 ----------------------------------------------------------------

info "easy-tdx 本地安装脚本"
echo "  项目路径: $PROJECT_ROOT"
echo "  安装模式: $INSTALL_MODE"
echo "  构建前端: $BUILD_UI"
if [[ -n "$VENV_DIR" ]]; then
  echo "  虚拟环境: $VENV_DIR"
fi
echo ""

# Python
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
  fail "未找到 Python（$PYTHON）。请先安装 Python >= 3.10"
fi
PY_VERSION="$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
info "检测到 Python $PY_VERSION"
[[ "$PY_VERSION" < "3.10" ]] && fail "Python 版本过低（$PY_VERSION），需要 >= 3.10"

# --- 虚拟环境（可选） --------------------------------------------------------

if [[ -n "$VENV_DIR" ]]; then
  info "创建虚拟环境: $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  PYTHON="$(which python3)"
  info "已激活虚拟环境，Python: $PYTHON"
fi

# 升级 pip
info "升级 pip..."
"$PYTHON" -m pip install --upgrade pip --quiet

# --- 安装 Python 包 ---------------------------------------------------------

if [[ "$INSTALL_MODE" == "dev" ]]; then
  info "开发模式安装（editable + dev + web 依赖）..."
  "$PYTHON" -m pip install -e ".[dev,web]"
else
  info "正式安装（含 web 依赖）..."
  "$PYTHON" -m pip install ".[web]"
fi
ok "Python 包安装完成"

# --- 验证 CLI 入口 ----------------------------------------------------------

info "验证 CLI 入口..."
if "$PYTHON" -c "import easy_tdx; print(easy_tdx.__version__)" >/dev/null 2>&1; then
  VER="$("$PYTHON" -c "import easy_tdx; print(easy_tdx.__version__)")"
  ok "easy-tdx v$VER 已安装"
else
  warn "无法导入 easy_tdx，请检查安装日志"
fi

# --- 前端构建 ---------------------------------------------------------------

if [[ "$BUILD_UI" == true ]]; then
  WEB_UI_DIR="$PROJECT_ROOT/web-ui"
  if [[ -d "$WEB_UI_DIR" ]]; then
    info "检测到前端项目: $WEB_UI_DIR"

    if ! command -v npm &>/dev/null; then
      warn "未找到 npm，跳过前端构建"
      warn "如需 Web UI，请先安装 Node.js >= 18（https://nodejs.org/）"
    else
      NODE_VER="$(npm --version)"
      info "npm 版本: $NODE_VER"

      info "安装前端依赖..."
      (cd "$WEB_UI_DIR" && npm install)

      info "构建前端..."
      (cd "$WEB_UI_DIR" && npm run build)

      if [[ -d "$WEB_UI_DIR/dist" ]]; then
        ok "前端构建完成 → $WEB_UI_DIR/dist"
      else
        warn "前端构建完成但 dist 目录不存在，请检查构建日志"
      fi
    fi
  else
    warn "未找到 web-ui 目录，跳过前端构建"
  fi
else
  info "跳过前端构建（--no-ui）"
fi

# --- 完成提示 ----------------------------------------------------------------

echo ""
ok "=========================================="
ok "  easy-tdx 安装完成！"
ok "=========================================="
echo ""
echo "常用命令:"
echo "  easy-tdx serve                      启动 Web API 服务器 (端口 8000)"
echo "  easy-tdx serve --port 9000          指定端口"
echo "  easy-tdx serve --reload              开发模式（热重载）"
echo "  easy-tdx --help                     查看所有 CLI 命令"
echo ""
if [[ "$INSTALL_MODE" == "dev" ]]; then
  echo "开发工具:"
  echo "  ruff check .                        Lint 检查"
  echo "  mypy src/easy_tdx                   类型检查"
  echo "  pytest                              运行测试"
  echo ""
fi
