#!/usr/bin/env python3
"""Independent block-level guard for the lane refresh workflow.

    python .github/lane-refresh/check_regions.py

Run after refresh_lanes.py and before the commit. For every file changed
against HEAD it compares `git show HEAD:<path>` with the working file after
cutting two blocks out of both:

  * the card grid: what follows `<div class="rpLaneWrapper">` + its <h2> line,
    up to the wrapper's closing `</div>` line;
  * the body of the one `<script type="application/ld+json">` block that holds
    the ItemList. Inside that block every node except the ItemList's
    `numberOfItems` and `itemListElement` must also be unchanged.

Everything left must be byte-identical (CRLF and LF are treated alike). It also
checks that the new ItemList count matches its rows and the cards on the page,
and that no `</` sits unescaped inside the JSON-LD.

Deliberately shares no code with refresh_lanes.py or lanes.py: it is a second,
simpler opinion on what changed, so a bug in the refresh cannot vouch for
itself. Exit 0 = every changed file passes (or nothing changed); exit 1 = refuse
to commit.
"""
import json
import os
import re
import subprocess
import sys

GRID_RE = re.compile(r'(<div class="rpLaneWrapper">\n\t<h2>[^\n]*</h2>\n)(.*?)(?=^</div>$)', re.S | re.M)
LD_RE = re.compile(r'(<script type="application/ld\+json">\n)(.*?)(\n</script>)', re.S)
CARD = '<a class="rpCard"'


class Fail(Exception):
    pass


def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, check=True).stdout


def normalise(raw, label):
    try:
        return raw.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise Fail("%s: not UTF-8" % label) from None


def cut(text, label):
    """(text with both blocks cut out, the ItemList JSON-LD minus its rows,
    the ItemList node, the card count, the raw JSON-LD body)."""
    grids = list(GRID_RE.finditer(text))
    if len(grids) != 1:
        raise Fail("%s: expected one card grid, found %d" % (label, len(grids)))
    g = grids[0]
    cards = g.group(2).count(CARD)
    if text.count(CARD) != cards:
        raise Fail("%s: card links outside the grid" % label)
    text = text[:g.start(2)] + "\x00GRID\x00\n" + text[g.end(2):]

    blocks = [m for m in LD_RE.finditer(text) if '"ItemList"' in m.group(2)]
    if len(blocks) != 1:
        raise Fail("%s: expected one ItemList JSON-LD block, found %d" % (label, len(blocks)))
    b = blocks[0]
    try:
        data = json.loads(b.group(2))
    except ValueError as exc:
        raise Fail("%s: ItemList JSON-LD does not parse: %s" % (label, exc)) from None
    items = [n for n in data.get("@graph", []) if isinstance(n, dict) and n.get("@type") == "ItemList"]
    if len(items) != 1:
        raise Fail("%s: expected one ItemList node, found %d" % (label, len(items)))
    node = dict(items[0])
    rest = json.loads(b.group(2))
    for n in rest["@graph"]:
        if isinstance(n, dict) and n.get("@type") == "ItemList":
            n.pop("numberOfItems", None)
            n.pop("itemListElement", None)
    text = text[:b.start(2)] + "\x00LD\x00" + text[b.end(2):]
    return text, rest, node, cards, b.group(2)


def check(root, path):
    try:
        old_raw = git("show", "HEAD:" + path)
    except subprocess.CalledProcessError:
        raise Fail("%s: not in HEAD" % path) from None
    try:
        with open(os.path.join(root, *path.split("/")), "rb") as fh:
            new_raw = fh.read()
    except OSError as exc:
        raise Fail("%s: cannot read the working file (%s)" % (path, exc)) from None
    old_text, old_rest, _, old_cards, _ = cut(normalise(old_raw, path + " (HEAD)"), path + " (HEAD)")
    new_text, new_rest, node, new_cards, new_ld = cut(normalise(new_raw, path), path)
    if old_text != new_text:
        a, b = old_text.split("\n"), new_text.split("\n")
        line = next((i + 1 for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)) + 1)
        raise Fail("%s: changed outside the card grid and ItemList block (first at line ~%d)" % (path, line))
    if old_rest != new_rest:
        raise Fail("%s: JSON-LD changed outside numberOfItems / itemListElement" % path)
    rows = node.get("itemListElement")
    if not isinstance(rows, list) or node.get("numberOfItems") != len(rows) or len(rows) != new_cards:
        raise Fail("%s: numberOfItems %s, %s rows, %d cards disagree"
                   % (path, node.get("numberOfItems"), len(rows) if isinstance(rows, list) else "?", new_cards))
    if "</" in new_ld:
        raise Fail("%s: unescaped </ inside the JSON-LD" % path)
    return old_cards, new_cards


def main():
    root = git("rev-parse", "--show-toplevel").decode().strip()
    changed = [p for p in git("diff", "--name-only", "HEAD").decode().split("\n") if p]
    bad = 0
    for path in changed:
        try:
            old_cards, new_cards = check(root, path)
            print("ok   %s  (%d -> %d cards; only the grid and ItemList changed)" % (path, old_cards, new_cards))
        except Fail as exc:
            print("::error::%s" % exc)
            bad += 1
    if not changed:
        print("nothing changed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
