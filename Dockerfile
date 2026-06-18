# =============================================================================
# Dockerfile — Plex / iTunes / Audio scripts suite
#
# Image légère Debian + Python 3.12 avec tous les outils requis par les
# scripts du projet. Les données (DB Plex, bibliothèque audio, logs) sont
# montées depuis l'hôte — rien n'est embarqué dans l'image.
#
# Build :
#   docker build -t plex-scripts .
#
# Voir docker-compose.yml pour l'exécution et les volumes.
# =============================================================================

FROM python:3.12-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Paris

# --- Paquets système ---------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 \
        ffmpeg \
        id3v2 \
        kid3-cli \
        libchromaprint-tools \
        default-jre-headless \
    libadwaita-1-0 \
    libgtk-4-1 \
    libpipewire-0.3-0 \
    libsoup-3.0-0 \
        jq \
        curl \
        rsync \
        cron \
        tzdata \
        ca-certificates \
        locales \
        fonts-noto-color-emoji \
        fonts-noto-extra \
        fonts-symbola \
    && sed -i 's/^# *\(fr_FR.UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# --- Police emoji compatible Pillow (vectorielle) ---------------------------
# NotoSansSymbols2-Regular est une police vectorielle qui rend les emojis
# en noir-et-blanc — compatible avec PIL (contrairement à NotoColorEmoji
# qui est bitmap et incompatible).
COPY docker/NotoSansSymbols2-Regular.ttf /usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf
RUN fc-cache -f 2>/dev/null || true

# --- Docker CLI (client seul, pour contrôler le daemon de l'hôte via socket) -
RUN DPKG_ARCH=$(dpkg --print-architecture) \
    && case "$DPKG_ARCH" in \
         amd64)   DOCKER_ARCH=x86_64   ;; \
         arm64)   DOCKER_ARCH=aarch64  ;; \
         armhf)   DOCKER_ARCH=armv7    ;; \
         *) echo "Arch inconnue: $DPKG_ARCH" && exit 1 ;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-27.3.1.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && chmod +x /usr/local/bin/docker \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v2.35.1/docker-compose-linux-${DOCKER_ARCH}" \
       -o /usr/local/bin/docker-compose \
    && chmod +x /usr/local/bin/docker-compose

ENV LANG=fr_FR.UTF-8 LC_ALL=fr_FR.UTF-8

# --- Utilisateur non-root ----------------------------------------------------
ARG APP_UID=1000
ARG APP_GID=1000
# DOCKER_GID doit correspondre au GID du groupe docker sur l'hôte
# (vérifier avec : getent group docker | cut -d: -f3)
ARG DOCKER_GID=132
RUN groupadd -g "${APP_GID}" app && \
    useradd  -m -u "${APP_UID}" -g app -s /bin/bash app && \
    groupadd -g "${DOCKER_GID}" docker_host 2>/dev/null || true && \
    usermod -aG docker_host app 2>/dev/null || true
ENV LANG=fr_FR.UTF-8 LC_ALL=fr_FR.UTF-8
WORKDIR /app

# --- Dépendances Python ------------------------------------------------------
COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# --- Code du projet ----------------------------------------------------------
COPY --chown=app:app . /app

# Rendre les scripts exécutables
RUN find /app -type f \( -name "*.sh" -o -name "*.py" \) \
        -not -path "*/.venv/*" -not -path "*/.git/*" \
        -exec chmod +x {} + && \
    mkdir -p /data/logs /data/songrec_queue && \
    if [ -f /app/utils/songrec-rename ]; then \
        chmod +x /app/utils/songrec-rename && \
        ln -sf /app/utils/songrec-rename /usr/local/bin/songrec-rename; \
    fi && \
    chown -R app:app /data /app

# --- Volumes standards -------------------------------------------------------
# /plex      → base Plex en lecture seule (monter ro)
# /music     → bibliothèque musicale
# /playlists → dossier de playlists M3U (optionnel)
# /data      → logs + queues (persistant)
VOLUME ["/plex", "/music", "/playlists", "/data"]

# --- Entrypoint --------------------------------------------------------------
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["help"]
