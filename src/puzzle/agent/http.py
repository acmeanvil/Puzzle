"""JSON over HTTPS for the providers that need no SDK (ADR 0084), standard library only.

Errors come back in one vocabulary (`ProviderError.kind`, decision 7): `auth`,
`rate_limit` (retried up to three times, honouring Retry-After), `quota`, `context`,
`model`, `tools`, `network`, `server`. The provider's own text stays in the message for
the log; `plain()` is the sentence the agent panel shows.

Requests carry a generic user agent and nothing about the user or the project
(decision 8).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator

USER_AGENT = "puzzle-agent"
_UNSTREAMED: set[str] = set()
RETRIES = 3
SERVER_RETRIES = 2  # a provider's own 5xx or "overloaded" answer, tried again after a moment
KINDS = ("auth", "rate_limit", "quota", "context", "model", "tools", "network", "server")
PLAIN = {
    "auth": "{who} rejected the key, or none is set: set one in Settings > Agent or with "
            "`puzzle agent-key set --provider {id}`.",
    "rate_limit": "{who} is limiting requests; try again in a minute.",
    "quota": "{who} says the account has no credit or quota left.",
    "context": "The conversation is too long for this model.",
    "model": "{who} doesn't offer this model to this key; choose another in the model "
             "button.",
    "tools": "{who} refused the tool calls; this model may not call tools.",
    "network": "{who} could not be reached (offline, or the address is wrong).",
    "server": "{who} had an error of its own; try again.",
}  # fmt: skip


class ProviderError(RuntimeError):
    """A provider's failure, with its `kind` (KINDS, or "" when unclassified)."""

    def __init__(self, message: str, kind: str = "", provider: str = "", status: int = 0):
        super().__init__(message)
        self.kind, self.provider, self.status = kind, provider, status

    def plain(self, who: str = "") -> str:
        """The sentence for the agent panel."""
        if self.kind not in PLAIN:
            return str(self)
        return PLAIN[self.kind].format(who=who or self.provider or "The provider",
                                       id=self.provider or "ID")  # fmt: skip


def classify(status: int, text: str) -> str:
    """An HTTP failure's kind from its status and the provider's words."""
    low = text.lower()
    if status in (401, 403):
        return "quota" if "billing" in low or "credit" in low else "auth"
    if status == 402 or "insufficient_quota" in low or ("quota" in low and status == 429):
        return "quota"
    if status == 429:
        return "rate_limit"
    if status == 404:
        return "model"
    if status in (400, 413, 422):
        if "api key" in low or "api_key_invalid" in low:  # Gemini says a bad key with 400
            return "auth"
        if any(w in low for w in ("context", "too long", "too many tokens", "maximum length",
                                  "prompt is too long", "token limit")):  # fmt: skip
            return "context"
        if "tool" in low or "function" in low:
            return "tools"
        if "model" in low and ("not found" in low or "does not exist" in low
                               or "not supported" in low):  # fmt: skip
            return "model"
        return ""
    if status >= 500:
        return "server"
    return ""


def headers_for(key: str | None, key_header: str, extra: dict | None = None) -> dict:
    out = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if key:
        if key_header == "bearer":
            out["Authorization"] = f"Bearer {key}"
        elif key_header in ("x-api-key", "x-goog-api-key"):
            out[key_header] = key
    return out | dict(extra or {})


def _open(request, timeout: float, who: str, sleep: Callable[[float], None] | None):
    sleep = sleep or time.sleep
    for attempt in range(RETRIES + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")[:2000]
            kind = classify(exc.code, text)
            if kind == "server" and attempt < SERVER_RETRIES:
                sleep(0.5 * 2.0**attempt)
                continue
            if kind == "rate_limit" and attempt < RETRIES:
                wait = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    seconds = float(wait) if wait else 2.0**attempt
                except ValueError:
                    seconds = 2.0**attempt
                sleep(min(seconds, 60.0))
                continue
            raise ProviderError(f"{who}: HTTP {exc.code}: {text}", kind, who, exc.code) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise ProviderError(f"{who}: {exc}", "network", who) from None
    raise AssertionError("unreachable")


def post_json(url: str, body: dict, headers: dict, timeout: float, who: str,
              sleep: Callable[[float], None] | None = None) -> dict:  # fmt: skip
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with _open(request, timeout, who, sleep) as response:
        return json.loads(response.read() or b"{}")


def get_json(url: str, headers: dict, timeout: float, who: str,
             sleep: Callable[[float], None] | None = None) -> dict:  # fmt: skip
    request = urllib.request.Request(url, headers=headers, method="GET")
    with _open(request, timeout, who, sleep) as response:
        return json.loads(response.read() or b"{}")


def post_stream(
    url: str,
    body: dict,
    headers: dict,
    timeout: float,
    who: str,
    sleep: Callable[[float], None] | None = None,
) -> Iterator[tuple[str, dict]]:
    """Server-sent events as (event name, data); a reply that isn't a stream (a proxy
    that buffers) comes back as one ("", data)."""
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with _open(request, timeout, who, sleep) as response:
        kind = response.headers.get("Content-Type", "") if response.headers else ""
        if "event-stream" not in kind:
            if url not in _UNSTREAMED:  # said once in the log (ADR 0084 decision 6)
                _UNSTREAMED.add(url)
                logging.getLogger("puzzle.agent").info(
                    "%s answered in one response, not a stream (a proxy that buffers?)", who
                )
            yield "", json.loads(response.read() or b"{}")
            return
        event, data = "", []
        for raw in _lines(response, who):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if data:
                    text = "\n".join(data)
                    if text.strip() != "[DONE]":
                        yield from _parsed(event, text)
                event, data = "", []
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data and "\n".join(data).strip() != "[DONE]":
            yield from _parsed(event, "\n".join(data))


def _lines(response, who: str) -> Iterator[bytes]:
    """A stream's lines; a connection dropped part way is a `network` ProviderError
    (ADR 0090, AGENT-13), not Python's own error text."""
    import http.client

    try:
        yield from response
    except (OSError, ValueError, http.client.HTTPException) as exc:  # reset, timed out, cut
        raise ProviderError(f"{who}: the connection dropped while it answered ({exc})",
                            "network", who) from None  # fmt: skip
    # A chunked body cut between chunks can end quietly instead: reading a line peeks
    # first, the peek swallows the error and closes the connection, and the read that
    # follows returns nothing. Only the closing chunk leaves no chunk open.
    if getattr(response, "chunked", False) and getattr(response, "chunk_left", None) is not None:
        raise ProviderError(f"{who}: the connection dropped while it answered "
                            "(the stream ended before its last chunk)", "network", who)  # fmt: skip


def _parsed(event: str, text: str) -> Iterator[tuple[str, dict]]:
    """One event's data as JSON; a keep-alive or garbled event is skipped."""
    try:
        data = json.loads(text)
    except ValueError:
        return
    yield event, data
