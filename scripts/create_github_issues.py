"""Create the user stories of docs/stories.json as GitHub issues, grouped by milestone.

Needs the GitHub CLI (`gh auth login` done once) and is run from the repository root. The variable
`GITHUB_REPOSITORY=owner/name` skips the lookup of the repository (a plain REST call needs no more):

    python scripts/create_github_issues.py            # create the missing issues
    python scripts/create_github_issues.py US-05      # create only the listed stories

Stories whose title already exists are skipped, so the script can be run again safely.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def gh(*args: str) -> object:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def main(only: list[str]) -> None:
    repo = (
        os.environ.get("GITHUB_REPOSITORY")
        or json.loads(
            subprocess.run(
                ["gh", "repo", "view", "--json", "nameWithOwner"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )["nameWithOwner"]
    )
    stories = json.loads(Path("docs/stories.json").read_text(encoding="utf8"))
    milestones: list[dict[str, object]] = gh(
        "api", f"repos/{repo}/milestones?state=all&per_page=100"
    )  # type: ignore[assignment]
    by_prefix = {str(m["title"]).split(".")[0]: m["number"] for m in milestones}
    issues: list[dict[str, object]] = gh("api", f"repos/{repo}/issues?state=all&per_page=100")  # type: ignore[assignment]
    existing = {str(i["title"]).split(":")[0] for i in issues}
    for s in stories:
        if (only and s["id"] not in only) or s["id"] in existing:
            continue
        labels = [
            "story",
            s["priority"].lower(),
            *sorted(
                {
                    ("doc" if c.strip().startswith("DOC") else c.strip()[0])
                    for c in s["function"].split(",")
                }
            ),
        ]
        body = (
            f"**Story**\n\n{s['story']}\n\n**Acceptance criteria**\n\n{s['criteria']}\n\n"
            f"**Function:** {s['function']}  \n**Story points:** {s['sp']}  \n"
            f"**Priority:** {s['priority']}"
        )
        cmd = [
            "api",
            "-X",
            "POST",
            f"repos/{repo}/issues",
            "-f",
            f"title={s['id']}: {s['story']}",
            "-f",
            f"body={body}",
            "-F",
            f"milestone={by_prefix[s['milestone']]}",
        ]
        for label in labels:
            cmd += ["-f", f"labels[]={label}"]
        created: dict[str, object] = gh(*cmd)  # type: ignore[assignment]
        print(s["id"], "->", created["html_url"])


if __name__ == "__main__":
    main(sys.argv[1:])
