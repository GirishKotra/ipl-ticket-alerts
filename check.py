#!/usr/bin/env python3
"""
Polls the SRH team page on district.in and fires an ntfy alert
when the 'Sunrisers Hyderabad vs Royal Challengers Bangalore' match
flips from 'Coming soon' to 'Book tickets'.

Designed to be run on a cron/systemd timer. Exits non-zero on errors
so cron can surface them.

Env vars:
  NTFY_TOPIC   (required)  e.g. srh-tix-watch-xKq92
  NTFY_SERVER  (optional)  default https://ntfy.sh
  STATE_FILE   (optional)  default ~/.srh-ticket-watcher.state
"""

import os
import re
import sys
import json
import time
import pathlib
import urllib.request
import urllib.error

PAGE_URL = "https://www.district.in/events/sunrisers-hyderabad-team"
MATCH_ANCHOR = "Sunrisers Hyderabad vs Royal Challengers Bangalore"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20
WINDOW = 600  # chars to scan after the match anchor

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
STATE_FILE = pathlib.Path(
    os.environ.get("STATE_FILE", pathlib.Path.home() / ".srh-ticket-watcher.state")
)


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr)


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")


def strip_html(html):
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S)
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def extract_match_block(text):
    """Return the ~600 chars of text starting at the RCB match anchor, or None."""
    idx = text.find(MATCH_ANCHOR)
    if idx == -1:
        return None
    return text[idx : idx + WINDOW]


def send_ntfy(title, message, priority="urgent", tags="rotating_light,cricket"):
    if not NTFY_TOPIC:
        log("NTFY_TOPIC not set — cannot send alert")
        return False
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": tags,
            "Click": PAGE_URL,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= r.status < 300
    except urllib.error.URLError as e:
        log(f"ntfy send failed: {e}")
        return False


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state))
    except OSError as e:
        log(f"could not write state: {e}")


def main():
    if not NTFY_TOPIC:
        log("FATAL: set NTFY_TOPIC env var")
        sys.exit(2)

    try:
        html = http_get(PAGE_URL)
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"fetch failed: {e}")
        sys.exit(1)

    text = strip_html(html)
    block = extract_match_block(text)

    state = load_state()

    if block is None:
        log("match anchor NOT found — page structure may have changed")
        # One-time alert on structure change so we don't miss the drop silently
        if not state.get("structure_alert_sent"):
            send_ntfy(
                "SRH watcher: page changed",
                "Match card anchor not found on district.in. "
                "The script may need updating. Check manually.",
                priority="high",
                tags="warning",
            )
            state["structure_alert_sent"] = True
            save_state(state)
        sys.exit(1)

    # Clear structure alert flag if we recover
    if state.get("structure_alert_sent"):
        state.pop("structure_alert_sent", None)
        save_state(state)

    block_lower = block.lower()
    has_coming_soon = "coming soon" in block_lower
    has_book_tickets = "book tickets" in block_lower

    log(f"coming_soon={has_coming_soon} book_tickets={has_book_tickets}")

    if has_book_tickets and not state.get("alert_sent"):
        ok = send_ntfy(
            "SRH vs RCB: TICKETS LIVE",
            f"'Book tickets' is now visible on {PAGE_URL}. GO GO GO.",
            priority="urgent",
            tags="rotating_light,cricket,tada",
        )
        if ok:
            state["alert_sent"] = True
            state["alerted_at"] = int(time.time())
            save_state(state)
            log("ALERT SENT")
        else:
            log("alert send failed — will retry next run")
            sys.exit(1)
    elif has_book_tickets:
        log("already alerted previously — skipping")
    else:
        log("still 'Coming soon' — no action")


if __name__ == "__main__":
    main()
