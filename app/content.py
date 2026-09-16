import httpx
from trafilatura import extract


def fetch_article_content(url: str) -> str | None:
    response = httpx.get(
        url,
        follow_redirects=True,
        timeout=15.0,
        headers={
            "User-Agent": "tech-digest/0.1"
        },
    )

    response.raise_for_status()

    html = response.text

    content = extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    )

    return content
