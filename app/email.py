import html
import os
from datetime import datetime

import httpx

from app.db import _parse_article_datetime
from app.share import edition_date, share_links

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 15.0
DEFAULT_FROM = "João Coelho Tech Digest <digest@digest.joaoac.com>"

# Web fonts do not load in Gmail; the system stack stays closest to the site's sans.
FONT_SANS = "-apple-system, BlinkMacSystemFont, 'Helvetica Neue', Helvetica, Arial, sans-serif"

# Same tokens as digest.joaoac.com (digest/src/styles/global.css).
PAPER = "#ffffff"
CANVAS = "#f5f5f7"
INK = "#1d1d1f"
COPY = "#424245"
MUTED = "#6e6e73"
FAINT = "#86868b"
LINE = "#e5e5ea"

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
        raise RuntimeError("RESEND_API_KEY is not configured")

    return api_key


def _get_recipients() -> list[str]:
    raw = os.getenv("RESEND_TO", "")

    recipients = [item.strip() for item in raw.split(",") if item.strip()]

    if not recipients:
        raise RuntimeError("RESEND_TO is not configured")

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
    return f"font-family:{FONT_SANS};font-weight:{weight};"


def _format_calendar_date(
    value: datetime,
    with_weekday: bool = False,
) -> str:
    label = f"{_MONTHS_SHORT[value.month - 1]} {value.day}, {value.year}"

    if not with_weekday:
        return label

    return f"{_WEEKDAYS[value.weekday()]}, {label}"


def _format_edition_date(
    value: datetime,
) -> tuple[str, str]:
    weekday = _WEEKDAYS[value.weekday()]
    long_date = f"{_MONTHS[value.month - 1]} {value.day}, {value.year}"
    return weekday, long_date


def _format_article_date(
    value: str | None,
) -> str | None:
    parsed = _parse_article_datetime(value)

    if parsed is None:
        return None

    local = parsed.astimezone()

    return f"{_MONTHS_SHORT[local.month - 1]} {local.day}".lower()


def digest_subject(
    sent_at: datetime | None = None,
) -> str:
    sent_at = sent_at or datetime.now().astimezone()

    return "João Coelho Tech Digest — " + _format_calendar_date(sent_at)


def _article_href(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    return ""


def _edition_stamp(value: datetime) -> str:
    return f"{_MONTHS_SHORT[value.month - 1]} {value.day}".lower()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _story_meta(
    article: dict,
    show_score: bool,
    show_topics: bool,
) -> str:
    source = _clean(article.get("source"))
    published = _format_article_date(article.get("published_at"))
    why = _clean(article.get("why_interesting")).lower()
    parts = [part for part in (source, published, why) if part]

    if show_topics:
        topics = (_clean(topic).lower() for topic in article.get("topics") or [])
        parts.extend(topic for topic in topics if topic)

    if show_score and article.get("relevance_score") is not None:
        parts.append(f"score {article['relevance_score']}")

    return " · ".join(parts)


def _render_story(
    number: int,
    article: dict,
    last: bool,
    show_score: bool,
    show_topics: bool,
) -> str:
    title = _clean(article.get("title")) or "Untitled"
    href = _article_href(_clean(article.get("url")))
    meta = _story_meta(article, show_score, show_topics)

    title_html = _esc(title)
    if href:
        title_html = (
            f'<a href="{_esc(href)}" style="color:{INK};text-decoration:none;">{title_html}</a>'
        )

    meta_html = ""
    if meta:
        meta_html = (
            f'<p style="margin:4px 0 0;{_sans()}font-size:13px;line-height:18px;'
            f'color:{MUTED};">{_esc(meta)}</p>'
        )

    border = f"border-top:1px solid {LINE};"
    if last:
        border += f"border-bottom:1px solid {LINE};"

    return (
        "<tr>"
        f'<td width="36" valign="top" style="width:36px;padding:18px 12px 18px 0;{border}'
        f'{_sans()}font-size:13px;line-height:24px;color:{FAINT};">{number:02d}</td>'
        f'<td valign="top" style="padding:18px 0;{border}">'
        f'<h2 class="story" style="margin:0;{_sans(600)}font-size:18px;line-height:24px;'
        f'color:{INK};">{title_html}</h2>'
        f"{meta_html}"
        "</td></tr>"
    )


SHARE_TITLE = "share this digest"
SHARE_LINE = "know someone who would like this edition? send it their way."
SHARE_LINK_LABEL = "link to this edition"


def digest_share_links(sent_at: datetime) -> dict[str, str]:
    date = edition_date(sent_at)
    _, long_date = _format_edition_date(datetime.fromisoformat(date))
    text = f"Tech Digest, {long_date}: a short list of software engineering articles worth reading."
    return share_links(date, text)


def _render_links(links: list[tuple[str, str]], color: str, size: int) -> str:
    style = (
        f"{_sans()}font-size:{size}px;line-height:20px;color:{color};"
        "text-decoration:underline;text-underline-offset:2px;"
    )
    separator = f'<span style="color:{FAINT};">&nbsp;·&nbsp;</span>'
    return separator.join(
        f'<a href="{_esc(href)}" style="{style}">{_esc(label)}</a>' for label, href in links
    )


def _render_share(links: dict[str, str]) -> str:
    separated = _render_links(
        [
            (SHARE_LINK_LABEL, links["link"]),
            ("x", links["x"]),
            ("linkedin", links["linkedin"]),
        ],
        color=COPY,
        size=13,
    )

    return (
        '<tr><td style="padding-top:32px;">'
        f'<p style="margin:0;{_sans(600)}font-size:15px;'
        f'line-height:22px;color:{INK};">{SHARE_TITLE}</p>'
        f'<p style="margin:4px 0 0;{_sans()}font-size:14px;'
        f'line-height:20px;color:{COPY};">{SHARE_LINE}</p>'
        f'<p style="margin:8px 0 0;">{separated}</p>'
        "</td></tr>"
    )


def _render_footer(unsubscribe_url: str | None) -> str:
    links = [("website", "https://joaoac.com"), ("x", "https://x.com/joaoac_")]
    if unsubscribe_url:
        links.append(("unsubscribe", unsubscribe_url))

    separated = _render_links(links, color=FAINT, size=12)

    return (
        '<tr><td style="padding-top:32px;">'
        f'<p style="margin:0;{_sans()}font-size:12px;line-height:20px;color:{FAINT};">'
        f'tech digest, by joão coelho<span style="color:{FAINT};">&nbsp;·&nbsp;</span>'
        f"{separated}</p>"
        "</td></tr>"
    )


def _render_stories(
    articles: list[dict],
    show_score: bool,
    show_topics: bool,
    top: str = "28px",
) -> str:
    stories = "\n".join(
        _render_story(
            index,
            article,
            last=index == len(articles),
            show_score=show_score,
            show_topics=show_topics,
        )
        for index, article in enumerate(articles, start=1)
    )
    return (
        f'<tr><td style="padding-top:{top};">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">\n'
        f"{stories}\n"
        "</table>"
        "</td></tr>"
    )


def _render_paragraph(text: str, top: str = "28px", color: str = COPY) -> str:
    return (
        f'<tr><td style="padding-top:{top};">'
        f'<p style="margin:0;{_sans()}font-size:15px;line-height:22px;color:{color};">'
        f"{_esc(text)}</p>"
        "</td></tr>"
    )


def _render_document(
    preheader: str,
    stamp: str,
    heading: str,
    subtitle: str,
    rows: str,
) -> str:
    # The site is light only, so the email declares only light and ships no dark styles.
    # Trailing backslashes only wrap long source lines; the rendered HTML is unchanged.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<style>
:root {{ color-scheme: light; supported-color-schemes: light; }}
body, table, td, p, h1, h2, a {{ -webkit-text-size-adjust:100%; }}
@media only screen and (max-width: 620px) {{
  .canvas-pad {{ padding:12px 8px !important; }}
  .sheet {{ padding:28px 22px 28px !important; }}
  .heading {{ font-size:24px !important; line-height:28px !important; }}
  .story {{ font-size:16px !important; line-height:22px !important; }}
}}
</style>
<title>tech digest</title>
</head>
<body bgcolor="{CANVAS}" style="margin:0;padding:0;background-color:{CANVAS};">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">\
{_esc(preheader)}&#847;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" \
bgcolor="{CANVAS}" style="background-color:{CANVAS};">
<tr><td align="center" class="canvas-pad" bgcolor="{CANVAS}" \
style="padding:32px 16px;background-color:{CANVAS};">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" \
bgcolor="{PAPER}" style="width:100%;max-width:600px;background-color:{PAPER};border-radius:24px;">
<tr><td class="sheet" bgcolor="{PAPER}" \
style="padding:40px 48px 32px;background-color:{PAPER};border-radius:24px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td valign="top">
<p style="margin:0;{_sans(600)}font-size:13px;line-height:18px;color:{MUTED};">tech digest</p>
<h1 class="heading" style="margin:6px 0 0;{_sans(700)}font-size:28px;line-height:31px;\
letter-spacing:-0.015em;color:{INK};">{_esc(heading)}</h1>
<p style="margin:6px 0 0;{_sans()}font-size:14px;line-height:20px;color:{MUTED};">\
{_esc(subtitle)}</p>
</td>
<td width="80" valign="top" align="right" style="width:80px;padding-left:16px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0">
<tr><td width="64" height="64" align="center" valign="middle" bgcolor="{INK}" \
style="width:64px;height:64px;border-radius:16px;background-color:{INK};\
{_sans(700)}font-size:13px;line-height:15px;color:{PAPER};">{_esc(stamp)}</td></tr>
</table>
</td>
</tr>
</table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
{rows}
</table>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def _edition_label(count: int, lookback_hours: int) -> str:
    noun = "article" if count == 1 else "articles"
    return f"{count} {noun} from the last {lookback_hours} hours"


def _edition_line(value: datetime, count: int, lookback_hours: int) -> str:
    weekday, long_date = _format_edition_date(value)
    return f"{weekday}, {long_date} · {_edition_label(count, lookback_hours)}".lower()


def render_html_digest(
    digest: dict,
    show_score: bool = False,
    show_topics: bool = False,
    sent_at: datetime | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    sent_at = sent_at or datetime.now().astimezone()
    articles = digest.get("articles") or []
    edition_line = _edition_line(sent_at, len(articles), digest.get("lookback_hours", 24))

    if articles:
        rows = (
            _render_stories(articles, show_score, show_topics)
            + "\n"
            + _render_share(digest_share_links(sent_at))
        )
    else:
        rows = _render_paragraph("no articles passed the relevance threshold.")

    return _render_document(
        preheader=f"{edition_line}.",
        stamp=_edition_stamp(sent_at),
        heading="today's edition",
        subtitle=edition_line,
        rows=f"{rows}\n{_render_footer(unsubscribe_url)}",
    )


WELCOME_SUBJECT = "Welcome to João Coelho Tech Digest"
WELCOME_LINES = (
    "thanks for subscribing.",
    "every morning you will get a short edition with the technology "
    "stories worth your time, picked from a small set of trusted sources.",
)
WELCOME_LATEST = "while you wait for the next one, here is the latest edition."
WELCOME_FIRST = "your first edition arrives with the next morning send."


def render_html_welcome(
    edition: dict | None,
    show_score: bool = False,
    show_topics: bool = False,
    sent_at: datetime | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    sent_at = sent_at or datetime.now().astimezone()
    articles = (edition or {}).get("articles") or []
    delivered_at = (edition or {}).get("delivered_at")
    intro = _render_paragraph(WELCOME_LINES[1])

    if articles:
        published = (delivered_at or sent_at).astimezone()
        edition_line = _edition_line(
            published,
            len(articles),
            (edition or {}).get("lookback_hours", 24),
        )
        rows = (
            intro
            + "\n"
            + _render_paragraph(WELCOME_LATEST, top="16px")
            + "\n"
            + _render_paragraph(f"latest edition · {edition_line}", top="20px", color=MUTED)
            + "\n"
            + _render_stories(articles, show_score, show_topics, top="16px")
        )
        preheader = f"{WELCOME_LINES[0]} {WELCOME_LATEST}"
    else:
        rows = intro + "\n" + _render_paragraph(WELCOME_FIRST, top="16px")
        preheader = f"{WELCOME_LINES[0]} {WELCOME_FIRST}"

    return _render_document(
        preheader=preheader,
        stamp="welcome",
        heading="welcome to tech digest",
        subtitle=WELCOME_LINES[0],
        rows=f"{rows}\n{_render_footer(unsubscribe_url)}",
    )


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
            "Authorization": (f"Bearer {_get_api_key()}"),
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

        raise RuntimeError(f"Resend API error ({response.status_code}): {detail}")

    payload = response.json()

    if "id" not in payload:
        raise RuntimeError(f"Resend API error: {payload}")

    return payload
