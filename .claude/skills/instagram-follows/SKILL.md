---
name: instagram-follows
description: Manage the Rangerly Instagram following with Tools/instagram-follows.py. Use when asked to open a batch of accounts to consider unfollowing, to mine a seed account's followers for mountain-bike accounts worth following, to screen or vet a list of accounts, to check who doesn't follow back, or to import a fresh Instagram data export.
---

# Instagram follow management

`Tools/instagram-follows.py` keeps a ledger of the follow graph in
`Instagram/follow-graph.json` — who follows back, who has already been
recommended, who was unfollowed and must never be suggested again. Run it from
the repo root. `--help` on any subcommand is authoritative if this file and the
script disagree.

The two halves of the work are not symmetrical. **Unfollowing needs no
investigation from you** — the user judges profiles by eye. **Following does**,
and the criteria are specific; they are below, and they are not guesses.

## Before anything else

```bash
Tools/instagram-follows.py status
```

No state file means the ledger has never been primed: stop and tell the user to
export their data (Instagram → Settings → Accounts Center → Your information
and permissions → Download your information → JSON, "Followers and following")
and `import` the zip. Do not invent a ledger.

If `updated` is more than a few weeks old, say so. The ledger only learns about
hand-unfollows at import time, so a stale one will re-recommend accounts the
user already dealt with.

## Workflow A — accounts to consider unfollowing

Just run the command. Do **not** open profiles yourself, read them, or form an
opinion about who to drop:

```bash
Tools/instagram-follows.py candidates -n 12 --open
```

That picks accounts the user follows who don't follow back, oldest follows
first, records them so they never come up again, and opens every profile as a
browser tab. The user flicks through the tabs and decides. That is the whole
job — your value here is the ledger's memory, not your judgement.

Notes:

- The batch is recorded whether or not the user acts on it, so don't run it
  speculatively. `--dry-run` shows a batch without recording or opening.
- Nothing needs to be written back. The user unfollows in the app and the next
  `import` infers it by diffing the export against the ledger.
- `--open` batches every URL into one browser invocation via `$BROWSER`
  (`omarchy-launch-browser`), so it is one window focus, not twelve.
- Without `--open` the text output lists the URLs anyway; `--format urls` emits
  them bare for piping.

## Workflow B — mining a seed account for accounts to follow

The user names a seed account; you look through **its followers** and return
accounts worth following. This is where browser work is warranted.

Load the Chrome tools in ONE call:

```
ToolSearch "select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp,mcp__claude-in-chrome__javascript_tool"
```

If those tools don't exist and there's no `enable__mcp__claude-in-chrome`,
Claude in Chrome isn't connected. Say so and stop — do not fall back to
WebFetch, which sees a logged-out page and will report every account as empty.
Follow the `chrome-browser` skill for tab hygiene.

### 1. Gather the seed's followers

Open `https://www.instagram.com/<seed>/followers/` — the list only renders for
a public account or one the user follows. It lazy-loads, so scroll the dialog
and pull handles as they appear:

```js
[...document.querySelectorAll('div[role="dialog"] a[href^="/"]')]
  .map(a => a.getAttribute('href').replace(/\//g, ''))
```

Collect a few hundred if they're there. Gathering handles is cheap; opening
profiles is not.

### 2. Screen against the ledger before opening anything

```bash
Tools/instagram-follows.py screen --file handles.txt --format json
```

Only `safe_to_follow` entries are worth a profile visit. **An account the user
unfollowed before is one they already judged — never re-propose it**, and don't
argue with the ledger. Report the excluded ones as a count with reasons.

### 3. Judge each remaining account on all five criteria

These come from what has actually worked. An account needs **all five**:

| Criterion | How to read it |
| --- | --- |
| **Followers ≈ following** | Roughly a 1:1 ratio — take 0.5–2.0 as "approximately equal". A large follower count against a small following means they won't follow back; the inverse is a follow farm. |
| **Posted within 3 months** | The decisive signal, and not on the profile grid. Open the first post and read `document.querySelector('time')?.getAttribute('datetime')`. An empty grid fails this. |
| **Some mountain bike content** | Bikes, trails or riding in the grid; MTB terms or brands in the bio (enduro, singletrack, DH, Santa Cruz, Trek, Specialized, trail crew). A general outdoors account with no riding fails. |
| **Not under 18** | Exclude anyone who reads as a minor — a stated age, a graduation year ("c/o 2027"), a high school, or plainly teenaged content. Judge conservatively: **if it's ambiguous, exclude**, and say why in the note. Getting this wrong matters more than missing a follow. |
| **Based in US or Canada** | Bio location, named trails, parks, states or provinces, language. Unstated and unguessable means exclude. |

Aim for about 25 accounts that pass. Work through profiles one at a time.

### 4. Record the findings

```bash
Tools/instagram-follows.py record - <<'JSON'
[
  {"handle": "rider_one", "verdict": "follow",  "source": "seedaccount",
   "note": "1.1k/980, posted 3d ago, enduro, Squamish BC"},
  {"handle": "rider_two", "verdict": "unclear", "source": "seedaccount",
   "note": "fits otherwise, no location anywhere"}
]
JSON
```

`source` is the seed account, so the user can tell which seeds are worth mining
again. Use `follow` for a recommendation and `followed` only if the user had
you do the following in Chrome — `followed` updates the graph immediately so
the account isn't re-proposed before the next import.

Finish by reporting the passing accounts with their evidence, and say which
criterion knocked out the near-misses. That is how the criteria get tuned.

## Pacing and when to stop

Scrolling a followers list and opening dozens of profiles is exactly what trips
Instagram's action blocks. Work one profile at a time, and the moment you see a
login wall, a "Try again later", or any action block: stop, record what you
have, and report it. Never retry through one.

If a profile won't load after two attempts, record it `unclear` with that as the
note and move on.

## Other commands

- `keep @handle --note "why"` — never recommend for unfollow.
- `reconsider @handle` — clears recommended/keep so it can come up again.
- `show @handle` — everything the ledger knows, prior notes and source included.
- `history -v` — past batches and how many were actually unfollowed.
- `followed @handle` / `unfollowed @handle` — record an action by hand.
- `import <export.zip>` — refresh ground truth; refuses a suspiciously small
  export unless `--force`.
