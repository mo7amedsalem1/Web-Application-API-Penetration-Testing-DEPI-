"""
modules/http_probe.py — Stage 2 & 3: HTTP probing via httpx and status filtering.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from utils.command_runner import CommandError, DependencyError, run_command, require_binary
from config import ReconConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class HttpResult:
    """Represents a single httpx probe result."""

    __slots__ = (
        "url", "status_code", "title", "webserver",
        "content_length", "location", "raw",
    )

    def __init__(
        self,
        url: str,
        status_code: int,
        title: Optional[str] = None,
        webserver: Optional[str] = None,
        content_length: Optional[int] = None,
        location: Optional[str] = None,
        raw: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.title = title
        self.webserver = webserver
        self.content_length = content_length
        self.location = location
        self.raw = raw or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "status_code": self.status_code,
            "title": self.title,
            "webserver": self.webserver,
            "content_length": self.content_length,
            "location": self.location,
        }

    @classmethod
    def from_httpx_json(cls, data: Dict[str, Any]) -> "HttpResult":
        """Parse an httpx JSON line into an HttpResult."""
        url = data.get("url") or data.get("input", "")
        status_code = data.get("status_code", 0)
        title = data.get("title") or None
        webserver = data.get("webserver") or data.get("server") or None
        content_length = data.get("content_length") or data.get("content-length") or None
        if isinstance(content_length, str):
            try:
                content_length = int(content_length)
            except ValueError:
                content_length = None
        location = data.get("location") or None
        return cls(
            url=url,
            status_code=int(status_code) if status_code else 0,
            title=title,
            webserver=webserver,
            content_length=content_length,
            location=location,
            raw=data,
        )


# ---------------------------------------------------------------------------
# Stage 2: HTTP probing
# ---------------------------------------------------------------------------

def probe_subdomains(subdomains: List[str], cfg: ReconConfig) -> List[HttpResult]:
    """
    Run httpx against *subdomains* and return a list of HttpResult objects.

    Also writes:
    - cfg.httpx_jsonl_file  — structured JSONL output
    - cfg.alive_subs_file   — plain text URLs of all live services
    """
    jsonl_file = cfg.httpx_jsonl_file
    alive_file = cfg.alive_subs_file

    # Resume logic
    if (cfg.skip_httpx or cfg.resume) and jsonl_file.exists():
        logger.info("[Stage 2] Reusing existing httpx results: %s", jsonl_file)
        return _load_httpx_jsonl(jsonl_file)

    if not subdomains:
        logger.warning("[Stage 2] No subdomains to probe.")
        return []

    binary = require_binary("httpx")
    logger.info("[Stage 2] Probing %d subdomains with httpx...", len(subdomains))

    # Provide subdomains via stdin
    stdin_data = "\n".join(subdomains).encode()

    args = [
        binary,
        "-json",                        # structured output
        "-o", str(jsonl_file),
        "-title",                       # include page title
        "-server",                      # include server header
        "-cl",                          # content length
        "-location",                    # include redirect location
        "-t", str(cfg.threads),
        "-timeout", str(cfg.timeout),
        "-silent",
        "-rl", str(cfg.rate),           # rate limit
    ]

    result = None
    try:
        result = run_command(
            args,
            input_data=stdin_data,
            timeout=cfg.timeout * len(subdomains) + 120,
            capture_output=True,
        )
    except DependencyError:
        raise
    except TimeoutError as exc:
        logger.warning("[Stage 2] httpx timed out; partial results may exist. %s", exc)
    except CommandError as exc:
        logger.error("[Stage 2] httpx error: %s", exc)

    results: List[HttpResult] = []
    if jsonl_file.exists():
        results = _load_httpx_jsonl(jsonl_file)
    elif result and result.stdout:
        # Parse stdout if -o failed
        results = _parse_httpx_stdout(result.stdout.decode(errors="replace"))
        _save_httpx_jsonl(results, jsonl_file)

    logger.info(
        "[Stage 2] httpx found %d live services.", len(results)
    )

    # Write human-readable alive subs
    _write_alive_subs(results, alive_file)

    return results


def _parse_httpx_stdout(text: str) -> List[HttpResult]:
    """Parse httpx JSON lines from stdout."""
    results: List[HttpResult] = []
    seen_urls: Set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON httpx line: %s", line[:120])
            continue
        r = HttpResult.from_httpx_json(data)
        if r.url and r.url not in seen_urls:
            seen_urls.add(r.url)
            results.append(r)
    return results


def _load_httpx_jsonl(path: Path) -> List[HttpResult]:
    """Load results from a JSONL file written by httpx."""
    results: List[HttpResult] = []
    seen: Set[str] = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Malformed JSON in httpx output (skipped): %s", exc)
                continue
            r = HttpResult.from_httpx_json(data)
            if r.url and r.url not in seen:
                seen.add(r.url)
                results.append(r)
    return results


def _save_httpx_jsonl(results: List[HttpResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")


def _write_alive_subs(results: List[HttpResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in sorted(results, key=lambda x: x.url):
            fh.write(f"{r.url}\n")


# ---------------------------------------------------------------------------
# Stage 3: Filter by status code
# ---------------------------------------------------------------------------

def filter_by_status(
    results: List[HttpResult],
    status_codes: List[int],
    cfg: ReconConfig,
) -> List[HttpResult]:
    """
    Return the subset of *results* whose status_code is in *status_codes*.
    Writes the filtered URLs to cfg.status200_file.
    """
    filtered = [r for r in results if r.status_code in status_codes]
    logger.info(
        "[Stage 3] %d of %d services match status codes %s.",
        len(filtered), len(results), status_codes,
    )
    _write_status_file(filtered, cfg.status200_file)
    return filtered


def _write_status_file(results: List[HttpResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in sorted(results, key=lambda x: x.url):
            fh.write(f"{r.url}\n")
    logger.debug("Wrote %d URLs to %s", len(results), path)
