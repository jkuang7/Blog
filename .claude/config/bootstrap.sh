#!/bin/bash
# Bootstrap script for Dev environment

DEV="/Volumes/Projects/Dev"
REPOS_DIR="$DEV/Repos"

# Hardcoded list of repos to clone
REPOS=(
    "git@github.com:jkuang7/Banksy.git"
    "git@github.com:jkuang7/DeckFoundry.git"
    "git@github.com:jkuang7/stonks.git"
    # Add more repos here as needed
)

# Create Repos directory if it doesn't exist
mkdir -p "$REPOS_DIR"

# Clone each repo (skip if already exists)
for repo in "${REPOS[@]}"; do
    repo_name=$(basename "$repo" .git)
    target_dir="$REPOS_DIR/$repo_name"

    if [ -d "$target_dir" ]; then
        echo "Skipping $repo_name (already exists)"
    else
        echo "Cloning $repo_name..."
        git clone "$repo" "$target_dir"
    fi
done

echo "Bootstrap complete!"
