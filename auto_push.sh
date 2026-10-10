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
if git diff-index --quiet HEAD -- 2>/dev/null; then
    echo "[AutoPush] Working tree clean, no new changes to commit."
else
    git commit -m "$msg"
fi

# Push to origin
echo "[AutoPush] Pushing branch '$branch' to GitHub..."
GIT_TERMINAL_PROMPT=0 git push origin "$branch"
echo "[AutoPush] Successfully pushed to origin/$branch!"
