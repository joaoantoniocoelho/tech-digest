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

FONT_SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"

PAPER = "#ffffff"
CANVAS = "#f4f4f5"
INK = "#18181b"
COPY = "#3f3f46"
MUTED = "#52525b"
FAINT = "#a1a1aa"
ACCENT = "#1f6a88"
ACCENT_MARK = "#64a9ca"

_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTHS_SHORT = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
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
        f"font-weight:{weight};"
    )


def _format_calendar_date(
    value: datetime,
    with_weekday: bool = False,
) -> str:
    label = (
        f"{_MONTHS_SHORT[value.month - 1]} "
        f"{value.day}, "
        f"{value.year}"
    )

    if not with_weekday:
        return label

    return f"{_WEEKDAYS[value.weekday()]}, {label}"


def _format_edition_date(
    value: datetime,
) -> tuple[str, str]:
    weekday = _WEEKDAYS[value.weekday()]
    long_date = (
        f"{_MONTHS[value.month - 1]} "
        f"{value.day}, "
        f"{value.year}"
    )
    return weekday, long_date


def _format_article_date(
    value: str | None,
) -> str | None:
    parsed = _parse_article_datetime(value)

    if parsed is None:
        return None

    local = parsed.astimezone()

    return (
        f"{_MONTHS_SHORT[local.month - 1]} {local.day}"
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


def _edition_stamp(value: datetime) -> str:
    month = _MONTHS_SHORT[value.month - 1].upper()
    return f"{month} {value.day}"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _story_bits(
    article: dict,
    show_score: bool,
    show_topics: bool,
    include_why: bool,
) -> tuple[str, str]:
    source = _clean(article.get("source"))
    published = _format_article_date(article.get("published_at"))
    why = _clean(article.get("why_interesting"))
    credit = [part for part in (source, published) if part]

    if show_topics:
        credit.extend(
            _clean(topic)
            for topic in article.get("topics") or []
            if _clean(topic)
        )

    if show_score and article.get("relevance_score") is not None:
        credit.append(f"Score {article['relevance_score']}")

    dek = ""
    if why:
        if include_why:
            credit.append(why)
        else:
            dek = why

    return " · ".join(credit), dek


def _title_html(title: str, href: str, mark_size: int) -> str:
    label = _esc(title)
    if not href:
        return label

    return (
        f'<a href="{_esc(href)}" class="ink" '
        f'style="color:{INK};text-decoration:none;">'
        f"{label}<span class=\"accent\" style=\"{_sans()}"
        f"color:{ACCENT};font-size:{mark_size}px;"
        f"line-height:1;margin-left:6px;\">&gt;</span></a>"
    )


def _render_story(
    article: dict,
    lead: bool,
    show_score: bool,
    show_topics: bool,
) -> str:
    title = _clean(article.get("title")) or "Untitled"
    href = _article_href(_clean(article.get("url")))
    credit, dek = _story_bits(
        article,
        show_score,
        show_topics,
        include_why=not lead,
    )
    title_html = _title_html(
        title,
        href,
        mark_size=13 if lead else 12,
    )
    gap = "48px" if lead else "26px"
    size = "20px" if lead else "17px"
    line = "26px" if lead else "23px"
    title_class = "lead" if lead else "story"
    credit_gap = "8px" if lead else "4px"

    credit_html = ""
    if credit:
        credit_html = (
            f'<p class="muted" style="margin:{credit_gap} 0 0;'
            f'{_sans()}font-size:13px;line-height:18px;color:{MUTED};">'
            f"{_esc(credit)}</p>"
        )

    dek_html = ""
    if dek:
        dek_html = (
            '<p class="copy" style="margin:4px 0 0;'
            f'{_sans()}font-size:14px;line-height:20px;color:{COPY};">'
            f"{_esc(dek)}</p>"
        )

    return (
        "<tr><td "
        f'style="padding-top:{gap};">'
        f'<h2 class="{title_class} ink" style="margin:0;'
        f'{_sans(500)}font-size:{size};line-height:{line};color:{INK};">'
        f"{title_html}</h2>"
        f"{credit_html}{dek_html}"
        "</td></tr>"
    )


def _render_footer(unsubscribe_url: str | None) -> str:
    link = (
        f'{_sans()}font-size:12px;line-height:18px;'
        f"color:{ACCENT};text-decoration:underline;"
        "text-underline-offset:2px;"
    )
    parts = [
        f'<a href="https://joaoac.com" class="accent" style="{link}">Website</a>',
        f'<a href="https://x.com/joaoac_dev" class="accent" style="{link}">X</a>',
    ]
    if unsubscribe_url:
        parts.append(
            f'<a href="{_esc(unsubscribe_url)}" class="accent" style="{link}">'
            "Unsubscribe</a>"
        )

    separated = (
        f'<span class="faint" style="color:{FAINT};">'
        "&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
    ).join(parts)

    return (
        "<tr><td style=\"padding-top:44px;\">"
        '<p class="muted" style="margin:0;'
        f'{_sans()}font-size:12px;line-height:18px;color:{MUTED};">'
        "Curated by João Coelho</p>"
        f'<p style="margin:8px 0 0;">{separated}</p>'
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
    noun = "story" if count == 1 else "stories"
    edition = (
        f"{count} selected {noun} from the last "
        f"{lookback_hours} hours"
    )
    weekday, long_date = _format_edition_date(sent_at)
    stamp = _edition_stamp(sent_at)
    preheader = f"{weekday}, {long_date}. {edition}."

    if articles:
        story_rows = "\n".join(
            _render_story(
                article,
                lead=index == 1,
                show_score=show_score,
                show_topics=show_topics,
            )
            for index, article in enumerate(articles, start=1)
        )
    else:
        story_rows = (
            "<tr><td style=\"padding-top:28px;\">"
            '<p class="copy" style="margin:0;'
            f'{_sans()}font-size:15px;line-height:22px;color:{COPY};">'
            "No articles passed the relevance threshold.</p>"
            "</td></tr>"
        )

    footer = _render_footer(unsubscribe_url)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<style>
:root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
body, table, td, p, h2, a {{ -webkit-text-size-adjust:100%; }}
@media only screen and (max-width: 620px) {{
  .canvas-pad {{ padding:8px 0 !important; }}
  .sheet {{ padding:28px 22px 32px !important; }}
  .lead {{ font-size:18px !important; line-height:24px !important; }}
  .story {{ font-size:16px !important; line-height:22px !important; }}
}}
@media (prefers-color-scheme: dark) {{
  .canvas {{ background-color:#000000 !important; }}
  .paper {{ background-color:#18181b !important; }}
  .ink {{ color:#f4f4f5 !important; }}
  .copy {{ color:#d4d4d8 !important; }}
  .muted {{ color:#a1a1aa !important; }}
  .faint {{ color:#71717a !important; }}
  .accent {{ color:#7dd3fc !important; }}
  .mark {{ background-color:#64a9ca !important; }}
}}
</style>
<title>João Coelho Tech Digest</title>
</head>
<body class="canvas" bgcolor="{CANVAS}" style="margin:0;padding:0;background-color:{CANVAS};">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{_esc(preheader)}&#847;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="canvas" bgcolor="{CANVAS}" style="background-color:{CANVAS};">
<tr><td align="center" class="canvas canvas-pad" bgcolor="{CANVAS}" style="padding:24px 16px;background-color:{CANVAS};">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" class="paper" bgcolor="{PAPER}" style="width:100%;max-width:600px;background-color:{PAPER};">
<tr><td class="paper sheet" bgcolor="{PAPER}" style="padding:36px 48px 40px;background-color:{PAPER};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td class="muted" style="{_sans()}font-size:11px;line-height:16px;letter-spacing:0.18em;color:{MUTED};">TECH DIGEST</td>
<td class="accent" align="right" style="{_sans()}font-size:11px;line-height:16px;letter-spacing:0.14em;color:{ACCENT};">{_esc(stamp)}</td>
</tr>
</table>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;">
<tr><td class="mark" width="28" height="2" bgcolor="{ACCENT_MARK}" style="width:28px;height:2px;background-color:{ACCENT_MARK};font-size:0;line-height:2px;">&nbsp;</td></tr>
</table>
<p class="ink" style="margin:20px 0 0;{_sans()}font-size:16px;line-height:24px;color:{INK};">Technology worth your time.</p>
<p class="muted" style="margin:10px 0 0;{_sans()}font-size:13px;line-height:18px;color:{MUTED};">{_esc(weekday)}, {_esc(long_date)}</p>
<p class="muted" style="margin:2px 0 0;{_sans()}font-size:13px;line-height:18px;color:{MUTED};">{_esc(edition)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
{story_rows}
{footer}
</table>
</td></tr>
</table>
</td></tr>
</table>
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
