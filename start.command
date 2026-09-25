#!/bin/bash
# AURIX launcher for macOS. Detects the Mac and starts the model server + AURIX.
# ===== settings (edit these) =====
NGL=99            # GPU layers for every model
CTX=8192          # context size
PORT=8080         # llama-server (models)
AURIX_PORT=8765   # AURIX (open this one)
MAX_MODELS=auto   # models kept in memory at once; auto = 2 with 12 GB+ RAM, else 1
TOOLS=""          # llama-server's own tools stay off; AURIX's permission manager is the gatekeeper
# =================================

ROOT="$(cd "$(dirname "$0")" && pwd)"
ARCH="$(uname -m)"
if [ "$ARCH" = "arm64" ]; then BINDIR="$ROOT/bin/mac"; else BINDIR="$ROOT/bin/mac-x64"; fi
BIN="$BINDIR/llama-server"
UV="$ROOT/bin/mac/uv"; [ -x "$UV" ] || UV="$(command -v uv)"
MODELS="$ROOT/models"
LOG="$ROOT/data/logs/server.log"
ALOG="$ROOT/data/logs/aurix.log"
mkdir -p "$ROOT/data/logs" "$ROOT/data/workspace"

RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
if [ "$MAX_MODELS" = "auto" ]; then if [ "$RAM_GB" -ge 12 ]; then MAX_MODELS=2; else MAX_MODELS=1; fi; fi
echo "This Mac: $ARCH, ${RAM_GB} GB RAM -> keeping up to $MAX_MODELS model(s) in memory"

xattr -dr com.apple.quarantine "$BINDIR" "$ROOT/bin/mac" 2>/dev/null
chmod +x "$BIN" "$UV" 2>/dev/null

[ -f "$BIN" ] || { echo "[x] llama-server not found in ${BINDIR#$ROOT/} (Intel Macs need the macos-x64 build there)"; read -n1; exit 1; }
[ -n "$UV" ] && [ -f "$UV" ] || { echo "[x] uv not found in bin/mac"; read -n1; exit 1; }

# macOS writes hidden "._" metadata files on exFAT drives; llama-server would list them as models
find "$MODELS" -name '._*' -delete 2>/dev/null
FOUND=$(find "$MODELS" -maxdepth 2 -name '*.gguf' ! -name 'mmproj*' 2>/dev/null)
[ -n "$FOUND" ] || { echo "[x] No .gguf models in models/"; read -n1; exit 1; }
echo "Models found:"
echo "$FOUND" | while read -r f; do
  d="$(dirname "$f")"; v=""; ls "$d"/mmproj*.gguf >/dev/null 2>&1 && [ "$d" != "$MODELS" ] && v="  (vision)"
  echo "  - $(basename "$f" .gguf)$v"
done

EXTRA=()
[ -n "$TOOLS" ] && EXTRA+=(--tools "$TOOLS")
[ -f "$ROOT/mcp.json" ] && EXTRA+=(--mcp-servers-config "$ROOT/mcp.json")

echo "Starting model server..."
cd "$ROOT/data/workspace"
"$BIN" --models-dir "$MODELS" --models-max $MAX_MODELS -ngl $NGL -c $CTX --load-mode none --jinja \
  "${EXTRA[@]}" --host 127.0.0.1 --port $PORT > "$LOG" 2>&1 &
PID=$!

echo "Starting AURIX (the first run downloads Python and packages once, a few minutes)..."
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
"$UV" run --quiet --no-project --python 3.12 --with-requirements aurix/requirements.txt \
  python -m aurix > "$ALOG" 2>&1 &
APID=$!

cleanup() { kill $APID $PID 2>/dev/null; pkill -f "python -m aurix" 2>/dev/null; pkill -f "$BIN" 2>/dev/null; }
trap cleanup EXIT

until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/health)" = "200" ]; do
  kill -0 $PID 2>/dev/null || { echo "[x] Model server stopped. Last log lines:"; tail -n 15 "$LOG"; read -n1; exit 1; }
  sleep 1
done
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$AURIX_PORT/api/health)" = "200" ]; do
  kill -0 $APID 2>/dev/null || { echo "[x] AURIX stopped. Last log lines:"; tail -n 20 "$ALOG"; read -n1; exit 1; }
  sleep 2
done

echo "Ready. Opening AURIX in your browser..."
open "http://127.0.0.1:$AURIX_PORT"
echo "Background tasks keep running while this window is open."
echo
read -p "Press Enter here to STOP everything."
cleanup
echo "Stopped. You can safely eject the pendrive."
