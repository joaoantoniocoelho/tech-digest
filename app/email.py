import html
import os
from datetime import datetime

import httpx

from app.db import _parse_article_datetime


RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 15.0
DEFAULT_FROM = (
    "Tech Digest <digest@digest.joaoac.com>"
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


def _bg(color: str) -> str:
    return (
        f"background-color:{color};"
        "background-image:linear-gradient("
        f"{color},{color});"
    )


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
        "Tech Digest — "
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

    separator = "border-top:1px solid #262626;" if index > 1 else ""

    if href:
        title_html = (
            f'<a href="{_esc(href)}" '
            'style="color:#f4f4f5;text-decoration:none;">'
            f"{_esc(title)}</a>"
        )
        read_link = (
            f'<a href="{_esc(href)}" '
            'style="color:#fde68a;text-decoration:underline;'
            'text-underline-offset:3px;">Read article →</a>'
        )
    else:
        title_html = _esc(title)
        read_link = ""

    why_html = ""
    if why:
        why_html = (
            '<p class="ibm-plex-sans" style="margin:0 0 14px;'
            f'{_sans()}font-size:15px;line-height:24px;color:#d4d4d8;">'
            '<span style="color:#7dd3fc;font-weight:600;">Why:</span> '
            f"{_esc(why)}</p>"
        )

    link_html = ""
    if read_link:
        link_html = (
            '<p class="ibm-plex-sans" style="margin:0;'
            f'{_sans(500)}font-size:14px;line-height:22px;">'
            f"{read_link}</p>"
        )

    return (
        '<tr><td bgcolor="#000000" '
        f'style="padding:25px 0 27px;{_bg("#000000")}'
        f'{separator}">'
        '<p class="ibm-plex-mono" style="margin:0 0 9px;'
        f'{_mono()}font-size:11px;line-height:17px;'
        'letter-spacing:0.06em;color:#a1a1aa;">'
        f'{index:02d} &nbsp; {_esc(" · ".join(details))}</p>'
        '<h2 class="ibm-plex-sans" style="margin:0 0 12px;'
        f'{_sans(600)}font-size:21px;line-height:29px;'
        'color:#f4f4f5;">'
        f'{title_html}</h2>{why_html}{link_html}'
        '</td></tr>'
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
    count_label = f"{count} article{'s' if count != 1 else ''} selected"
    preheader = f"{count_label} from the last {lookback_hours} hours"
    lede = f"Best articles from the last {lookback_hours} hours"
    date_label = _format_calendar_date(sent_at, with_weekday=True)

    unsubscribe_html = ""
    if unsubscribe_url:
        unsubscribe_html = (
            '<p class="ibm-plex-sans" style="margin:12px 0 0;'
            f'{_sans()}font-size:13px;line-height:21px;color:#a1a1aa;">'
            f'<a href="{_esc(unsubscribe_url)}" '
            'style="color:#a1a1aa;text-decoration:underline;">'
            "Unsubscribe</a></p>"
        )

    if articles:
        article_rows = "\n".join(
            _render_article(article, index, show_score, show_topics)
            for index, article in enumerate(articles, start=1)
        )
    else:
        article_rows = (
            '<tr><td style="padding:25px 0;">'
            '<p class="ibm-plex-sans" style="margin:0;'
            f'{_sans()}font-size:15px;line-height:24px;color:#d4d4d8;">'
            'No articles passed the relevance threshold.</p></td></tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{_esc(_SANS_STYLESHEET)}" rel="stylesheet">
<link href="{_esc(_MONO_STYLESHEET)}" rel="stylesheet">
<style>
:root {{ color-scheme: dark; supported-color-schemes: dark; }}
.ibm-plex-sans {{ font-family: "IBM Plex Sans", Arial, sans-serif; }}
.ibm-plex-mono {{ font-family: "IBM Plex Mono", monospace; }}
/* Gmail mobile can invert text while keeping a gradient background dark. */
u + .body .gmail-blend-screen {{ background:#000000; mix-blend-mode:screen; }}
u + .body .gmail-blend-difference {{ background:#000000; mix-blend-mode:difference; }}
@media only screen and (max-width:600px) {{
  .email-shell {{ padding:24px 20px !important; }}
  .headline {{ font-size:30px !important; line-height:37px !important; }}
}}
</style>
<title>Tech Digest</title>
</head>
<body class="body ibm-plex-sans" bgcolor="#000000" style="margin:0;padding:0;{_bg('#000000')}color:#f4f4f5;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{_esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#000000" style="{_bg('#000000')}">
<tr><td align="center" bgcolor="#000000" style="{_bg('#000000')}">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#000000" style="width:100%;max-width:600px;{_bg('#000000')}">
<tr><td class="email-shell" bgcolor="#000000" style="padding:36px 30px 32px;{_bg('#000000')}color:#f4f4f5;">
<div class="gmail-blend-screen"><div class="gmail-blend-difference">
<p class="ibm-plex-mono" style="margin:0 0 25px;{_mono()}font-size:12px;line-height:18px;letter-spacing:0.18em;color:#64a9ca;">TECH DIGEST</p>
<h1 class="headline ibm-plex-sans" style="margin:0 0 11px;{_sans(600)}font-size:36px;line-height:43px;color:#f4f4f5;">Technology worth your time<span style="color:#7dd3fc;">.</span></h1>
<p class="ibm-plex-sans" style="margin:0 0 8px;{_sans()}font-size:16px;line-height:25px;color:#d4d4d8;">{_esc(lede)}</p>
<p class="ibm-plex-mono" style="margin:0 0 32px;{_mono()}font-size:12px;line-height:20px;color:#a1a1aa;">{_esc(date_label)} &nbsp; · &nbsp; {_esc(count_label)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:2px solid #4a8aa8;">
{article_rows}
</table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="padding:25px 0 0;border-top:1px solid #262626;">
<p class="ibm-plex-sans" style="margin:0 0 10px;{_sans()}font-size:13px;line-height:21px;color:#a1a1aa;">Curated by João Coelho. Each link goes to its original publisher.</p>
<p class="ibm-plex-mono" style="margin:0;{_mono()}font-size:13px;line-height:22px;color:#f4f4f5;">
<a href="https://x.com/joaoac_dev" style="color:#fde68a;text-decoration:underline;">X</a>
&nbsp; · &nbsp;
<a href="https://joaoac.com" style="color:#fde68a;text-decoration:underline;">Website</a>
</p>
{unsubscribe_html}
</td></tr></table>
</div></div>
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
