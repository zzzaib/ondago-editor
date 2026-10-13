#!/bin/bash
# OnDaGo Editor - one-click installer for macOS (Apple Silicon M1/M2/M3/M4... and Intel Macs)
# Installs everything INSIDE this folder: uv, Python 3.11, venv, PyTorch, FFmpeg, Ollama, all models.
# Safe to re-run: finished steps are skipped. No admin password needed.
#
#   bash install.command            normal install
#   bash install.command --llm qwen3.5:4b   pick a smaller editor model
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
TOOLS="$ROOT/tools"
mkdir -p "$TOOLS"
exec > >(tee -a "$ROOT/install.log") 2>&1

LLM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --llm) LLM="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

say()  { printf "\n\033[36m==> %s\033[0m\n" "$*"; }
ok()   { printf "    \033[32m[ok]\033[0m %s\n" "$*"; }
warn() { printf "    \033[33m[!]\033[0m %s\n" "$*"; }
die()  { printf "\n\033[31m[FAILED]\033[0m %s\n" "$*"; echo "See install.log in this folder, fix the issue and run install.command again."; exit 1; }

fetch() {  # fetch URL DEST  (resumable, retried)
  local url="$1" dest="$2"
  if [ -s "$dest" ]; then ok "cached $(basename "$dest")"; return 0; fi
  for i in 1 2 3; do
    if curl -L --fail --retry 5 --retry-delay 3 -C - -o "$dest.part" "$url"; then mv -f "$dest.part" "$dest"; return 0; fi
    warn "download attempt $i failed ($url)"; sleep 3
  done
  rm -f "$dest.part"
  return 1
}

printf "\033[35mOnDaGo Editor installer (macOS)\033[0m\n"
echo "Install folder: $ROOT"
case "$ROOT" in *" "*) warn "The folder path contains a space. That is fine on macOS, but if anything fails, move the folder to e.g. ~/OnDaGo and re-run." ;; esac

# ------------------------------------------------------------------ checks
say "Checking system"
ARCH="$(uname -m)"
MACOS="$(sw_vers -productVersion 2>/dev/null || echo 0)"
MAJOR="${MACOS%%.*}"
RAM_GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 8589934592) / 1073741824 ))
CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "$ARCH")"
ok "macOS $MACOS, $CHIP ($ARCH), ${RAM_GB} GB memory"
[ "${MAJOR:-0}" -ge 12 ] || die "macOS 12 (Monterey) or newer is needed."
FREE_GB=$(df -g "$ROOT" | awk 'NR==2 {print $4}')
if [ "${FREE_GB:-0}" -lt 25 ]; then warn "Only ${FREE_GB} GB free. About 25 GB is needed."; else ok "${FREE_GB} GB free"; fi
if [ "$ARCH" = "arm64" ]; then
  ok "Apple Silicon: Metal GPU + hardware video encoder will be used"
else
  warn "Intel Mac: works, but AI steps run on the CPU and are slower than on Apple Silicon"
fi
if [ -z "$LLM" ]; then
  if [ "$RAM_GB" -lt 16 ]; then LLM="qwen3.5:4b"; warn "Less than 16 GB memory: using the smaller editor model ($LLM)"; else LLM="qwen3.5:9b"; fi
fi
ok "Editor model: $LLM"
command -v curl >/dev/null || die "curl not found."

# ------------------------------------------------------------------ uv + python
say "Python runtime (uv)"
UV="$TOOLS/uv/uv"
if [ ! -x "$UV" ]; then
  mkdir -p "$TOOLS/uv"
  if [ "$ARCH" = "arm64" ]; then UVT="uv-aarch64-apple-darwin"; else UVT="uv-x86_64-apple-darwin"; fi
  fetch "https://github.com/astral-sh/uv/releases/latest/download/$UVT.tar.gz" "$TOOLS/uv.tar.gz" || die "Could not download uv."
  tar -xzf "$TOOLS/uv.tar.gz" -C "$TOOLS/uv" || die "Could not unpack uv."
  found="$(find "$TOOLS/uv" -type f -name uv | head -n 1)"
  [ -n "$found" ] && [ "$found" != "$UV" ] && cp "$found" "$UV"
  chmod +x "$UV"; rm -f "$TOOLS/uv.tar.gz"
fi
ok "uv ready"
export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_CACHE_DIR="$TOOLS/uv-cache"
export UV_HTTP_TIMEOUT=300
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  "$UV" venv .venv --python 3.11 --seed || die "Could not create the Python environment."
fi
ok "Python environment: $ROOT/.venv"

# ------------------------------------------------------------------ packages
say "PyTorch - large download, please wait"
CONSTR="$TOOLS/constraints.txt"
if [ "$ARCH" = "arm64" ]; then
  TORCH=(torch torchvision)
else
  # PyTorch's last Intel-Mac build is 2.2.x (it needs NumPy 1.x)
  TORCH=("torch==2.2.2" "torchvision==0.17.2" "numpy<2")
fi
okp=0
for i in 1 2 3; do
  if "$UV" pip install --python "$PY" "${TORCH[@]}"; then okp=1; break; fi
  warn "PyTorch download interrupted (attempt $i of 3), retrying..."; sleep 5
done
[ "$okp" = 1 ] || die "PyTorch install failed (network). Re-run install.command to continue."
"$PY" - > "$CONSTR" <<'EOF'
import torch, torchvision, numpy
print(f"torch=={torch.__version__}")
print(f"torchvision=={torchvision.__version__}")
if numpy.__version__.startswith("1."):
    print("numpy<2")
EOF
ok "$(head -n 1 "$CONSTR")"

say "App libraries"
"$UV" pip install --python "$PY" -r requirements.txt -c "$CONSTR" || die "Library install failed."
ok "libraries installed"

# ------------------------------------------------------------------ ffmpeg
say "FFmpeg (Apple VideoToolbox hardware encoding + libass)"
FFB="$TOOLS/ffmpeg/bin"
if [ ! -x "$FFB/ffmpeg" ] || [ ! -x "$FFB/ffprobe" ]; then
  mkdir -p "$FFB"
  if [ "$ARCH" = "arm64" ]; then FA="arm64"; else FA="amd64"; fi
  for tool in ffmpeg ffprobe; do
    if fetch "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FA/release/$tool.zip" "$TOOLS/$tool.zip"; then
      unzip -o -q "$TOOLS/$tool.zip" -d "$FFB" || warn "Could not unpack $tool."
      rm -f "$TOOLS/$tool.zip"
    fi
  done
  if [ ! -f "$FFB/ffmpeg" ] || [ ! -f "$FFB/ffprobe" ]; then
    # fallback: Homebrew's FFmpeg, if Homebrew is installed
    BREW="$(command -v brew || true)"
    [ -z "$BREW" ] && [ -x /opt/homebrew/bin/brew ] && BREW=/opt/homebrew/bin/brew
    [ -z "$BREW" ] && [ -x /usr/local/bin/brew ] && BREW=/usr/local/bin/brew
    [ -n "$BREW" ] || die "Could not download FFmpeg. Check your internet, or install Homebrew (brew.sh) and re-run."
    warn "Using Homebrew's FFmpeg instead"
    "$BREW" install ffmpeg || die "brew install ffmpeg failed."
    BP="$("$BREW" --prefix)/bin"
    ln -sf "$BP/ffmpeg" "$FFB/ffmpeg"; ln -sf "$BP/ffprobe" "$FFB/ffprobe"
  fi
  chmod +x "$FFB/ffmpeg" "$FFB/ffprobe" 2>/dev/null
  xattr -dr com.apple.quarantine "$FFB" 2>/dev/null || true
fi
"$FFB/ffmpeg" -hide_banner -version | head -n 1 || die "FFmpeg does not run on this Mac."
ok "ffmpeg ready"

# ------------------------------------------------------------------ ollama
say "Ollama (local AI model runtime)"
OLD="$TOOLS/ollama"
if [ ! -x "$OLD/ollama" ]; then
  mkdir -p "$OLD"
  if fetch "https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz" "$TOOLS/ollama.tgz"; then
    tar -xzf "$TOOLS/ollama.tgz" -C "$OLD" && rm -f "$TOOLS/ollama.tgz"
  fi
  if [ ! -x "$OLD/ollama" ]; then
    warn "Trying the Ollama app download instead"
    fetch "https://ollama.com/download/Ollama-darwin.zip" "$TOOLS/Ollama-darwin.zip" || die "Could not download Ollama."
    rm -rf "$TOOLS/ollama-app"; mkdir -p "$TOOLS/ollama-app"
    unzip -o -q "$TOOLS/Ollama-darwin.zip" -d "$TOOLS/ollama-app" || die "Could not unpack Ollama."
    cp -R "$TOOLS/ollama-app/Ollama.app/Contents/Resources/." "$OLD/" 2>/dev/null
    rm -rf "$TOOLS/ollama-app" "$TOOLS/Ollama-darwin.zip"
  fi
  chmod +x "$OLD/ollama" 2>/dev/null
  xattr -dr com.apple.quarantine "$OLD" 2>/dev/null || true
fi
[ -x "$OLD/ollama" ] || die "Ollama is missing."
ok "ollama ready"

# ------------------------------------------------------------------ models
say "Downloading AI models (Whisper, $LLM, SigLIP, background removal)"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1
# The FLUX image model needs an NVIDIA RTX GPU, so it is skipped on Mac (thumbnails use blurred/gradient backgrounds).
"$PY" installer/setup_models.py --lite --llm "$LLM" || die "Model setup reported a critical failure (see above)."

# ------------------------------------------------------------------ app launcher
say "Creating the OnDaGo Editor app"
APPDIR="$HOME/Applications/OnDaGo Editor.app"
mkdir -p "$APPDIR/Contents/MacOS" "$APPDIR/Contents/Resources"
cat > "$APPDIR/Contents/MacOS/OnDaGo" <<EOF
#!/bin/bash
cd "$ROOT"
exec "$PY" -m app.main "\$@"
EOF
chmod +x "$APPDIR/Contents/MacOS/OnDaGo"
ICON_SRC="$ROOT/app/remote/static/icon-512.png"
if [ -f "$ICON_SRC" ] && command -v iconutil >/dev/null; then
  SET="$TOOLS/OnDaGo.iconset"; rm -rf "$SET"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON_SRC" --out "$SET/icon_${s}x${s}.png" >/dev/null 2>&1
    d=$((s * 2)); sips -z $d $d "$ICON_SRC" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
  done
  iconutil -c icns "$SET" -o "$APPDIR/Contents/Resources/OnDaGo.icns" 2>/dev/null || true
  rm -rf "$SET"
fi
cat > "$APPDIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>OnDaGo Editor</string>
  <key>CFBundleDisplayName</key><string>OnDaGo Editor</string>
  <key>CFBundleIdentifier</key><string>com.ondago.editor</string>
  <key>CFBundleExecutable</key><string>OnDaGo</string>
  <key>CFBundleIconFile</key><string>OnDaGo</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>2.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict></plist>
EOF
touch "$APPDIR"
ln -sfn "$APPDIR" "$HOME/Desktop/OnDaGo Editor" 2>/dev/null || true
ok "App: ~/Applications/OnDaGo Editor.app (also on your Desktop and in Launchpad)"

echo ""
printf "\033[32mInstall complete! Open \"OnDaGo Editor\" from your Desktop, Launchpad or ~/Applications (or run.command).\033[0m\n"
