#!/usr/bin/env bash
# Start the local-only EngineAI services.  No public binding, TLS or external
# connector is enabled here.  Run from WSL after activating the eng environment:
#   cd ~/engineai && ./start_local_services.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${ENGINEAI_PYTHON:-$(command -v python)}"
NODE="${ENGINEAI_NODE:-$HOME/.local/opt/node/bin/node}"
LOGDIR="$ROOT/out/services"
mkdir -p "$LOGDIR"

start() {
  local name="$1" port="$2" script="$3"
  if curl --max-time 1 -fsS "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
    echo "$name already responds on http://127.0.0.1:${port}"
    return
  fi
  nohup "$PYTHON" "$ROOT/$script" --host 127.0.0.1 --port "$port" >"$LOGDIR/$name.log" 2>&1 &
  echo "$!" >"$LOGDIR/$name.pid"
  sleep 0.4
  if curl --max-time 2 -fsS "http://127.0.0.1:${port}/api/health" >/dev/null; then
    echo "$name started: http://127.0.0.1:${port}"
  else
    echo "$name did not become healthy; inspect $LOGDIR/$name.log" >&2
    return 1
  fi
}

start_node() {
  local name="$1" port="$2" script="$3"
  if curl --max-time 1 -fsS "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
    echo "$name already responds on http://127.0.0.1:${port}"
    return
  fi
  if [[ ! -x "$NODE" ]]; then
    echo "$name needs Node.js at $NODE; see browser_worker/README.md" >&2
    return 1
  fi
  nohup setsid "$NODE" "$ROOT/$script" </dev/null >"$LOGDIR/$name.log" 2>&1 &
  echo "$!" >"$LOGDIR/$name.pid"
  sleep 0.6
  if curl --max-time 4 -fsS "http://127.0.0.1:${port}/api/health" >/dev/null; then
    echo "$name started: http://127.0.0.1:${port}"
  else
    echo "$name did not become healthy; inspect $LOGDIR/$name.log" >&2
    return 1
  fi
}

start live_workspace 8101 live_workspace.py
start live_sandbox 8102 live_sandbox.py
start project_hub 8103 project_hub.py
start assurance_chat 8104 assurance_chat.py
start_node browser_worker 8105 browser_worker/server.mjs

echo "All services are bound to localhost only.  Do not expose them until authentication, TLS and a reverse-proxy review are complete."
