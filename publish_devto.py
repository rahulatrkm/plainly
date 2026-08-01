"""Publish the Plainly launch article to dev.to.  DEVTO_API_KEY=<key> python publish_devto.py --publish"""
from __future__ import annotations
import json, os, sys, urllib.request, urllib.error

LIVE = "https://plainly-n6ni.onrender.com"
REPO = "https://github.com/rahulatrkm/plainly"

BODY = f"""\
## Everyone keeps running into documents that gatekeep their own life

A blood test full of abbreviations. A legal letter. A bank statement. A
government form. A dense insurance policy. To understand your *own* information
you often need a professional — which costs time and money.

So I built [Plainly]({LIVE}): paste any confusing text and get it back in plain
language — **at the reading level you choose** (from "like I'm 10" to "keep it
precise") and **in your language**.

You get a TL;DR, key points, things to watch out for (deadlines, anything that
needs action), the hard words decoded, and practical next steps.

## Example

Paste a cryptic blood report like `Hb 9.2 g/dL, MCV 72 fL, ferritin 8 ng/mL` and
it comes back with: "Your blood test shows anemia — low red blood cells — most
likely caused by not having enough iron," plus what each term means and what to
do next.

## It also checks if something is real

There's a second mode — **RealCheck** — where you paste a suspicious message or a
claim and it reasons calmly about whether it's plausible, calling out scam and
misinformation red flags (urgency, pay-a-fee, prize-you-didn't-enter, crypto
doubling, verify-your-account links) and how to check it yourself.

## Honest about what it is

It's an **AI explainer/reasoner, not a doctor, lawyer or fact-checking oracle** —
great for *understanding* and for asking better questions, not a final authority.
Nothing is stored, no signup, free.

- Try it: **{LIVE}**
- Open source (MIT): [{REPO}]({REPO})

Under the hood it's a tiny Python (stdlib) backend forwarding to a free LLM, with
a strict "JSON only" contract + retries to keep responses reliable. What kind of
confusing document should I make it handle better?
"""

ARTICLE = {"article": {
    "title": "I built a tool that explains any confusing document in plain words (your level, your language)",
    "published": False,
    "tags": ["ai", "webdev", "python", "showdev"],
    "canonical_url": REPO,
    "description": ("Paste a medical report, legal letter, bank statement or government form and get "
                    "it in plain language at your reading level and language. Plus a scam/reality checker. Free."),
    "body_markdown": BODY,
}}

def find_existing(key, title):
    for state in ("unpublished", "published"):
        req = urllib.request.Request(f"https://dev.to/api/articles/me/{state}?per_page=50",
            headers={"api-key": key, "User-Agent": "plainly-pub/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            for a in json.loads(r.read().decode()):
                if a.get("title") == title:
                    return a
    return None

def main():
    key = os.environ.get("DEVTO_API_KEY")
    if not key:
        print("Set DEVTO_API_KEY", file=sys.stderr); return 1
    ARTICLE["article"]["published"] = "--publish" in sys.argv
    existing = find_existing(key, ARTICLE["article"]["title"])
    data = json.dumps(ARTICLE).encode()
    if existing:
        req = urllib.request.Request(f"https://dev.to/api/articles/{existing['id']}", data=data,
            method="PUT", headers={"Content-Type": "application/json", "api-key": key, "User-Agent": "plainly-pub/1.0"})
    else:
        req = urllib.request.Request("https://dev.to/api/articles", data=data, method="POST",
            headers={"Content-Type": "application/json", "api-key": key, "User-Agent": "plainly-pub/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(("PUBLISHED" if "--publish" in sys.argv else "DRAFT") + ":", json.loads(r.read().decode()).get("url"))
    except urllib.error.HTTPError as e:
        print(f"error {e.code}: {e.read().decode()[:300]}", file=sys.stderr); return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
