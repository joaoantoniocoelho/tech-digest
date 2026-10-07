# Minimal stub: feedparser ships neither inline types nor a types-* package.
from typing import Any

class FeedParserDict(dict[str, Any]):
    def __getattr__(self, name: str) -> Any: ...

def parse(
    url_file_stream_or_string: Any,
    etag: str | None = None,
    modified: Any = None,
    agent: str | None = None,
    referrer: str | None = None,
    handlers: list[Any] | None = None,
    request_headers: dict[str, str] | None = None,
    response_headers: dict[str, str] | None = None,
    resolve_relative_uris: bool | None = None,
    sanitize_html: bool | None = None,
) -> FeedParserDict: ...
