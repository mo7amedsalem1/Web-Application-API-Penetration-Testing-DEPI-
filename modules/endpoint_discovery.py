"""
modules/endpoint_discovery.py — Stage 4: Directory/endpoint discovery via ffuf.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse, urljoin

from utils.command_runner import CommandError, DependencyError, run_command, require_binary
from config import ReconConfig

logger = logging.getLogger(__name__)

# Sanitize hostname for use in filenames
_UNSAFE_CHARS = re.compile(r"[^\w\-.]")


def target_to_filename(url: str) -> str:
    """Convert a URL to a safe filename component."""
    parsed = urlparse(url)
    name = f"{parsed.scheme}_{parsed.netloc}{parsed.path.rstrip('/')}"
    return _UNSAFE_CHARS.sub("_", name)[:200] + ".json"


class FfufResult:
    """Represents a single FFUF-discovered endpoint."""

    __slots__ = ("url", "status_code", "content_length", "words", "lines", "redirect_location", "source")

    def __init__(
        self,
        url: str,
        status_code: int,
        content_length: int = 0,
        words: int = 0,
        lines: int = 0,
        redirect_location: Optional[str] = None,
        source: str = "ffuf",
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.content_length = content_length
        self.words = words
        self.lines = lines
        self.redirect_location = redirect_location
        self.source = source

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "status_code": self.status_code,
            "content_length": self.content_length,
            "words": self.words,
            "lines": self.lines,
            "redirect_location": self.redirect_location,
            "source": self.source,
        }


def discover_endpoints(
    targets: List[str],
    cfg: ReconConfig,
) -> Dict[str, List[FfufResult]]:
    """
    Run ffuf against each target URL.

    Returns a dict mapping target_url -> list of FfufResult.
    Individual JSON outputs are saved under cfg.ffuf_dir.
    """
    if not targets:
        logger.warning("[Stage 4] No targets for endpoint discovery.")
        return {}

    if cfg.wordlist is None:
        logger.error("[Stage 4] No wordlist specified (--wordlist). Skipping ffuf.")
        return {}

    if cfg.skip_ffuf:
        logger.info("[Stage 4] --skip-ffuf set; loading any existing ffuf results.")
        return _load_existing_ffuf_results(targets, cfg)

    try:
        binary = require_binary("ffuf")
    except DependencyError as exc:
        logger.error("[Stage 4] ffuf not available: %s", exc)
        raise

    all_results: Dict[str, List[FfufResult]] = {}
    failed: List[str] = []

    with ThreadPoolExecutor(max_workers=cfg.max_ffuf_procs) as pool:
        future_to_target = {
            pool.submit(_run_ffuf_single, target, binary, cfg): target
            for target in targets
        }
        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                results = future.result()
                all_results[target] = results
                logger.info(
                    "[Stage 4] %s — %d endpoints found.", target, len(results)
                )
            except Exception as exc:
                logger.error(
                    "[Stage 4] ffuf failed for %s: %s", target, exc
                )
                failed.append(target)
                all_results[target] = []  # Preserve empty entry; don't drop

    if failed:
        logger.warning(
            "[Stage 4] ffuf failed for %d target(s): %s", len(failed), failed
        )

    return all_results


def _run_ffuf_single(
    target: str,
    binary: str,
    cfg: ReconConfig,
) -> List[FfufResult]:
    """Run ffuf against a single *target* and return parsed results."""
    filename = target_to_filename(target)
    out_path = cfg.ffuf_dir / filename

    # Resume: reuse valid existing JSON
    if cfg.resume and out_path.exists() and out_path.stat().st_size > 0:
        logger.info("[Stage 4] Resume: reusing existing ffuf result for %s", target)
        return _parse_ffuf_json(out_path, target)

    fuzz_url = _build_fuzz_url(target)
    if fuzz_url is None:
        logger.warning("[Stage 4] Could not build FUZZ URL for: %s", target)
        return []

    args = [
        binary,
        "-u", fuzz_url,
        "-w", str(cfg.wordlist),
        "-mc", "all",
        "-fc", "404",
        "-of", "json",
        "-o", str(out_path),
        "-t", str(cfg.ffuf_threads),
        "-timeout", str(cfg.ffuf_timeout),
        "-noninteractive",
        "-s",  # silent mode
    ]

    if cfg.ffuf_rate > 0:
        args += ["-rate", str(cfg.ffuf_rate)]

    logger.info("[Stage 4] Running ffuf on: %s", target)
    try:
        run_command(args, timeout=cfg.ffuf_timeout * 600, capture_output=True)
    except TimeoutError as exc:
        logger.warning("[Stage 4] ffuf timed out for %s: %s", target, exc)
    except CommandError as exc:
        logger.error("[Stage 4] ffuf error for %s: %s", target, exc)
        raise

    if out_path.exists():
        return _parse_ffuf_json(out_path, target)
    return []


def _build_fuzz_url(target: str) -> Optional[str]:
    """Append /FUZZ to the target URL base path."""
    parsed = urlparse(target)
    if not parsed.scheme or not parsed.netloc:
        return None
    # Use base of target (scheme + netloc) with /FUZZ
    base = f"{parsed.scheme}://{parsed.netloc}"
    return urljoin(base, "/FUZZ")


def _parse_ffuf_json(path: Path, target: str) -> List[FfufResult]:
    """Parse a ffuf JSON output file into a list of FfufResult."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        logger.warning("Malformed ffuf JSON at %s: %s", path, exc)
        return []
    except OSError as exc:
        logger.warning("Cannot read ffuf output %s: %s", path, exc)
        return []

    entries = data.get("results", [])
    seen: Set[str] = set()
    results: List[FfufResult] = []

    for entry in entries:
        url = entry.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)

        status = entry.get("status", 0)
        length = entry.get("length", 0)
        words = entry.get("words", 0)
        lines = entry.get("lines", 0)
        redirect = entry.get("redirectlocation") or None

        results.append(FfufResult(
            url=url,
            status_code=int(status),
            content_length=int(length),
            words=int(words),
            lines=int(lines),
            redirect_location=redirect,
            source="ffuf",
        ))

    return results


def _load_existing_ffuf_results(
    targets: List[str], cfg: ReconConfig
) -> Dict[str, List[FfufResult]]:
    """Load previously saved ffuf JSON files for the given targets."""
    all_results: Dict[str, List[FfufResult]] = {}
    for target in targets:
        filename = target_to_filename(target)
        out_path = cfg.ffuf_dir / filename
        if out_path.exists():
            all_results[target] = _parse_ffuf_json(out_path, target)
        else:
            all_results[target] = []
    return all_results
