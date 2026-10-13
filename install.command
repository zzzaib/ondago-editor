#!/bin/bash
# OnDaGo Editor installer for macOS. Double-click this file (or run:  bash install.command)
cd "$(dirname "$0")" || exit 1
# make sure the other Mac scripts are runnable (git on Windows can drop the executable flag)
chmod +x install.command run.command installer/bootstrap_mac.sh 2>/dev/null
echo ""
echo " OnDaGo Editor installer (macOS)"
echo " Downloads Python, PyTorch, FFmpeg, Ollama and the AI models into this folder."
echo " First install is about 20-25 GB and can take a while. Re-running is safe."
echo ""
if ! bash installer/bootstrap_mac.sh "$@"; then
  echo ""
  read -r -p "Install FAILED. Press Enter to close..." _
  exit 1
fi
echo ""
read -r -p "Launch OnDaGo Editor now? [Y/n] " ans
case "$ans" in
  [nN]*) exit 0 ;;
  *) open "$HOME/Applications/OnDaGo Editor.app" 2>/dev/null || (nohup .venv/bin/python -m app.main >/dev/null 2>&1 &) ;;
esac
