import html
import os
from datetime import datetime

import httpx

from app.db import _parse_article_datetime


RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 15.0
DEFAULT_FROM = (
    "João Coelho Tech Digest <digest@digest.joaoac.com>"
)

FONT_SANS = "'IBM Plex Sans', sans-serif"
FONT_MONO = "'IBM Plex Mono', monospace"

_SANS_STYLESHEET = (
    "https://fonts.googleapis.com/css2"
    "?family=IBM+Plex+Sans:ital,wght@0,100..700;1,100..700"
    "&display=swap"
)
_MONO_STYLESHEET = (
    "https://fonts.googleapis.com/css2"
    "?family=IBM+Plex+Mono:wght@400;500"
    "&display=swap"
)


def _get_api_key() -> str:
    api_key = os.getenv(
        "RESEND_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "RESEND_API_KEY is not configured"
        )

    return api_key


def _get_recipients() -> list[str]:
    raw = os.getenv("RESEND_TO", "")

    recipients = [
        item.strip()
        for item in raw.split(",")
        if item.strip()
    ]

    if not recipients:
        raise RuntimeError(
            "RESEND_TO is not configured"
        )

    return recipients


def _get_from() -> str:
    sender = os.getenv(
        "RESEND_FROM",
        "",
    ).strip()

    return sender or DEFAULT_FROM


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _sans(weight: int = 400) -> str:
    return (
        f"font-family:{FONT_SANS};"
        "font-optical-sizing:auto;"
        "font-style:normal;"
        "font-variation-settings:'wdth' 100;"
        f"font-weight:{weight};"
    )


def _mono() -> str:
    return (
        f"font-family:{FONT_MONO};"
        "font-optical-sizing:auto;"
        "font-style:normal;"
    )


def _format_calendar_date(
    value: datetime,
    with_weekday: bool = False,
) -> str:
    label = (
        f"{value.strftime('%b')} "
        f"{value.day}, "
        f"{value.year}"
    )

    if not with_weekday:
        return label

    return f"{value.strftime('%A')}, {label}"


def _format_article_date(
    value: str | None,
) -> str | None:
    parsed = _parse_article_datetime(value)

    if parsed is None:
        return None

    local = parsed.astimezone()

    return (
        f"{local.strftime('%b')} {local.day}"
    )


def digest_subject(
    sent_at: datetime | None = None,
) -> str:
    sent_at = (
        sent_at
        or datetime.now().astimezone()
    )

    return (
        "João Coelho Tech Digest — "
        + _format_calendar_date(sent_at)
    )


def _article_href(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    return ""


def _render_article(
    article: dict,
    index: int,
    show_score: bool,
    show_topics: bool,
) -> str:
    title = (article.get("title") or "Untitled").strip() or "Untitled"
    href = _article_href((article.get("url") or "").strip())
    why = (article.get("why_interesting") or "").strip()
    source = (article.get("source") or "").strip()
    published = _format_article_date(article.get("published_at"))

    details = [part for part in (source, published) if part]
    if show_topics:
        details.extend(
            str(topic).strip()
            for topic in article.get("topics") or []
            if str(topic).strip()
        )
    if show_score and article.get("relevance_score") is not None:
        details.append(f"Score {article['relevance_score']}")

    hair = (
        "border-top:1px solid #d4d4d8;"
        if index > 1
        else ""
    )
    hair_class = " dm-hair" if index > 1 else ""
    meta = " · ".join(details)
    kicker = f"{index:02d}"
    if meta:
        kicker = f"{kicker} · {_esc(meta)}"

    if href:
        title_html = (
            f'<a href="{_esc(href)}" class="dm-fg" '
            'style="color:#000000;text-decoration:underline;'
            'text-underline-offset:3px;">'
            f"{_esc(title)}</a>"
        )
    else:
        title_html = _esc(title)

    why_html = ""
    if why:
        why_html = (
            '<p class="ibm-plex-sans dm-quiet" style="margin:10px 0 0;'
            f'{_sans()}font-size:15px;line-height:24px;color:#262626;">'
            f"{_esc(why)}</p>"
        )

    return (
        f'<tr><td class="dm-bg{hair_class}" bgcolor="#f4f4f5" '
        'style="padding:26px 0 2px;background-color:#f4f4f5;'
        f'{hair}">'
        '<p class="ibm-plex-mono dm-mark" style="margin:0 0 8px;'
        f'{_mono()}font-size:12px;line-height:18px;color:#4a8aa8;">'
        f"{kicker}</p>"
        '<h2 class="ibm-plex-sans dm-fg" style="margin:0;'
        f'{_sans(500)}font-size:18px;line-height:26px;color:#000000;">'
        f"{title_html}</h2>{why_html}"
        "</td></tr>"
    )


def render_html_digest(
    digest: dict,
    show_score: bool = False,
    show_topics: bool = False,
    sent_at: datetime | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    sent_at = sent_at or datetime.now().astimezone()
    articles = digest.get("articles") or []
    lookback_hours = digest.get("lookback_hours", 24)
    count = len(articles)
    noun = "article" if count == 1 else "articles"
    edition = f"{count} {noun} · last {lookback_hours} hours"
    preheader = edition
    date_label = _format_calendar_date(sent_at, with_weekday=True)

    unsubscribe_html = ""
    if unsubscribe_url:
        unsubscribe_html = (
            '<p class="ibm-plex-sans" style="margin:14px 0 0;'
            f'{_sans()}font-size:13px;line-height:20px;">'
            f'<a href="{_esc(unsubscribe_url)}" class="dm-meta" '
            "style=\"color:#262626;text-decoration:underline;"
            'text-underline-offset:3px;">'
            "Unsubscribe</a></p>"
        )

    if articles:
        article_rows = "\n".join(
            _render_article(article, index, show_score, show_topics)
            for index, article in enumerate(articles, start=1)
        )
    else:
        article_rows = (
            '<tr><td class="dm-bg" bgcolor="#f4f4f5" '
            'style="padding:26px 0 2px;background-color:#f4f4f5;">'
            '<p class="ibm-plex-sans dm-quiet" style="margin:0;'
            f'{_sans()}font-size:15px;line-height:24px;color:#262626;">'
            "No articles passed the relevance threshold.</p></td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{_esc(_SANS_STYLESHEET)}" rel="stylesheet">
<link href="{_esc(_MONO_STYLESHEET)}" rel="stylesheet">
<style>
:root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
.ibm-plex-sans {{ font-family: "IBM Plex Sans", Arial, sans-serif; }}
.ibm-plex-mono {{ font-family: "IBM Plex Mono", monospace; }}
@media (prefers-color-scheme: dark) {{
  .dm-bg {{ background-color:#000000 !important; }}
  .dm-fg {{ color:#f4f4f5 !important; }}
  .dm-quiet {{ color:#d4d4d8 !important; }}
  .dm-meta {{ color:#a1a1aa !important; }}
  .dm-brand {{ color:#64a9ca !important; }}
  .dm-mark {{ color:#7dd3fc !important; }}
  .dm-link {{ color:#fde68a !important; }}
  .dm-hair {{ border-color:#262626 !important; }}
}}
</style>
<title>João Coelho Tech Digest</title>
</head>
<body class="body dm-bg dm-fg ibm-plex-sans" bgcolor="#f4f4f5" style="margin:0;padding:0;background-color:#f4f4f5;color:#000000;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{_esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="dm-bg" bgcolor="#f4f4f5" style="background-color:#f4f4f5;">
<tr><td align="center" class="dm-bg" bgcolor="#f4f4f5" style="background-color:#f4f4f5;">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" class="dm-bg" bgcolor="#f4f4f5" style="width:100%;max-width:560px;background-color:#f4f4f5;">
<tr><td class="dm-bg" bgcolor="#f4f4f5" style="padding:40px 28px 36px;background-color:#f4f4f5;color:#000000;">
<p class="ibm-plex-mono dm-brand" style="margin:0 0 22px;{_mono()}font-size:12px;line-height:16px;letter-spacing:0.08em;color:#4a8aa8;">João Coelho Tech Digest</p>
<h1 class="ibm-plex-sans dm-fg" style="margin:0 0 6px;{_sans(500)}font-size:22px;line-height:30px;color:#000000;">{_esc(date_label)}</h1>
<p class="ibm-plex-mono dm-meta" style="margin:0 0 28px;{_mono()}font-size:13px;line-height:20px;color:#262626;">{_esc(edition)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid #4a8aa8;">
{article_rows}
</table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td class="dm-hair" style="padding:26px 0 0;border-top:1px solid #d4d4d8;">
<p class="ibm-plex-sans dm-meta" style="margin:0 0 12px;{_sans()}font-size:13px;line-height:20px;color:#262626;">Curated by João Coelho. Each link goes to its original publisher.</p>
<p class="ibm-plex-mono" style="margin:0;{_mono()}font-size:13px;line-height:20px;">
<a href="https://x.com/joaoac_dev" class="dm-link" style="color:#4a8aa8;text-decoration:underline;text-underline-offset:3px;">X</a>
<span class="dm-meta" style="color:#262626;"> · </span>
<a href="https://joaoac.com" class="dm-link" style="color:#4a8aa8;text-decoration:underline;text-underline-offset:3px;">Website</a>
</p>
{unsubscribe_html}
</td></tr></table>
</td></tr></table>
</td></tr></table>
</body>
</html>
"""


def send_email(
    subject: str,
    html_body: str,
    text: str,
    to: list[str] | None = None,
) -> dict:
    recipients = to or _get_recipients()

    response = httpx.post(
        RESEND_API_URL,
        headers={
            "Authorization": (
                f"Bearer {_get_api_key()}"
            ),
            "Content-Type": "application/json",
        },
        json={
            "from": _get_from(),
            "to": recipients,
            "subject": subject,
            "html": html_body,
            "text": text,
        },
        timeout=RESEND_TIMEOUT_SECONDS,
    )

    if not response.is_success:
        detail = response.text.strip()

        raise RuntimeError(
            "Resend API error "
            f"({response.status_code}): "
            f"{detail}"
        )

    payload = response.json()

    if "id" not in payload:
        raise RuntimeError(
            f"Resend API error: {payload}"
        )

    return payload
