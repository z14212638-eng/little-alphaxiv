#!/usr/bin/env bash
# Run the Little Alphaxiv backend proxy.
# Usage: ./run.sh   (activates Agent_env if available, installs deps if missing)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate Agent_env if conda is present (per global Python env rule).
if command -v conda >/dev/null 2>&1; then
  if conda env list | grep -q "Agent_env"; then
    # shellcheck disable=SC1091
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate Agent_env
  fi
fi

# Install deps if fastapi isn't importable.
python -c "import fastapi" 2>/dev/null || pip install -r requirements.txt

# Run uvicorn. Backend dir is parent of app/ package, so module is app.main.
exec uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
