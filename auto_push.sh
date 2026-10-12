#!/bin/bash
# ==========================================================
# Automated Git Commit & Push Script
# ==========================================================

set -e

# Load GITHUB_TOKEN from .env if not in environment
if [ -z "$GITHUB_TOKEN" ] && [ -f .env ]; then
    GITHUB_TOKEN=$(grep -E "^GITHUB_TOKEN=" .env | cut -d'=' -f2- | tr -d '"'"'")
fi

if [ -n "$GITHUB_TOKEN" ]; then
    git remote set-url origin "https://${GITHUB_TOKEN}@github.com/Rizwanami2/Tradingbot.git"
fi

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
msg="${1:-Auto commit after task completion}"

# Stage all changes
git add .

# Commit if there are changes
export GIT_AUTO_PUSHING=1
if git diff-index --quiet HEAD -- 2>/dev/null; then
    echo "[AutoPush] Working tree clean, no new changes to commit."
else
    git commit -m "$msg"
fi

# Push to origin
echo "[AutoPush] Pushing branch '$branch' to GitHub..."
if GIT_TERMINAL_PROMPT=0 git push origin "$branch"; then
    echo "[AutoPush] Successfully pushed to origin/$branch!"
else
    echo "[AutoPush] Push failed. Make sure GITHUB_TOKEN is set in .env or your environment."
    exit 1
fi
