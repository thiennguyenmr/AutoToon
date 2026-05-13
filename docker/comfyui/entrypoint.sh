#!/bin/bash
# Seed any curated workflows shipped with the image into ComfyUI's user
# workflows directory on first start (idempotent — never overwrites
# user-modified files).
set -e

USER_WF_DIR=/app/ComfyUI/user/default/workflows
SEED_DIR=/app/seed-workflows

if [ -d "$SEED_DIR" ]; then
    mkdir -p "$USER_WF_DIR"
    for src in "$SEED_DIR"/*.json; do
        [ -e "$src" ] || continue
        name=$(basename "$src")
        if [ ! -e "$USER_WF_DIR/$name" ]; then
            cp -v "$src" "$USER_WF_DIR/$name"
        fi
    done
fi

exec "$@"
