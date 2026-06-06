#!/usr/bin/env bash

if [[ -n "${BASH_VERSION:-}" ]]; then
  SCRIPT_PATH="${BASH_SOURCE[0]}"
elif [[ -n "${ZSH_VERSION:-}" ]]; then
  SCRIPT_PATH="${(%):-%N}"
else
  SCRIPT_PATH="$0"
fi

ROOT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -e "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# H5P-Libraries herunterladen, falls noch nicht vorhanden
if [[ ! -d "$ROOT_DIR/libraries" ]] || [[ -z "$(ls -A "$ROOT_DIR/libraries" 2>/dev/null)" ]]; then
  echo "H5P-Libraries werden heruntergeladen..."
  inv update-h5p-libraries
fi

echo "Virtuelle Umgebung aktiv: $VIRTUAL_ENV"
echo "CLI verfuegbar: course-sync --help"
echo "Verfuegbare Tasks:"
inv -l
