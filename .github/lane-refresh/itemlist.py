"""ItemList rows for a lane page's JSON-LD, lifted from the Media Server's
`tools/build_page.py` (`listing_node`, 2026-11-04) so the refreshed schema has
the same shape, key order and formatting as the block already on the pages.

`refresh_lanes.py` builds `itemListElement` from the same listings, in the same
order and with the same lane-side deep link, as the cards, so the schema and the
grid can never disagree. Every string goes through `lanes.clean()`.
"""
import lanes

BASE = "https://www.reidpeevey.com"


def listing_node(p, position, prefer=None):
    c = lanes.clean
    href = c(lanes.deep_link(p, prefer))
    node = {
        "@type": "RealEstateListing",
        "position": position,
        "name": c(p.get("name") or p.get("address") or "Commercial property"),
        "url": href if href.startswith("http") else BASE + href,
        "image": c(lanes.photo(p)) if (p.get("photos")) else BASE + lanes.FALLBACK_IMG,
    }
    address = {"@type": "PostalAddress",
               "addressLocality": c(p.get("city") or "Waco"),
               "addressRegion": c(p.get("state") or "TX"),
               "addressCountry": "US"}
    if not p.get("hide_address") and p.get("address"):
        address["streetAddress"] = c(p["address"])
    if p.get("zip"):
        address["postalCode"] = c(p["zip"])
    node["address"] = address
    return node


def item_list_elements(listings, prefer=None):
    return [listing_node(p, i, prefer) for i, p in enumerate(listings, 1)]
