"""
modules/url_collection.py — Stage 5a: Optional URL discovery via katana / gau.

IMPORTANT
---------
This module is OPTIONAL.  Both katana and gau must be installed and
explicitly enabled via --crawl / --collect-urls respectively.
If a tool is absent, the stage is cleanly skipped with a warning —
no results are fabricated.
"""
from __future__ import annotations

import logging
from typing import Dict, List
from urllib.parse import urlparse

from utils.command_runner import find_binary, run_command
from config import ReconConfig

logger = logging.getLogger(__name__)


def collect_urls(
    targets: List[str],
    cfg: ReconConfig,
) -> Dict[str, List[str]]:
    """
    Collect URLs for each target using enabled optional tools.

    Returns a dict mapping target_url -> list of discovered URLs.
    URL attribution: a URL is assigned to the target whose hostname matches
    the URL's hostname.  No cross-host attribution is performed.
    """
    results: Dict[str, List[str]] = {t: [] for t in targets}

    if not targets:
        return results

    if cfg.crawl:
        katana_path = find_binary("katana")
        if katana_path is None:
            logger.warning(
                "[Stage 5] --crawl requested but 'katana' is not installed. "
                "Skipping crawling. Install katana: "
                "go install github.com/projectdiscovery/katana/cmd/katana@latest"
            )
        else:
            logger.info("[Stage 5] Crawling targets with katana...")
            for target in targets:
                urls = _run_katana(target, katana_path, cfg)
                _merge_urls_by_host(urls, results)
            logger.info("[Stage 5] Crawling complete.")

    if cfg.collect_urls:
        gau_path = find_binary("gau")
        if gau_path is None:
            logger.warning(
                "[Stage 5] --collect-urls requested but 'gau' is not installed. "
                "Skipping URL collection. Install gau: "
                "go install github.com/lc/gau/v2/cmd/gau@latest"
            )
        else:
            logger.info("[Stage 5] Collecting historical URLs with gau...")
            for target in targets:
                host = urlparse(target).netloc
                urls = _run_gau(host, gau_path, cfg)
                _merge_urls_by_host(urls, results)
            logger.info("[Stage 5] Historical URL collection complete.")

    # Deduplicate each host's list
    for key in results:
        results[key] = sorted(set(results[key]))

    return results


def _run_katana(target: str, binary: str, cfg: ReconConfig) -> List[str]:
    """Run katana against *target* and return discovered URLs."""
    args = [
        binary,
        "-u", target,
        "-silent",
        "-d", "3",          # crawl depth
        "-jc",              # parse JavaScript
        "-timeout", str(cfg.timeout),
        "-c", str(cfg.threads),
        "-rl", str(cfg.rate),
    ]
    try:
        result = run_command(args, timeout=cfg.timeout * 120, capture_output=True)
    except Exception as exc:
        logger.warning("katana failed for %s: %s", target, exc)
        return []

    stdout = result.stdout.decode(errors="replace") if result.stdout else ""
    return _extract_urls(stdout)


def _run_gau(hostname: str, binary: str, cfg: ReconConfig) -> List[str]:
    """Run gau against *hostname* and return collected URLs."""
    args = [
        binary,
        "--threads", str(cfg.threads),
        "--timeout", str(cfg.timeout),
        hostname,
    ]
    try:
        result = run_command(args, timeout=cfg.timeout * 120, capture_output=True)
    except Exception as exc:
        logger.warning("gau failed for %s: %s", hostname, exc)
        return []

    stdout = result.stdout.decode(errors="replace") if result.stdout else ""
    return _extract_urls(stdout)


def _extract_urls(text: str) -> List[str]:
    """Extract and normalise URLs from newline-delimited text output."""
    urls: List[str] = []
    for line in text.splitlines():
        url = line.strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            urls.append(url)
    return urls


def _merge_urls_by_host(
    urls: List[str],
    target_map: Dict[str, List[str]],
) -> None:
    """
    Assign each URL to the correct target based on hostname matching.

    A URL is attributed to a target only when the URL's hostname exactly
    matches the target's hostname.  URLs for unknown hosts are discarded
    to prevent cross-host attribution.
    """
    # Build reverse map: hostname -> target key
    host_to_target: Dict[str, str] = {}
    for target in target_map:
        parsed = urlparse(target)
        host_to_target[parsed.netloc] = target

    for url in urls:
        url_host = urlparse(url).netloc
        if url_host in host_to_target:
            target_key = host_to_target[url_host]
            target_map[target_key].append(url)
        else:
            logger.debug(
                "URL hostname '%s' does not match any target; discarding: %s",
                url_host, url,
            )
