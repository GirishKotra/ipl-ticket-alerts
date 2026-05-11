#!/usr/bin/env python3
"""
Polls a district.in IPL team page and fires an ntfy alert when a match
card flips from 'Coming soon' to 'Book tickets'.

Runs a configurable number of polls per invocation with a sleep between
them; owns the loop itself so cross-poll state (error streaks, recovery)
can be tracked without round-tripping a file. Designed for GitHub
Actions but also runs standalone.

Main alerts go to NTFY_TOPIC. HTTP/network failures are routed to a
derived topic {NTFY_TOPIC}-api-errors with different priority:

  - First fetch failure in a run -> 'high' priority alert, continue polling.
  - End-of-run still failing      -> 'high' priority alert + disable this
                                     workflow via GitHub API so cron halts
                                     until the user manually re-enables.
  - Mid-run recovery              -> 'default' priority ping, cron continues.

Env vars:
  NTFY_TOPIC    (required)    e.g. vk18mgrx7
  NTFY_SERVER   (optional)    default https://ntfy.sh
  PAGE_URL      (optional)    default SRH team page
  MATCH_ANCHOR  (optional)    title substring to locate the match card;
                              blank = scan all actionable cards
  CARD_CLASS    (optional)    district.in card class, default css-ka0bpq
  POLL_COUNT    (optional)    polls per invocation, default 1
  POLL_INTERVAL (optional)    seconds between polls, default 70
  DRY_RUN       (optional)    '1' skips the push (for testing)
  STATE_FILE    (optional)    default ~/.ipl-ticket-alerts.state

Auto-pause (GHA only, set by the Actions runner):
  GITHUB_ACTIONS, GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_WORKFLOW_REF
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
POLL_COUNT = int(_env("POLL_COUNT", "1"))
POLL_INTERVAL = int(_env("POLL_INTERVAL", "70"))

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
ERROR_TOPIC = f"{NTFY_TOPIC}-api-errors" if NTFY_TOPIC else None
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
STATE_FILE = pathlib.Path(
    os.environ.get("STATE_FILE", pathlib.Path.home() / ".ipl-ticket-alerts.state")
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


def send_ntfy(title, message, priority="urgent", tags="rotating_light,cricket", topic=None):
    target = topic or NTFY_TOPIC
    if DRY_RUN:
        log(f"DRY_RUN: would send ntfy topic={target} title={title!r} priority={priority} msg={message!r}")
        return True
    if not target:
        log("ntfy topic not set — cannot send alert")
        return False
    url = f"{NTFY_SERVER}/{target}"
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


def describe_error(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} {e.reason}"
    if isinstance(e, urllib.error.URLError):
        return f"network error: {e.reason}"
    if isinstance(e, TimeoutError):
        return "timeout"
    return f"{type(e).__name__}: {e}"


def disable_workflow():
    """Disable the currently-running GHA workflow so no further cron runs fire.
    No-op outside GitHub Actions. Returns True on success (or dry run / local)."""
    if DRY_RUN:
        log("DRY_RUN: would disable the workflow")
        return True
    if not os.environ.get("GITHUB_ACTIONS"):
        log("not running in GitHub Actions — skipping workflow disable")
        return True
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")            # "owner/name"
    wf_ref = os.environ.get("GITHUB_WORKFLOW_REF", "")    # "owner/name/.github/workflows/file.yml@ref"
    if not (token and repo and wf_ref):
        log(f"missing GHA env for workflow disable (token={bool(token)} repo={bool(repo)} ref={bool(wf_ref)})")
        return False
    # workflow file basename lives between the last '/' and the '@'
    path_part = wf_ref.split("@", 1)[0]                   # "owner/name/.github/workflows/file.yml"
    wf_file = path_part.rsplit("/", 1)[-1]                # "file.yml"
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{wf_file}/disable"
    req = urllib.request.Request(
        url,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ipl-ticket-alerts",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            ok = r.status == 204
            log(f"workflow disable status={r.status} ({'ok' if ok else 'unexpected'})")
            return ok
    except urllib.error.HTTPError as e:
        log(f"workflow disable failed: HTTP {e.code} {e.reason}")
        return False
    except urllib.error.URLError as e:
        log(f"workflow disable failed: {e}")
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


def process_page(html, state):
    """Handle the DOM analysis for a single successful fetch. Updates state in place.
    Does not exit — returns None."""
    cards = find_match_cards(html)
    log(f"found {len(cards)} actionable match card(s) on page")

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
        return

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
        return

    # Recovered from prior structure/anchor alert
    changed = False
    for k in ("structure_alert_sent", "anchor_alert_sent"):
        if k in state:
            state.pop(k)
            changed = True
    if changed:
        save_state(state)

    any_book_tickets = False
    any_coming_soon = False
    matched_title = None
    for _, text in target_cards:
        lo = text.lower()
        if "book tickets" in lo:
            any_book_tickets = True
        if "coming soon" in lo:
            any_coming_soon = True
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
    elif any_book_tickets:
        log("already alerted previously — skipping")
    else:
        log("still 'Coming soon' — no action")


def main():
    if not NTFY_TOPIC and not DRY_RUN:
        log("FATAL: set NTFY_TOPIC env var (or DRY_RUN=1 to skip alerting)")
        sys.exit(2)

    log(f"config: page={PAGE_URL} anchor={MATCH_ANCHOR!r} card_class={CARD_CLASS} "
        f"polls={POLL_COUNT}x{POLL_INTERVAL}s dry_run={DRY_RUN}")

    state = load_state()

    first_fail_alerted = False   # has this run sent the "first failure" alert?
    last_error = None            # Exception from most recent poll, or None
    had_any_failure = False      # any poll in this run failed?

    for i in range(1, POLL_COUNT + 1):
        log(f"--- poll {i}/{POLL_COUNT} ---")
        try:
            html = http_get(PAGE_URL)
            last_error = None
            process_page(html, state)
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
            had_any_failure = True
            detail = describe_error(e)
            log(f"fetch failed: {detail}")
            if not first_fail_alerted:
                send_ntfy(
                    "API error — continuing this run",
                    f"Fetch failed for {PAGE_URL}: {detail}. "
                    f"Remaining polls this run: {POLL_COUNT - i}.",
                    priority="high",
                    tags="warning",
                    topic=ERROR_TOPIC,
                )
                first_fail_alerted = True
        except Exception as e:  # defensive — don't let process_page crash the loop
            log(f"unexpected error during poll: {type(e).__name__}: {e}")

        if i < POLL_COUNT:
            time.sleep(POLL_INTERVAL)

    # End-of-run reconciliation
    if last_error is not None:
        detail = describe_error(last_error)
        log(f"end-of-run: still failing ({detail}) — pausing watcher")
        send_ntfy(
            "API still failing — PAUSING watcher",
            f"Fetch of {PAGE_URL} failed through the end of this run ({detail}). "
            "Disabling the scheduled workflow. Re-enable manually via the Actions UI "
            "once district.in is healthy.",
            priority="high",
            tags="no_entry_sign,warning",
            topic=ERROR_TOPIC,
        )
        disable_workflow()
    elif had_any_failure:
        log("end-of-run: recovered after earlier failures")
        send_ntfy(
            "API recovered",
            f"Fetch of {PAGE_URL} was intermittently failing earlier this run "
            "but the last poll succeeded. Cron continues.",
            priority="default",
            tags="white_check_mark",
            topic=ERROR_TOPIC,
        )


if __name__ == "__main__":
    main()
