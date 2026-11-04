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

1. pulls the on-market inventory from the Buildout API once;
2. runs `refresh_lanes.py`, which splices each page's card grid and its ItemList
   JSON-LD (`numberOfItems` + `itemListElement`) and leaves every other byte of
   the page alone;
3. checks that only those six `index.html` files changed;
4. if anything changed, commits `Lane refresh YYYY-MM-DD (auto)` as
   `github-actions[bot]` and pushes, which publishes through GitHub Pages.

Only public listings make a card: a listing is dropped when it is a draft or a
proposal, and the side a lane lists (lease or sale) has to be published,
searchable (Private is the unsearchable one) and not a proposal. Industrial and
restaurant take either side, need at least one public side, and link only to a
public side.

## Files

| File | What it is |
|---|---|
| `lanes.py` | Buildout pull, lane filters, public-only guard, card markup. Port of the Media Server's `tools/build_lanes.py`. |
| `itemlist.py` | The ItemList row builder, lifted from `tools/build_page.py`. |
| `lanes.json` | The six lanes: filter, page path, the empty-lane text. |
| `refresh_lanes.py` | One pull, then the splice into the six pages. |

## Guards

`refresh_lanes.py` exits nonzero and writes nothing when:

- the pull returns 0 properties, or looks truncated;
- the new card total across the six pages is under 50% of what is on the pages
  now (a Buildout outage must not blank the pages);
- a page does not hold exactly one card region and exactly one ItemList block,
  has a card outside that region, has mixed line endings, or its JSON-LD is not
  in the exact format the generator writes.

Every page is checked before Buildout is called and every page is built before
any is written. The workflow then refuses to commit if anything other than the
six pages changed. Running it twice on the same data changes nothing.

## Secret

The job needs the Actions repository secret `BUILDOUT_API_KEY` (Settings >
Secrets and variables > Actions). The key is read only from that environment
variable. It is never printed, logged, cached or written to a file. If the
secret is missing the run fails with a clear message and changes nothing.

The raw Buildout payload is never written or committed: it carries internal
fields like notes, custom fields and documents, and this repo is public.
`.gitignore` blocks `*.payload.json`, `lanes.cache.json` and `properties.json`
as a backstop.

## Run it locally

From the repo root, with the key in the environment for that one process:

```
BUILDOUT_API_KEY=... python .github/lane-refresh/refresh_lanes.py --dry-run
BUILDOUT_API_KEY=... python .github/lane-refresh/refresh_lanes.py
```

`--dry-run` prints each lane's added, removed and changed cards and writes
nothing. `--offline PATH` reads a payload JSON from PATH instead of calling
Buildout, for tests. PATH has to be outside the repo. `python
.github/lane-refresh/lanes.py` prints per-lane counts only.

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
file list in the workflow's "Allowed-files guard" step together.
