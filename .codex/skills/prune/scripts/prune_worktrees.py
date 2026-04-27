#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Worktree:
    path: Path
    head: str
    branch: str | None
    detached: bool


def run(cmd: list[str], check: bool = True) -> str:
    result = subprocess.run(cmd, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "command failed")
    return result.stdout


def parse_worktrees(repo: Path) -> list[Worktree]:
    output = run(["git", "-C", str(repo), "worktree", "list", "--porcelain"])
    blocks = [block for block in output.strip().split("\n\n") if block.strip()]
    worktrees: list[Worktree] = []
    for block in blocks:
        path: Path | None = None
        head = ""
        branch: str | None = None
        detached = False
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.split(" ", 1)[1])
            elif line.startswith("HEAD "):
                head = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                branch = line.split(" ", 1)[1]
            elif line == "detached":
                detached = True
        if path is None:
            raise RuntimeError(f"failed to parse worktree block:\n{block}")
        worktrees.append(Worktree(path=path, head=head, branch=branch, detached=detached))
    return worktrees


def canonical_repo_path(worktrees: list[Worktree]) -> Path:
    candidates = [wt.path.resolve() for wt in worktrees if (wt.path / ".git").is_dir()]
    if candidates:
        return candidates[0]
    return worktrees[0].path.resolve()


def status_porcelain(path: Path) -> str:
    return run(["git", "-C", str(path), "status", "--porcelain"]).strip()


def current_branch(path: Path) -> str:
    return run(["git", "-C", str(path), "branch", "--show-current"]).strip()


def main_commit(path: Path, main_branch: str) -> str:
    return run(["git", "-C", str(path), "rev-parse", main_branch]).strip()


def switch_keep_to_main(keep: Path, main_branch: str, apply: bool) -> None:
    branch = current_branch(keep)
    head = run(["git", "-C", str(keep), "rev-parse", "HEAD"]).strip()
    main_head = main_commit(keep, main_branch)
    dirty = bool(status_porcelain(keep))
    if branch == main_branch:
        return
    if dirty:
        print(f"skip checkout for kept worktree {keep}: dirty")
        return
    if head != main_head:
        print(f"skip checkout for kept worktree {keep}: HEAD differs from {main_branch}")
        return
    cmd = ["git", "-C", str(keep), "checkout", main_branch]
    if apply:
        run(cmd)
        print(f"checked out {main_branch} in {keep}")
    else:
        print("would run:", " ".join(cmd))


def remove_worktree(keep: Path, target: Path, force_dirty: bool, apply: bool) -> bool:
    dirty = bool(status_porcelain(target))
    if dirty and not force_dirty:
        print(f"skip {target}: dirty")
        return False
    cmd = ["git", "-C", str(keep), "worktree", "remove"]
    if dirty and force_dirty:
        cmd.append("--force")
    cmd.append(str(target))
    if apply:
        run(cmd)
        print(f"removed {target}")
    else:
        print("would run:", " ".join(cmd))
    return True


def prune(keep: Path, apply: bool) -> None:
    cmd = ["git", "-C", str(keep), "worktree", "prune"]
    if apply:
        run(cmd)
        print("pruned git worktree metadata")
    else:
        print("would run:", " ".join(cmd))


def remove_empty_dir(path: Path, apply: bool) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        next(path.iterdir())
        return False
    except StopIteration:
        if apply:
            path.rmdir()
            print(f"removed empty leftover directory {path}")
        else:
            print(f"would remove empty leftover directory {path}")
        return True


def cleanup_empty_leftovers(targets: list[Path], apply: bool) -> None:
    for target in targets:
        remove_empty_dir(target, apply)
        remove_empty_dir(target.parent, apply)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune linked git worktrees while keeping one checkout.")
    parser.add_argument("--repo", required=True, help="Any checkout path inside the target git repository.")
    parser.add_argument("--keep", help="Path of the worktree to keep. Defaults to the canonical repo checkout.")
    parser.add_argument("--main-branch", default="main", help="Primary branch to keep checked out.")
    parser.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run.")
    parser.add_argument("--force-dirty", action="store_true", help="Allow forced removal of dirty worktrees.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    worktrees = parse_worktrees(repo)
    keep = Path(args.keep).resolve() if args.keep else canonical_repo_path(worktrees)

    if not any(wt.path.resolve() == keep for wt in worktrees):
        print(f"keep path is not an active worktree: {keep}", file=sys.stderr)
        return 2

    print(f"keeping: {keep}")
    switch_keep_to_main(keep, args.main_branch, args.apply)

    removed_targets: list[Path] = []
    for wt in worktrees:
        target = wt.path.resolve()
        if target == keep:
            continue
        if remove_worktree(keep, target, args.force_dirty, args.apply):
            removed_targets.append(target)

    prune(keep, args.apply)
    cleanup_empty_leftovers(removed_targets, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
