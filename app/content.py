import httpx
from trafilatura import extract


ARTICLE_TIMEOUT_SECONDS = 15.0


def fetch_article_content(url: str) -> str | None:
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=ARTICLE_TIMEOUT_SECONDS,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(X11; Linux x86_64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/140.0 Safari/537.36"
                )
            },
        )

        response.raise_for_status()

    except httpx.TimeoutException:
        return None

    except httpx.HTTPStatusError:
        return None

    except httpx.HTTPError:
        return None

    html = response.text

    if not html.strip():
        return None

    content = extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    )

    if not content:
        return None

    content = content.strip()

    if not content:
        return None

    return content
