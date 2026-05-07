# GitHub Actions setup

Runs the watcher every ~90s for free. GHA cron minimum is 5 minutes, so each
scheduled run polls 4 times at 90s intervals (covers ~4.5 of every 5 minutes).

## One-time setup

```bash
cd ~/srh-ticket-watcher

git init
git add .
git commit -m "initial: SRH ticket watcher"

# Create the repo (requires `gh` CLI + auth). PRIVATE is fine — Actions minutes
# are 2000/mo free on private, more than enough.
gh repo create srh-ticket-watcher --private --source=. --push
```

No prefer `gh` CLI? Create the repo in the GitHub web UI, then:

```bash
git remote add origin git@github.com:<you>/srh-ticket-watcher.git
git push -u origin main
```

## Verify it's running

1. Open the repo on GitHub → **Actions** tab.
2. You'll see the workflow listed. First run happens on the next `*/5` boundary —
   to not wait, click **Run workflow** (manual trigger is enabled).
3. Watch the logs — each poll should print `coming_soon=True book_tickets=False`.

## How state works

When the script alerts, it writes `.state/watcher.state` inside the job, then
the workflow commits + pushes it back to the repo. Future runs clone the repo
(with state) and won't re-alert. `[skip ci]` in the commit message prevents a
push from retriggering the workflow.

## Things to watch out for

- **GHA schedule delay**: scheduled runs can be delayed 5–15 min when GitHub is
  busy. `workflow_dispatch` is instant if you want to kick one manually.
- **Disabled workflows**: GitHub auto-disables scheduled workflows on repos with
  no activity for 60 days. Not a concern for a 15-day window.
- **When tickets drop**: you'll get the ntfy push, then the state file gets
  committed. If you want to re-arm (e.g. test or wait for a different match),
  delete `.state/watcher.state` from the repo and push.

## Turning it off

- Temporary: Actions tab → workflow → **Disable workflow**.
- Permanent: `gh repo delete srh-ticket-watcher --yes` or just remove
  `.github/workflows/watcher.yml` and push.
