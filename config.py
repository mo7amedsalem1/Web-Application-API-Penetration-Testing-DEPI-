"""
config.py — Central configuration and defaults for the recon pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Tool names (resolved at runtime by validation.py)
# ---------------------------------------------------------------------------
REQUIRED_TOOLS: List[str] = ["subfinder", "httpx", "ffuf"]
OPTIONAL_TOOLS: List[str] = ["katana", "gau"]

# ---------------------------------------------------------------------------
# Default CLI values
# ---------------------------------------------------------------------------
DEFAULT_OUTPUT_DIR: str = "output"
DEFAULT_STATUS_CODES: List[int] = [200]
DEFAULT_THREADS: int = 10
DEFAULT_RATE: int = 150          # requests/second (conservative)
DEFAULT_TIMEOUT: int = 10        # seconds
DEFAULT_FFUF_THREADS: int = 40
DEFAULT_FFUF_RATE: int = 0       # 0 = unlimited (ffuf default)
DEFAULT_FFUF_TIMEOUT: int = 10
DEFAULT_FFUF_FC: str = "404"     # filter these status codes
DEFAULT_FFUF_MC: str = "all"     # match-codes (all, then fc removes 404)
DEFAULT_MAX_FFUF_PROCS: int = 3  # max concurrent ffuf processes


@dataclass
class ReconConfig:
    """Runtime configuration assembled from CLI args."""

    domain: str
    output_dir: Path
    wordlist: Optional[Path] = None
    status_codes: List[int] = field(default_factory=lambda: list(DEFAULT_STATUS_CODES))
    threads: int = DEFAULT_THREADS
    rate: int = DEFAULT_RATE
    timeout: int = DEFAULT_TIMEOUT
    ffuf_threads: int = DEFAULT_FFUF_THREADS
    ffuf_rate: int = DEFAULT_FFUF_RATE
    ffuf_timeout: int = DEFAULT_FFUF_TIMEOUT
    max_ffuf_procs: int = DEFAULT_MAX_FFUF_PROCS

    crawl: bool = False
    collect_urls: bool = False

    skip_subfinder: bool = False
    skip_httpx: bool = False
    skip_ffuf: bool = False
    resume: bool = False

    # Paths (set in __post_init__)
    all_subs_file: Path = field(init=False)
    alive_subs_file: Path = field(init=False)
    status200_file: Path = field(init=False)
    httpx_jsonl_file: Path = field(init=False)
    ffuf_dir: Path = field(init=False)
    all_urls_file: Path = field(init=False)
    parameters_file: Path = field(init=False)
    final_report_txt: Path = field(init=False)
    final_report_json: Path = field(init=False)

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        self.all_subs_file = self.output_dir / "AllSubs.txt"
        self.alive_subs_file = self.output_dir / "AliveSubs.txt"
        self.status200_file = self.output_dir / "Status200.txt"
        self.httpx_jsonl_file = self.output_dir / "httpx_results.jsonl"
        self.ffuf_dir = self.output_dir / "ffuf"
        self.all_urls_file = self.output_dir / "all_urls.txt"
        self.parameters_file = self.output_dir / "parameters.json"
        self.final_report_txt = self.output_dir / "final_report.txt"
        self.final_report_json = self.output_dir / "final_report.json"

    def create_dirs(self) -> None:
        """Create all required output directories."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ffuf_dir.mkdir(parents=True, exist_ok=True)
