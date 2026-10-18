from datetime import datetime, timezone
from urllib.parse import urlencode

from app.publication import site_url

# Same ref values the public edition page uses, so signups from shares land in
# the same acquisition buckets regardless of where the share started.
SHARE_REFS = {"link": "share", "x": "x", "linkedin": "linkedin"}


def edition_date(sent_at: datetime) -> str:
    # Public editions are grouped by the UTC date of delivered_at.
    return sent_at.astimezone(timezone.utc).date().isoformat()


def edition_url(date: str, ref: str) -> str:
    return f"{site_url()}/digest/{date}?{urlencode({'ref': ref})}"


def x_share_url(text: str, url: str) -> str:
    return f"https://x.com/intent/post?{urlencode({'text': text, 'url': url})}"


def linkedin_share_url(url: str) -> str:
    return "https://www.linkedin.com/sharing/share-offsite/?" + urlencode({"url": url})


def share_links(date: str, text: str) -> dict[str, str]:
    return {
        "link": edition_url(date, SHARE_REFS["link"]),
        "x": x_share_url(text, edition_url(date, SHARE_REFS["x"])),
        "linkedin": linkedin_share_url(edition_url(date, SHARE_REFS["linkedin"])),
    }
