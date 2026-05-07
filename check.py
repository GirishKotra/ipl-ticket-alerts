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

def _env(name, default):
    """os.environ.get but treats empty strings as unset."""
    v = os.environ.get(name)
    return v if v else default


PAGE_URL = _env(
    "PAGE_URL",
    "https://www.district.in/events/sunrisers-hyderabad-team",
)
MATCH_ANCHOR = _env(
    "MATCH_ANCHOR",
    "Sunrisers Hyderabad vs Royal Challengers Bangalore",
)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20
CARD_CLASS = _env("CARD_CLASS", "css-ka0bpq")  # district.in's actionable match card

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
STATE_FILE = pathlib.Path(
    os.environ.get("STATE_FILE", pathlib.Path.home() / ".srh-ticket-watcher.state")
)
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


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


def _extract_balanced_div(html, start):
    """Starting at `<div` position `start`, return the substring up to its matching `</div>`."""
    pos = start
    depth = 0
    while pos < len(html):
        open_m = html.find("<div", pos)
        close_m = html.find("</div>", pos)
        if close_m == -1:
            return None
        if open_m != -1 and open_m < close_m:
            depth += 1
            pos = open_m + 4
        else:
            depth -= 1
            pos = close_m + 6
            if depth == 0:
                return html[start:pos]
    return None


def find_match_cards(html):
    """Return list of (raw_card_html, stripped_text) for every actionable card on the page."""
    open_pat = re.compile(rf'<div class="{re.escape(CARD_CLASS)}">')
    cards = []
    for m in open_pat.finditer(html):
        card_html = _extract_balanced_div(html, m.start())
        if card_html is None:
            continue
        cards.append((card_html, strip_html(card_html)))
    return cards


def send_ntfy(title, message, priority="urgent", tags="rotating_light,cricket"):
    if DRY_RUN:
        log(f"DRY_RUN: would send ntfy title={title!r} priority={priority} msg={message!r}")
        return True
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
    if not NTFY_TOPIC and not DRY_RUN:
        log("FATAL: set NTFY_TOPIC env var (or DRY_RUN=1 to skip alerting)")
        sys.exit(2)

    log(f"config: page={PAGE_URL} anchor={MATCH_ANCHOR!r} card_class={CARD_CLASS} dry_run={DRY_RUN}")

    try:
        html = http_get(PAGE_URL)
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"fetch failed: {e}")
        sys.exit(1)

    cards = find_match_cards(html)
    log(f"found {len(cards)} actionable match card(s) on page")

    state = load_state()

    if not cards:
        log("no cards found — page structure may have changed")
        if not state.get("structure_alert_sent"):
            send_ntfy(
                "Ticket watcher: page changed",
                f"No cards matching class {CARD_CLASS!r} on {PAGE_URL}. "
                "The script may need updating.",
                priority="high",
                tags="warning",
            )
            state["structure_alert_sent"] = True
            save_state(state)
        sys.exit(1)

    # Pick the card for our match. If MATCH_ANCHOR is empty string, scan all cards.
    target_cards = [
        (h, t) for (h, t) in cards
        if (not MATCH_ANCHOR) or (MATCH_ANCHOR in t)
    ]

    if not target_cards:
        log(f"no card contains anchor {MATCH_ANCHOR!r} — match may have moved or page changed")
        if not state.get("anchor_alert_sent"):
            send_ntfy(
                "Ticket watcher: match not listed",
                f"Card for {MATCH_ANCHOR!r} no longer on {PAGE_URL}. "
                f"{len(cards)} other cards present — check manually.",
                priority="high",
                tags="warning",
            )
            state["anchor_alert_sent"] = True
            save_state(state)
        sys.exit(1)

    # Recovered from prior structure/anchor alert
    for k in ("structure_alert_sent", "anchor_alert_sent"):
        if k in state:
            state.pop(k)
            save_state(state)

    # Determine status per target card
    any_book_tickets = False
    any_coming_soon = False
    matched_title = None
    for _, text in target_cards:
        lo = text.lower()
        if "book tickets" in lo:
            any_book_tickets = True
        if "coming soon" in lo:
            any_coming_soon = True
        # first 80 chars is usually the match title
        if matched_title is None:
            matched_title = text[:80]

    log(f"target_cards={len(target_cards)} coming_soon={any_coming_soon} book_tickets={any_book_tickets}")
    log(f"  sample card text: {matched_title!r}")

    if any_book_tickets and not state.get("alert_sent"):
        ok = send_ntfy(
            "TICKETS LIVE",
            f"'Book tickets' is now visible on {PAGE_URL} — {matched_title}. GO GO GO.",
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
    elif any_book_tickets:
        log("already alerted previously — skipping")
    else:
        log("still 'Coming soon' — no action")


if __name__ == "__main__":
    main()
