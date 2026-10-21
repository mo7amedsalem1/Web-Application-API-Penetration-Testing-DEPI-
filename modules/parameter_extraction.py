"""
modules/parameter_extraction.py — Stage 5b: Query parameter extraction.

IMPORTANT
---------
This module extracts parameter names ONLY from URLs that were actually
observed during the reconnaissance pipeline.  It never invents parameter
names through brute-force or inference.  The distinction between
endpoint discovery (ffuf) and parameter discovery is clearly maintained.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Set
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def extract_parameters(
    url_groups: Dict[str, List[str]],
) -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    """
    Extract query parameters from all collected URLs.

    Parameters
    ----------
    url_groups:
        Mapping of target_url -> list of discovered URLs (from all sources).

    Returns
    -------
    A nested dict:
        {
          "https://api.example.com": {
              "https://api.example.com/search?q=test&page=1": {
                  "q": ["test"],
                  "page": ["1"],
              },
              ...
          },
          ...
        }

    Only URLs with at least one query parameter are included.
    No parameter names are inferred or fabricated.
    """
    result: Dict[str, Dict[str, Dict[str, List[str]]]] = {}

    for target, urls in url_groups.items():
        target_params: Dict[str, Dict[str, List[str]]] = {}
        for url in urls:
            params = _parse_url_params(url)
            if params:
                target_params[url] = params
        if target_params:
            result[target] = target_params

    # Log summary
    total_urls = sum(len(v) for v in result.values())
    all_params: Set[str] = set()
    for target_data in result.values():
        for params in target_data.values():
            all_params.update(params.keys())
    logger.info(
        "[Stage 5] Found %d URLs with parameters; %d unique parameter names.",
        total_urls, len(all_params),
    )
    return result


def _parse_url_params(url: str) -> Dict[str, List[str]]:
    """
    Parse query parameters from a URL using urllib.parse.

    Returns an empty dict if the URL has no query string.
    Values are preserved as-is from the original URL.
    """
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return {}
        return parse_qs(parsed.query, keep_blank_values=True)
    except Exception as exc:
        logger.debug("Could not parse URL '%s': %s", url, exc)
        return {}


def get_parameter_summary(
    param_data: Dict[str, Dict[str, Dict[str, List[str]]]],
) -> Dict[str, List[str]]:
    """
    Flatten param_data into a host -> sorted list of unique parameter names.

    Useful for the final report.
    """
    summary: Dict[str, List[str]] = {}
    for target, url_map in param_data.items():
        seen: Set[str] = set()
        for params in url_map.values():
            seen.update(params.keys())
        summary[target] = sorted(seen)
    return summary


def all_urls_with_params(
    param_data: Dict[str, Dict[str, Dict[str, List[str]]]],
) -> List[str]:
    """Return a flat sorted list of all URLs that have at least one parameter."""
    urls: Set[str] = set()
    for url_map in param_data.values():
        urls.update(url_map.keys())
    return sorted(urls)
