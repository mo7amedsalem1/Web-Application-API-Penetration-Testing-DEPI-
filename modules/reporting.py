"""
modules/reporting.py — Stage 6: Report generation.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from config import ReconConfig
from modules.endpoint_discovery import FfufResult
from modules.http_probe import HttpResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data aggregation helpers
# ---------------------------------------------------------------------------

def aggregate_all_urls(
    ffuf_results: Dict[str, List[FfufResult]],
    crawl_results: Dict[str, List[str]],
    http_results: List[HttpResult],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Merge all discovered URLs grouped by hostname.

    Each entry: {"url": str, "status_code": int|None, "source": str}

    URL attribution is done strictly by hostname.  A URL is placed under
    the group that matches its own hostname — never inferred from the target
    that produced it.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_per_host: Dict[str, Set[str]] = defaultdict(set)

    def _add(url: str, status: Optional[int], source: str) -> None:
        host = urlparse(url).netloc
        if not host:
            return
        if url not in seen_per_host[host]:
            seen_per_host[host].add(url)
            grouped[host].append({
                "url": url,
                "status_code": status,
                "source": source,
            })

    # HTTP probe results (the root of each live service)
    for r in http_results:
        _add(r.url, r.status_code, "httpx")

    # FFUF endpoint results
    for target, results in ffuf_results.items():
        for fr in results:
            _add(fr.url, fr.status_code, "ffuf")

    # Crawl / URL collection results
    for target, urls in crawl_results.items():
        for url in urls:
            _add(url, None, "crawl/gau")

    # Sort each group by URL for stable output
    for host in grouped:
        grouped[host].sort(key=lambda x: x["url"])

    return dict(grouped)


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_all_urls(
    grouped_urls: Dict[str, List[Dict[str, Any]]],
    path: Path,
) -> None:
    """Write a deduplicated flat list of all discovered URLs."""
    all_urls: Set[str] = set()
    for entries in grouped_urls.values():
        for entry in entries:
            all_urls.add(entry["url"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for url in sorted(all_urls):
            fh.write(url + "\n")
    logger.info("Wrote %d unique URLs to %s", len(all_urls), path)


def write_parameters_json(
    param_data: Dict[str, Dict[str, Dict[str, List[str]]]],
    path: Path,
) -> None:
    """Write URL-to-parameter mapping as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(param_data, fh, indent=2, ensure_ascii=False)
    logger.info("Wrote parameter map to %s", path)


def write_final_report_txt(
    domain: str,
    grouped_urls: Dict[str, List[Dict[str, Any]]],
    param_data: Dict[str, Dict[str, Dict[str, List[str]]]],
    http_results: List[HttpResult],
    cfg: ReconConfig,
) -> None:
    """
    Write the human-readable final report grouped by subdomain.

    Format per subdomain:

        Subdomain: https://api.example.com
          - https://api.example.com/
          - https://api.example.com/search?q=test  [200, ffuf]
            Parameters: q
    """
    # Build a quick lookup: host -> HttpResult for metadata
    alive_by_host: Dict[str, HttpResult] = {}
    for r in http_results:
        host = urlparse(r.url).netloc
        alive_by_host[host] = r

    path = cfg.final_report_txt
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"Domain: {domain}\n\n")

        for host, entries in sorted(grouped_urls.items()):
            # Reconstruct the subdomain URL from the httpx result if possible
            probe = alive_by_host.get(host)
            sub_url = probe.url if probe else f"https://{host}"
            fh.write(f"Subdomain: {sub_url}\n")

            for entry in sorted(entries, key=lambda x: x["url"]):
                url = entry["url"]
                status = entry.get("status_code")
                source = entry.get("source", "unknown")
                status_str = f"[{status}, {source}]" if status else f"[{source}]"
                fh.write(f"  - {url}  {status_str}\n")

                # Print parameters if present
                url_params = _get_params_for_url(url, param_data, host)
                if url_params:
                    param_names = ", ".join(sorted(url_params))
                    fh.write(f"    Parameters: {param_names}\n")

            fh.write("\n")

    logger.info("Final text report written: %s", path)


def write_final_report_json(
    domain: str,
    grouped_urls: Dict[str, List[Dict[str, Any]]],
    param_data: Dict[str, Dict[str, Dict[str, List[str]]]],
    http_results: List[HttpResult],
    ffuf_results: Dict[str, List[FfufResult]],
    cfg: ReconConfig,
) -> None:
    """Write the machine-readable final report as JSON."""
    report: Dict[str, Any] = {
        "domain": domain,
        "summary": {
            "total_subdomains_found": len(grouped_urls),
            "total_urls_discovered": sum(len(v) for v in grouped_urls.values()),
            "total_urls_with_params": sum(
                len(url_map) for url_map in param_data.values()
            ),
        },
        "subdomains": {},
    }

    alive_by_host: Dict[str, HttpResult] = {}
    for r in http_results:
        alive_by_host[urlparse(r.url).netloc] = r

    for host, entries in sorted(grouped_urls.items()):
        probe = alive_by_host.get(host)
        sub_data: Dict[str, Any] = {
            "status_code": probe.status_code if probe else None,
            "title": probe.title if probe else None,
            "webserver": probe.webserver if probe else None,
            "urls": [],
        }
        for entry in sorted(entries, key=lambda x: x["url"]):
            url = entry["url"]
            url_params = _get_params_for_url(url, param_data, host)
            sub_data["urls"].append({
                "url": url,
                "status_code": entry.get("status_code"),
                "source": entry.get("source"),
                "parameters": sorted(url_params) if url_params else [],
            })
        report["subdomains"][host] = sub_data

    path = cfg.final_report_json
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    logger.info("Final JSON report written: %s", path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_params_for_url(
    url: str,
    param_data: Dict[str, Dict[str, Dict[str, List[str]]]],
    host: str,
) -> Set[str]:
    """Return the set of parameter names observed in *url*, or empty set."""
    # Look up by host key (target)
    for target_key, url_map in param_data.items():
        if urlparse(target_key).netloc == host or target_key == host:
            if url in url_map:
                return set(url_map[url].keys())
    # Fallback: search all hosts
    for url_map in param_data.values():
        if url in url_map:
            return set(url_map[url].keys())
    return set()
