"""Public timer for the NDX Morning Brief: starts the private brief workflow on time.

GitHub runs this account's scheduled jobs hours late (Nov 2026: about 6-7 h) and skips many,
so a cron near 09:00 New York can't start the brief. This job is scheduled through the night
instead; whichever slot GitHub fires first waits on the runner until TARGET (08:52 New York,
DST-aware), then dispatches the private repo's brief.yml (a dispatch starts at once, unlike a
schedule). The brief sets itself up and waits until exactly 09:00:00 before collecting.
At each CHECK it dispatches again if today's page still isn't live and no brief is running.

This repo is public so the waiting runner costs nothing; it holds no news, code or keys of the
brief. BRIEF_TOKEN (a fine-grained token limited to the private repo, Actions read and write) is
only sent to api.github.com and never printed. Standard library only.

    python timer.py                               # as the workflow does
    python timer.py --now "2026-11-20 03:40"      # print the plan for that New York time; no waiting
    python timer.py --check                       # can BRIEF_TOKEN read the private repo's runs?
    python timer.py --recover                     # start a same-day catch-up brief now (labelled recovery)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
BRIEF_REPO = "ssslithy/NDQ-Morning-Brief"
PAGE = "https://ssslithy.github.io/ndq-brief-site/"
TARGET = time(8, 52)                              # dispatch; the brief then waits for 09:00:00
CHECKS = (time(9, 8), time(9, 17), time(9, 24))   # re-dispatch if the page still isn't live
LAST = CHECKS[-1]
MAX_WAIT = timedelta(hours=5, minutes=20)         # + 32 min of checks stays inside the 355-min job limit
CHAIN_SLEEP = timedelta(hours=5, minutes=20)
MAX_LEAD = timedelta(hours=13)                    # further away than this: a later slot will act

# NYSE full-day closures (the brief also checks the exchange calendar itself).
HOLIDAYS = {date.fromisoformat(d) for d in (
    "2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19 2026-07-03 2026-09-07 "
    "2026-11-26 2026-12-25 2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31 2027-06-18 "
    "2027-07-05 2027-09-06 2027-11-25 2027-12-24").split()}


def trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def ny_at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, NY)


def plan(now: datetime) -> tuple[str, datetime | None, date]:
    """("skip", None, day) | ("chain", wake_utc, day) | ("wait", target_utc, day)."""
    local = now.astimezone(NY)
    d = local.date()
    if local.time() > LAST:
        d += timedelta(days=1)                    # today's chance has passed: aim at the next day
    while not trading_day(d):
        d += timedelta(days=1)
    target = ny_at(d, TARGET)
    wait = target - now
    if wait > MAX_LEAD:
        return "skip", None, d
    if wait > MAX_WAIT:
        return "chain", now + CHAIN_SLEEP, d
    return "wait", target, d


def published(page: str, d: date) -> bool:
    """Today's 09:00 reading is live: the page's metadata names today's session, collected today
    between 08:00 and 16:00 New York by a scheduled run that finished ok."""
    meta = dict(re.findall(r'<meta name="(ndx-[^"]+)" content="([^"]*)">', page))
    try:
        got = datetime.fromisoformat(meta["ndx-collected-at"]).astimezone(NY)
    except (KeyError, ValueError):
        return False
    return (meta.get("ndx-session") == d.isoformat() and got.date() == d
            and time(8) <= got.time() < time(16)
            and meta.get("ndx-status") in ("ok", "partial")
            and meta.get("ndx-trigger") == "scheduled-0900")


def is_published(d: date) -> bool:
    try:
        with urllib.request.urlopen(f"{PAGE}?timer={_time.time_ns()}", timeout=30) as r:
            return published(r.read().decode("utf-8", "replace"), d)
    except Exception as e:  # noqa: BLE001
        print(f"could not read the public page ({type(e).__name__})")
        return False


def api(path: str, token: str, data: dict | None = None):
    req = urllib.request.Request(f"https://api.github.com/{path}", method="POST" if data is not None else "GET",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ndx-brief-timer"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {e.code} for {path.split('?')[0]}") from None
    return json.loads(body) if body.strip() else None


def brief_active(token: str) -> bool:
    runs = api(f"repos/{BRIEF_REPO}/actions/workflows/brief.yml/runs?per_page=10", token)["workflow_runs"]
    return any(r["status"] in ("queued", "in_progress", "waiting", "pending", "requested") for r in runs)


def dispatch_brief(token: str, **inputs) -> None:
    api(f"repos/{BRIEF_REPO}/actions/workflows/brief.yml/dispatches", token,
        {"ref": "main", "inputs": {k: str(v).lower() for k, v in inputs.items()}})
    print(f"dispatched the brief {inputs} at {datetime.now(timezone.utc):%H:%M:%S} UTC", flush=True)


def others_waiting(gh_token: str) -> int:
    """Other runs of this timer in progress (a later slot leaves the waiting to them)."""
    repo, mine = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID")
    try:
        runs = api(f"repos/{repo}/actions/workflows/timer.yml/runs?status=in_progress&per_page=20", gh_token)
        return sum(1 for r in runs["workflow_runs"] if str(r["id"]) != mine)
    except Exception as e:  # noqa: BLE001
        print(f"could not list timer runs ({e})")
        return 0


def chain(gh_token: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    api(f"repos/{repo}/actions/workflows/timer.yml/dispatches", gh_token, {"ref": "main"})
    print("dispatched the next timer run to carry on waiting")


def sleep_until(t: datetime) -> None:
    s = (t - datetime.now(timezone.utc)).total_seconds()
    if s > 0:
        print(f"sleeping {s / 3600:.2f} h until {t.astimezone(NY):%a %H:%M} New York", flush=True)
        _time.sleep(s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help="pretend it is this New York time; print the plan only")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--recover", action="store_true")
    a = ap.parse_args(argv)
    if a.now:
        now = datetime.fromisoformat(a.now).replace(tzinfo=NY).astimezone(timezone.utc)
        kind, when, d = plan(now)
        print(f"{kind} {d}" + (f" {when.astimezone(NY):%a %H:%M}" if when else ""))
        return 0
    token, gh_token = os.environ.get("BRIEF_TOKEN", "").strip(), os.environ.get("GH_TOKEN", "").strip()
    if not token:
        print("BRIEF_TOKEN secret is not set: cannot start the brief.")
        return 1
    if a.check:
        print(f"token can read the brief's runs; one is active now: {brief_active(token)}")
        return 0
    if a.recover:
        dispatch_brief(token, recovery=True)
        return 0
    now = datetime.now(timezone.utc)
    kind, when, d = plan(now)
    print(f"now {now.astimezone(NY):%a %d %b %H:%M} New York; next brief {d}; plan: {kind}"
          + (f" until {when.astimezone(NY):%a %H:%M} New York" if when else ""), flush=True)
    if kind == "skip":
        return 0
    if others_waiting(gh_token):
        print("another timer run is already waiting; exiting")
        return 0
    if is_published(d):
        print("today's brief is already live; exiting")
        return 0
    if kind == "chain":
        sleep_until(when)
        chain(gh_token)
        return 0
    for at in [when] + [ny_at(d, t) for t in CHECKS]:
        sleep_until(at)
        if is_published(d):
            print("today's brief is live; done")
            return 0
        if brief_active(token):
            print("a brief is already queued or running; checking again later")
            continue
        dispatch_brief(token, wait_for_0900=True, scheduled=True)
    print("last check passed; the brief's own checks report any miss")
    return 0


if __name__ == "__main__":
    sys.exit(main())
