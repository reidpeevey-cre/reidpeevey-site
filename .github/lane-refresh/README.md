# Lane refresh

Keeps the listing cards on the six type pages in step with Buildout:

- /commercial-property-for-lease-waco/
- /retail-space-for-lease-waco/
- /industrial-property-waco/
- /warehouse-for-lease-waco/
- /restaurant-space-waco/
- /commercial-land-for-sale-waco/

The cards are static HTML on our own domain (good for SEO), so without a refresh
they go stale. In Nov 2026 they still showed listings Pat had since set to
Proposal or Private in Buildout. This job fixes that and keeps it fixed.

## What it does

`.github/workflows/lane-refresh.yml` runs daily at 12:15 UTC (6:15 AM CST,
7:15 AM CDT; GitHub can run scheduled jobs late) and on demand from the Actions
tab ("Run workflow"). It:

1. checks out the current `main` (not the SHA that queued the run);
2. runs `refresh_lanes.py`: one Buildout pull, paged 100 rows at a time, then a
   splice of each page's card grid and its ItemList JSON-LD (`numberOfItems` +
   `itemListElement`), leaving every other byte of the page alone;
3. checks that only those six `index.html` files changed, then runs
   `check_regions.py`, an independent check that nothing outside the card grid
   and the ItemList block differs from `HEAD`;
4. if anything changed, commits `Lane refresh YYYY-MM-DD (auto)` as
   `github-actions[bot]`, rebases onto `main` (`git pull --rebase`), pushes
   (one retry of the pull and push), and asks GitHub Pages for a build.

Only public listings make a card: a listing is dropped when it is a draft or a
proposal, and the side a lane lists (lease or sale) has to be published,
searchable (Private is the unsearchable one) and not a proposal. A side that is
not public is masked off the card, so its price and availability never show.

Card links follow the lane: the three lease lanes link a listing's lease side,
the land lane its sale side, and industrial and restaurant (either side) keep
sale first. The ItemList `url` is always the card's link. A Buildout listing
URL is used only when it starts with `https://` or `http://`; otherwise the
card links to the /properties search for that listing.

Every Buildout string that reaches a card or the ItemList has each run of
control characters (newlines, tabs and the like) turned into one space, and the
JSON-LD is written with `</` as `<\/` and `<!--` as `<!--`, so listing text
can never break the page or close the `<script>` block.

## Files

| File | What it is |
|---|---|
| `lanes.py` | Buildout pull, lane filters, public-only guard, card markup. Port of the Media Server's `tools/build_lanes.py`. |
| `itemlist.py` | The ItemList row builder, lifted from `tools/build_page.py`. |
| `lanes.json` | The six lanes: filter, page path, the empty-lane text. |
| `refresh_lanes.py` | One pull, then the splice into the six pages. |
| `check_regions.py` | The workflow's independent block-level guard. Shares no code with the others. |

## Guards

`refresh_lanes.py` exits nonzero and writes nothing when:

- the pull returns 0 properties, hits the 20-page cap, or has fewer rows than
  the first page's `count`;
- the new card total across the six pages is under 50% of what is on the pages
  now (a Buildout outage must not blank the pages);
- a page does not hold exactly one card region and exactly one ItemList block,
  has a card outside that region, has mixed line endings, or its JSON-LD is not
  in the exact format the generator writes;
- self-check: a rebuilt page would fail any of those checks on the next run, or
  rebuilding it again from the same pull would change it. So a broken page is
  never written, and a written page never blocks tomorrow's run.

Every page is checked before Buildout is called and every page is built and
self-checked before any is written. The workflow then refuses to commit if
anything other than the six pages changed, or if `check_regions.py` finds a
change outside the card grid and ItemList block. Running it twice on the same
data changes nothing.

## Secret

The job needs the Actions repository secret `BUILDOUT_API_KEY` (Settings >
Secrets and variables > Actions). The key is read only from that environment
variable. It is never printed, logged, cached or written to a file. If the
secret is missing the run fails with a clear message and changes nothing. The
workflow's own token has `contents: write` (the commit) and `pages: write` (the
build request) and nothing else.

The raw Buildout payload is never written or committed: it carries internal
fields like notes, custom fields and documents, and this repo is public.
`.gitignore` blocks `*.payload.json`, `lanes.cache.json` and `properties.json`
as a backstop.

## Run it locally

From the repo root, with the key in the environment for that one process:

```
BUILDOUT_API_KEY=... python .github/lane-refresh/refresh_lanes.py --dry-run
BUILDOUT_API_KEY=... python .github/lane-refresh/refresh_lanes.py
python .github/lane-refresh/check_regions.py
```

`--dry-run` prints each lane's added, removed and changed cards and writes
nothing. `--offline PATH` reads a payload JSON from PATH instead of calling
Buildout, for tests. PATH has to be outside the repo. `python
.github/lane-refresh/lanes.py` prints per-lane counts only. `check_regions.py`
checks every file changed against `HEAD`, so run it when only lane pages are
modified.

Review the diff before pushing anything by hand. Normally the scheduled job does
the refresh; there is no need to commit refreshed pages from a workstation.

## Source of truth

This folder is now the source of truth for the type pages' listing cards. The
Media Server's `tools/build_lanes.py` and `tools/build_page.py` still build page
copy from the specs, but a full rebuild there would reintroduce whatever cards
that copy renders, so rebuild a type page there only on purpose and let the next
daily run reset its cards. Lane filters, the broker map and card markup changes
belong here.

If a lane is ever added or a page moves, update `lanes.json` and the allowed
file list in the workflow's "Allowed-files and block-level guard" step together.
