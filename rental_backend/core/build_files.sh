#!/bin/bash
set -e

export DJANGO_SETTINGS_MODULE=core.settings

# Vercel's Python runtime is managed by uv (PEP 668) — plain pip install fails.
if command -v uv >/dev/null 2>&1; then
  uv pip install --system -r requirements.txt
else
  python -m pip install --break-system-packages -r requirements.txt
fi

python manage.py collectstatic --noinput
