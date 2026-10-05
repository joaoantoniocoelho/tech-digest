import os

import httpx

from app.db import list_public_editions
from app.log import log_event, redact_text

DEFAULT_SITE_URL = "https://digest.joaoac.com"
REVALIDATION_ATTEMPTS = 3


def revalidate_published_edition() -> bool:
    token = os.getenv("DIGEST_REVALIDATE_TOKEN", "").strip()
    if not token:
        log_event("digest_revalidate", status="disabled")
        return False

    try:
        editions = list_public_editions()
        if not editions:
            log_event("digest_revalidate", status="skipped", reason="no_editions")
            return False

        date = str(editions[0]["date"])
        previous_date = str(editions[1]["date"]) if len(editions) > 1 else None
        site_url = os.getenv("DIGEST_SITE_URL", DEFAULT_SITE_URL).rstrip("/")
        for attempt in range(1, REVALIDATION_ATTEMPTS + 1):
            try:
                response = httpx.post(
                    f"{site_url}/api/revalidate",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"date": date, "previousDate": previous_date},
                    timeout=5.0,
                )
                response.raise_for_status()
                log_event("digest_revalidate", status="ok", date=date)
                return True
            except httpx.HTTPError as error:
                if attempt == REVALIDATION_ATTEMPTS:
                    raise error
    except Exception as error:
        log_event(
            "digest_revalidate",
            status="error",
            error=redact_text(f"{type(error).__name__}: {error}"),
        )
    return False
