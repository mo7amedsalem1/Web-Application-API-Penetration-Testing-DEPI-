"""
validation.py — Input validation and dependency checks.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from utils.command_runner import DependencyError, find_binary

logger = logging.getLogger(__name__)

# RFC-1123 / RFC-952 hostname pattern (simple but effective for CLI validation)
_DOMAIN_RE = re.compile(
    r"^(?:[a-zA-Z0-9]"
    r"(?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"\.)+[a-zA-Z]{2,}$"
)


def validate_domain(domain: str) -> str:
    """
    Validate that *domain* looks like a valid hostname.

    Returns the lowercased domain or raises ValueError.
    """
    domain = domain.strip().lower()
    if not _DOMAIN_RE.match(domain):
        raise ValueError(
            f"Invalid domain format: '{domain}'. "
            "Expected something like 'example.com'."
        )
    return domain


def validate_wordlist(path_str: Optional[str]) -> Optional[Path]:
    """
    Return a resolved Path for *path_str* or raise FileNotFoundError.
    Returns None when path_str is None.
    """
    if path_str is None:
        return None
    p = Path(path_str).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Wordlist not found: {p}")
    if not p.is_file():
        raise ValueError(f"Wordlist path is not a file: {p}")
    return p


def validate_status_codes(raw: str) -> List[int]:
    """
    Parse a comma-separated string of HTTP status codes.

    Raises ValueError for codes outside 100-599.
    """
    codes: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            code = int(part)
        except ValueError:
            raise ValueError(f"Invalid status code: '{part}'")
        if not (100 <= code <= 599):
            raise ValueError(f"Status code out of range: {code}")
        codes.append(code)
    if not codes:
        raise ValueError("At least one status code must be specified.")
    return sorted(set(codes))


def check_required_tools(tool_names: List[str]) -> Dict[str, Optional[str]]:
    """
    Check which tools are available on PATH.

    Returns a dict mapping tool_name -> path_or_None.
    Raises DependencyError listing all missing required tools.
    """
    results: Dict[str, Optional[str]] = {}
    missing: List[str] = []
    for name in tool_names:
        path = find_binary(name)
        results[name] = path
        if path is None:
            missing.append(name)
            logger.warning("Required tool not found: %s", name)
        else:
            logger.info("Found tool: %s -> %s", name, path)
    if missing:
        raise DependencyError(
            "The following required tools are missing from PATH:\n"
            + "\n".join(f"  - {t}" for t in missing)
            + "\n\nInstall them before running this tool.\n"
            + "See README.md for installation instructions."
        )
    return results


def check_optional_tools(tool_names: List[str]) -> Dict[str, Optional[str]]:
    """
    Check which optional tools are available.  Never raises.
    Returns a dict mapping tool_name -> path_or_None.
    """
    results: Dict[str, Optional[str]] = {}
    for name in tool_names:
        path = find_binary(name)
        results[name] = path
        if path is None:
            logger.info("Optional tool not available: %s (feature disabled)", name)
        else:
            logger.info("Optional tool found: %s -> %s", name, path)
    return results


def safe_output_path(base: Path, filename: str) -> Path:
    """
    Safely construct an output path, ensuring it stays within *base*.

    Raises ValueError if the resolved path escapes the base directory.
    """
    base = base.resolve()
    target = (base / filename).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(
            f"Output path '{filename}' escapes the output directory."
        )
    return target
