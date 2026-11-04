#!/usr/bin/env python3
"""Pull the firm's on-market Buildout inventory and render listing cards.

Repo port of the Media Server's `tools/build_lanes.py` (2026-11-04). The six
type pages ( /retail-space-for-lease-waco/ and friends ) carry STATIC cards
generated here, not a filtered Buildout iframe. The iframe is cross-origin so
it is worth exactly zero for SEO; these cards are real HTML on our own domain.
`refresh_lanes.py` imports `cards_for()` and splices the result into the pages.

    BUILDOUT_API_KEY=... python .github/lane-refresh/lanes.py [--limit 200]
    python .github/lane-refresh/lanes.py --offline C:\\scratch\\payload.json

Changes from the Media Server original, and nothing else:

  * The key comes ONLY from the `BUILDOUT_API_KEY` environment variable (an
    Actions secret on GitHub). It is never printed and never written anywhere.
  * No cache file is ever written. The raw payload carries internal fields
    (notes, custom fields, documents) and this repo is public.
  * `--offline PATH` reads a payload JSON the caller supplies, for tests. The
    path must be outside the repo so a payload can never sit in the tree.
  * Public-only guard (Pat's Buildout bug report, 2026-11-04): a listing is
    dropped when `draft` or `proposal` is set, and the side a lane lists must be
    public, meaning `<side>`, `<side>_listing_published` and
    `<side>_listing_searchable` are all set and `<side>_proposal` is not. The
    side-agnostic lanes (industrial, restaurant) need at least one public side,
    and `deep_link()` only ever points at a public side.
  * A live pull that comes back with `limit` rows or more, or with fewer rows
    than the payload's own `count`, is refused as possibly truncated instead of
    silently dropping listings.

Card markup, filters, sort order, the broker map, the fallback image and the
price, size and availability logic are unchanged, so an unchanged listing
renders byte-identical to what is on the pages today.

FIELD NOTE (probed 2026-08-26, 96 on-market properties): `properties.json`
carries `hide_sale_price` + `sale_price_dollars`, but it carries NO lease rate
and NO lease-rate hide flag - not `lease_rate_dollars`, not `lease_rate_units`,
nothing rate-shaped at all. Buildout keeps asking rates on SPACES, and
`spaces.json` currently answers HTTP 500 on this key. So a lease card shows
no rate line today. `lease_rate()` below already reads the two documented
field names when they appear, so the day the payload (or a spaces join) carries
them, rates render with no code change.
"""
import argparse
import html
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

API = "https://buildout.com/api/v1/%s/properties.json?on_market_only=true&limit=%d"
KEY_ENV = "BUILDOUT_API_KEY"

# Buildout broker id -> display name. Copied from the BPO engine's own map,
# `Tech/Active/bpo-automation/server.py` BROKER_ID_NAME (lines 129-134), which
# came from the old listing tool's broker dropdown. The property payload carries
# only ids, never names. Names and work emails are already public on /agents.
BROKERS = {
    166000: ("Pat Farrar", "pat@reidpeevey.com"),
    167152: ("Jim Peevey SIOR CCIM", "jim@reidpeevey.com"),
    167153: ("Randy Reid", "randy@reidpeevey.com"),
    167154: ("Griffin Peevey", "griffin@reidpeevey.com"),
    264710: ("Josh Barrett", "josh@reidpeevey.com"),
}

# The theme has no generic listing placeholder. sectionpic-1.jpg is the neutral
# banner the whole site already uses, so it stands in when a listing has no
# photo.
FALLBACK_IMG = "/wp-content/themes/reidpeevey/images/sectionpic-1.jpg"

# Buildout warehouse/distribution subtypes, and the retail restaurant subtype.
WAREHOUSE_SUBTYPES = (302, 303)
RESTAURANT_SUBTYPE = 208


# ---------------------------------------------------------------- data access

def api_key():
    key = (os.environ.get(KEY_ENV) or "").strip()
    if not key:
        raise SystemExit("%s is not set. Export it in the environment (an Actions "
                         "secret on GitHub); it is never read from a file." % KEY_ENV)
    return key


def _scrub(text, key):
    return str(text).replace(key, "***") if key else str(text)


def fetch(limit=200):
    """Live pull. The key never reaches stdout, stderr or disk, and nothing is
    cached. Errors are re-raised without the request URL, which carries the key."""
    key = api_key()
    try:
        with urllib.request.urlopen(API % (key, limit), timeout=120) as fh:
            payload = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - re-raised as a scrubbed message
        raise SystemExit("Buildout pull failed: %s: %s"
                         % (type(exc).__name__, _scrub(exc, key))) from None
    return payload


def _inside_repo(path):
    path = os.path.normcase(os.path.realpath(path))
    repo = os.path.normcase(os.path.realpath(REPO))
    try:
        return os.path.commonpath([path, repo]) == repo
    except ValueError:  # different drives on Windows
        return False


def load_payload(path):
    """A payload JSON from disk, for tests. Refused inside the repo."""
    if _inside_repo(path):
        raise SystemExit("--offline payload must live outside the repo: %s" % path)
    if not os.path.isfile(path):
        raise SystemExit("--offline payload not found: %s" % path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load(offline=None, limit=200):
    """The property list. `offline` is a payload path, or None for a live pull."""
    payload = load_payload(offline) if offline else fetch(limit)
    if isinstance(payload, list):
        props = payload
    elif isinstance(payload, dict):
        props = payload.get("properties") or []
    else:
        raise SystemExit("Buildout payload is neither an object nor a list")
    if not offline:
        total = payload.get("count") if isinstance(payload, dict) else None
        if len(props) >= limit or (isinstance(total, int) and total > len(props)):
            raise SystemExit("Buildout returned %d rows (count %s, limit %d): possibly "
                             "truncated. Raise --limit." % (len(props), total, limit))
    return props


# --------------------------------------------------------- public-only guard

SIDES = ("sale", "lease")


def side_public(p, side):
    """A side is public when it is offered, published, searchable and not a
    proposal. A Private listing in Buildout is the unsearchable one."""
    return (bool(p.get(side))
            and bool(p.get(side + "_listing_published"))
            and bool(p.get(side + "_listing_searchable"))
            and not p.get(side + "_proposal"))


def public_sides(p):
    """The public sides in deep-link order (sale first, as the original)."""
    if p.get("draft") or p.get("proposal"):
        return []
    return [s for s in SIDES if side_public(p, s)]


def public_view(p):
    """The record as a card may show it: a side that is not public (proposal,
    private, unpublished) is switched off, so availability, price and the
    ItemList never mention it. Returns `p` itself when nothing is masked, so
    today's cards stay byte-identical."""
    pub = set(public_sides(p))
    if all(s in pub for s in SIDES if p.get(s)):
        return p
    q = dict(p)
    for s in SIDES:
        if s not in pub:
            q[s] = False
    return q


# ------------------------------------------------------------------- filters

def _blob(p):
    parts = [p.get("name"), p.get("sale_description"), p.get("lease_description"),
             p.get("best_use"), p.get("location_description")]
    return " ".join(x for x in parts if x).lower()


def f_all_lease(p):
    return bool(p.get("lease"))


def f_retail_lease(p):
    return p.get("property_type_id") == 2 and bool(p.get("lease"))


def f_industrial(p):
    return p.get("property_type_id") == 3


def f_warehouse_lease(p):
    if p.get("property_type_id") != 3 or not p.get("lease"):
        return False
    return (p.get("property_subtype_id") in WAREHOUSE_SUBTYPES
            or "warehouse" in _blob(p))


def f_restaurant(p):
    if p.get("property_type_id") != 2:
        return False
    return ("restaurant" in _blob(p)
            or p.get("property_subtype_id") == RESTAURANT_SUBTYPE)


def f_land_sale(p):
    return p.get("property_type_id") == 5 and bool(p.get("sale"))


FILTERS = {
    "all_lease": f_all_lease,
    "retail_lease": f_retail_lease,
    "industrial": f_industrial,
    "warehouse_lease": f_warehouse_lease,
    "restaurant": f_restaurant,
    "land_sale": f_land_sale,
}

# The side each lane lists. None = side-agnostic: any one public side will do.
LANE_SIDE = {
    "all_lease": "lease",
    "retail_lease": "lease",
    "industrial": None,
    "warehouse_lease": "lease",
    "restaurant": None,
    "land_sale": "sale",
}

# The six lanes, in the order the Phase 4 plan lists them.
LANES = [
    ("all_lease", "Commercial property for lease", "/commercial-property-for-lease-waco/"),
    ("retail_lease", "Retail space for lease", "/retail-space-for-lease-waco/"),
    ("industrial", "Industrial property", "/industrial-property-waco/"),
    ("warehouse_lease", "Warehouse space for lease", "/warehouse-for-lease-waco/"),
    ("restaurant", "Restaurant space", "/restaurant-space-waco/"),
    ("land_sale", "Commercial land for sale", "/commercial-land-for-sale-waco/"),
]


def is_public_for(p, filter_key):
    sides = public_sides(p)
    want = LANE_SIDE[filter_key]
    return (want in sides) if want else bool(sides)


# -------------------------------------------------------------- card pieces

def listed_on(p):
    """Sort key: the newer of the two listed-on dates. ISO strings sort right."""
    return max(p.get("sale_listed_on") or "", p.get("lease_listed_on") or "")


def deep_link(p):
    """Where a card points. Buildout's own listing URL when it published one,
    otherwise the /properties embed deep link, which the payload's slug spells.
    Only a public side is ever linked; with both sides public this is the
    original sale-first order. (No public side at all cannot reach here through
    `select()`; it keeps the original behaviour.)"""
    sides = public_sides(p)
    for side in sides:
        if p.get(side + "_listing_url"):
            return p[side + "_listing_url"]
    if sides:
        kind = sides[0]
    else:
        for key in ("sale_listing_url", "lease_listing_url"):
            if p.get(key):
                return p[key]
        kind = "sale" if p.get("sale") else "lease"
    return "/properties/?propertyId=%s-%s" % (p.get("id"), kind)


def photo(p):
    for shot in (p.get("photos") or []):
        url = (shot.get("formats") or {}).get("medium")
        if url:
            return url
    return FALLBACK_IMG


def address_line(p):
    """Buildout lets an owner hide a street address; respect that."""
    if p.get("hide_address"):
        street = p.get("hide_address_label_override") or "Address not disclosed"
    else:
        street = p.get("address") or ""
    city = p.get("city") or "Waco"
    return ", ".join(x for x in (street, city, "TX") if x)


def size_line(p):
    sf = p.get("building_size_sf")
    if sf:
        return "{:,.0f} SF".format(float(sf))
    acres = p.get("lot_size_acres")
    if acres:
        acres = float(acres)
        return ("%g acres" % acres) if acres != 1 else "1 acre"
    return ""


def availability(p):
    sale, lease = bool(p.get("sale")), bool(p.get("lease"))
    if sale and lease:
        return "For Sale or Lease"
    if sale:
        return "For Sale"
    if lease:
        return "For Lease"
    return ""


def sale_price(p):
    """Only when the listing does not hide it. `hide_sale_price` true means the
    owner asked for `hidden_price_label` ("Subject To Offer") instead."""
    if not p.get("sale"):
        return ""
    if p.get("hide_sale_price"):
        return p.get("hidden_price_label") or "Price on request"
    dollars = p.get("sale_price_dollars")
    if not dollars:
        return ""
    return "${:,.0f}".format(float(dollars))


def lease_rate(p):
    """See the FIELD NOTE at the top: properties.json carries no rate today, so
    this returns "" for every lease listing. The two documented field names are
    read first so the moment they appear the cards use them."""
    if not p.get("lease"):
        return ""
    for flag in ("hide_lease_rate", "hide_lease_price", "hide_rate"):
        if p.get(flag):
            return p.get("hidden_price_label") or ""
    dollars = p.get("lease_rate_dollars")
    if not dollars:
        return ""   # no rate in the payload: omit the line rather than print a wall of "Rate on request"
    units = (p.get("lease_rate_units") or "dollars_per_sf_per_year").lower()
    suffix = {
        "dollars_per_sf_per_year": "/SF/yr",
        "dollars_per_sf_per_month": "/SF/mo",
        "dollars_per_month": "/mo",
        "dollars_per_year": "/yr",
    }.get(units, "/SF/yr")
    return "${:,.2f}{}".format(float(dollars), suffix)


def price_line(p):
    parts = [x for x in (sale_price(p), lease_rate(p)) if x]
    return " | ".join(parts)


def broker_name(p):
    for key in ("broker_id", "second_broker_id", "third_broker_id"):
        bid = p.get(key)
        if bid in BROKERS:
            return BROKERS[bid][0]
    for bid in (p.get("broker_ids") or []):
        if bid in BROKERS:
            return BROKERS[bid][0]
    return ""


# -------------------------------------------------------------- card render

def e(s):
    return html.escape(str(s or ""), quote=True)


def card_html(p, indent="\t\t"):
    name = p.get("name") or p.get("address") or "Commercial property"
    alt = "%s, %s, TX" % (name, p.get("city") or "Waco")
    specs = " \u00b7 ".join(x for x in (size_line(p), availability(p)) if x)
    price = price_line(p)
    broker = broker_name(p)
    lines = [
        '%s<a class="rpCard" href="%s">' % (indent, e(deep_link(p))),
        '%s\t<img src="%s" alt="%s" loading="lazy">' % (indent, e(photo(p)), e(alt)),
        '%s\t<div class="rpCardBody">' % indent,
        '%s\t\t<h3>%s</h3>' % (indent, e(name)),
        '%s\t\t<div class="rpCardAddr">%s</div>' % (indent, e(address_line(p))),
    ]
    if specs:
        lines.append('%s\t\t<div class="rpCardSpecs">%s</div>' % (indent, e(specs)))
    if price:
        lines.append('%s\t\t<div class="rpCardPrice">%s</div>' % (indent, e(price)))
    if broker:
        lines.append('%s\t\t<div class="rpCardBroker">%s</div>' % (indent, e(broker)))
    lines.append('%s\t</div>' % indent)
    lines.append('%s</a>' % indent)
    return "\n".join(lines)


def select(props, filter_key):
    fn = FILTERS.get(filter_key)
    if fn is None:
        raise SystemExit("unknown lane filter: %s (have %s)"
                         % (filter_key, ", ".join(sorted(FILTERS))))
    return sorted((public_view(p) for p in props
                   if is_public_for(p, filter_key) and fn(p)),
                  key=listed_on, reverse=True)


def cards_for(props, filter_key, indent="\t\t"):
    """(html, listings) - the grid's inner cards plus the records behind them so
    the caller can build the ItemList schema from the same rows."""
    picked = select(props, filter_key)
    return "\n".join(card_html(p, indent) for p in picked), picked


def browse_by_type(props):
    """Per-lane live counts: name, count, href."""
    return [dict(key=key, name=name, href=href, count=len(select(props, key)))
            for key, name, href in LANES]


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Per-lane listing counts from Buildout.")
    ap.add_argument("--offline", metavar="PATH", default=None,
                    help="read this payload JSON (outside the repo) instead of calling Buildout")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    props = load(args.offline, args.limit)
    print("on-market listings: %d  (%s)"
          % (len(props), "offline payload" if args.offline else "live pull, nothing cached"))
    print()
    print("  %-16s %5s  %s" % ("lane", "count", "page"))
    print("  %-16s %5s  %s" % ("-" * 16, "-" * 5, "-" * 40))
    for row in browse_by_type(props):
        print("  %-16s %5d  %s" % (row["key"], row["count"], row["href"]))
    print()
    dropped = sum(1 for p in props if not public_sides(p))
    no_photo = sum(1 for p in props if not p.get("photos"))
    no_broker = sum(1 for p in props if not broker_name(p))
    print("listings with no public side (dropped by the guard): %d" % dropped)
    print("listings with no photo (would use the fallback): %d" % no_photo)
    print("listings whose broker id is not in the map: %d" % no_broker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
