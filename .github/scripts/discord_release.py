"""Post a full release to the Discord #release-notes channel.

Called by the "Post to Discord" job in addon-version.yml after a release on main.
The message is the version's ⭐ notice from sonorium_addon/CHANGELOG.md plus a link
to the GitHub release. The webhook URL comes from DISCORD_RELEASE_WEBHOOK; without
it the job does nothing.

    python .github/scripts/discord_release.py 1.8.0            post
    python .github/scripts/discord_release.py 1.8.0 --print    show the message only
"""
import json
import os
import re
import sys
import urllib.request

CHANGELOG = "sonorium_addon/CHANGELOG.md"
LIMIT = 2000  # Discord's message limit


def notice(version: str, changelog: str = CHANGELOG):
    """The title and first paragraph of the version's ⭐ notice, or (None, None)."""
    with open(changelog, encoding="utf-8") as f:
        text = f.read()
    section = re.search(rf"^## v{re.escape(version)}\b.*?$(.*?)(?=^## v|\Z)", text, re.M | re.S)
    if not section:
        return None, None
    quote = re.search(r"^> ## ⭐ ?(.+?)\n((?:>.*(?:\n|$))*)", section.group(1), re.M)
    if not quote:
        return None, None
    title = re.sub(rf"^{re.escape(version)}:?\s*", "", quote.group(1).strip())
    body = "\n".join(line[1:].strip() for line in quote.group(2).splitlines())
    paragraphs = [" ".join(p.split()) for p in body.split("\n\n") if p.strip()]
    summary = paragraphs[0] if paragraphs else ""
    # Links to README anchors don't work outside GitHub: keep only their text
    summary = re.sub(r"\[([^\]]+)\]\(#[^)]*\)", r"\1", summary)
    return title or None, summary or None


def message(version: str, repo: str) -> str:
    title, summary = notice(version)
    link = f"Release notes and downloads: <https://github.com/{repo}/releases/tag/addon-v{version}>"
    head = f"# Sonorium {version}\n" + (f"**{title}**\n" if title else "")
    room = LIMIT - len(head) - len(link) - 2
    if summary and len(summary) > room:
        summary = summary[: room - 1].rsplit(" ", 1)[0] + "…"
    if not title and not summary:
        summary = "A smaller update with fixes and improvements."
    return head + (f"{summary}\n" if summary else "") + "\n" + link


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: discord_release.py <version> [--print]")
    version = sys.argv[1]
    content = message(version, os.environ.get("REPO", "synssins/sonorium"))
    if "--print" in sys.argv:
        print(content)
        return
    webhook = os.environ.get("DISCORD_RELEASE_WEBHOOK", "").strip()
    if not webhook:
        print("DISCORD_RELEASE_WEBHOOK is not set: nothing posted")
        return
    req = urllib.request.Request(
        webhook + "?wait=true",
        data=json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "sonorium-release (github actions)"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"Posted Sonorium {version} to Discord ({r.status})")


if __name__ == "__main__":
    main()
