#!/usr/bin/env bash
# Shared env loader for DeepScene.
#
# Discovery order:
#   1. process env (already exported)
#   2. ~/.config/deepscene/env
#   3. ./.env only when DEEPSCENE_LOAD_LOCAL_ENV=1

[[ -n "${DEEPSCENE_ENV_LOADED:-}" ]] && return 0
DEEPSCENE_ENV_LOADED=1

umask 077

export DEEPSCENE_VERSION="${DEEPSCENE_VERSION:-0.4.0}"
export DEEPSCENE_USER_AGENT="${DEEPSCENE_USER_AGENT:-deepscene/${DEEPSCENE_VERSION}}"

_load_dotenv() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    if [[ -z "${!key:-}" ]]; then
      value="${value%\"}"
      value="${value#\"}"
      value="${value%\'}"
      value="${value#\'}"
      export "$key=$value"
    fi
  done < "$file"
}

if [[ "${DEEPSCENE_LOAD_LOCAL_ENV:-}" == "1" ]]; then
  _load_dotenv "./.env"
fi
_load_dotenv "$HOME/.config/deepscene/env"

deepscene_cache_dir() {
  local base="${DEEPSCENE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/deepscene}"

  if [[ -L "$base" ]]; then
    echo "[deepscene] refusing symlink cache dir: $base" >&2
    return 1
  fi

  mkdir -p "$base"
  chmod 700 "$base" 2>/dev/null || true

  if [[ ! -d "$base" || -L "$base" || ! -O "$base" ]]; then
    echo "[deepscene] cache dir must be a real directory owned by this user: $base" >&2
    return 1
  fi

  printf '%s\n' "$base"
}

deepscene_mktemp() {
  local prefix="$1"
  local dir
  dir="$(deepscene_cache_dir)" || return 1
  mktemp "$dir/${prefix}.XXXXXX"
}

deepscene_validate_url() {
  local url="$1"
  python3 - "$url" <<'PY'
import ipaddress
import sys
from urllib.parse import urlparse

url = sys.argv[1]
parsed = urlparse(url)
host = parsed.hostname

if parsed.scheme not in {"http", "https"}:
    print("[deepscene] URL must use http or https", file=sys.stderr)
    sys.exit(64)
if not host:
    print("[deepscene] URL must include a hostname", file=sys.stderr)
    sys.exit(64)

host_l = host.rstrip(".").lower()
if host_l in {"localhost"} or host_l.endswith(".localhost") or host_l.endswith(".local"):
    print(f"[deepscene] refusing local hostname: {host}", file=sys.stderr)
    sys.exit(64)

try:
    ip = ipaddress.ip_address(host_l.strip("[]"))
except ValueError:
    sys.exit(0)

if (
    ip.is_private
    or ip.is_loopback
    or ip.is_link_local
    or ip.is_multicast
    or ip.is_reserved
    or ip.is_unspecified
):
    print(f"[deepscene] refusing non-public IP address: {host}", file=sys.stderr)
    sys.exit(64)
PY
}

deepscene_run_with_timeout() {
  local timeout_sec="${DEEPSCENE_COMMAND_TIMEOUT_SEC:-}"
  if [[ -n "$timeout_sec" && "$timeout_sec" =~ ^[0-9]+$ && "$timeout_sec" -gt 0 ]] && command -v timeout >/dev/null 2>&1; then
    timeout "$timeout_sec" "$@"
  else
    "$@"
  fi
}

deepscene_audio_mode_check() {
  if [[ -n "${GOOGLE_AI_KEY:-}" ]]; then
    return 0
  fi
  echo "[deepscene] GOOGLE_AI_KEY is required for Gemini audio analysis." >&2
  return 1
}
