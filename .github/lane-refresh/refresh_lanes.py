#!/usr/bin/env python3
r"""Refresh the six type pages' listing cards from live Buildout, in place.

    BUILDOUT_API_KEY=... python .github/lane-refresh/refresh_lanes.py
    BUILDOUT_API_KEY=... python .github/lane-refresh/refresh_lanes.py --dry-run
    python .github/lane-refresh/refresh_lanes.py --offline C:\scratch\payload.json

One Buildout pull, then a SPLICE, not a page rebuild. For each page in
`lanes.json` exactly two regions are replaced and every other byte is left as
it is:

  1. the cards inside `<div class="rpLaneWrapper"> ... <div class="rpLane">`,
     rendered by `lanes.cards_for()` so the markup matches the original
     generator. An empty lane gets the same `<p class="rpLaneEmpty">` line
     `build_page.py` emits, with that lane's `lane_empty` text.
  2. `numberOfItems` and `itemListElement` of the ItemList node in the page's
     own JSON-LD block, built by `itemlist.listing_node()` from the same rows.

Why splice: the pages carry later hand edits (internal-link fixes, footer and
nav changes) that a spec rebuild would not reproduce.

Guards. Any of these exits nonzero and writes NOTHING:

  * the pull returns 0 properties;
  * the new card total across the six pages is under 50% of the current
    on-page total (a Buildout outage must not blank the pages);
  * a page does not hold exactly one card region and exactly one ItemList
    JSON-LD block, holds a card outside that region, has mixed line endings,
    or its JSON-LD does not round-trip byte-for-byte through `json.dumps`.

Every page is read and checked before Buildout is called, and every page is
built before the first one is written. Line endings are preserved: CRLF on a
Windows checkout with autocrlf, LF on the Actions runner.

Idempotent: a second run on the same data changes nothing. `--dry-run` prints
the per-lane added / removed / changed cards and writes nothing. The payload is
never printed and never written; only listing names and addresses that would
appear on the public pages are printed.
"""
import argparse
import html
import json
import os
import re
import sys

import itemlist
import lanes

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "lanes.json")
MIN_RATIO = 0.5
TMP_SUFFIX = ".lane-refresh.tmp"

LANE_OPEN = '\t<div class="rpLane">\n'
LANE_CLOSE = '\t</div>\n'

# The card region: the wrapper and its <h2> stay; group 2 is what gets replaced,
# either the rpLane grid or the rpLaneEmpty paragraph. Matched on LF text.
LANE_RE = re.compile(
    r'(<div class="rpLaneWrapper">\n\t<h2>[^\n]*</h2>\n)'
    r'(\t<div class="rpLane">\n(?:\t\t[^\n]*\n)*\t</div>\n'
    r'|\t<p class="rpLaneEmpty">[^\n]*</p>\n)'
    r'(?=</div>)')
CARD_RE = re.compile(r'\t\t<a class="rpCard" href="([^"]*)">\n(?:\t\t\t[^\n]*\n)*\t\t</a>')
H3_RE = re.compile(r'<h3>(.*?)</h3>')
PROPERTY_ID_RE = re.compile(r'propertyId=(\d+)-(?:sale|lease)\b')
# The page's own JSON-LD block. The site-wide Rank Math block carries a class
# attribute, so it never matches.
LDJSON_RE = re.compile(r'<script type="application/ld\+json">\n(.*?)\n</script>', re.S)


class Abort(Exception):
    pass


# ------------------------------------------------------------------ page I/O

def read_page(path):
    """(LF text, newline, raw bytes). Refuses mixed line endings, and proves the
    LF round trip reproduces the file exactly before anything else happens."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Abort("%s: not valid UTF-8 (%s)" % (path, exc)) from None
    crlf = text.count("\r\n")
    if crlf and crlf == text.count("\n"):
        nl = "\r\n"
        text = text.replace("\r\n", "\n")
    elif crlf == 0:
        nl = "\n"
    else:
        raise Abort("%s: mixed line endings (%d CRLF of %d LF)" % (path, crlf, text.count("\n")))
    if encode(text, nl) != raw:
        raise Abort("%s: line-ending round trip is not byte-identical" % path)
    return text, nl, raw


def encode(text, nl):
    return (text.replace("\n", nl) if nl != "\n" else text).encode("utf-8")


# ---------------------------------------------------------------- page parse

def card_name(block):
    m = H3_RE.search(block)
    return html.unescape(m.group(1)) if m else "(no name)"


def parse(path, text):
    """Locate both splice regions and the cards currently on the page."""
    regions = list(LANE_RE.finditer(text))
    if len(regions) != 1:
        raise Abort("%s: expected exactly one lane card region, found %d" % (path, len(regions)))
    region = regions[0]
    inner = region.group(2)

    cards = []
    if inner.startswith(LANE_OPEN):
        body = inner[len(LANE_OPEN):-len(LANE_CLOSE)]
        blocks = list(CARD_RE.finditer(body))
        if "".join(b.group(0) + "\n" for b in blocks) != body:
            raise Abort("%s: the lane grid holds something other than cards" % path)
        cards = [(b.group(1), b.group(0), card_name(b.group(0))) for b in blocks]
    if text.count('<a class="rpCard"') != len(cards):
        raise Abort("%s: %d rpCard links on the page but %d inside the lane region"
                    % (path, text.count('<a class="rpCard"'), len(cards)))

    scripts = [m for m in LDJSON_RE.finditer(text) if '"ItemList"' in m.group(1)]
    if len(scripts) != 1:
        raise Abort("%s: expected exactly one ItemList JSON-LD block, found %d" % (path, len(scripts)))
    script = scripts[0]
    try:
        schema = json.loads(script.group(1))
    except ValueError as exc:
        raise Abort("%s: ItemList JSON-LD does not parse: %s" % (path, exc)) from None
    if json.dumps(schema, indent=1) != script.group(1):
        raise Abort("%s: ItemList JSON-LD is not in json.dumps(indent=1) form; "
                    "refusing to rewrite it" % path)
    graph = schema.get("@graph") if isinstance(schema, dict) else None
    nodes = [n for n in (graph or []) if isinstance(n, dict) and n.get("@type") == "ItemList"]
    if len(nodes) != 1:
        raise Abort("%s: expected exactly one ItemList node, found %d" % (path, len(nodes)))
    node = nodes[0]
    for key in ("numberOfItems", "itemListElement"):
        if key not in node:
            raise Abort("%s: ItemList node has no %s" % (path, key))
    if script.end(1) > region.start(2):
        raise Abort("%s: JSON-LD block is not above the lane region" % path)

    return dict(region=region, script=script, schema=schema, node=node, cards=cards)


# --------------------------------------------------------------------- build

def rebuild(text, parsed, props, lane):
    """The page text with both regions replaced, plus the new card list."""
    html_cards, listings = lanes.cards_for(props, lane["filter"])
    if listings:
        inner = LANE_OPEN + html_cards + "\n" + LANE_CLOSE
    else:
        inner = '\t<p class="rpLaneEmpty">%s</p>\n' % lanes.e(lane["lane_empty"])

    node = parsed["node"]
    node["numberOfItems"] = len(listings)
    node["itemListElement"] = itemlist.item_list_elements(listings)
    ld = json.dumps(parsed["schema"], indent=1)

    region, script = parsed["region"], parsed["script"]
    out = (text[:script.start(1)] + ld + text[script.end(1):region.start(2)]
           + inner + text[region.end(2):])
    new_cards = [(lanes.deep_link(p), lanes.card_html(p), p) for p in listings]
    return out, new_cards


def card_key(href):
    """A card's identity is its Buildout property id, so a listing whose deep
    link moves from the lease side to the sale side reads as changed, not as
    one removal plus one addition."""
    m = PROPERTY_ID_RE.search(href)
    return m.group(1) if m else href


def report(page, old_cards, new_cards):
    old = {card_key(href): (block, name, href) for href, block, name in old_cards}
    new = {card_key(href): (block, p, href) for href, block, p in new_cards}
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and new[k][0] != old[k][0]]
    print("%s: %d -> %d cards (+%d -%d ~%d)"
          % (page, len(old_cards), len(new_cards), len(added), len(removed), len(changed)))
    for k in removed:
        print("  - %s  [%s]" % (old[k][1], old[k][2]))
    for k in added:
        p = new[k][1]
        name = p.get("name") or p.get("address") or "Commercial property"
        print("  + %s | %s  [%s]" % (name, lanes.address_line(p), new[k][2]))
    for k in changed:
        print("  ~ %s  [%s]" % (old[k][1], new[k][2]))


# ---------------------------------------------------------------------- main

def run(args):
    with open(CONFIG, encoding="utf-8") as fh:
        config = json.load(fh)["lanes"]
    for lane in config:
        if lane["filter"] not in lanes.FILTERS:
            raise Abort("lanes.json: unknown filter %s" % lane["filter"])

    site = os.path.abspath(args.site)
    pages = []
    for lane in config:
        path = os.path.join(site, *lane["page"].split("/"))
        if not os.path.isfile(path):
            raise Abort("page not found: %s" % lane["page"])
        text, nl, raw = read_page(path)
        pages.append(dict(lane=lane, path=path, text=text, nl=nl, raw=raw,
                          parsed=parse(lane["page"], text)))

    props = lanes.load(args.offline, args.limit)
    if not props:
        raise Abort("Buildout returned 0 properties; nothing written")

    old_total = new_total = 0
    for pg in pages:
        out, new_cards = rebuild(pg["text"], pg["parsed"], props, pg["lane"])
        pg["new_raw"] = encode(out, pg["nl"])
        pg["new_cards"] = new_cards
        old_total += len(pg["parsed"]["cards"])
        new_total += len(new_cards)

    for pg in pages:
        report(pg["lane"]["page"], pg["parsed"]["cards"], pg["new_cards"])
    print("total: %d -> %d cards across %d pages (%d properties in the pull)"
          % (old_total, new_total, len(pages), len(props)))

    if new_total < MIN_RATIO * old_total:
        raise Abort("new card total %d is under %d%% of the current %d; refusing to write"
                    % (new_total, MIN_RATIO * 100, old_total))

    changed = [pg for pg in pages if pg["new_raw"] != pg["raw"]]
    if args.dry_run:
        print("dry run: %d page(s) would change, nothing written" % len(changed))
        return 0
    if not changed:
        print("no changes")
        return 0

    # Every page is built; stage all, then swap all in.
    for pg in changed:
        with open(pg["path"] + TMP_SUFFIX, "wb") as fh:
            fh.write(pg["new_raw"])
    for pg in changed:
        os.replace(pg["path"] + TMP_SUFFIX, pg["path"])
    print("wrote %d page(s): %s" % (len(changed), ", ".join(pg["lane"]["page"] for pg in changed)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Splice live Buildout cards into the six type pages.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print per-lane added/removed cards, write nothing")
    ap.add_argument("--offline", metavar="PATH", default=None,
                    help="read this payload JSON (must be outside the repo) instead of calling Buildout")
    ap.add_argument("--site", default=lanes.REPO,
                    help="site root holding the pages (default: this repo)")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    try:
        return run(args)
    except Abort as exc:
        print("ABORT: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
