#!/bin/bash
# AURIX launcher for Linux (x86_64). Run:  bash start.sh
# Picks the fastest llama.cpp build on the drive:
#   bin/linux-cuda   (NVIDIA, if nvidia-smi works)
#   bin/linux-vulkan (NVIDIA / AMD / Intel GPUs with Vulkan drivers)
#   bin/linux        (CPU, works everywhere)
NGL=99; CTX=8192; PORT=8080; AURIX_PORT=8765; MAX_MODELS=auto

ROOT="$(cd "$(dirname "$0")" && pwd)"
MODELS="$ROOT/models"; LOG="$ROOT/data/logs/server.log"; ALOG="$ROOT/data/logs/aurix.log"
mkdir -p "$ROOT/data/logs" "$ROOT/data/workspace"

# ---- detect hardware ----
RAM_GB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1048576 ))
if [ "$MAX_MODELS" = "auto" ]; then if [ "$RAM_GB" -ge 12 ]; then MAX_MODELS=2; else MAX_MODELS=1; fi; fi
GPU="no dedicated GPU driver found"; BUILD="linux"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  GPU="NVIDIA $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
  [ -f "$ROOT/bin/linux-cuda/llama-server" ] && BUILD="linux-cuda"
fi
if [ "$BUILD" = "linux" ] && ldconfig -p 2>/dev/null | grep -q 'libvulkan.so.1' && [ -f "$ROOT/bin/linux-vulkan/llama-server" ]; then
  BUILD="linux-vulkan"; [ "$GPU" = "no dedicated GPU driver found" ] && GPU="Vulkan GPU"
fi
[ "$BUILD" = "linux" ] && NGL=0
echo "This computer: $(uname -m), ${RAM_GB} GB RAM, $GPU"
echo "Using bin/$BUILD, up to $MAX_MODELS model(s) in memory"

BINDIR="$ROOT/bin/$BUILD"
UV="$ROOT/bin/linux/uv"; [ -f "$UV" ] || UV="$(command -v uv)"
[ -f "$BINDIR/llama-server" ] || { echo "[x] No llama.cpp build in bin/$BUILD (see README, Linux)"; exit 1; }
[ -n "$UV" ] && [ -f "$UV" ] || { echo "[x] Put uv in bin/linux, or install it: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

# ---- USB drives are often mounted without permission to run programs (noexec).
#      Test it; if programs can't run from the drive, run them from a temp copy. ----
chmod +x "$BINDIR/llama-server" "$UV" 2>/dev/null
if ! "$BINDIR/llama-server" --version >/dev/null 2>&1 || ! "$UV" --version >/dev/null 2>&1; then
  RUN="${TMPDIR:-/tmp}/aurix-bin"
  echo "The drive doesn't allow running programs directly; using a temporary copy in $RUN"
  rm -rf "$RUN"; mkdir -p "$RUN/uv"
  cp -R "$BINDIR/." "$RUN/"; cp "$UV" "$RUN/uv/uv"; chmod +x "$RUN"/llama-* "$RUN/uv/uv"
  BINDIR="$RUN"; UV="$RUN/uv/uv"
fi
BIN="$BINDIR/llama-server"

# ---- models ----
find "$MODELS" -name '._*' -delete 2>/dev/null
FOUND=$(find "$MODELS" -maxdepth 2 -name '*.gguf' ! -name 'mmproj*' 2>/dev/null)
[ -n "$FOUND" ] || { echo "[x] No .gguf models in models/"; exit 1; }
echo "Models found:"; echo "$FOUND" | while read -r f; do echo "  - $(basename "$f" .gguf)"; done

# ---- start ----
echo "Starting model server..."
cd "$ROOT/data/workspace"
LD_LIBRARY_PATH="$BINDIR:$LD_LIBRARY_PATH" "$BIN" --models-dir "$MODELS" --models-max $MAX_MODELS \
  -ngl $NGL -c $CTX --load-mode none --jinja --host 127.0.0.1 --port $PORT > "$LOG" 2>&1 &
PID=$!
echo "Starting AURIX (the first run downloads Python and packages once, a few minutes)..."
cd "$ROOT"; export PYTHONDONTWRITEBYTECODE=1
"$UV" run --quiet --no-project --python 3.12 --with-requirements aurix/requirements.txt python -m aurix > "$ALOG" 2>&1 &
APID=$!
cleanup() { kill $APID $PID 2>/dev/null; pkill -f "$BIN" 2>/dev/null; }
trap cleanup EXIT

until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/health)" = "200" ]; do
  kill -0 $PID 2>/dev/null || { echo "[x] Model server stopped. Last log lines:"; tail -n 15 "$LOG"; exit 1; }; sleep 1; done
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$AURIX_PORT/api/health)" = "200" ]; do
  kill -0 $APID 2>/dev/null || { echo "[x] AURIX stopped. Last log lines:"; tail -n 20 "$ALOG"; exit 1; }; sleep 2; done

echo "Ready: http://127.0.0.1:$AURIX_PORT"
(xdg-open "http://127.0.0.1:$AURIX_PORT" >/dev/null 2>&1 &)
echo "Background tasks keep running while this window is open."
read -p "Press Enter to STOP everything."
