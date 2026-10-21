#!/usr/bin/env python3
"""
recon.py — Web Reconnaissance Automation Tool (CLI entry point).

Usage:
    py -3 recon.py --domain example.com --wordlist /path/to/wordlist.txt

This tool is intended ONLY for domains you own or have explicit written
authorization to test.  Unauthorized use is illegal.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Bootstrap: ensure project root is on sys.path regardless of cwd
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RATE,
    DEFAULT_STATUS_CODES,
    DEFAULT_THREADS,
    DEFAULT_TIMEOUT,
    OPTIONAL_TOOLS,
    REQUIRED_TOOLS,
    ReconConfig,
)
from modules.endpoint_discovery import discover_endpoints
from modules.http_probe import filter_by_status, probe_subdomains
from modules.parameter_extraction import extract_parameters
from modules.reporting import (
    aggregate_all_urls,
    write_all_urls,
    write_final_report_json,
    write_final_report_txt,
    write_parameters_json,
)
from modules.subdomains import enumerate_subdomains
from modules.url_collection import collect_urls
from utils.command_runner import DependencyError
from utils.validation import (
    check_optional_tools,
    check_required_tools,
    validate_domain,
    validate_status_codes,
    validate_wordlist,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s  %(levelname)-8s  %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recon.py",
        description=(
            "Web Reconnaissance Automation Tool — subfinder | httpx | ffuf pipeline.\n\n"
            "  IMPORTANT: Only use against domains you own or have explicit\n"
            "  written authorisation to test.  Unauthorised use is illegal.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  py -3 recon.py --domain example.com --wordlist wordlist.txt
  py -3 recon.py --domain example.com --wordlist wordlist.txt --crawl --collect-urls
  py -3 recon.py --domain example.com --skip-subfinder --resume
  py -3 recon.py --domain example.com --wordlist w.txt --status-codes 200,403
""",
    )

    # Core
    parser.add_argument(
        "--domain", "-d",
        required=True,
        metavar="DOMAIN",
        help="Target domain (e.g. example.com).  Must be a domain you own or are authorised to test.",
    )
    parser.add_argument(
        "--wordlist", "-w",
        metavar="PATH",
        default=None,
        help="Path to the ffuf wordlist file.  Required for endpoint discovery.",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="DIR",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )

    # Filtering
    parser.add_argument(
        "--status-codes",
        metavar="CODES",
        default=",".join(str(c) for c in DEFAULT_STATUS_CODES),
        help=(
            "Comma-separated HTTP status codes to include in ffuf targets "
            f"(default: {','.join(str(c) for c in DEFAULT_STATUS_CODES)})."
        ),
    )

    # Performance
    parser.add_argument(
        "--threads", "-t",
        type=int,
        default=DEFAULT_THREADS,
        metavar="N",
        help=f"Concurrency level for httpx (default: {DEFAULT_THREADS}).",
    )
    parser.add_argument(
        "--rate", "-r",
        type=int,
        default=DEFAULT_RATE,
        metavar="N",
        help=f"Maximum requests per second (default: {DEFAULT_RATE}).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        metavar="SECS",
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )

    # Optional features
    parser.add_argument(
        "--crawl",
        action="store_true",
        default=False,
        help="Enable URL crawling via katana (must be installed).",
    )
    parser.add_argument(
        "--collect-urls",
        action="store_true",
        default=False,
        help="Enable historical URL collection via gau (must be installed).",
    )

    # Skip / resume
    parser.add_argument(
        "--skip-subfinder",
        action="store_true",
        default=False,
        help="Skip subdomain enumeration and reuse existing AllSubs.txt.",
    )
    parser.add_argument(
        "--skip-httpx",
        action="store_true",
        default=False,
        help="Skip HTTP probing and reuse existing httpx_results.jsonl.",
    )
    parser.add_argument(
        "--skip-ffuf",
        action="store_true",
        default=False,
        help="Skip endpoint discovery (ffuf) entirely.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Reuse valid existing results for all stages where possible.",
    )

    # Verbosity
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable debug-level logging.",
    )

    return parser


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_interrupted = False


def _handle_sigint(signum, frame):
    global _interrupted
    _interrupted = True
    print(
        "\n\n[!] Interrupted (Ctrl+C). "
        "Attempting to write partial results before exiting...",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(cfg: ReconConfig) -> int:
    """
    Execute the full reconnaissance pipeline.

    Returns an exit code (0 = success, 1 = partial failure, 2 = fatal error).
    """
    logger = logging.getLogger(__name__)
    start_time = time.monotonic()

    cfg.create_dirs()

    # ----- Stage 1: Subdomain enumeration --------------------------------
    logger.info("=" * 60)
    logger.info("STAGE 1 — Subdomain Enumeration")
    logger.info("=" * 60)
    try:
        subdomains = enumerate_subdomains(cfg)
    except DependencyError as exc:
        logger.error("Stage 1 failed: %s", exc)
        return 2
    except Exception as exc:
        logger.error("Stage 1 unexpected error: %s", exc, exc_info=True)
        return 2

    if not subdomains:
        logger.warning("No subdomains found. Continuing with the target domain itself.")
        subdomains = [cfg.domain]

    if _interrupted:
        logger.info("Interrupted after Stage 1.")
        return 1

    # ----- Stage 2: HTTP probing -----------------------------------------
    logger.info("=" * 60)
    logger.info("STAGE 2 — HTTP Probing (%d hosts)", len(subdomains))
    logger.info("=" * 60)
    try:
        http_results = probe_subdomains(subdomains, cfg)
    except DependencyError as exc:
        logger.error("Stage 2 failed: %s", exc)
        return 2
    except Exception as exc:
        logger.error("Stage 2 unexpected error: %s", exc, exc_info=True)
        return 2

    if _interrupted:
        logger.info("Interrupted after Stage 2.")
        return 1

    # ----- Stage 3: Filter by status code --------------------------------
    logger.info("=" * 60)
    logger.info("STAGE 3 — Filter by Status Code %s", cfg.status_codes)
    logger.info("=" * 60)
    eligible_targets = filter_by_status(http_results, cfg.status_codes, cfg)
    eligible_urls = [r.url for r in eligible_targets]
    logger.info("%d eligible targets after filtering.", len(eligible_urls))

    if _interrupted:
        logger.info("Interrupted after Stage 3.")
        return 1

    # ----- Stage 4: Endpoint discovery (ffuf) ----------------------------
    logger.info("=" * 60)
    logger.info("STAGE 4 — Endpoint Discovery (ffuf)")
    logger.info("=" * 60)
    ffuf_results = {}
    if not cfg.skip_ffuf:
        if cfg.wordlist is None:
            logger.warning(
                "No --wordlist provided.  Skipping ffuf endpoint discovery."
            )
        else:
            try:
                ffuf_results = discover_endpoints(eligible_urls, cfg)
            except DependencyError as exc:
                logger.error("Stage 4 failed: %s", exc)
                # Non-fatal: continue with empty results
            except Exception as exc:
                logger.error("Stage 4 unexpected error: %s", exc, exc_info=True)
    else:
        logger.info("Stage 4 skipped (--skip-ffuf).")

    if _interrupted:
        logger.info("Interrupted after Stage 4. Generating partial report...")
        _write_reports(cfg, subdomains, http_results, ffuf_results, {}, {})
        return 1

    # ----- Stage 5: URL and parameter discovery --------------------------
    logger.info("=" * 60)
    logger.info("STAGE 5 — URL & Parameter Discovery")
    logger.info("=" * 60)
    crawl_results = {}
    if cfg.crawl or cfg.collect_urls:
        try:
            crawl_results = collect_urls(eligible_urls, cfg)
        except Exception as exc:
            logger.error("Stage 5 URL collection error: %s", exc, exc_info=True)

    # Combine all URLs for parameter extraction
    all_url_groups: dict = {}
    for target_url in eligible_urls:
        from urllib.parse import urlparse
        host = urlparse(target_url).netloc
        combined: list = []
        # Add httpx base URL
        combined.append(target_url)
        # Add ffuf results
        for fr in ffuf_results.get(target_url, []):
            combined.append(fr.url)
        # Add crawl/gau results
        combined.extend(crawl_results.get(target_url, []))
        all_url_groups[target_url] = list(dict.fromkeys(combined))

    param_data = extract_parameters(all_url_groups)

    if _interrupted:
        logger.info("Interrupted after Stage 5. Generating partial report...")
        _write_reports(cfg, subdomains, http_results, ffuf_results, crawl_results, param_data)
        return 1

    # ----- Stage 6: Report generation ------------------------------------
    logger.info("=" * 60)
    logger.info("STAGE 6 — Report Generation")
    logger.info("=" * 60)
    _write_reports(cfg, subdomains, http_results, ffuf_results, crawl_results, param_data)

    elapsed = time.monotonic() - start_time
    logger.info("=" * 60)
    logger.info("Reconnaissance complete in %.1f seconds.", elapsed)
    logger.info("Results saved to: %s", cfg.output_dir)
    logger.info("=" * 60)
    return 0


def _write_reports(cfg, subdomains, http_results, ffuf_results, crawl_results, param_data):
    """Write all output files. Called on success and on Ctrl+C."""
    logger = logging.getLogger(__name__)
    try:
        grouped_urls = aggregate_all_urls(ffuf_results, crawl_results, http_results)
        write_all_urls(grouped_urls, cfg.all_urls_file)
        write_parameters_json(param_data, cfg.parameters_file)
        write_final_report_txt(cfg.domain, grouped_urls, param_data, http_results, cfg)
        write_final_report_json(cfg.domain, grouped_urls, param_data, http_results, ffuf_results, cfg)
        logger.info("Output files:")
        logger.info("  AllSubs.txt        : %s", cfg.all_subs_file)
        logger.info("  AliveSubs.txt      : %s", cfg.alive_subs_file)
        logger.info("  Status200.txt      : %s", cfg.status200_file)
        logger.info("  httpx_results.jsonl: %s", cfg.httpx_jsonl_file)
        logger.info("  ffuf/              : %s", cfg.ffuf_dir)
        logger.info("  all_urls.txt       : %s", cfg.all_urls_file)
        logger.info("  parameters.json    : %s", cfg.parameters_file)
        logger.info("  final_report.txt   : %s", cfg.final_report_txt)
        logger.info("  final_report.json  : %s", cfg.final_report_json)
    except Exception as exc:
        logger.error("Report generation failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    signal.signal(signal.SIGINT, _handle_sigint)

    # --- Validate inputs ---
    try:
        domain = validate_domain(args.domain)
    except ValueError as exc:
        logger.error("Invalid domain: %s", exc)
        return 2

    try:
        wordlist = validate_wordlist(args.wordlist)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Wordlist error: %s", exc)
        return 2

    try:
        status_codes = validate_status_codes(args.status_codes)
    except ValueError as exc:
        logger.error("Status codes error: %s", exc)
        return 2

    output_dir = Path(args.output).resolve()

    # --- Check required dependencies (only those stages will run) ---
    skip_stages = {
        "subfinder": args.skip_subfinder,
        "httpx": args.skip_httpx,
        "ffuf": args.skip_ffuf or args.wordlist is None,
    }
    needed = [t for t in REQUIRED_TOOLS if not skip_stages.get(t, False)]
    # If resuming, tools may not be needed
    if args.resume:
        needed = []
        for t in REQUIRED_TOOLS:
            if not skip_stages.get(t, False):
                needed.append(t)

    try:
        check_required_tools(needed)
    except DependencyError as exc:
        logger.error("\n%s", exc)
        logger.info("Tip: See README.md for installation instructions.")
        return 2

    # --- Check optional tools ---
    check_optional_tools(OPTIONAL_TOOLS)

    # --- Build config ---
    cfg = ReconConfig(
        domain=domain,
        output_dir=output_dir,
        wordlist=wordlist,
        status_codes=status_codes,
        threads=args.threads,
        rate=args.rate,
        timeout=args.timeout,
        crawl=args.crawl,
        collect_urls=args.collect_urls,
        skip_subfinder=args.skip_subfinder,
        skip_httpx=args.skip_httpx,
        skip_ffuf=args.skip_ffuf or args.wordlist is None,
        resume=args.resume,
    )

    logger.info("Target domain : %s", cfg.domain)
    logger.info("Output dir    : %s", cfg.output_dir)
    logger.info("Wordlist      : %s", cfg.wordlist or "(none)")
    logger.info("Status codes  : %s", cfg.status_codes)
    logger.info("Threads       : %d", cfg.threads)
    logger.info("Rate limit    : %d req/s", cfg.rate)
    logger.info("Timeout       : %ds", cfg.timeout)

    return run_pipeline(cfg)


if __name__ == "__main__":
    sys.exit(main())
