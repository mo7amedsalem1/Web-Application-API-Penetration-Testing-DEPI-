"""
tests/test_recon.py — Unit tests for the web recon pipeline.

All external tools are mocked; no real network requests are made.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import ReconConfig, DEFAULT_STATUS_CODES
from modules.http_probe import (
    HttpResult,
    filter_by_status,
    _parse_httpx_stdout,
    _load_httpx_jsonl,
)
from modules.subdomains import _parse_subs_from_text, _load_subs
from modules.parameter_extraction import (
    extract_parameters,
    _parse_url_params,
    get_parameter_summary,
)
from modules.endpoint_discovery import target_to_filename, _parse_ffuf_json
from modules.reporting import aggregate_all_urls, _get_params_for_url
from utils.validation import (
    validate_domain,
    validate_status_codes,
    validate_wordlist,
    check_required_tools,
    check_optional_tools,
)
from utils.command_runner import DependencyError, find_binary, run_command


# ============================================================================
# Helpers
# ============================================================================

def make_cfg(tmp_path: Path, **kwargs) -> ReconConfig:
    return ReconConfig(
        domain=kwargs.get("domain", "example.com"),
        output_dir=tmp_path,
        **{k: v for k, v in kwargs.items() if k != "domain"},
    )


# ============================================================================
# 1. Input validation
# ============================================================================

class TestValidateDomain(unittest.TestCase):
    def test_valid_domain(self):
        self.assertEqual(validate_domain("Example.COM"), "example.com")

    def test_valid_subdomain(self):
        self.assertEqual(validate_domain("api.example.com"), "api.example.com")

    def test_invalid_domain_no_tld(self):
        with self.assertRaises(ValueError):
            validate_domain("localhost")

    def test_invalid_domain_spaces(self):
        with self.assertRaises(ValueError):
            validate_domain("bad domain.com")

    def test_invalid_domain_empty(self):
        with self.assertRaises(ValueError):
            validate_domain("")

    def test_invalid_domain_ip(self):
        with self.assertRaises(ValueError):
            validate_domain("192.168.1.1")


class TestValidateStatusCodes(unittest.TestCase):
    def test_single_code(self):
        self.assertEqual(validate_status_codes("200"), [200])

    def test_multiple_codes(self):
        self.assertEqual(validate_status_codes("200,403,301"), [200, 301, 403])

    def test_deduplication(self):
        self.assertEqual(validate_status_codes("200,200"), [200])

    def test_invalid_code_text(self):
        with self.assertRaises(ValueError):
            validate_status_codes("ok")

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            validate_status_codes("99")

    def test_empty_string(self):
        with self.assertRaises(ValueError):
            validate_status_codes("")


# ============================================================================
# 2. Subdomain parsing and deduplication
# ============================================================================

class TestSubdomainParsing(unittest.TestCase):
    def test_basic_parsing(self):
        text = "api.example.com\nadmin.example.com\napi.example.com\n"
        result = _parse_subs_from_text(text, "example.com")
        self.assertEqual(result, ["admin.example.com", "api.example.com"])

    def test_filters_out_of_scope(self):
        text = "api.example.com\nmalicious.evil.com\n"
        result = _parse_subs_from_text(text, "example.com")
        self.assertNotIn("malicious.evil.com", result)
        self.assertIn("api.example.com", result)

    def test_filters_malformed(self):
        text = "api.example.com\n\nnot_a_hostname!!\n"
        result = _parse_subs_from_text(text, "example.com")
        self.assertIn("api.example.com", result)
        self.assertNotIn("not_a_hostname!!", result)

    def test_case_normalisation(self):
        text = "API.EXAMPLE.COM\n"
        result = _parse_subs_from_text(text, "example.com")
        self.assertIn("api.example.com", result)

    def test_empty_input(self):
        result = _parse_subs_from_text("", "example.com")
        self.assertEqual(result, [])


class TestLoadSubsFromFile(unittest.TestCase):
    def test_load_and_deduplicate(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("api.example.com\nadmin.example.com\napi.example.com\n")
            path = Path(f.name)
        try:
            result = _load_subs(path)
            self.assertEqual(result, ["admin.example.com", "api.example.com"])
        finally:
            path.unlink()


# ============================================================================
# 3. HTTP status code parsing and filtering
# ============================================================================

class TestHttpResultParsing(unittest.TestCase):
    def test_parse_valid_json_line(self):
        data = {
            "url": "https://api.example.com",
            "status_code": 200,
            "title": "API Home",
            "webserver": "nginx",
            "content_length": 1024,
        }
        r = HttpResult.from_httpx_json(data)
        self.assertEqual(r.url, "https://api.example.com")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.title, "API Home")
        self.assertEqual(r.webserver, "nginx")
        self.assertEqual(r.content_length, 1024)

    def test_parse_missing_optional_fields(self):
        data = {"url": "https://example.com", "status_code": 301}
        r = HttpResult.from_httpx_json(data)
        self.assertIsNone(r.title)
        self.assertIsNone(r.webserver)

    def test_parse_malformed_status_code(self):
        data = {"url": "https://example.com", "status_code": None}
        r = HttpResult.from_httpx_json(data)
        self.assertEqual(r.status_code, 0)

    def test_parse_httpx_stdout(self):
        lines = [
            json.dumps({"url": "https://api.example.com", "status_code": 200}),
            "not json",
            json.dumps({"url": "https://admin.example.com", "status_code": 403}),
        ]
        results = _parse_httpx_stdout("\n".join(lines))
        self.assertEqual(len(results), 2)

    def test_parse_httpx_jsonl_skips_malformed(self):
        """Malformed JSON lines should be skipped without crashing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"url": "https://a.example.com", "status_code": 200}) + "\n")
            f.write("THIS IS NOT JSON\n")
            f.write(json.dumps({"url": "https://b.example.com", "status_code": 404}) + "\n")
            path = Path(f.name)
        try:
            results = _load_httpx_jsonl(path)
            self.assertEqual(len(results), 2)
            urls = [r.url for r in results]
            self.assertIn("https://a.example.com", urls)
            self.assertIn("https://b.example.com", urls)
        finally:
            path.unlink()


class TestFilterByStatus(unittest.TestCase):
    def setUp(self):
        self.results = [
            HttpResult("https://a.example.com", 200),
            HttpResult("https://b.example.com", 301),
            HttpResult("https://c.example.com", 403),
            HttpResult("https://d.example.com", 200),
        ]

    def test_filter_200_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            filtered = filter_by_status(self.results, [200], cfg)
            urls = [r.url for r in filtered]
            self.assertEqual(sorted(urls), [
                "https://a.example.com", "https://d.example.com"
            ])

    def test_filter_multiple_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            filtered = filter_by_status(self.results, [200, 403], cfg)
            self.assertEqual(len(filtered), 3)

    def test_filter_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            filtered = filter_by_status(self.results, [500], cfg)
            self.assertEqual(filtered, [])

    def test_filter_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            filter_by_status(self.results, [200], cfg)
            self.assertTrue(cfg.status200_file.exists())
            content = cfg.status200_file.read_text()
            self.assertIn("https://a.example.com", content)
            self.assertNotIn("https://b.example.com", content)

    def test_status_code_not_inferred_from_url(self):
        """Status code must come from actual probe data, never the URL."""
        results = [HttpResult("https://200ok.example.com", 403)]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            filtered = filter_by_status(results, [200], cfg)
            self.assertEqual(filtered, [])


# ============================================================================
# 4. Query parameter extraction
# ============================================================================

class TestParameterExtraction(unittest.TestCase):
    def test_basic_extraction(self):
        params = _parse_url_params("https://api.example.com/search?q=test&page=1")
        self.assertIn("q", params)
        self.assertIn("page", params)
        self.assertEqual(params["q"], ["test"])

    def test_no_params(self):
        params = _parse_url_params("https://api.example.com/v1/users")
        self.assertEqual(params, {})

    def test_empty_value_preserved(self):
        params = _parse_url_params("https://api.example.com/search?q=")
        self.assertIn("q", params)

    def test_multiple_values(self):
        params = _parse_url_params("https://api.example.com/?ids=1&ids=2")
        self.assertEqual(params["ids"], ["1", "2"])

    def test_malformed_url(self):
        # Should not raise
        params = _parse_url_params("not a url at all %%")
        self.assertIsInstance(params, dict)

    def test_extract_parameters_groups_by_target(self):
        url_groups = {
            "https://api.example.com": [
                "https://api.example.com/search?q=hello",
                "https://api.example.com/v1/users",
            ],
        }
        result = extract_parameters(url_groups)
        self.assertIn("https://api.example.com", result)
        # Only the URL with params should be in the result
        target_data = result["https://api.example.com"]
        self.assertIn("https://api.example.com/search?q=hello", target_data)
        self.assertNotIn("https://api.example.com/v1/users", target_data)

    def test_no_parameters_invented(self):
        """URLs without parameters must not appear in the parameter map."""
        url_groups = {
            "https://api.example.com": [
                "https://api.example.com/dashboard",
                "https://api.example.com/login",
            ],
        }
        result = extract_parameters(url_groups)
        # result should be empty (no URL has params)
        self.assertEqual(result, {})

    def test_parameter_summary(self):
        param_data = {
            "https://api.example.com": {
                "https://api.example.com/search?q=test&page=1": {
                    "q": ["test"],
                    "page": ["1"],
                }
            }
        }
        summary = get_parameter_summary(param_data)
        self.assertIn("https://api.example.com", summary)
        self.assertIn("q", summary["https://api.example.com"])
        self.assertIn("page", summary["https://api.example.com"])


# ============================================================================
# 5. URL grouping by hostname
# ============================================================================

class TestUrlGrouping(unittest.TestCase):
    def test_groups_by_hostname(self):
        from modules.endpoint_discovery import FfufResult
        ffuf_results = {
            "https://api.example.com": [
                FfufResult("https://api.example.com/v1", 200),
                FfufResult("https://api.example.com/v2", 200),
            ]
        }
        http_results = [
            HttpResult("https://api.example.com", 200),
            HttpResult("https://admin.example.com", 200),
        ]
        grouped = aggregate_all_urls(ffuf_results, {}, http_results)
        self.assertIn("api.example.com", grouped)
        self.assertIn("admin.example.com", grouped)

    def test_no_cross_host_attribution(self):
        """URLs from one host must not appear under a different host."""
        from modules.endpoint_discovery import FfufResult
        http_results = [
            HttpResult("https://api.example.com", 200),
            HttpResult("https://admin.example.com", 200),
        ]
        ffuf_results = {
            "https://api.example.com": [
                FfufResult("https://api.example.com/search", 200),
            ]
        }
        grouped = aggregate_all_urls(ffuf_results, {}, http_results)
        api_urls = [e["url"] for e in grouped.get("api.example.com", [])]
        admin_urls = [e["url"] for e in grouped.get("admin.example.com", [])]
        self.assertIn("https://api.example.com/search", api_urls)
        self.assertNotIn("https://api.example.com/search", admin_urls)

    def test_deduplication_within_group(self):
        from modules.endpoint_discovery import FfufResult
        http_results = [HttpResult("https://api.example.com", 200)]
        ffuf_results = {
            "https://api.example.com": [
                FfufResult("https://api.example.com/v1", 200),
                FfufResult("https://api.example.com/v1", 200),  # duplicate
            ]
        }
        grouped = aggregate_all_urls(ffuf_results, {}, http_results)
        api_urls = [e["url"] for e in grouped["api.example.com"]]
        self.assertEqual(api_urls.count("https://api.example.com/v1"), 1)


# ============================================================================
# 6. FFUF JSON parsing
# ============================================================================

class TestFfufParsing(unittest.TestCase):
    def test_parse_valid_ffuf_json(self):
        data = {
            "results": [
                {
                    "url": "https://api.example.com/admin",
                    "status": 200,
                    "length": 512,
                    "words": 30,
                    "lines": 10,
                }
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            results = _parse_ffuf_json(path, "https://api.example.com")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].url, "https://api.example.com/admin")
            self.assertEqual(results[0].status_code, 200)
        finally:
            path.unlink()

    def test_parse_empty_results(self):
        data = {"results": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            results = _parse_ffuf_json(path, "https://api.example.com")
            self.assertEqual(results, [])
        finally:
            path.unlink()

    def test_parse_malformed_ffuf_json(self):
        """Malformed ffuf JSON should return empty list without crashing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("THIS IS NOT JSON {{{")
            path = Path(f.name)
        try:
            results = _parse_ffuf_json(path, "https://api.example.com")
            self.assertEqual(results, [])
        finally:
            path.unlink()

    def test_ffuf_deduplication(self):
        data = {
            "results": [
                {"url": "https://api.example.com/admin", "status": 200},
                {"url": "https://api.example.com/admin", "status": 200},  # dup
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            results = _parse_ffuf_json(path, "https://api.example.com")
            urls = [r.url for r in results]
            self.assertEqual(urls.count("https://api.example.com/admin"), 1)
        finally:
            path.unlink()

    def test_target_to_filename(self):
        name = target_to_filename("https://api.example.com/v1")
        self.assertTrue(name.endswith(".json"))
        self.assertNotIn("/", name)
        self.assertNotIn(":", name.replace(".json", "").replace("https_", ""))


# ============================================================================
# 7. Missing dependency handling
# ============================================================================

class TestDependencyChecks(unittest.TestCase):
    @patch("utils.validation.find_binary", return_value=None)
    def test_missing_required_tool_raises(self, mock_find):
        with self.assertRaises(DependencyError):
            check_required_tools(["subfinder"])

    @patch("utils.validation.find_binary", return_value="/usr/bin/subfinder")
    def test_present_tool_does_not_raise(self, mock_find):
        result = check_required_tools(["subfinder"])
        self.assertEqual(result["subfinder"], "/usr/bin/subfinder")

    @patch("utils.validation.find_binary", return_value=None)
    def test_missing_optional_tool_does_not_raise(self, mock_find):
        result = check_optional_tools(["katana", "gau"])
        self.assertIsNone(result["katana"])
        self.assertIsNone(result["gau"])

    @patch("utils.command_runner.find_binary", return_value=None)
    def test_run_command_raises_dependency_error(self, mock_find):
        from utils.command_runner import run_command
        with self.assertRaises(DependencyError):
            run_command(["subfinder", "-d", "example.com"])


# ============================================================================
# 8. Subprocess failure handling
# ============================================================================

class TestSubprocessFailure(unittest.TestCase):
    @patch("utils.command_runner.find_binary", return_value="/usr/bin/subfinder")
    @patch("subprocess.run")
    def test_non_zero_exit_no_check(self, mock_run, mock_find):
        """Without check=True, non-zero exit should not raise."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = b""
        mock_proc.stderr = b"error"
        mock_run.return_value = mock_proc
        from utils.command_runner import run_command
        result = run_command(["subfinder", "-d", "example.com"], check=False)
        self.assertEqual(result.returncode, 1)

    @patch("utils.command_runner.find_binary", return_value="/usr/bin/subfinder")
    @patch("subprocess.run")
    def test_non_zero_exit_with_check_raises(self, mock_run, mock_find):
        """With check=True, non-zero exit should raise CommandError."""
        from utils.command_runner import CommandError, run_command
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = b""
        mock_proc.stderr = b"fatal error"
        mock_run.return_value = mock_proc
        with self.assertRaises(CommandError):
            run_command(["subfinder", "-d", "example.com"], check=True)


# ============================================================================
# 9. Resume behaviour
# ============================================================================

class TestResumeBehaviour(unittest.TestCase):
    def test_skip_subfinder_reuses_file(self):
        """When skip_subfinder=True and file exists, subfinder is not called."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), skip_subfinder=True)
            cfg.create_dirs()
            cfg.all_subs_file.write_text("api.example.com\nadmin.example.com\n")

            with patch("modules.subdomains.require_binary") as mock_bin:
                with patch("modules.subdomains.run_command") as mock_run:
                    from modules.subdomains import enumerate_subdomains
                    result = enumerate_subdomains(cfg)
                    mock_bin.assert_not_called()
                    mock_run.assert_not_called()
                    self.assertIn("api.example.com", result)

    def test_skip_httpx_reuses_file(self):
        """When skip_httpx=True and jsonl exists, httpx is not called."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), skip_httpx=True)
            cfg.create_dirs()
            line = json.dumps({"url": "https://api.example.com", "status_code": 200})
            cfg.httpx_jsonl_file.write_text(line + "\n")

            with patch("modules.http_probe.require_binary") as mock_bin:
                with patch("modules.http_probe.run_command") as mock_run:
                    from modules.http_probe import probe_subdomains
                    results = probe_subdomains(["api.example.com"], cfg)
                    mock_bin.assert_not_called()
                    mock_run.assert_not_called()
                    self.assertEqual(len(results), 1)
                    self.assertEqual(results[0].url, "https://api.example.com")


# ============================================================================
# 10. Sample report from mocked data
# ============================================================================

class TestSampleReport(unittest.TestCase):
    def test_generates_txt_report(self):
        """Generate a full text report from mocked pipeline data."""
        from modules.reporting import (
            aggregate_all_urls,
            write_final_report_txt,
        )
        from modules.endpoint_discovery import FfufResult

        http_results = [
            HttpResult("https://api.example.com", 200, title="API", webserver="nginx"),
            HttpResult("https://admin.example.com", 200, title="Admin"),
        ]
        ffuf_results = {
            "https://api.example.com": [
                FfufResult("https://api.example.com/v1/users", 200),
                FfufResult("https://api.example.com/v1/products", 200),
            ],
            "https://admin.example.com": [
                FfufResult("https://admin.example.com/login", 200),
                FfufResult("https://admin.example.com/dashboard", 200),
            ],
        }
        crawl_results = {
            "https://api.example.com": [
                "https://api.example.com/search?q=test",
            ]
        }
        param_data = {
            "https://api.example.com": {
                "https://api.example.com/search?q=test": {"q": ["test"]}
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            cfg.create_dirs()

            grouped = aggregate_all_urls(ffuf_results, crawl_results, http_results)
            write_final_report_txt("example.com", grouped, param_data, http_results, cfg)

            report = cfg.final_report_txt.read_text()

            # Assertions on report content
            self.assertIn("Domain: example.com", report)
            self.assertIn("api.example.com", report)
            self.assertIn("admin.example.com", report)
            self.assertIn("https://api.example.com/search?q=test", report)
            self.assertIn("Parameters: q", report)
            self.assertIn("https://admin.example.com/login", report)
            # Make sure parameters are not invented for non-param URLs
            self.assertNotIn("dashboard\n    Parameters:", report)


# ============================================================================
# Run
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
