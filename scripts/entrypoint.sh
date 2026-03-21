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
    git rebase --abort 2>/dev/null || true
    git pull --rebase origin main || {
        git rebase --abort 2>/dev/null || true
        git reset --hard origin/main
    }
else
    echo "Cloning repo..."
    git clone "https://x-access-token:${GITHUB_PAT}@github.com/${GIT_REPO}.git" "$REPO_DIR"
    cd "$REPO_DIR"
fi

echo "Starting monitor loop (interval: ${CHECK_INTERVAL}s)..."

while true; do
    echo "=== Check starting at $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="

    # Pull latest before running (rebase to handle diverging status commits)
    cd "$REPO_DIR"
    git rebase --abort 2>/dev/null || true
    git pull --rebase origin main || {
        git rebase --abort 2>/dev/null || true
        git reset --hard origin/main
    }

    # Run the Ticketmaster checker
    STATUS_PATH="$REPO_DIR/data/status.json" python /app/scripts/check_tickets.py || echo "Ticketmaster check failed, will retry next cycle"

    # Run the StubHub checker
    STUBHUB_STATUS_PATH="$REPO_DIR/data/stubhub.json" python /app/scripts/check_stubhub.py || echo "StubHub check failed, will retry next cycle"

    # Run the Emirates flight scraper
    DATA_FILE="$REPO_DIR/data/flights.json" python /app/scripts/fetch_flights.py || echo "Flight check failed, will retry next cycle"

    # Commit and push if there are changes
    if git diff --quiet data/status.json data/stubhub.json data/flights.json 2>/dev/null; then
        echo "No changes to push"
    else
        git add data/status.json data/stubhub.json data/flights.json
        git commit -m "Update data [skip ci]"
        git push origin main || {
            echo "Push failed, rebasing..."
            git rebase --abort 2>/dev/null || true
            git pull --rebase origin main || git reset --hard origin/main
            git push origin main || echo "Push still failed, will retry next cycle"
        }
        echo "Pushed updated data"
    fi

    echo "Sleeping ${CHECK_INTERVAL}s..."
    sleep "$CHECK_INTERVAL"
done
