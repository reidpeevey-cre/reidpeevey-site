"""ItemList rows for a lane page's JSON-LD, lifted from the Media Server's
`tools/build_page.py` (`listing_node`, 2026-11-04) so the refreshed schema has
the same shape, key order and formatting as the block already on the pages.

`refresh_lanes.py` builds `itemListElement` from the same listings, in the same
order, as the cards, so the schema and the grid can never disagree.
"""
import lanes

BASE = "https://www.reidpeevey.com"


def listing_node(p, position):
    href = lanes.deep_link(p)
    node = {
        "@type": "RealEstateListing",
        "position": position,
        "name": p.get("name") or p.get("address") or "Commercial property",
        "url": href if href.startswith("http") else BASE + href,
        "image": lanes.photo(p) if (p.get("photos")) else BASE + lanes.FALLBACK_IMG,
    }
    address = {"@type": "PostalAddress",
               "addressLocality": p.get("city") or "Waco",
               "addressRegion": p.get("state") or "TX",
               "addressCountry": "US"}
    if not p.get("hide_address") and p.get("address"):
        address["streetAddress"] = p["address"]
    if p.get("zip"):
        address["postalCode"] = p["zip"]
    node["address"] = address
    return node


def item_list_elements(listings):
    return [listing_node(p, i) for i, p in enumerate(listings, 1)]
