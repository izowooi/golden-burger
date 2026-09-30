#!/usr/bin/env bash
# Polymarket APIs are geo-blocked from the MacBook (HTTP 451); the Mac mini runs a VPN 24/7.
# This opens a SOCKS5 tunnel through it so local tools can reach the APIs:
#   scripts/tunnel.sh            # start (idempotent)
#   curl --socks5-hostname 127.0.0.1:11080 https://gamma-api.polymarket.com/events?limit=1
#   HTTPS_PROXY=socks5h://127.0.0.1:11080 uv run polylab ...   (requests needs PySocks)
set -euo pipefail
PORT="${POLYLAB_TUNNEL_PORT:-11080}"
HOST="${POLYLAB_MACMINI:-jongwoopark@192.168.50.23}"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "tunnel already listening on $PORT"
else
  ssh -f -N -D "$PORT" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 "$HOST"
  echo "tunnel started on socks5h://127.0.0.1:$PORT"
fi
