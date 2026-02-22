import os
import time
from urllib.parse import urlparse, parse_qs, urljoin

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

app = FastAPI()

PLAYLIST_URL = os.environ["PLAYLIST_URL"]
PROXY_HOST = os.environ["PROXY_HOST"]
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8080"))
CACHE_TTL = int(os.environ.get("CACHE_TTL", "300"))
CATCHUP_DAYS = int(os.environ.get("CATCHUP_DAYS", "0"))

_cache: dict = {"body": None, "expires": 0}


def proxy_base() -> str:
    return f"http://{PROXY_HOST}:{PROXY_PORT}"


def rewrite_stream_url(original_url: str) -> str:
    """
    http://ru7.tvtm.one/ch001/index.m3u8?token=user.v2_XXX
    →
    http://proxy/stream/ru7.tvtm.one/user.v2_XXX/ch001/index.m3u8
    """
    parsed = urlparse(original_url)
    token = parse_qs(parsed.query).get("token", [""])[0]
    # strip leading slash from path
    path_no_slash = parsed.path.lstrip("/")
    return f"{proxy_base()}/stream/{parsed.netloc}/{token}/{path_no_slash}"


def make_segments_absolute(m3u8_text: str, base_url: str) -> str:
    """Make relative segment URLs absolute using base_url."""
    lines = []
    for line in m3u8_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            # relative segment path → absolute
            lines.append(urljoin(base_url, stripped))
        else:
            lines.append(line)
    return "\n".join(lines)


def inject_catchup(extinf_line: str, proxy_url: str) -> str:
    attrs = (
        f' catchup="default"'
        f' catchup-days="{CATCHUP_DAYS}"'
        f' catchup-source="{proxy_url}?archive={{utc}}&archive_end={{lutc}}"'
    )
    in_quote = False
    for i, ch in enumerate(extinf_line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ',' and not in_quote:
            return extinf_line[:i] + attrs + extinf_line[i:]
    return extinf_line + attrs


async def fetch_playlist() -> str:
    now = time.time()
    if _cache["body"] is not None and now < _cache["expires"]:
        return _cache["body"]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(PLAYLIST_URL)
        resp.raise_for_status()

    raw = resp.text
    lines = []
    pending_extinf: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#EXTINF"):
            pending_extinf = line
        elif stripped.startswith("http") and ".m3u8" in stripped:
            proxy_url = rewrite_stream_url(stripped)
            if CATCHUP_DAYS > 0 and pending_extinf is not None:
                pending_extinf = inject_catchup(pending_extinf, proxy_url)
            if pending_extinf is not None:
                lines.append(pending_extinf)
                pending_extinf = None
            lines.append(proxy_url)
        else:
            if pending_extinf is not None and not stripped.startswith("#"):
                lines.append(pending_extinf)
                pending_extinf = None
            lines.append(line)
    if pending_extinf is not None:
        lines.append(pending_extinf)

    result = "\n".join(lines)
    _cache["body"] = result
    _cache["expires"] = now + CACHE_TTL
    return result


@app.get("/playlist.m3u8", response_class=PlainTextResponse)
async def playlist():
    try:
        content = await fetch_playlist()
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail=str(exc))
    return PlainTextResponse(content, media_type="application/vnd.apple.mpegurl")


@app.get("/stream/{provider_host}/{token}/{channel_path:path}", response_class=PlainTextResponse)
async def stream(
    provider_host: str,
    token: str,
    channel_path: str,
    archive: str | None = Query(default=None),
    archive_end: str | None = Query(default=None),
):
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        if archive is not None and channel_path.endswith(".m3u8"):
            # Archive mode (Shift catchup)
            channel = channel_path.split("/")[0]
            provider_url = f"http://{provider_host}/{channel}/mono.m3u8"
            params = {"token": token, "utc": archive, "lutc": archive_end}
            resp = await client.get(provider_url, params=params)
        else:
            # Live stream
            provider_url = f"http://{provider_host}/{channel_path}"
            params = {"token": token}
            resp = await client.get(provider_url, params=params)

    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, detail=f"Provider error: {resp.status_code}")

    # Base URL for resolving relative segment paths (use final URL after redirects)
    base_url = str(resp.url)
    content = make_segments_absolute(resp.text, base_url)
    return PlainTextResponse(content, media_type="application/vnd.apple.mpegurl")
