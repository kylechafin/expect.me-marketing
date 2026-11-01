#!/usr/bin/env python3
"""Track the Rangerly Instagram follow graph in one git-checked JSON file.

WHY THIS EXISTS. Instagram will tell you who you follow and who follows you,
but it will not tell you who it *used* to tell you, and that is the half that
matters. Pruning non-reciprocal follows is a slow loop: look at fifteen
accounts, unfollow the dead ones, come back in a fortnight. Without memory the
loop never advances -- the same fifteen accounts, the ones you already decided
to keep, surface again every time, and an account you deliberately dropped a
year ago gets re-followed because nothing remembers that you dropped it.

So this keeps a ledger instead of a snapshot. Every handle that has ever been
on either side of the graph gets a record, and the records accumulate: when it
was first seen as a follow, when it was recommended for unfollowing, when it
went away. Two things fall out of that for free.

  - A batch of unfollow candidates never repeats itself. Recommending a handle
    writes the date into its record, and the candidate pool excludes anything
    already recommended. "Don't show me these again" is the default, not a
    decision you have to make each time.

  - A list of prospective follows can be screened against your own history,
    which is the thing Instagram's own suggestions cannot do. An account you
    unfollowed is an account you already judged.

Unfollows are INFERRED, not confessed. You unfollow by hand, in the app, and
there is no sane way for this script to watch you do it -- so it does not ask.
It compares the fresh export against the ledger: a handle that was following
and is now absent was unfollowed, and the date it noticed is close enough. That
means `import` is the only bookkeeping step you owe it. (An account that went
private, got deleted, or renamed also reads as "gone", which lands in the same
bucket -- "do not suggest following this again" -- and is the right answer for
all four cases anyway.)

THE STATE FILE is one JSON object, handles sorted, falsy fields dropped, in
Instagram/follow-graph.json. Sorted and one-field-per-line so that a git diff
after an import reads as the handful of accounts that actually changed rather
than a reshuffle of eighteen hundred. Check it in; it is the whole database and
it is the only thing that needs to travel between machines.

USAGE, in the order you will actually want it:

    # Prime, and re-prime later, from a Meta data export (zip, folder, or the
    # individual JSON/HTML files inside it). Safe to run repeatedly.
    Tools/instagram-follows.py import ~/Downloads/instagram-export.zip

    Tools/instagram-follows.py status

    # Periodic prune: 10 accounts you follow who don't follow back, oldest
    # follows first, never a repeat. Eyeball them, unfollow the dead ones.
    Tools/instagram-follows.py candidates -n 10
    Tools/instagram-follows.py candidates -n 10 --dry-run   # don't record

    # Vet a list you're thinking of following, against your own history.
    Tools/instagram-follows.py screen @one @two @three
    pbpaste | Tools/instagram-follows.py screen -
    Tools/instagram-follows.py screen --file list.txt --format handles

    # Hand corrections.
    Tools/instagram-follows.py keep @someone --note "friend, never prune"
    Tools/instagram-follows.py unfollowed @someone     # if you can't wait for import
    Tools/instagram-follows.py reconsider @someone     # let it be recommended again
    Tools/instagram-follows.py show @someone
    Tools/instagram-follows.py history

Handles are accepted however you have them: bare, @-prefixed, or as a profile
URL, separated by whitespace, commas or newlines. Everything is stored and
compared lowercased, which is how Instagram treats handles too.

Standard library only, by design -- this has to run on any machine that has the
repo checked out, with nothing installed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_STATE = REPO / "Instagram" / "follow-graph.json"

TODAY = dt.date.today().isoformat()

STATE_VERSION = 1


# ---------------------------------------------------------------- handles ---

# Paths Instagram serves that are not profiles. A link to /explore/ in an
# export is navigation chrome, not an account.
RESERVED = {
    "about", "accounts", "api", "challenge", "developer", "direct", "explore",
    "graphql", "help", "legal", "logout", "login", "p", "privacy", "reel",
    "reels", "s", "session", "stories", "terms", "top", "tv", "web",
    "your_activity",
}

HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")
URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/([^/?#\s]+)", re.IGNORECASE
)


def norm_handle(raw):
    """Normalise anything handle-shaped to a bare lowercase handle, or None.

    Accepts "handle", "@handle", "instagram.com/handle/", a full https URL,
    and tolerates trailing punctuation from pasted lists.
    """
    if not raw:
        return None
    h = str(raw).strip().strip("<>\"'")
    m = URL_RE.match(h)
    if m:
        h = m.group(1)
    h = h.strip().lstrip("@").strip("/").strip(" ,;:.|\t")
    h = h.lower()
    if not HANDLE_RE.match(h) or h in RESERVED:
        return None
    return h


def parse_handles(words):
    """Split a pile of words/lines into normalised handles, order preserved."""
    out, seen, bad = [], set(), []
    for word in words:
        for tok in re.split(r"[\s,;|]+", str(word)):
            if not tok:
                continue
            h = norm_handle(tok)
            if h is None:
                bad.append(tok)
            elif h not in seen:
                seen.add(h)
                out.append(h)
    return out, bad


def read_handle_args(args):
    """Handles from positional args, --file, and/or stdin ("-" or a pipe)."""
    words = list(getattr(args, "handles", None) or [])
    use_stdin = "-" in words
    words = [w for w in words if w != "-"]

    if getattr(args, "file", None):
        words.append(Path(args.file).read_text(encoding="utf-8", errors="replace"))

    if use_stdin or (not words and not sys.stdin.isatty()):
        words.append(sys.stdin.read())

    handles, bad = parse_handles(words)
    if bad:
        warn(f"ignored {len(bad)} unrecognised token(s): {', '.join(bad[:8])}"
             + (" ..." if len(bad) > 8 else ""))
    return handles


# ------------------------------------------------------------------ state ---

def new_state():
    return {
        "version": STATE_VERSION,
        "updated": None,
        "imports": [],
        "batches": [],
        "accounts": {},
    }


def load_state(path, *, required=True):
    if not path.exists():
        if required:
            die(f"no state file at {path}\n"
                f"Prime it first:  {rel(Path(__file__))} import <export.zip>")
        return new_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON ({exc})")
    if state.get("version", 1) > STATE_VERSION:
        die(f"{path} was written by a newer version of this script "
            f"(file v{state['version']}, script v{STATE_VERSION})")
    for key, default in (("imports", []), ("batches", []), ("accounts", {})):
        state.setdefault(key, default)
    return state


def save_state(path, state):
    """Write the ledger: handles sorted, empty fields dropped, atomically."""
    state["version"] = STATE_VERSION
    state["updated"] = TODAY

    pruned = {}
    for handle, rec in state["accounts"].items():
        kept = {k: v for k, v in rec.items() if v not in (None, False, [], "", {})}
        if kept:
            pruned[handle] = dict(sorted(kept.items()))
    state["accounts"] = dict(sorted(pruned.items()))

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def rec_for(state, handle):
    return state["accounts"].setdefault(handle, {})


def add_date(rec, field, date=None):
    """Append a date to a dated-event list, without duplicating a same-day one."""
    date = date or TODAY
    dates = rec.setdefault(field, [])
    if date not in dates:
        dates.append(date)
        dates.sort()
    return dates


# ------------------------------------------------------- export extraction ---

# One export file maps to one edge type. Detected by filename first (a
# followers_N.json is a bare list with no key to go on), then by the
# relationships_* key as a fallback for files renamed on the way here.
KEY_KINDS = {
    "relationships_following": "following",
    "relationships_followers": "followers",
    "relationships_follow_requests_sent": "pending",
    "relationships_unfollowed_users": "unfollowed",
    "relationships_blocked_users": "blocked",
}

# Export files that are relationship-shaped but are NOT edges we track, listed
# so they are skipped deliberately rather than guessed at.
IGNORED_NAMES = (
    "close_friends", "restricted_profiles", "removed_suggestions",
    "recent_follow_requests", "profiles_you've_unfollowed_recently",
    "hide_story_from", "follow_requests_you've_received",
)


def kind_for(name, data):
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    stem = base.rsplit(".", 1)[0]

    if stem.startswith("following"):
        return "following"
    if stem.startswith("followers"):
        return "followers"
    if "pending_follow_request" in stem:
        return "pending"
    if "recently_unfollowed" in stem or "unfollowed_profiles" in stem:
        return "unfollowed"
    if "blocked_profile" in stem:
        return "blocked"
    if any(tag in stem for tag in IGNORED_NAMES):
        return None

    if isinstance(data, dict):
        for key in data:
            if key in KEY_KINDS:
                return KEY_KINDS[key]
    return None


def entry_pairs(item):
    """Yield (handle, timestamp) from one export entry."""
    if isinstance(item, str):
        h = norm_handle(item)
        if h:
            yield h, None
        return
    if not isinstance(item, dict):
        return

    found = False
    for slot in item.get("string_list_data") or []:
        if not isinstance(slot, dict):
            continue
        h = norm_handle(slot.get("value")) or norm_handle(slot.get("href"))
        if h:
            found = True
            yield h, slot.get("timestamp")
    if not found:
        # blocked_profiles.json and friends carry the handle in `title`.
        h = norm_handle(item.get("title"))
        if h:
            yield h, None


def pairs_from_json(data):
    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in KEY_KINDS:
            if isinstance(data.get(key), list):
                items = data[key]
                break
        if items is None:
            for value in data.values():
                if isinstance(value, list):
                    items = value
                    break
    for item in items or []:
        yield from entry_pairs(item)


# The HTML export is the one most people click by mistake. It has no structure
# worth parsing, just anchors, so: take each profile link and look at the text
# immediately after it for the date Instagram prints next to it.
HTML_LINK_RE = re.compile(
    r'href="(https?://(?:www\.)?instagram\.com/[^"]+)"'
    r'([\s\S]{0,300}?)'
    r'(?=href="https?://(?:www\.)?instagram\.com/|\Z)',
    re.IGNORECASE,
)
HTML_DATE_RE = re.compile(r"\b([A-Z][a-z]{2}) (\d{1,2}), (\d{4})\b")


def pairs_from_html(text):
    for href, trailing in HTML_LINK_RE.findall(text):
        h = norm_handle(href)
        if not h:
            continue
        ts = None
        m = HTML_DATE_RE.search(re.sub(r"<[^>]+>", " ", trailing))
        if m:
            try:
                ts = dt.datetime.strptime(" ".join(m.groups()), "%b %d %Y").date()
            except ValueError:
                ts = None
        yield h, ts


def iter_export_files(path):
    """Yield (display name, bytes) for every plausible export file at `path`."""
    p = Path(path).expanduser()
    if not p.exists():
        die(f"no such path: {p}")

    wanted = (".json", ".html", ".htm")
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.is_file() and f.suffix.lower() in wanted:
                yield str(f.relative_to(p)), f.read_bytes()
    elif p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            for name in sorted(z.namelist()):
                if not name.endswith("/") and name.lower().endswith(wanted):
                    yield name, z.read(name)
    else:
        yield p.name, p.read_bytes()


def collect_export(path):
    """Read an export into {kind: {handle: date-or-None}} plus a file log."""
    found = {k: {} for k in ("following", "followers", "pending", "unfollowed",
                             "blocked")}
    log = []
    seen_any = False

    for name, blob in iter_export_files(path):
        seen_any = True
        text = blob.decode("utf-8", errors="replace")
        data, pairs = None, None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None

        kind = kind_for(name, data)
        if kind is None:
            continue

        pairs = pairs_from_json(data) if data is not None else pairs_from_html(text)

        count = 0
        for handle, stamp in pairs:
            count += 1
            # First sighting of a handle in a kind wins; exports list the
            # newest first, and the earliest date is the interesting one, but
            # only one of the two can be had from a single file, so prefer
            # whichever we already recorded and fill gaps.
            prior = found[kind].get(handle, "unset")
            date = to_date(stamp)
            if prior == "unset" or (prior is None and date):
                found[kind][handle] = date
        if count:
            log.append((name, kind, count))

    if not seen_any:
        die(f"found no .json or .html files under {path}")
    if not log:
        die("none of those files looked like an Instagram connections export.\n"
            "Expected following.json / followers_1.json (or their .html twins),\n"
            "usually under connections/followers_and_following/ in the export.")
    return found, log


def to_date(stamp):
    """Instagram epoch seconds (or an already-parsed date) -> ISO date string."""
    if stamp is None:
        return None
    if isinstance(stamp, dt.date):
        return stamp.isoformat()
    try:
        stamp = int(stamp)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    try:
        return dt.date.fromtimestamp(stamp).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# --------------------------------------------------------------- commands ---

def cmd_import(args):
    state = load_state(args.state, required=False)
    accounts = state["accounts"]
    found, log = collect_export(args.path)

    print(f"Read {len(log)} file(s) from {args.path}:")
    for name, kind, count in log:
        print(f"  {name}  ->  {kind} ({count})")
    print()

    before_following = {h for h, r in accounts.items() if r.get("following")}
    before_followers = {h for h, r in accounts.items() if r.get("follower")}

    summary = {"date": TODAY, "source": str(args.path)}
    changes = {k: 0 for k in ("new_following", "new_followers", "gone_following",
                              "gone_followers", "new_accounts")}

    def touch(handle):
        if handle not in accounts:
            changes["new_accounts"] += 1
        return rec_for(state, handle)

    # --- following ---------------------------------------------------------
    if found["following"]:
        now = set(found["following"])
        pending = set(found["pending"])
        shrink = len(before_following) - len(now)
        if before_following and len(now) < 0.5 * len(before_following) and not args.force:
            die(f"refusing to import: the ledger has {len(before_following)} "
                f"accounts you follow but this export lists only {len(now)}.\n"
                "That is the shape of a partial or wrong export, and importing "
                "it would mark\nhundreds of accounts as unfollowed. Pass "
                "--force if the drop is real.")

        for handle, date in found["following"].items():
            rec = touch(handle)
            if not rec.get("following"):
                changes["new_following"] += 1
            rec["following"] = True
            rec.setdefault("following_since", date or TODAY)
            rec.pop("pending", None)

        for handle in sorted(before_following - now - pending):
            rec = accounts[handle]
            rec["following"] = False
            add_date(rec, "unfollowed")
            changes["gone_following"] += 1

        summary["following"] = len(now)
        if shrink > 0:
            summary["following_dropped"] = shrink

    # --- followers ---------------------------------------------------------
    if found["followers"]:
        now = set(found["followers"])
        for handle, date in found["followers"].items():
            rec = touch(handle)
            if not rec.get("follower"):
                changes["new_followers"] += 1
            rec["follower"] = True
            rec.setdefault("follower_since", date or TODAY)

        for handle in sorted(before_followers - now):
            rec = accounts[handle]
            rec["follower"] = False
            add_date(rec, "unfollowed_me")
            changes["gone_followers"] += 1

        summary["followers"] = len(now)

    # --- pending / unfollowed / blocked -----------------------------------
    if found["pending"]:
        for handle, date in found["pending"].items():
            rec = touch(handle)
            rec["pending"] = True
            rec.setdefault("requested_on", date or TODAY)
        for handle, rec in accounts.items():
            if rec.get("pending") and handle not in found["pending"]:
                rec.pop("pending", None)
        summary["pending"] = len(found["pending"])

    if found["unfollowed"]:
        for handle, date in found["unfollowed"].items():
            rec = touch(handle)
            add_date(rec, "unfollowed", date or TODAY)
        summary["unfollowed_in_export"] = len(found["unfollowed"])

    if found["blocked"]:
        for handle, date in found["blocked"].items():
            rec = touch(handle)
            rec["blocked"] = True
        summary["blocked"] = len(found["blocked"])

    state["imports"].append(summary)
    save_state(args.state, state)

    print(f"{'New accounts seen:':<26}{changes['new_accounts']}")
    print(f"{'Newly following:':<26}{changes['new_following']}")
    print(f"{'No longer following:':<26}{changes['gone_following']}"
          + ("   (recorded as unfollowed)" if changes["gone_following"] else ""))
    print(f"{'New followers:':<26}{changes['new_followers']}")
    print(f"{'Lost followers:':<26}{changes['gone_followers']}")
    print(f"\nLedger written to {rel(args.state)} "
          f"({len(accounts)} accounts tracked).")
    print_status(state, prefix="\n")


def cmd_status(args):
    state = load_state(args.state)
    if args.format == "json":
        emit_json(status_counts(state))
        return
    print_status(state)


def status_counts(state):
    """The numbers behind `status`, so text and --format json cannot drift."""
    accounts = state["accounts"]
    following = {h for h, r in accounts.items() if r.get("following")}
    followers = {h for h, r in accounts.items() if r.get("follower")}
    recommended = {h for h, r in accounts.items() if r.get("recommended")}
    keep = {h for h, r in accounts.items() if r.get("keep")}
    pending = {h for h, r in accounts.items() if r.get("pending")}
    oneway = following - followers
    return {
        "updated": state.get("updated"),
        "tracked": len(accounts),
        "following": len(following),
        "followers": len(followers),
        "mutual": len(following & followers),
        "one_way": len(oneway),
        "fans": len(followers - following),
        "pending": len(pending),
        "recommended": len(recommended),
        "keep": len(keep),
        "unfollowed_all_time": sum(1 for r in accounts.values()
                                   if r.get("unfollowed")),
        "investigated": sum(1 for r in accounts.values()
                            if r.get("investigated")),
        "candidate_pool": len(oneway - recommended - keep - pending),
    }


def print_status(state, prefix=""):
    c = status_counts(state)
    fans, pool, oneway = c["fans"], c["candidate_pool"], c["one_way"]

    rows = [
        ("Following", c["following"]),
        ("Followers", c["followers"]),
        ("Mutual", c["mutual"]),
        ("You follow, they don't", oneway),
        ("They follow, you don't", fans),
        ("Pending requests", c["pending"]),
        (None, None),
        ("Already recommended", c["recommended"]),
        ("Investigated", c["investigated"]),
        ("Marked keep", c["keep"]),
        ("Unfollowed, all time", c["unfollowed_all_time"]),
        ("Candidate pool left", pool),
    ]
    print(f"{prefix}Follow graph as of {c['updated'] or 'never'}"
          f"  ({c['tracked']} accounts tracked)")
    for label, value in rows:
        if label is None:
            print()
        else:
            print(f"  {label:<24}{value:>6}")

    if fans:
        print(f"\n{fans} account(s) follow you that you don't follow back"
              f" -- `screen` them to see which are safe to follow.")
    if not pool and oneway:
        print("\nCandidate pool is empty: every non-reciprocal follow has been "
              "recommended\nor marked keep. `reconsider` a handle to let it "
              "come up again.")


def cmd_candidates(args):
    state = load_state(args.state)
    accounts = state["accounts"]

    pool = [
        (h, r) for h, r in accounts.items()
        if r.get("following") and not r.get("follower")
        and not r.get("keep") and not r.get("recommended")
        and not r.get("pending")
    ]

    if not pool:
        oneway = sum(1 for r in accounts.values()
                     if r.get("following") and not r.get("follower"))
        if oneway:
            die("nothing left to recommend: all "
                f"{oneway} non-reciprocal follows have already been "
                "recommended or\nmarked keep. Use `reconsider <handle>` to put "
                "one back in the pool.")
        die("nothing to recommend -- every account you follow follows you back.")

    if args.order == "random":
        random.Random(args.seed).shuffle(pool)
    else:
        # Unknown dates sort last: a handle with no first-seen date came from an
        # HTML export and we can't claim it is old.
        pool.sort(key=lambda kv: (kv[1].get("following_since") is None,
                                  kv[1].get("following_since") or "",
                                  kv[0]),
                  reverse=(args.order == "newest"))

    picks = pool[: args.number]

    if args.format == "json":
        emit_json({
            "generated": TODAY,
            "order": args.order,
            "pool_size": len(pool),
            "recorded": not args.dry_run,
            "accounts": [account_json(h, r, "unfollow-candidate") for h, r in picks],
        })
    elif args.format == "handles":
        for handle, _ in picks:
            print(handle)
    elif args.format == "urls":
        for handle, _ in picks:
            print(profile_url(handle))
    elif args.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(["handle", "url", "following_since", "follower"])
        for handle, rec in picks:
            w.writerow([handle, profile_url(handle),
                        rec.get("following_since") or "", "no"])
    else:
        print(f"{len(picks)} account(s) you follow that do not follow you back"
              f"   [{args.order} first, {len(pool)} in the pool]\n")
        width = max(len(h) for h, _ in picks) + 1
        for handle, rec in picks:
            since = rec.get("following_since")
            since = f"followed {since}" if since else "followed date unknown"
            print(f"  @{handle:<{width}} {since:<26} {profile_url(handle)}")

        if args.dry_run:
            print("\n--dry-run: nothing recorded, these will come up again.")
        else:
            print(f"\nRecorded as recommended on {TODAY} -- they will not be "
                  "suggested again.")
            print("Unfollow the dead ones by hand; the next `import` will "
                  "notice and remember.")
            print("\nTo spare one of them permanently:\n"
                  f"  {rel(Path(__file__))} keep @handle")

    if not args.dry_run:
        batch = {"date": TODAY, "order": args.order,
                 "handles": [h for h, _ in picks]}
        state["batches"].append(batch)
        for handle, _ in picks:
            add_date(rec_for(state, handle), "recommended")
        save_state(args.state, state)

    # Saved before the tabs open: if the browser misbehaves the batch is still
    # on record, and the URLs are in the text output above either way.
    if args.open_tabs:
        open_profiles([profile_url(h) for h, _ in picks],
                      assume_yes=args.yes)


def classify(rec):
    """Verdict for a prospective follow. Order matters: first match wins."""
    if rec is None:
        return "ok"
    if rec.get("blocked"):
        return "blocked"
    if rec.get("following"):
        return "following"
    if rec.get("pending"):
        return "pending"
    if rec.get("unfollowed"):
        return "unfollowed"
    if rec.get("recommended"):
        return "recommended"
    if rec.get("follower"):
        return "followback"
    return "ok"


VERDICTS = [
    ("blocked",     "BLOCKED",        "you blocked these -- do not follow"),
    ("unfollowed",  "AVOID",          "you unfollowed these before"),
    ("recommended", "CAUTION",        "you flagged these for unfollow before"),
    ("following",   "ALREADY",        "you already follow these"),
    ("pending",     "PENDING",        "follow request already sent"),
    ("followback",  "FOLLOW BACK",    "they follow you and you don't follow them"),
    ("ok",          "OK",             "new to the ledger, safe to follow"),
]
SAFE = {"followback", "ok"}


def cmd_screen(args):
    state = load_state(args.state)
    handles = read_handle_args(args)
    if not handles:
        die("no handles given. Pass them as arguments, --file, or on stdin.")

    groups = {key: [] for key, _, _ in VERDICTS}
    for handle in handles:
        groups[classify(state["accounts"].get(handle))].append(handle)

    if args.format == "json":
        out = []
        for key, label, _ in VERDICTS:
            for handle in groups[key]:
                rec = state["accounts"].get(handle)
                item = account_json(handle, rec or {}, label.lower().replace(" ", "-"))
                item["verdict"] = key
                item["safe_to_follow"] = key in SAFE
                item["detail"] = detail_for(rec, key)
                out.append(item)
        emit_json({
            "generated": TODAY,
            "screened": len(handles),
            "safe": sum(len(groups[k]) for k in SAFE),
            "accounts": out,
        })
        return

    if args.format == "handles":
        for key in ("followback", "ok"):
            for handle in groups[key]:
                print(handle)
        return

    if args.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(["handle", "verdict", "safe", "url", "detail"])
        for key, label, _ in VERDICTS:
            for handle in groups[key]:
                w.writerow([handle, label, "yes" if key in SAFE else "no",
                            profile_url(handle),
                            detail_for(state["accounts"].get(handle), key)])
        return

    safe = sum(len(groups[k]) for k in SAFE)
    print(f"Screened {len(handles)} handle(s) against "
          f"{len(state['accounts'])} tracked accounts: "
          f"{safe} safe to follow, {len(handles) - safe} not.\n")

    for key, label, blurb in VERDICTS:
        hits = groups[key]
        if not hits:
            continue
        print(f"{label}  ({len(hits)}) -- {blurb}")
        width = max(len(h) for h in hits) + 1
        for handle in hits:
            extra = detail_for(state["accounts"].get(handle), key)
            print(f"  @{handle:<{width}} {extra:<24} {profile_url(handle)}")
        print()


def detail_for(rec, key):
    if not rec:
        return ""
    if key == "unfollowed":
        return "unfollowed " + rec["unfollowed"][-1]
    if key == "recommended":
        return "recommended " + rec["recommended"][-1]
    if key == "following":
        since = rec.get("following_since")
        bits = [f"since {since}"] if since else []
        # You already put this one on an unfollow list and left it alone; worth
        # saying so, since "already following" would otherwise read as approval.
        if rec.get("recommended"):
            bits.append(f"flagged {rec['recommended'][-1]}")
        return ", ".join(bits)
    if key == "pending":
        on = rec.get("requested_on")
        return f"requested {on}" if on else ""
    if key == "followback":
        since = rec.get("follower_since")
        return f"follower since {since}" if since else "follows you"
    if rec.get("unfollowed_me"):
        return "unfollowed you " + rec["unfollowed_me"][-1]
    return ""


# What an investigating agent is allowed to conclude. Deliberately separates
# "I judged this" from "this happened": `unfollow` is a recommendation and
# leaves the edge intact, `unfollowed` asserts the button was actually pressed.
# Only the second one rewrites the graph, so a wrong judgement costs a note,
# not a corrupted ledger.
RECORD_VERDICTS = {
    "unfollow":   "recommend unfollowing, edge left alone",
    "unfollowed": "the unfollow has actually happened",
    "keep":       "worth keeping, never recommend again",
    "follow":     "worth following, not followed yet",
    "followed":   "the follow has actually happened",
    "unclear":    "investigated, no conclusion",
}


def read_json_input(args):
    """JSON from --file, a positional '-', or a pipe."""
    if getattr(args, "file", None):
        text = Path(args.file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty() or "-" in (getattr(args, "handles", None) or []):
        text = sys.stdin.read()
    else:
        die("no input. Pipe JSON in, pass --file PATH, or use '-' for stdin.")
    text = text.strip()
    if not text:
        die("input was empty")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        die(f"input is not valid JSON ({exc})")


def cmd_record(args):
    """Write a batch of investigation findings back into the ledger.

    This is the half of the loop that an agent drives: it reads a batch out
    with `candidates --format json`, looks at the profiles, and hands the
    verdicts back here in one call rather than one subprocess per account.
    """
    state = load_state(args.state)
    raw = read_json_input(args)
    items = raw.get("accounts") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        die("expected a JSON list of {handle, verdict, note} objects, or an "
            'object with an "accounts" list')

    when = args.date or TODAY
    applied, counts = [], {}

    for item in items:
        if isinstance(item, str):
            item = {"handle": item}
        if not isinstance(item, dict):
            warn(f"skipped a non-object entry: {item!r}")
            continue

        handle = norm_handle(item.get("handle") or item.get("username"))
        if not handle:
            warn(f"skipped unrecognisable handle: {item.get('handle')!r}")
            continue

        verdict = str(item.get("verdict") or "unclear").strip().lower()
        if verdict not in RECORD_VERDICTS:
            warn(f"@{handle}: unknown verdict {verdict!r}, recorded as "
                 f"'unclear'. Valid: {', '.join(sorted(RECORD_VERDICTS))}")
            verdict = "unclear"

        rec = rec_for(state, handle)
        add_date(rec, "investigated", when)
        rec["verdict"] = verdict
        note = str(item.get("note") or "").strip()
        if note:
            rec["note"] = note
        # Where the prospect came from, so you can tell which seed accounts
        # are worth mining again and which were a waste of an evening.
        source = norm_handle(item.get("source"))
        if source:
            rec["source"] = source

        if verdict == "unfollowed":
            rec["following"] = False
            rec.pop("pending", None)
            add_date(rec, "unfollowed", when)
        elif verdict == "keep":
            rec["keep"] = True
        elif verdict == "followed":
            rec["following"] = True
            rec.setdefault("following_since", when)
            rec.pop("pending", None)

        counts[verdict] = counts.get(verdict, 0) + 1
        applied.append((handle, verdict, note))

    if not applied:
        die("nothing recorded -- no usable entries in the input")

    save_state(args.state, state)

    if args.format == "json":
        emit_json({"recorded": len(applied), "date": when, "verdicts": counts,
                   "accounts": [{"handle": h, "verdict": v, "note": n}
                                for h, v, n in applied]})
        return

    width = max(len(h) for h, _, _ in applied) + 1
    for handle, verdict, note in applied:
        line = f"  {verdict:<11} @{handle:<{width}}"
        print(line + (f" {note}" if note else ""))
    print(f"\nRecorded {len(applied)} finding(s) on {when}: "
          + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
    if counts.get("unfollow"):
        print(f"\n{counts['unfollow']} marked for unfollowing but still "
              "followed -- unfollow them in the app,\nthen the next `import` "
              "will confirm it. Or re-record them as 'unfollowed'.")


def cmd_followed(args):
    """Mark follows that just happened, so screening won't re-suggest them."""
    state = load_state(args.state)
    handles = read_handle_args(args)
    if not handles:
        die("no handles given.")
    for handle in handles:
        rec = rec_for(state, handle)
        rec["following"] = True
        rec.setdefault("following_since", args.date or TODAY)
        rec.pop("pending", None)
        print(f"followed  @{handle}  {args.date or TODAY}")
    save_state(args.state, state)
    print(f"\n{len(handles)} account(s) recorded as followed.")


def cmd_keep(args):
    state = load_state(args.state)
    handles = read_handle_args(args)
    if not handles:
        die("no handles given.")
    for handle in handles:
        rec = rec_for(state, handle)
        rec["keep"] = True
        if args.note:
            rec["note"] = args.note
        print(f"keep  @{handle}" + (f"  ({args.note})" if args.note else ""))
    save_state(args.state, state)
    print(f"\n{len(handles)} account(s) will never be recommended for unfollow.")


def cmd_unfollowed(args):
    state = load_state(args.state)
    handles = read_handle_args(args)
    if not handles:
        die("no handles given.")
    for handle in handles:
        rec = rec_for(state, handle)
        rec["following"] = False
        rec.pop("pending", None)
        add_date(rec, "unfollowed", args.date)
        print(f"unfollowed  @{handle}  {args.date or TODAY}")
    save_state(args.state, state)
    print(f"\n{len(handles)} account(s) recorded as unfollowed; they will never "
          "be screened as safe to follow.")


def cmd_reconsider(args):
    state = load_state(args.state)
    handles = read_handle_args(args)
    if not handles:
        die("no handles given.")
    for handle in handles:
        rec = state["accounts"].get(handle)
        if not rec:
            warn(f"@{handle} is not in the ledger")
            continue
        dropped = [f for f in ("recommended", "keep") if rec.pop(f, None)]
        print(f"reconsider  @{handle}"
              + (f"  (cleared {', '.join(dropped)})" if dropped else
                 "  (nothing to clear)"))
    save_state(args.state, state)


def cmd_show(args):
    state = load_state(args.state)
    handles = read_handle_args(args)
    if not handles:
        die("no handles given.")
    for handle in handles:
        rec = state["accounts"].get(handle)
        print(f"@{handle}   {profile_url(handle)}")
        if rec is None:
            print("  not in the ledger\n")
            continue
        for key, value in sorted(rec.items()):
            if isinstance(value, list):
                value = ", ".join(value)
            print(f"  {key:<18}{value}")
        print()


def cmd_history(args):
    state = load_state(args.state)
    batches = state["batches"]
    imports = state["imports"]

    if imports:
        print(f"Imports ({len(imports)}):")
        for entry in imports[-args.limit:]:
            bits = [f"{k}={v}" for k, v in entry.items()
                    if k not in ("date", "source")]
            print(f"  {entry['date']}  {' '.join(bits)}")
        print()

    if not batches:
        print("No unfollow batches recommended yet.")
        return

    print(f"Unfollow batches ({len(batches)}):")
    accounts = state["accounts"]
    for batch in batches[-args.limit:]:
        gone = sum(1 for h in batch["handles"]
                   if accounts.get(h, {}).get("unfollowed"))
        print(f"\n  {batch['date']}  {len(batch['handles'])} recommended, "
              f"{gone} since unfollowed  [{batch.get('order', '?')}]")
        if args.verbose:
            for handle in batch["handles"]:
                rec = accounts.get(handle, {})
                mark = "x" if rec.get("unfollowed") else "."
                print(f"    {mark} @{handle}")


# ----------------------------------------------------------------- plumbing ---

def profile_url(handle):
    return f"https://www.instagram.com/{handle}/"


# Opening more than this many tabs at once is almost certainly a typo in -n.
OPEN_TAB_LIMIT = 25


def open_profiles(urls, *, assume_yes=False):
    """Open every profile in the user's browser, for judging by eye.

    Batched into ONE browser invocation wherever possible. $BROWSER here is
    omarchy-launch-browser, which forwards all its arguments to the real
    browser, so a single call yields a tab per URL, one window focus, and no
    need to stagger anything -- which is what you want when the whole point is
    to flick through a dozen profiles and decide.
    """
    if not urls:
        return
    if len(urls) > OPEN_TAB_LIMIT and not assume_yes:
        if not sys.stdin.isatty():
            die(f"refusing to open {len(urls)} tabs unattended. "
                "Pass --yes if you meant it.")
        reply = input(f"Open {len(urls)} browser tabs? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Not opening tabs.")
            return

    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}

    browser = os.environ.get("BROWSER", "").strip()
    if browser:
        try:
            subprocess.Popen(shlex.split(browser) + list(urls), **quiet)
            print(f"\nOpened {len(urls)} tab(s) via $BROWSER ({browser}).")
            return
        except OSError as exc:
            warn(f"$BROWSER ({browser}) would not run ({exc}); "
                 "falling back to xdg-open")

    if shutil.which("xdg-open"):
        # xdg-open takes one URL per call, so this is a tab at a time.
        for url in urls:
            try:
                subprocess.Popen(["xdg-open", url], **quiet)
            except OSError as exc:
                die(f"could not open {url} ({exc})")
        print(f"\nOpened {len(urls)} tab(s) via xdg-open.")
        return

    import webbrowser
    for url in urls:
        webbrowser.open_new_tab(url)
    print(f"\nOpened {len(urls)} tab(s) via the default browser.")


def emit_json(payload):
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def account_json(handle, rec, kind=None):
    """One account as an agent wants it: flat, URL included, no empty keys."""
    out = {"handle": handle, "url": profile_url(handle)}
    if kind:
        out["kind"] = kind
    for field in ("following", "follower", "pending", "keep", "blocked"):
        if rec.get(field):
            out[field] = True
    for field in ("following_since", "follower_since", "requested_on",
                  "note", "source"):
        if rec.get(field):
            out[field] = rec[field]
    # Named apart from screen's own `verdict`, which is a classification of the
    # edge; this one is a judgement someone already recorded about the account.
    if rec.get("verdict"):
        out["recorded_verdict"] = rec["verdict"]
    for field in ("recommended", "unfollowed", "unfollowed_me", "investigated"):
        if rec.get(field):
            out["last_" + field] = rec[field][-1]
    return out


def rel(path):
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def die(msg):
    sys.stdout.flush()   # so the error lands after whatever was being reported
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def build_parser():
    p = argparse.ArgumentParser(
        prog=rel(Path(__file__)),
        description="Track the Instagram follow graph and prune it without "
                    "ever seeing the same account twice.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="The ledger lives in %s and is meant to be checked in."
               % rel(DEFAULT_STATE),
    )
    p.add_argument("--state", type=Path, default=DEFAULT_STATE,
                   metavar="PATH", help="ledger file (default: %(default)s)")
    sub = p.add_subparsers(dest="command", required=True)

    def handle_args(sp):
        sp.add_argument("handles", nargs="*", metavar="HANDLE",
                        help="handles, @handles or profile URLs; "
                             "'-' or a pipe reads stdin")
        sp.add_argument("--file", metavar="PATH",
                        help="read handles from a file as well")

    sp = sub.add_parser("import", help="prime or refresh from a Meta export")
    sp.add_argument("path", help="export .zip, extracted folder, or a single "
                                 "following/followers JSON or HTML file")
    sp.add_argument("--force", action="store_true",
                    help="import even if the following count collapsed")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("status", help="counts for the whole graph")
    sp.add_argument("--format", choices=("text", "json"), default="text")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("candidates",
                        help="next batch to consider unfollowing")
    sp.add_argument("-n", "--number", type=int, default=10, metavar="N",
                    help="how many to list (default: %(default)s)")
    sp.add_argument("--order", choices=("oldest", "newest", "random"),
                    default="oldest",
                    help="which non-reciprocal follows to pick "
                         "(default: %(default)s)")
    sp.add_argument("--seed", type=int, default=None,
                    help="seed for --order random")
    sp.add_argument("--dry-run", action="store_true",
                    help="show a batch without recording it")
    sp.add_argument("--format",
                    choices=("text", "csv", "handles", "urls", "json"),
                    default="text",
                    help="urls: one profile URL per line, for piping; "
                         "json: structured output for an agent to act on")
    sp.add_argument("--open", action="store_true", dest="open_tabs",
                    help="open every profile in your browser, to judge by eye")
    sp.add_argument("--yes", action="store_true",
                    help=f"skip the confirmation above {OPEN_TAB_LIMIT} tabs")
    sp.set_defaults(func=cmd_candidates)

    sp = sub.add_parser("screen",
                        help="vet prospective follows against your history")
    handle_args(sp)
    sp.add_argument("--format", choices=("text", "csv", "handles", "json"),
                    default="text",
                    help="handles: only the safe ones, for piping; "
                         "json: structured output for an agent")
    sp.set_defaults(func=cmd_screen)

    sp = sub.add_parser(
        "record",
        help="write a batch of investigation findings back (JSON in)",
        description="Reads JSON -- a list of {handle, verdict, note} objects, "
                    "or an object with an \"accounts\" list -- from stdin or "
                    "--file. Verdicts: "
                    + "; ".join(f"{k} ({v})"
                                for k, v in sorted(RECORD_VERDICTS.items())),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument("handles", nargs="*", metavar="-",
                    help="'-' to read stdin explicitly")
    sp.add_argument("--file", metavar="PATH", help="read the JSON from a file")
    sp.add_argument("--date", help="ISO date for the findings (default: today)")
    sp.add_argument("--format", choices=("text", "json"), default="text")
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("followed",
                        help="record a follow by hand (import infers these)")
    handle_args(sp)
    sp.add_argument("--date", help="ISO date (default: today)")
    sp.set_defaults(func=cmd_followed)

    sp = sub.add_parser("keep", help="never recommend these for unfollow")
    handle_args(sp)
    sp.add_argument("--note", help="why, for your own benefit later")
    sp.set_defaults(func=cmd_keep)

    sp = sub.add_parser("unfollowed",
                        help="record an unfollow by hand (import infers these)")
    handle_args(sp)
    sp.add_argument("--date", help="ISO date (default: today)")
    sp.set_defaults(func=cmd_unfollowed)

    sp = sub.add_parser("reconsider",
                        help="clear recommended/keep so a handle can come up again")
    handle_args(sp)
    sp.set_defaults(func=cmd_reconsider)

    sp = sub.add_parser("show", help="everything the ledger knows about a handle")
    handle_args(sp)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("history", help="past imports and unfollow batches")
    sp.add_argument("--limit", type=int, default=10,
                    help="how many of each to show (default: %(default)s)")
    sp.add_argument("-v", "--verbose", action="store_true",
                    help="list the handles in each batch")
    sp.set_defaults(func=cmd_history)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if getattr(args, "date", None):
        try:
            dt.date.fromisoformat(args.date)
        except ValueError:
            die(f"--date must be an ISO date like 2026-10-28, got {args.date!r}")
    args.func(args)


if __name__ == "__main__":
    main()
