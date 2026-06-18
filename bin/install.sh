#!/usr/bin/env bash
# =============================================================================
# install.sh — Mise en place complète du projet "scripts" (Plex / iTunes / audio)
#
# Ce script fait TOUT, automatiquement :
#   1. Vérifie l'OS et les prérequis système
#   2. Installe les paquets apt manquants (sudo)
#   3. Crée le virtualenv .venv/ et installe les dépendances pip
#   4. Rend exécutables tous les .sh / .py du projet
#   5. Crée les répertoires de logs et de files d'attente
#   6. Installe ET active les timers systemd utilisateur
#   7. Active le linger pour que les timers tournent sans session
#
# Usage :
#   ./install.sh                 # Installation complète automatique
#   ./install.sh --interactive   # Demander confirmation à chaque étape
#   ./install.sh --no-systemd    # Ne pas installer/activer les timers
#   ./install.sh --no-apt        # Ne pas installer les paquets système
#   ./install.sh --no-linger     # Ne pas activer le linger (pas de sudo)
#   ./install.sh --help          # Afficher l'aide
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
        *) warn "Option inconnue: $arg" ;;
    esac
done

ask() {
    local prompt="$1"; local default="${2:-y}"
    if [[ $INTERACTIVE -eq 0 ]]; then return 0; fi
    local yn
    read -r -p "$prompt [$default] " yn
    yn="${yn:-$default}"
    [[ "$yn" =~ ^[yYoO]$ ]]
}

# --------------------------- 0. Pré-vérifications ----------------------------
section "Vérification de l'environnement"

if [[ "$(uname -s)" != "Linux" ]]; then
    err "Ce projet cible Linux. OS détecté: $(uname -s)"; exit 1
fi
ok "Linux : $(lsb_release -ds 2>/dev/null || grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '"')"

if [[ $EUID -eq 0 ]]; then
    err "Ne pas exécuter en root. Relancez en utilisateur normal."; exit 1
fi

if ! command -v sudo &>/dev/null; then
    warn "sudo absent : l'étape apt sera sautée."; SKIP_APT=1
fi

# --------------------------- 1. Paquets système ------------------------------
APT_PACKAGES=(python3 python3-venv python3-pip sqlite3 ffmpeg id3v2 libnotify-bin jq curl rsync git)

if [[ $SKIP_APT -eq 0 ]]; then
    section "Paquets système (apt)"
    MISSING=()
    for pkg in "${APT_PACKAGES[@]}"; do
        dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
    done
    if [[ ${#MISSING[@]} -eq 0 ]]; then
        ok "Tous les paquets apt requis sont déjà installés."
    else
        warn "Paquets manquants : ${MISSING[*]}"
        if ask "Installer les paquets manquants ?" "y"; then
            sudo apt update
            sudo apt install -y "${MISSING[@]}"
            ok "Paquets installés."
        else
            warn "Installation apt ignorée."
        fi
    fi
    if command -v songrec &>/dev/null; then
        ok "songrec détecté : $(command -v songrec)"
    elif apt-cache show songrec &>/dev/null; then
        if ask "Installer songrec (requis pour le workflow 2⭐) ?" "y"; then
            sudo apt install -y songrec
            ok "songrec installé."
        else
            warn "songrec non installé : le workflow 2⭐ restera incomplet."
        fi
    else
        warn "songrec non disponible via apt sur cette distribution. Installez-le manuellement."
    fi
else
    info "Étape apt ignorée (--no-apt)."
fi

# --------------------------- 2. Virtualenv Python ----------------------------
section "Environnement virtuel Python (.venv)"
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"; ok "Venv créé : $VENV_DIR"
else
    ok "Venv existant : $VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip setuptools wheel

# --------------------------- 3. Dépendances pip ------------------------------
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    cat > "$REQUIREMENTS_FILE" <<'EOF'
# Dépendances Python du projet scripts/
mutagen>=1.47
plexapi>=4.15
requests>=2.31
EOF
    ok "requirements.txt créé."
fi
pip install --quiet -r "$REQUIREMENTS_FILE"
ok "Dépendances Python installées."

# --------------------------- 4. Permissions ----------------------------------
section "Permissions des scripts"
find "$SCRIPT_DIR" -type f \( -name "*.sh" -o -name "*.py" \) \
    -not -path "*/.venv/*" -not -path "*/.git/*" -exec chmod +x {} +
ok "Tous les .sh et .py sont exécutables."

section "Installation de songrec-rename"
SONGREC_RENAME_TARGET="/usr/local/bin/songrec-rename"
if command -v sudo &>/dev/null && ask "Installer songrec-rename dans /usr/local/bin ?" "y"; then
    cat > /tmp/songrec-rename <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$SCRIPT_DIR/utils/songrec_rename_cli.py" "\$@"
EOF
    sudo install -m 755 /tmp/songrec-rename "$SONGREC_RENAME_TARGET"
    rm -f /tmp/songrec-rename
    ok "songrec-rename installé : $SONGREC_RENAME_TARGET"
else
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/songrec-rename" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$SCRIPT_DIR/utils/songrec_rename_cli.py" "\$@"
EOF
    chmod +x "$HOME/.local/bin/songrec-rename"
    ok "songrec-rename installé : $HOME/.local/bin/songrec-rename"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) warn "Ajoutez $HOME/.local/bin à votre PATH pour utiliser songrec-rename partout." ;;
    esac
fi

# --------------------------- 5. Répertoires ----------------------------------
section "Répertoires de travail"
for d in "$HOME/.plex/logs/plex_daily" "$HOME/logs/plex_ratings" "$HOME/songrec_queue"; do
    mkdir -p "$d" && ok "  $d"
done

# --------------------------- 6. systemd --------------------------------------
ENABLED_TIMERS=()
if [[ $SKIP_SYSTEMD -eq 0 ]]; then
    section "Services & timers systemd (--user)"
    SYSTEMD_SRC="$SCRIPT_DIR/systemd"
    USER_UNIT_DIR="$HOME/.config/systemd/user"

    if [[ ! -d "$SYSTEMD_SRC" ]]; then
        warn "Dossier systemd/ introuvable."
    elif ! command -v systemctl &>/dev/null; then
        warn "systemctl absent."
    else
        mkdir -p "$USER_UNIT_DIR"
        for unit in "$SYSTEMD_SRC"/*.service "$SYSTEMD_SRC"/*.timer; do
            [[ -e "$unit" ]] || continue
            cp "$unit" "$USER_UNIT_DIR/"
            ok "Copié : $(basename "$unit")"
        done
        systemctl --user daemon-reload
        ok "daemon-reload effectué."

        if ask "Activer tous les timers maintenant ?" "y"; then
            for timer in "$SYSTEMD_SRC"/*.timer; do
                [[ -e "$timer" ]] || continue
                t_name="$(basename "$timer")"
                if systemctl --user enable --now "$t_name" 2>/dev/null; then
                    ok "Activé : $t_name"
                    ENABLED_TIMERS+=("$t_name")
                else
                    warn "Échec activation : $t_name"
                fi
            done
        fi

        if [[ $SKIP_LINGER -eq 0 ]] && command -v loginctl &>/dev/null; then
            LINGER_STATE=$(loginctl show-user "$USER" 2>/dev/null | grep -E '^Linger=' | cut -d= -f2 || echo "no")
            if [[ "$LINGER_STATE" != "yes" ]]; then
                if ask "Activer le linger (sudo) pour timers hors session ?" "y"; then
                    sudo loginctl enable-linger "$USER" && ok "Linger activé."
                fi
            else
                ok "Linger déjà actif."
            fi
        fi
    fi
else
    info "Étape systemd ignorée (--no-systemd)."
fi

# --------------------------- 7. Résumé ---------------------------------------
section "Installation terminée 🎉"
cat <<EOF

${BOLD}Prochaines étapes :${NC}
  1. ${CYAN}source .venv/bin/activate${NC}
  2. ${CYAN}python3 itunes/itunes_analyzer.py --stats${NC}
  3. ${CYAN}./workflows/plex_daily_workflow.sh${NC}
  4. Tutoriel : ${CYAN}cat TUTO.md${NC}   (English: ${CYAN}TUTO_EN.md${NC})

EOF

if [[ ${#ENABLED_TIMERS[@]} -gt 0 ]]; then
    echo -e "${BOLD}Timers activés :${NC}"
    printf '   - %s\n' "${ENABLED_TIMERS[@]}"
    echo
    echo "  Lister : ${CYAN}systemctl --user list-timers${NC}"
fi
echo
ok "Tout est prêt. Bon tri musical ! 🎶"
