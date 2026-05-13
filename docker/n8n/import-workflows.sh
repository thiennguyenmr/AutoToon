#!/bin/sh
set -e

WORKFLOW_DIR="/home/node/workflows"

# Import all workflow JSON files if directory exists and has files
if [ -d "$WORKFLOW_DIR" ] && [ "$(ls -A "$WORKFLOW_DIR"/*.json 2>/dev/null)" ]; then
  echo "==> Waiting for database to be ready..."

  for file in "$WORKFLOW_DIR"/*.json; do
    echo "==> Importing workflow: $(basename "$file")"
    n8n import:workflow --input="$file" || echo "    WARNING: Failed to import $(basename "$file")"
  done
  echo "==> Workflow import complete"
else
  echo "==> No workflow files found in $WORKFLOW_DIR"
fi

# Start n8n (exec replaces shell so signals are forwarded properly)
exec n8n
