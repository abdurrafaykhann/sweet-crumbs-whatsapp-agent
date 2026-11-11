#!/usr/bin/env bash
# Publish this repo to a Hugging Face Docker Space.
#   scripts/deploy_hf.sh https://huggingface.co/spaces/<user>/<space>
# Spaces read their settings from YAML at the top of README.md, so the script adds it to a temporary copy
# (the README on GitHub stays clean). Sign in with a Hugging Face access token with write access when git asks for a password.
set -euo pipefail
SPACE_URL="${1:?usage: scripts/deploy_hf.sh https://huggingface.co/spaces/<user>/<space>}"
TMP="$(mktemp -d)"
git archive HEAD | tar -x -C "$TMP"
{ printf -- '---\ntitle: Sweet Crumbs WhatsApp Agent\nemoji: 🧁\ncolorFrom: yellow\ncolorTo: red\nsdk: docker\napp_port: 8000\npinned: false\n---\n\n'; cat "$TMP/README.md"; } > "$TMP/README.new" && mv "$TMP/README.new" "$TMP/README.md"
cd "$TMP"
git init -q -b main && git add -A && git -c user.name="deploy" -c user.email="deploy@example.com" commit -q -m "Deploy $(date -u +%F)"
git push --force "$SPACE_URL" main
echo "Pushed. Watch the build log on the Space page; it takes a few minutes."
