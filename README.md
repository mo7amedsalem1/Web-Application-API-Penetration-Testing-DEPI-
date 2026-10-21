# Web Reconnaissance Automation Tool

A modular, safe Python CLI tool that automates web reconnaissance using
**subfinder**, **httpx**, and **ffuf** — plus optional **katana** and **gau** integration.

> **Legal notice:** This tool must only be used against domains and web applications
> that you own or have **explicit written authorisation** to test.
> Unauthorised use is illegal and unethical.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Stage Descriptions](#stage-descriptions)
3. [Installation](#installation)
4. [Dependency Setup](#dependency-setup)
5. [Usage](#usage)
6. [CLI Options Reference](#cli-options-reference)
7. [Output Files](#output-files)
8. [Concepts: Endpoint Discovery vs URL Collection vs Parameter Extraction](#concepts)
9. [Sample Report](#sample-report)
10. [Troubleshooting](#troubleshooting)
11. [Running Tests](#running-tests)

---

## Architecture Overview

```
recon.py                    ← CLI entry point (argparse, pipeline orchestration)
config.py                   ← Central defaults and ReconConfig dataclass
modules/
    subdomains.py           ← Stage 1: subfinder integration
    http_probe.py           ← Stage 2 & 3: httpx + status-code filtering
    endpoint_discovery.py   ← Stage 4: ffuf directory/endpoint fuzzing
    url_collection.py       ← Stage 5a: optional katana / gau URL collection
    parameter_extraction.py ← Stage 5b: urllib.parse query-string extraction
    reporting.py            ← Stage 6: all output file generation
utils/
    command_runner.py       ← Safe subprocess wrapper (no shell=True)
    validation.py           ← Input and dependency validation
tests/
    test_recon.py           ← Unit tests (all external tools mocked)
generate_sample_report.py   ← Generates sample output from mock data
```

---

## Stage Descriptions

| # | Name | Tool | Output |
|---|------|------|--------|
| 1 | Subdomain Enumeration | `subfinder` | `AllSubs.txt` |
| 2 | HTTP Probing | `httpx` | `httpx_results.jsonl`, `AliveSubs.txt` |
| 3 | Status Filtering | Python | `Status200.txt` |
| 4 | Endpoint Discovery | `ffuf` | `ffuf/*.json` |
| 5a | URL Collection (optional) | `katana`, `gau` | merged into `all_urls.txt` |
| 5b | Parameter Extraction | Python `urllib.parse` | `parameters.json` |
| 6 | Report Generation | Python | `final_report.txt`, `final_report.json` |

---

## Installation

### 1. Clone / copy the project

```bash
# Place the project files in your working directory, e.g.:
cd C:\Users\UTD\Desktop\depi
```

### 2. Python 3.11+

The tool uses only the Python standard library.  No `pip install` is required.

```powershell
py -3 --version   # should show 3.11 or later
```

---

## Dependency Setup

### Required tools

These **must** be on your `PATH` before running the tool:

| Tool | Purpose | Install |
|------|---------|---------|
| `subfinder` | Subdomain enumeration | [ProjectDiscovery releases](https://github.com/projectdiscovery/subfinder/releases) |
| `httpx` | HTTP probing | [ProjectDiscovery releases](https://github.com/projectdiscovery/httpx/releases) |
| `ffuf` | Directory/endpoint fuzzing | [ffuf releases](https://github.com/ffuf/ffuf/releases) |

#### Windows — recommended via Go

```powershell
# Install Go first: https://go.dev/dl/
go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install github.com/projectdiscovery/httpx/cmd/httpx@latest
go install github.com/ffuf/ffuf/v2@latest
```

After installation, add your Go bin directory to PATH:

```powershell
$env:PATH += ";$env:USERPROFILE\go\bin"
# Or add permanently via System Properties > Environment Variables
```

Verify:

```powershell
subfinder -version
httpx -version
ffuf -V
```

### Optional tools

These are needed only when the corresponding flag is passed:

| Tool | Flag | Purpose | Install |
|------|------|---------|---------|
| `katana` | `--crawl` | Active web crawling | `go install github.com/projectdiscovery/katana/cmd/katana@latest` |
| `gau` | `--collect-urls` | Historical URL collection | `go install github.com/lc/gau/v2/cmd/gau@latest` |

If an optional tool is missing, the tool prints a clear warning and **skips that feature** — it does not crash or fabricate results.

### Wordlist

For ffuf endpoint discovery you need a wordlist.  Recommended sources:

- **SecLists** — [danielmiessler/SecLists](https://github.com/danielmiessler/SecLists)
  - `Discovery/Web-Content/common.txt`
  - `Discovery/Web-Content/raft-medium-directories.txt`
- **dirbuster** lists (often pre-installed on Kali / Parrot)

---

## Usage

### Basic run

```powershell
py -3 recon.py --domain example.com --wordlist C:\wordlists\common.txt
```

### With crawling and URL collection

```powershell
py -3 recon.py --domain example.com `
    --wordlist C:\wordlists\raft-medium-directories.txt `
    --crawl --collect-urls `
    --output output_example
```

### Skip stages / resume a previous run

```powershell
# Reuse existing subdomain results, re-run everything else
py -3 recon.py --domain example.com --wordlist wordlist.txt --skip-subfinder

# Resume: reuse all valid existing stage output
py -3 recon.py --domain example.com --wordlist wordlist.txt --resume
```

### Probe-only run (no endpoint discovery)

```powershell
py -3 recon.py --domain example.com --skip-ffuf
```

### Custom status-code filter

```powershell
# Run ffuf against 200 AND 403 responses
py -3 recon.py --domain example.com --wordlist w.txt --status-codes 200,403
```

### Generate sample report (no real requests)

```powershell
py -3 generate_sample_report.py
```

---

## CLI Options Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--domain`, `-d` | *required* | Target domain (e.g. `example.com`) |
| `--wordlist`, `-w` | `None` | Path to ffuf wordlist. Required for endpoint discovery |
| `--output`, `-o` | `output` | Output directory |
| `--status-codes` | `200` | Comma-separated HTTP status codes for ffuf targeting |
| `--threads`, `-t` | `10` | Concurrency level for httpx |
| `--rate`, `-r` | `150` | Max requests per second (conservative default) |
| `--timeout` | `10` | Request timeout in seconds |
| `--crawl` | off | Enable web crawling via **katana** (must be installed) |
| `--collect-urls` | off | Enable historical URL collection via **gau** (must be installed) |
| `--skip-subfinder` | off | Skip subfinder, reuse existing `AllSubs.txt` |
| `--skip-httpx` | off | Skip httpx, reuse existing `httpx_results.jsonl` |
| `--skip-ffuf` | off | Skip ffuf endpoint discovery entirely |
| `--resume` | off | Reuse all valid existing results from previous run |
| `--verbose`, `-v` | off | Enable debug-level logging |
| `--help` | — | Show help and exit |

---

## Output Files

All output files are written to the directory specified by `--output` (default: `output/`).

```
output/
├── AllSubs.txt              # All discovered subdomains (one per line)
├── AliveSubs.txt            # Live HTTP/HTTPS services found by httpx
├── Status200.txt            # Services matching the --status-codes filter
├── httpx_results.jsonl      # Full httpx results (JSON Lines, one entry per line)
├── ffuf/
│   └── https_api_example_com.json   # Individual ffuf results per target
├── all_urls.txt             # Deduplicated union of all discovered URLs
├── parameters.json          # URL → parameter name mapping
├── final_report.txt         # Human-readable grouped report
└── final_report.json        # Machine-readable full report
```

### `httpx_results.jsonl` fields

Each JSON line contains: `url`, `status_code`, `title`, `webserver`, `content_length`, `location`

### `parameters.json` structure

```json
{
  "https://api.example.com": {
    "https://api.example.com/search?q=test&page=1": {
      "q": ["test"],
      "page": ["1"]
    }
  }
}
```

Only URLs where parameters were **actually observed** appear here.

---

## Concepts

### Endpoint Discovery (ffuf)

ffuf performs **directory and file fuzzing** by replacing the `FUZZ` placeholder in
a URL template with each entry from the wordlist.  This discovers paths like
`/admin`, `/api/v1`, `/backup.zip`, etc.

- **Does NOT** discover query parameters.
- **Does NOT** crawl JavaScript or follow links.
- Results depend entirely on the wordlist quality.

### URL Collection (katana / gau)

- **katana** crawls the live website, following links and parsing JavaScript.
- **gau** queries public URL archives (Wayback Machine, Common Crawl, etc.) for
  historically observed URLs.
- These sources often surface query-parameterised URLs that ffuf cannot find.
- Both are **optional** and require their respective binaries.

### Parameter Extraction

Parameter names are extracted from collected URLs using Python's `urllib.parse.parse_qs`.

- **Only parameters actually present in observed URLs are reported.**
- No brute-force parameter guessing is performed.
- If you want active parameter discovery, tools like `arjun` can be added as an optional
  future stage.

**The three stages are complementary, not equivalent:**

```
ffuf          → discovers paths/endpoints (structure)
katana/gau    → discovers full URLs including parameters (surface area)
urllib.parse  → extracts parameter names from collected URLs (input vectors)
```

---

## Sample Report

Run the sample report generator to see what output looks like:

```powershell
py -3 generate_sample_report.py
```

This creates `sample_output/` with all seven output files populated from realistic
mock data — no network requests are made.

Sample `final_report.txt` excerpt:

```
Domain: example.com

Subdomain: https://api.example.com
  - https://api.example.com  [200, httpx]
  - https://api.example.com/docs  [200, ffuf]
  - https://api.example.com/health  [200, ffuf]
  - https://api.example.com/search?q=demo  [crawl/gau]
    Parameters: q
  - https://api.example.com/search?q=test&page=1  [crawl/gau]
    Parameters: page, q
  - https://api.example.com/v1/products  [200, ffuf]
  - https://api.example.com/v1/products?category=electronics&sort=price  [crawl/gau]
    Parameters: category, sort
  - https://api.example.com/v1/users  [200, ffuf]
  - https://api.example.com/v1/users?limit=50&offset=0  [crawl/gau]
    Parameters: limit, offset
  - https://api.example.com/v2  [200, ffuf]

Subdomain: https://admin.example.com
  - https://admin.example.com  [200, httpx]
  - https://admin.example.com/dashboard  [200, ffuf]
  - https://admin.example.com/login  [200, ffuf]
  - https://admin.example.com/settings  [200, ffuf]
  - https://admin.example.com/users  [200, ffuf]
  - https://admin.example.com/users?role=admin  [crawl/gau]
    Parameters: role
```

---

## Troubleshooting

### "Required binary 'subfinder' not found on PATH"

Install the tool and ensure its location is on your `PATH`:

```powershell
# Add Go bin to current session
$env:PATH += ";$env:USERPROFILE\go\bin"

# Verify
subfinder -version
```

### httpx flags not recognised

Different httpx versions use slightly different flag names.  Run:

```powershell
httpx -h | Select-String "server\|title\|content"
```

If `-server` is not present, the tool still works — it gracefully handles missing
fields in the JSON output.

### ffuf produces no results

- Check the wordlist path is correct and the file is not empty.
- Try a smaller wordlist to verify ffuf is running.
- Increase `--rate` carefully only on targets you control.

### Stage skipping with `--resume`

`--resume` checks whether the output file for each stage exists and is non-empty.
Delete the specific file to force a stage to re-run.

### Ctrl+C handling

Pressing Ctrl+C at any point causes the tool to attempt writing partial results
before exiting.  Output files may be incomplete but will not be corrupted mid-write.

---

## Running Tests

```powershell
cd C:\Users\UTD\Desktop\depi
py -3 -m pytest tests/ -v
# or without pytest:
py -3 -m unittest tests.test_recon -v
```

All tests mock external binaries — no network access is required.

---

## Security Notes

- All external commands are executed via **argument arrays** (`subprocess.run(args)`),
  never via `shell=True`. User input is never interpolated into shell strings.
- The tool restricts scope to the explicitly provided domain and its subdomains.
- Redirect destinations outside the target domain are recorded but not followed
  for further probing.
- Conservative rate limits are applied by default to avoid service disruption.
- Output paths are validated to prevent path-traversal issues.
