"""
modules/subdomains.py — Stage 1: Subdomain enumeration via subfinder.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Set

from utils.command_runner import CommandError, DependencyError, run_command, require_binary
from config import ReconConfig

logger = logging.getLogger(__name__)

# Hostname validation pattern
_HOSTNAME_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)


def enumerate_subdomains(cfg: ReconConfig) -> List[str]:
    """
    Run subfinder against cfg.domain and return a deduplicated, sorted list
    of discovered subdomains.

    Persists results to cfg.all_subs_file.

    If cfg.skip_subfinder is True and cfg.all_subs_file exists, the file is
    read and returned without running subfinder again.
    """
    out_file = cfg.all_subs_file

    # Resume: skip re-running if results already exist
    if cfg.skip_subfinder and out_file.exists():
        logger.info("[Stage 1] Reusing existing subdomain file: %s", out_file)
        return _load_subs(out_file)

    if cfg.resume and out_file.exists():
        logger.info("[Stage 1] Resume mode: reusing existing subdomain file.")
        return _load_subs(out_file)

    binary = require_binary("subfinder")
    logger.info("[Stage 1] Starting subdomain enumeration for domain: %s", cfg.domain)

    args = [
        binary,
        "-d", cfg.domain,
        "-o", str(out_file),
        "-silent",          # machine-readable output
    ]

    try:
        result = run_command(args, timeout=cfg.timeout * 60)  # generous timeout for enumeration
    except DependencyError as exc:
        logger.error("[Stage 1] Subfinder not available: %s", exc)
        raise
    except TimeoutError as exc:
        logger.warning("[Stage 1] Subfinder timed out: %s", exc)
        result = None
    except CommandError as exc:
        logger.error("[Stage 1] Subfinder returned an error: %s", exc)
        raise

    # Parse and normalise
    raw_output = ""
    if result and result.stdout:
        raw_output = result.stdout.decode(errors="replace")

    # Subfinder writes to -o file; read from there for canonical results
    subs: List[str] = []
    if out_file.exists():
        subs = _load_subs(out_file)
    else:
        # Fall back to stdout parsing
        subs = _parse_subs_from_text(raw_output, cfg.domain)
        _save_subs(subs, out_file)

    logger.info("[Stage 1] Discovered %d unique subdomains.", len(subs))
    return subs


def _parse_subs_from_text(text: str, parent_domain: str) -> List[str]:
    """
    Parse subdomain entries from subfinder text output.
    Returns normalised, deduplicated, sorted list.
    Only keeps entries that are subdomains of *parent_domain*.
    """
    seen: Set[str] = set()
    results: List[str] = []
    suffix = f".{parent_domain.lower()}"
    for line in text.splitlines():
        host = line.strip().lower()
        if not host:
            continue
        if not _HOSTNAME_RE.match(host):
            logger.debug("Skipping malformed hostname: %s", host)
            continue
        # Scope check: must be parent_domain itself or a subdomain
        if host != parent_domain.lower() and not host.endswith(suffix):
            logger.debug("Skipping out-of-scope hostname: %s", host)
            continue
        if host not in seen:
            seen.add(host)
            results.append(host)
    return sorted(results)


def _load_subs(path: Path) -> List[str]:
    """Read, normalise, and deduplicate subdomains from a file."""
    seen: Set[str] = set()
    results: List[str] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            host = line.strip().lower()
            if host and host not in seen:
                if _HOSTNAME_RE.match(host):
                    seen.add(host)
                    results.append(host)
    return sorted(results)


def _save_subs(subs: List[str], path: Path) -> None:
    """Write subdomains to *path*, one per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(subs))
        if subs:
            fh.write("\n")
    logger.debug("Saved %d subdomains to %s", len(subs), path)
