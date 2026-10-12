#!/bin/bash
# Start OnDaGo Editor on macOS.  "bash run.command debug" keeps it in this Terminal window with logs.
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "OnDaGo Editor is not installed yet. Running install.command ..."
  exec bash install.command
fi
if [ "${1:-}" = "debug" ]; then
  exec .venv/bin/python -m app.main
fi
nohup .venv/bin/python -m app.main >/dev/null 2>&1 &
echo "OnDaGo Editor is starting..."
