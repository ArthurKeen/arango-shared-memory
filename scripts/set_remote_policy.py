#!/usr/bin/env python3
"""Make `arango-solutions` the primary remote and `ArthurKeen` the secondary, fleet-wide.

Policy installed per repository:

    origin   fetch  -> github.com/arango-solutions/<repo>      (primary: pull/fetch/status)
    origin   push   -> github.com/arango-solutions/<repo>      (both, in this order)
                    -> github.com/ArthurKeen/<repo>            (secondary mirror)
    personal fetch/push -> github.com/ArthurKeen/<repo>        (explicit single-target ops)

So `git pull` tracks the org, and one `git push` reaches both repos. Git pushes the URLs
in listed order and STOPS at the first failure, which is why the org is listed first: a
rejected org push (the authoritative one) must not be preceded by a successful personal
push that would leave the mirror ahead of the primary.

Why a named `personal` remote survives: `git push --all origin` now fans out, so there has
to be a way to address one side deliberately — e.g. pushing a spike to your own fork
without publishing it to the org.

The portfolio arrived at four different conventions for the same thing (`origin`,
`arthurkeen`, `upstream`, `fork`, `arango-solutions`), which is exactly the kind of drift
that makes a team-wide rule unenforceable. This normalises them.

Idempotent and dry-run by default:

    python3 scripts/set_remote_policy.py --root ~/code            # preview
    python3 scripts/set_remote_policy.py --root ~/code --apply
    python3 scripts/set_remote_policy.py --repo ~/code/foo --apply

A repo whose org counterpart does not exist yet is reported as `needs-org-repo` and left
completely untouched — configuring a push to a nonexistent repository would break every
subsequent `git push`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ORG = "arango-solutions"
PERSONAL = "ArthurKeen"
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist"}


def git(repo: Path, *args: str) -> str | None:
    """Run a git command in `repo`; return stdout, or None on any failure."""
    try:
        out = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def remotes(repo: Path) -> dict[str, str]:
    raw = git(repo, "remote", "-v") or ""
    found: dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            found.setdefault(parts[0], parts[1])
    return found


def _slug(url: str) -> str | None:
    """owner/name from any github URL form (https, ssh, with or without .git)."""
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def org_repo_exists(url: str) -> bool:
    """Does the org counterpart exist and is it readable with the caller's creds?

    Uses `git ls-remote`, so it answers the question that actually matters — can git
    talk to it — rather than trusting a configured remote to imply existence. This is
    the bootstrap case: the very first flip happens when NO org remote is configured
    yet, which an inspect-the-remotes-only classifier reports as `needs-org-repo`
    forever.
    """
    try:
        out = subprocess.run(["git", "ls-remote", "--exit-code", "-h", url],
                             capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def classify(repo: Path) -> tuple[str, str | None, str | None]:
    """-> (status, org_url, personal_url)."""
    org_url = personal_url = None
    for url in remotes(repo).values():
        slug = _slug(url)
        if not slug:
            continue
        owner, name = slug.split("/", 1)
        if owner.lower() == ORG.lower():
            org_url = f"https://github.com/{ORG}/{name}.git"
        elif owner.lower() == PERSONAL.lower():
            personal_url = f"https://github.com/{PERSONAL}/{name}.git"
    if org_url and personal_url:
        return "ready", org_url, personal_url
    if personal_url and not org_url:
        # No org remote configured — but the org repo may still exist (first flip).
        inferred = f"https://github.com/{ORG}/{repo.name}.git"
        if org_repo_exists(inferred):
            return "ready", inferred, personal_url
        return "needs-org-repo", None, personal_url
    if org_url and not personal_url:
        return "org-only", org_url, None
    return "no-github-remote", None, None


def desired_state(repo: Path, org_url: str, personal_url: str) -> bool:
    """True when the policy is already fully in place (idempotence check)."""
    fetch = git(repo, "remote", "get-url", "origin")
    pushes = (git(repo, "remote", "get-url", "--push", "--all", "origin") or "").splitlines()
    personal = git(repo, "remote", "get-url", "personal")
    return (fetch == org_url and pushes == [org_url, personal_url]
            and personal == personal_url)


def apply_policy(repo: Path, org_url: str, personal_url: str) -> list[str]:
    steps: list[str] = []
    git(repo, "remote", "set-url", "origin", org_url)
    steps.append(f"origin fetch -> {org_url}")
    # Reset push URLs, then add both in order (org first — see module docstring).
    git(repo, "remote", "set-url", "--push", "origin", org_url)
    git(repo, "remote", "set-url", "--add", "--push", "origin", personal_url)
    steps.append(f"origin push  -> {org_url} + {personal_url}")
    if git(repo, "remote", "get-url", "personal") is None:
        git(repo, "remote", "add", "personal", personal_url)
    else:
        git(repo, "remote", "set-url", "personal", personal_url)
    steps.append(f"personal     -> {personal_url}")
    return steps


def discover(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob("*/.git") if p.is_dir())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("~/code").expanduser())
    ap.add_argument("--repo", type=Path, help="single repository instead of --root")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    repos = [args.repo.expanduser().resolve()] if args.repo else discover(args.root.expanduser().resolve())
    tally: dict[str, int] = {}
    for repo in repos:
        status, org_url, personal_url = classify(repo)
        tally[status] = tally.get(status, 0) + 1
        if status != "ready":
            if status == "needs-org-repo":
                print(f"  needs-org-repo  {repo.name}  (create {ORG}/{repo.name} first — untouched)")
            continue
        assert org_url and personal_url
        if desired_state(repo, org_url, personal_url):
            tally["already-correct"] = tally.get("already-correct", 0) + 1
            tally["ready"] -= 1
            print(f"  ok              {repo.name}")
            continue
        if not args.apply:
            print(f"  would-set       {repo.name}")
            continue
        for step in apply_policy(repo, org_url, personal_url):
            pass
        print(f"  set             {repo.name}")

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(tally.items()) if v))
    if not args.apply:
        print("  (dry run — re-run with --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
