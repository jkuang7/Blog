# Dev Environment

Umbrella repo for shell configs, utilities, and project repositories.

## Structure

```
Dev/
├── .claude/config/     # Shell aliases, bootstrap script
├── Archive/            # Archived projects
├── Career/             # Career-related materials
└── Repos/              # Active project repos (git-ignored)
```

## Prerequisites

- SSH key configured for GitHub (`ssh -T git@github.com` should work)
- Ensure `.custom` is sourced in your shell config

## Setup

Add this to your `~/.zshrc` if not already present:

```bash
source /Volumes/Projects/Dev/.claude/config/.custom
```

Then reload:

```bash
source ~/.zshrc
```

## Commands

| Command | Description |
|---------|-------------|
| `bootstrap` | Clone all repos defined in `bootstrap.sh` |
| `repos` | Claude-powered summary of uncommitted changes across all repos |

## Adding New Repos

Edit `/Volumes/Projects/Dev/.claude/config/bootstrap.sh` and add to the `REPOS` array:

```bash
REPOS=(
    "git@github.com:jkuang7/Banksy.git"
    "git@github.com:jkuang7/DeckFoundry.git"
    "git@github.com:jkuang7/stonks.git"
    "git@github.com:jkuang7/NewRepo.git"  # Add here
)
```

Then run `bootstrap` to clone new repos (existing ones are skipped).

## Git Workflow

This repo tracks configs only. Each repo in `Repos/` is a separate git repo:

- **Dev changes** (bootstrap, aliases): commit here
- **Project changes**: `cd` into the specific repo and commit there

Use `repos` to check status across all projects at once.
