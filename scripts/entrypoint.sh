#!/bin/bash
set -e

REPO_DIR="/repo/wwe"
GIT_REPO="${GIT_REPO:-fradetti/wwe}"
CHECK_INTERVAL="${CHECK_INTERVAL:-900}"

# Configure git
git config --global user.name "wwe-monitor"
git config --global user.email "wwe-monitor@bot"

# Clone or pull the repo
if [ -d "$REPO_DIR/.git" ]; then
    echo "Repo exists, pulling latest..."
    cd "$REPO_DIR"
    git pull --ff-only origin main || true
else
    echo "Cloning repo..."
    git clone "https://x-access-token:${GITHUB_PAT}@github.com/${GIT_REPO}.git" "$REPO_DIR"
    cd "$REPO_DIR"
fi

echo "Starting monitor loop (interval: ${CHECK_INTERVAL}s)..."

while true; do
    echo "=== Check starting at $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="

    # Pull latest before running
    git pull --ff-only origin main || true

    # Run the ticket checker — output goes to data/status.json in the repo
    STATUS_PATH="$REPO_DIR/data/status.json" python /app/scripts/check_tickets.py || echo "Check failed, will retry next cycle"

    # Commit and push if there are changes
    if git diff --quiet data/status.json 2>/dev/null; then
        echo "No changes to push"
    else
        git add data/status.json
        git commit -m "Update ticket status [skip ci]"
        git push origin main
        echo "Pushed updated status"
    fi

    echo "Sleeping ${CHECK_INTERVAL}s..."
    sleep "$CHECK_INTERVAL"
done
