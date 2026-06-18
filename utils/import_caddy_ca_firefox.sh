#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRT_PATH="${BASE_DIR}/docker-data/tls/caddy-local-root.crt"
CERT_NAME="Caddy Local CA"

if [[ ! -f "$CRT_PATH" ]]; then
  echo "[i] Export de la CA locale depuis le conteneur Caddy..."
  mkdir -p "${BASE_DIR}/docker-data/tls"
  docker cp plex-scripts-webui-tls:/data/caddy/pki/authorities/local/root.crt "$CRT_PATH"
fi

if [[ ! -f "$CRT_PATH" ]]; then
  echo "[x] Certificat introuvable: $CRT_PATH"
  exit 1
fi

CERTUTIL="$(command -v certutil || true)"
if [[ -z "$CERTUTIL" ]]; then
  echo "[i] certutil non trouve, extraction locale depuis libnss3-tools..."
  NSS_DIR="${BASE_DIR}/.tools/nss"
  mkdir -p "$NSS_DIR"
  pushd "$NSS_DIR" >/dev/null
  apt-get download libnss3-tools >/dev/null
  DEB="$(ls -1 libnss3-tools_*.deb | head -n1)"
  dpkg-deb -x "$DEB" ./pkg >/dev/null
  popd >/dev/null
  CERTUTIL="${NSS_DIR}/pkg/usr/bin/certutil"
fi

if [[ ! -x "$CERTUTIL" ]]; then
  echo "[x] certutil introuvable/mauvais chemin: $CERTUTIL"
  exit 1
fi

shopt -s nullglob
profiles_raw=("$HOME"/.mozilla/firefox/*.default* "$HOME"/.mozilla/firefox/*.default-release*)
if [[ ${#profiles_raw[@]} -eq 0 ]]; then
  echo "[x] Aucun profil Firefox detecte dans $HOME/.mozilla/firefox"
  exit 1
fi

declare -A seen
profiles=()
for profile in "${profiles_raw[@]}"; do
  [[ -d "$profile" ]] || continue
  if [[ -z "${seen[$profile]:-}" ]]; then
    seen[$profile]=1
    profiles+=("$profile")
  fi
done

echo "[i] Import de la CA dans ${#profiles[@]} profil(s) Firefox..."
for profile in "${profiles[@]}"; do
  [[ -d "$profile" ]] || continue
  "$CERTUTIL" -A -n "$CERT_NAME" -t "C,," -i "$CRT_PATH" -d "sql:$profile" 2>/dev/null \
    || "$CERTUTIL" -M -n "$CERT_NAME" -t "C,," -d "sql:$profile" >/dev/null 2>&1 \
    || true
  echo "[ok] $profile"
  "$CERTUTIL" -L -d "sql:$profile" | grep -F "$CERT_NAME" >/dev/null || {
    echo "[x] Echec import pour $profile"
    exit 1
  }
done

echo "[ok] CA locale Caddy importee. Redemarrer Firefox puis tester https://localhost:9443"
