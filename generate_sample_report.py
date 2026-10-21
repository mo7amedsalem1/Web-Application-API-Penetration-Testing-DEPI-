#!/usr/bin/env python3
"""
generate_sample_report.py — Generate a sample final report from mocked data.

This script does NOT send any real requests.  It demonstrates what the
output files look like after a successful recon run.

Usage:
    py -3 generate_sample_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import ReconConfig
from modules.endpoint_discovery import FfufResult
from modules.http_probe import HttpResult
from modules.reporting import (
    aggregate_all_urls,
    write_all_urls,
    write_final_report_json,
    write_final_report_txt,
    write_parameters_json,
)

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_DOMAIN = "example.com"

MOCK_HTTP_RESULTS = [
    HttpResult(
        url="https://api.example.com",
        status_code=200,
        title="API Gateway",
        webserver="nginx/1.24.0",
        content_length=1842,
    ),
    HttpResult(
        url="https://admin.example.com",
        status_code=200,
        title="Admin Panel",
        webserver="Apache/2.4.54",
        content_length=5201,
    ),
    HttpResult(
        url="https://shop.example.com",
        status_code=301,
        title=None,
        webserver="cloudflare",
        content_length=0,
        location="https://www.example.com/shop",
    ),
    HttpResult(
        url="https://staging.example.com",
        status_code=403,
        title="403 Forbidden",
        webserver="nginx/1.22.1",
        content_length=153,
    ),
]

MOCK_FFUF_RESULTS = {
    "https://api.example.com": [
        FfufResult("https://api.example.com/v1/users", 200, content_length=3140),
        FfufResult("https://api.example.com/v1/products", 200, content_length=8900),
        FfufResult("https://api.example.com/v2", 200, content_length=220),
        FfufResult("https://api.example.com/health", 200, content_length=27),
        FfufResult("https://api.example.com/docs", 200, content_length=12300),
    ],
    "https://admin.example.com": [
        FfufResult("https://admin.example.com/login", 200, content_length=4200),
        FfufResult("https://admin.example.com/dashboard", 200, content_length=18000),
        FfufResult("https://admin.example.com/users", 200, content_length=5500),
        FfufResult("https://admin.example.com/settings", 200, content_length=3100),
    ],
}

MOCK_CRAWL_RESULTS = {
    "https://api.example.com": [
        "https://api.example.com/search?q=test&page=1",
        "https://api.example.com/search?q=demo",
        "https://api.example.com/v1/users?limit=50&offset=0",
        "https://api.example.com/v1/products?category=electronics&sort=price",
    ],
    "https://admin.example.com": [
        "https://admin.example.com/users?role=admin",
        "https://admin.example.com/login",
    ],
}

MOCK_PARAM_DATA = {
    "https://api.example.com": {
        "https://api.example.com/search?q=test&page=1": {
            "q": ["test"],
            "page": ["1"],
        },
        "https://api.example.com/search?q=demo": {
            "q": ["demo"],
        },
        "https://api.example.com/v1/users?limit=50&offset=0": {
            "limit": ["50"],
            "offset": ["0"],
        },
        "https://api.example.com/v1/products?category=electronics&sort=price": {
            "category": ["electronics"],
            "sort": ["price"],
        },
    },
    "https://admin.example.com": {
        "https://admin.example.com/users?role=admin": {
            "role": ["admin"],
        },
    },
}


def main():
    output_dir = PROJECT_ROOT / "sample_output"
    cfg = ReconConfig(domain=MOCK_DOMAIN, output_dir=output_dir)
    cfg.create_dirs()

    # Write AllSubs.txt
    all_subs = ["api.example.com", "admin.example.com", "shop.example.com", "staging.example.com"]
    cfg.all_subs_file.write_text("\n".join(all_subs) + "\n")

    # Write AliveSubs.txt
    alive = [r.url for r in MOCK_HTTP_RESULTS]
    cfg.alive_subs_file.write_text("\n".join(sorted(alive)) + "\n")

    # Write Status200.txt
    status200 = [r.url for r in MOCK_HTTP_RESULTS if r.status_code == 200]
    cfg.status200_file.write_text("\n".join(sorted(status200)) + "\n")

    # Write httpx_results.jsonl
    with open(cfg.httpx_jsonl_file, "w") as fh:
        for r in MOCK_HTTP_RESULTS:
            fh.write(json.dumps(r.to_dict()) + "\n")

    # Write ffuf sample JSONs
    for target, results in MOCK_FFUF_RESULTS.items():
        from modules.endpoint_discovery import target_to_filename
        fname = target_to_filename(target)
        ffuf_json = {
            "results": [
                {
                    "url": r.url,
                    "status": r.status_code,
                    "length": r.content_length,
                    "words": r.words,
                    "lines": r.lines,
                }
                for r in results
            ]
        }
        (cfg.ffuf_dir / fname).write_text(json.dumps(ffuf_json, indent=2))

    # Aggregate and write reports
    grouped = aggregate_all_urls(MOCK_FFUF_RESULTS, MOCK_CRAWL_RESULTS, MOCK_HTTP_RESULTS)
    write_all_urls(grouped, cfg.all_urls_file)
    write_parameters_json(MOCK_PARAM_DATA, cfg.parameters_file)
    write_final_report_txt(MOCK_DOMAIN, grouped, MOCK_PARAM_DATA, MOCK_HTTP_RESULTS, cfg)
    write_final_report_json(MOCK_DOMAIN, grouped, MOCK_PARAM_DATA, MOCK_HTTP_RESULTS, MOCK_FFUF_RESULTS, cfg)

    print(f"\nSample report written to: {output_dir}")
    print("\nFiles generated:")
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(output_dir)}")

    print("\n--- final_report.txt preview ---")
    print(cfg.final_report_txt.read_text())


if __name__ == "__main__":
    main()
