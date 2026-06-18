#!/usr/bin/env bash
# =============================================================================
# install_en.sh — Full setup for the "scripts" project (Plex / iTunes / audio)
#
# This script does EVERYTHING automatically:
#   1. Checks OS and system prerequisites
#   2. Installs missing apt packages (sudo)
#   3. Creates .venv/ and installs pip dependencies
#   4. Makes all .sh / .py files executable
#   5. Creates log and queue directories
#   6. Installs AND enables user systemd timers
#   7. Enables lingering so timers run without an active session
#
# Usage:
#   ./install_en.sh                # Full automatic install
#   ./install_en.sh --interactive  # Ask before each step
#   ./install_en.sh --no-systemd   # Skip systemd setup
#   ./install_en.sh --no-apt       # Skip apt install
#   ./install_en.sh --no-linger    # Skip linger enable
#   ./install_en.sh --help         # Show help
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}ℹ️  $*${NC}"; }
ok()      { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()     { echo -e "${RED}❌ $*${NC}" >&2; }
section() { echo -e "\n${BOLD}${CYAN}=== $* ===${NC}"; }

INTERACTIVE=0
SKIP_SYSTEMD=0
SKIP_APT=0
SKIP_LINGER=0

for arg in "$@"; do
    case "$arg" in
        --interactive|-i) INTERACTIVE=1 ;;
        --no-systemd)     SKIP_SYSTEMD=1 ;;
        --no-apt)         SKIP_APT=1 ;;
        --no-linger)      SKIP_LINGER=1 ;;
        --help|-h)
            sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) warn "Unknown option: $arg" ;;
    esac
done

ask() {
    local prompt="$1"; local default="${2:-y}"
    if [[ $INTERACTIVE -eq 0 ]]; then return 0; fi
    local yn
    read -r -p "$prompt [$default] " yn
    yn="${yn:-$default}"
    [[ "$yn" =~ ^[yY]$ ]]
}

# --------------------------- 0. Pre-checks -----------------------------------
section "Environment check"

if [[ "$(uname -s)" != "Linux" ]]; then
    err "This project targets Linux. Detected OS: $(uname -s)"; exit 1
fi
ok "Linux: $(lsb_release -ds 2>/dev/null || grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '"')"

if [[ $EUID -eq 0 ]]; then
    err "Do not run as root. Re-run as a regular user."; exit 1
fi

if ! command -v sudo &>/dev/null; then
    warn "sudo missing: apt step will be skipped."; SKIP_APT=1
fi

# --------------------------- 1. System packages ------------------------------
APT_PACKAGES=(python3 python3-venv python3-pip sqlite3 ffmpeg id3v2 libnotify-bin jq curl rsync git)

if [[ $SKIP_APT -eq 0 ]]; then
    section "System packages (apt)"
    MISSING=()
    for pkg in "${APT_PACKAGES[@]}"; do
        dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
    done
    if [[ ${#MISSING[@]} -eq 0 ]]; then
        ok "All required apt packages are already installed."
    else
        warn "Missing packages: ${MISSING[*]}"
        if ask "Install missing packages?" "y"; then
            sudo apt update
            sudo apt install -y "${MISSING[@]}"
            ok "Packages installed."
        else
            warn "apt install skipped."
        fi
    fi
    if command -v songrec &>/dev/null; then
        ok "songrec found: $(command -v songrec)"
    elif apt-cache show songrec &>/dev/null; then
        if ask "Install songrec (required for the 2★ workflow)?" "y"; then
            sudo apt install -y songrec
            ok "songrec installed."
        else
            warn "songrec not installed: the 2★ workflow will stay incomplete."
        fi
    else
        warn "songrec not available via apt on this distribution. Install it manually."
    fi
else
    info "apt step skipped (--no-apt)."
fi

# --------------------------- 2. Python virtualenv ----------------------------
section "Python virtual environment (.venv)"
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"; ok "Venv created: $VENV_DIR"
else
    ok "Existing venv: $VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip setuptools wheel

# --------------------------- 3. pip requirements -----------------------------
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    cat > "$REQUIREMENTS_FILE" <<'EOF'
# Python dependencies for the scripts/ project
mutagen>=1.47
plexapi>=4.15
requests>=2.31
EOF
    ok "requirements.txt created."
fi
pip install --quiet -r "$REQUIREMENTS_FILE"
ok "Python dependencies installed."

# --------------------------- 4. Permissions ----------------------------------
section "Script permissions"
find "$SCRIPT_DIR" -type f \( -name "*.sh" -o -name "*.py" \) \
    -not -path "*/.venv/*" -not -path "*/.git/*" -exec chmod +x {} +
ok "All .sh and .py files are executable."

section "Install songrec-rename"
SONGREC_RENAME_TARGET="/usr/local/bin/songrec-rename"
if command -v sudo &>/dev/null && ask "Install songrec-rename into /usr/local/bin?" "y"; then
    cat > /tmp/songrec-rename <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$SCRIPT_DIR/utils/songrec_rename_cli.py" "\$@"
EOF
    sudo install -m 755 /tmp/songrec-rename "$SONGREC_RENAME_TARGET"
    rm -f /tmp/songrec-rename
    ok "songrec-rename installed: $SONGREC_RENAME_TARGET"
else
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/songrec-rename" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$SCRIPT_DIR/utils/songrec_rename_cli.py" "\$@"
EOF
    chmod +x "$HOME/.local/bin/songrec-rename"
    ok "songrec-rename installed: $HOME/.local/bin/songrec-rename"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) warn "Add $HOME/.local/bin to your PATH to use songrec-rename everywhere." ;;
    esac
fi

# --------------------------- 5. Directories ----------------------------------
section "Working directories"
for d in "$HOME/.plex/logs/plex_daily" "$HOME/logs/plex_ratings" "$HOME/songrec_queue"; do
    mkdir -p "$d" && ok "  $d"
done

# --------------------------- 6. systemd --------------------------------------
ENABLED_TIMERS=()
if [[ $SKIP_SYSTEMD -eq 0 ]]; then
    section "systemd services & timers (--user)"
    SYSTEMD_SRC="$SCRIPT_DIR/systemd"
    USER_UNIT_DIR="$HOME/.config/systemd/user"

    if [[ ! -d "$SYSTEMD_SRC" ]]; then
        warn "systemd/ folder missing."
    elif ! command -v systemctl &>/dev/null; then
        warn "systemctl not available."
    else
        mkdir -p "$USER_UNIT_DIR"
        for unit in "$SYSTEMD_SRC"/*.service "$SYSTEMD_SRC"/*.timer; do
            [[ -e "$unit" ]] || continue
            cp "$unit" "$USER_UNIT_DIR/"
            ok "Copied: $(basename "$unit")"
        done
        systemctl --user daemon-reload
        ok "daemon-reload done."

        if ask "Enable all timers now?" "y"; then
            for timer in "$SYSTEMD_SRC"/*.timer; do
                [[ -e "$timer" ]] || continue
                t_name="$(basename "$timer")"
                if systemctl --user enable --now "$t_name" 2>/dev/null; then
                    ok "Enabled: $t_name"
                    ENABLED_TIMERS+=("$t_name")
                else
                    warn "Failed to enable: $t_name"
                fi
            done
        fi

        if [[ $SKIP_LINGER -eq 0 ]] && command -v loginctl &>/dev/null; then
            LINGER_STATE=$(loginctl show-user "$USER" 2>/dev/null | grep -E '^Linger=' | cut -d= -f2 || echo "no")
            if [[ "$LINGER_STATE" != "yes" ]]; then
                if ask "Enable lingering (sudo) so timers run without an active session?" "y"; then
                    sudo loginctl enable-linger "$USER" && ok "Lingering enabled."
                fi
            else
                ok "Lingering already enabled."
            fi
        fi
    fi
else
    info "systemd step skipped (--no-systemd)."
fi

# --------------------------- 7. Summary --------------------------------------
section "Installation complete 🎉"
cat <<EOF

${BOLD}Next steps:${NC}
  1. ${CYAN}source .venv/bin/activate${NC}
  2. ${CYAN}python3 itunes/itunes_analyzer.py --stats${NC}
  3. ${CYAN}./workflows/plex_daily_workflow.sh${NC}
  4. Tutorial: ${CYAN}cat TUTO_EN.md${NC}   (Français : ${CYAN}TUTO.md${NC})

EOF

if [[ ${#ENABLED_TIMERS[@]} -gt 0 ]]; then
    echo -e "${BOLD}Enabled timers:${NC}"
    printf '   - %s\n' "${ENABLED_TIMERS[@]}"
    echo
    echo "  List:  ${CYAN}systemctl --user list-timers${NC}"
    echo "  Logs:  ${CYAN}journalctl --user -u <service> -f${NC}"
fi
echo
ok "All set. Happy music sorting! 🎶"
