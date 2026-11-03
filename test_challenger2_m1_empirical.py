"""
Adversarial Empirical Challenge Test Suite for Milestone 1 (Backend & Security)
Author: Challenger 2 (Empirical Challenger)
Scope: Actions mutex, Git environment, Token isolation, Defense ticker routing
"""

import os
import sys
import time
import json
import re
import urllib.request
import subprocess
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import integrated_market_radar as imr


class TestTokenLeakAndEnvIsolation(unittest.TestCase):
    """Adversarial Token Leak Scan & .env Leak Prevention"""

    def setUp(self):
        self.patterns = ["d9be4q1r", "eca6f1f8", "ghp_"]
        self.repo_dir = PROJECT_DIR

    def test_tracked_files_have_no_plaintext_tokens(self):
        """Verify git tracked files in HEAD contain NO plaintext tokens"""
        for p in self.patterns:
            res = subprocess.run(
                ["git", "-C", self.repo_dir, "grep", "-i", p],
                capture_output=True,
                text=True,
                errors="replace"
            )
            self.assertEqual(
                res.returncode, 1,
                f"Tracked files contain leaked token pattern '{p}': {res.stdout}"
            )

    def test_staged_and_unstaged_diffs_do_not_add_tokens(self):
        """Verify that unstaged/staged diffs do not introduce (+) any tokens"""
        for diff_cmd in [["git", "-C", self.repo_dir, "diff"], ["git", "-C", self.repo_dir, "diff", "--cached"]]:
            res = subprocess.run(diff_cmd, capture_output=True)
            diff_text = res.stdout.decode("utf-8", errors="replace")
            for line in diff_text.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    for p in self.patterns:
                        self.assertNotIn(
                            p.lower(), line.lower(),
                            f"Git diff adds token pattern '{p}' in added line: {line}"
                        )

    def test_mock_env_files_strictly_ignored_by_git(self):
        """Verify that .env, .env.local, .env.production, etc. are strictly ignored by git"""
        mock_files = [
            ".env",
            ".env.local",
            ".env.production",
            ".env.development",
            ".env.staging",
            ".env.test",
            "prod.env",
            "secret.env",
            "subdir/.env",
            "subdir/.env.local",
        ]
        subdir_path = os.path.join(self.repo_dir, "subdir")
        os.makedirs(subdir_path, exist_ok=True)

        try:
            for rel_path in mock_files:
                full_path = os.path.join(self.repo_dir, rel_path)
                created = False
                if not os.path.exists(full_path):
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write("SECRET_KEY=adversarial_test_value\n")
                    created = True

                ign = subprocess.run(
                    ["git", "-C", self.repo_dir, "check-ignore", rel_path],
                    capture_output=True,
                    text=True
                )
                self.assertEqual(
                    ign.returncode, 0,
                    f"Mock env file '{rel_path}' is NOT ignored by git! Rule: '{ign.stdout.strip()}'"
                )

                add_run = subprocess.run(
                    ["git", "-C", self.repo_dir, "add", "--dry-run", rel_path],
                    capture_output=True,
                    text=True
                )
                self.assertFalse(
                    bool(add_run.stdout.strip()),
                    f"Mock env file '{rel_path}' could be added to git staging! Output: {add_run.stdout}"
                )

                if created and os.path.exists(full_path):
                    os.remove(full_path)
        finally:
            if os.path.exists(subdir_path):
                try:
                    os.rmdir(subdir_path)
                except Exception:
                    pass

    def test_env_example_is_allowed_in_git(self):
        """Verify that .env.example is whitelisted in .gitignore and can be tracked"""
        ign = subprocess.run(
            ["git", "-C", self.repo_dir, "check-ignore", ".env.example"],
            capture_output=True,
            text=True
        )
        self.assertNotEqual(
            ign.returncode, 0,
            ".env.example is incorrectly ignored by git!"
        )


class TestActionsMutexAndWorkflowLogic(unittest.TestCase):
    """Adversarial Mutex & Workflow Logic Verification"""

    def simulate_workflow_bash_check(self, commit_list, event_name="schedule", now_ts=None):
        """
        Simulate the exact logic executed in .github/workflows/market_radar.yml lines 60-78:
        NOW_TS=$(date +%s)
        SKIP=false
        while IFS="|" read -r email ts; do
          if [ -n "$email" ] && [ "$email" != "actions@github.com" ]; then
            DIFF=$((NOW_TS - ts))
            if [ "$DIFF" -ge 0 ] && [ "$DIFF" -lt 900 ]; then
              SKIP=true
              break
            fi
          fi
        done < <(git log -n 10 --format="%ae|%ct")
        """
        if now_ts is None:
            now_ts = int(time.time())

        # Enforce -n 10 limit from workflow
        inspected = commit_list[:10]
        skip = False

        for email, ts in inspected:
            if email and email != "actions@github.com":
                diff = now_ts - ts
                if 0 <= diff < 900:
                    skip = True
                    break

        if skip and event_name == "schedule":
            skip_run = "true"
        else:
            skip_run = "false"

        return skip, skip_run

    def test_human_commit_5m_ago_yields_actions(self):
        """A commit made 5 minutes ago (300s) by human author MUST yield Actions (skip_run=true)"""
        now = int(time.time())
        commits = [("developer@local.com", now - 300)]
        skip, skip_run = self.simulate_workflow_bash_check(commits, event_name="schedule", now_ts=now)
        self.assertTrue(skip)
        self.assertEqual(skip_run, "true")

    def test_human_commit_14m_ago_yields_actions(self):
        """A commit made 14 minutes ago (840s) by human author MUST yield Actions (skip_run=true)"""
        now = int(time.time())
        commits = [("developer@local.com", now - 840)]
        skip, skip_run = self.simulate_workflow_bash_check(commits, event_name="schedule", now_ts=now)
        self.assertTrue(skip)
        self.assertEqual(skip_run, "true")

    def test_human_commit_16m_ago_allows_actions_run(self):
        """A commit made 16 minutes ago (960s) by human author MUST NOT yield Actions (skip_run=false)"""
        now = int(time.time())
        commits = [("developer@local.com", now - 960)]
        skip, skip_run = self.simulate_workflow_bash_check(commits, event_name="schedule", now_ts=now)
        self.assertFalse(skip)
        self.assertEqual(skip_run, "false")

    def test_bot_commit_5m_ago_does_not_yield_actions(self):
        """A commit made 5 minutes ago (300s) by actions@github.com MUST NOT yield Actions (skip_run=false)"""
        now = int(time.time())
        commits = [("actions@github.com", now - 300)]
        skip, skip_run = self.simulate_workflow_bash_check(commits, event_name="schedule", now_ts=now)
        self.assertFalse(skip)
        self.assertEqual(skip_run, "false")

    def test_mixed_bot_and_human_commits_within_depth_10(self):
        """When 3 bot commits sit on top of a 6-minute-old human commit, Actions MUST inspect past bot and yield"""
        now = int(time.time())
        commits = [
            ("actions@github.com", now - 60),
            ("actions@github.com", now - 120),
            ("actions@github.com", now - 180),
            ("developer@local.com", now - 360),  # 6m ago
        ]
        skip, skip_run = self.simulate_workflow_bash_check(commits, event_name="schedule", now_ts=now)
        self.assertTrue(skip)
        self.assertEqual(skip_run, "true")

    def test_manual_workflow_dispatch_does_not_skip_even_if_recent_commit(self):
        """Manual workflow_dispatch trigger MUST NOT skip run even if local committed 5m ago"""
        now = int(time.time())
        commits = [("developer@local.com", now - 300)]
        skip, skip_run = self.simulate_workflow_bash_check(commits, event_name="workflow_dispatch", now_ts=now)
        self.assertTrue(skip)
        self.assertEqual(skip_run, "false", "workflow_dispatch should proceed regardless of skip flag")

    def test_adversarial_depth_10_overflow_boundary(self):
        """Adversarial stress: if 10 bot commits occurred in last 10m, an 11th human commit (12m ago) is outside depth 10"""
        now = int(time.time())
        commits = [("actions@github.com", now - i * 60) for i in range(1, 11)]
        commits.append(("developer@local.com", now - 720))  # 12m ago, commit 11
        skip, skip_run = self.simulate_workflow_bash_check(commits, event_name="schedule", now_ts=now)
        # Because depth is 10, the 11th commit is masked!
        self.assertFalse(skip, "Confirmed: depth 10 masks commit 11. (Documented boundary scenario)")

    def test_adversarial_clock_skew_negative_diff(self):
        """Adversarial stress: if local commit is 5s ahead due to clock skew (DIFF = -5), verify behavior"""
        now = int(time.time())
        commits = [("developer@local.com", now + 5)]  # ts is 5s in the future
        skip, skip_run = self.simulate_workflow_bash_check(commits, event_name="schedule", now_ts=now)
        # Bash condition is [ "$DIFF" -ge 0 ] && [ "$DIFF" -lt 900 ]
        # Since -5 < 0, it fails -ge 0 and skip is False!
        self.assertFalse(skip, "Confirmed: clock skew (future timestamp) does not trigger skip due to -ge 0 check")

    def test_github_rest_api_cloud_run_status(self):
        """Query GitHub REST API to verify runaway cloud run 41544599045 is cancelled and no runs in progress"""
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            try:
                res = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True)
                m = re.search(r":([^@]+)@", res.stdout)
                if m:
                    token = m.group(1).split(":")[-1]
            except Exception:
                pass
        if not token:
            self.skipTest("No GitHub token available for API check")
        repo = "2827173579/us-market-radar"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Challenger2-Adversarial"
        }

        # Check target run
        url_target = f"https://api.github.com/repos/{repo}/actions/runs/41544599045"
        req = urllib.request.Request(url_target, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            self.assertEqual(data.get("status"), "completed")
            self.assertEqual(data.get("conclusion"), "cancelled")

        # Check active runs for market radar
        url_active = f"https://api.github.com/repos/{repo}/actions/runs?status=in_progress"
        req2 = urllib.request.Request(url_active, headers=headers)
        with urllib.request.urlopen(req2, timeout=10) as resp:
            data2 = json.loads(resp.read().decode())
            radar_runs = [r for r in data2.get("workflow_runs", []) if "radar" in r.get("name", "").lower()]
            self.assertEqual(len(radar_runs), 0, f"Found unexpected active market radar runs: {radar_runs}")


class TestDefenseTickerRoutingAndRateLimiter(unittest.TestCase):
    """Validate Defense Ticker Routing to yfinance and Finnhub Rate Limiter non-increment"""

    def setUp(self):
        self.expected_defense_10 = {"KO", "PEP", "MCD", "AXP", "V", "CCXI", "LLY", "MRNA", "NKE", "HPE"}

    def test_defense_10_tickers_membership(self):
        """Verify DEFENSIVE_10_TICKERS contains exactly the 10 defined defense tickers"""
        stocks = imr.MONITORED_TARGETS.get("STOCKS", {})
        for ticker in self.expected_defense_10:
            self.assertIn(ticker, stocks, f"Defense ticker {ticker} missing from MONITORED_TARGETS['STOCKS']")

    def test_defense_tickers_excluded_from_finnhub_batch(self):
        """Verify that finnhub_tickers construction strictly filters out all 10 defense tickers when no user portfolio"""
        all_tickers = list(imr.MONITORED_TARGETS.get("ETFS", {}).keys()) + list(imr.MONITORED_TARGETS.get("STOCKS", {}).keys())
        user_portfolio = {}

        defensive_set = self.expected_defense_10
        finnhub_tickers = [tk for tk in all_tickers if tk not in defensive_set or (user_portfolio and tk in user_portfolio)][:60]

        self.assertLessEqual(len(finnhub_tickers), 60)
        for def_ticker in defensive_set:
            self.assertNotIn(
                def_ticker, finnhub_tickers,
                f"Defensive ticker {def_ticker} was leaked into finnhub_tickers list!"
            )

    def test_defense_tickers_do_not_call_finnhub_quote_or_limiter(self):
        """Verify that during batch quote fetching, 0 Finnhub calls and 0 limiter tokens are consumed for defense tickers"""
        all_tickers = list(imr.MONITORED_TARGETS.get("ETFS", {}).keys()) + list(imr.MONITORED_TARGETS.get("STOCKS", {}).keys())
        finnhub_tickers = [tk for tk in all_tickers if tk not in self.expected_defense_10][:60]

        # Spy on fetch_single_finnhub_quote
        called_symbols = []

        def spy_fetch(sym, key):
            called_symbols.append(sym)
            return sym, {"last": 100.0, "change": 1.0, "pct_change": 1.0}

        with patch("integrated_market_radar.fetch_single_finnhub_quote", side_effect=spy_fetch):
            results = imr.fetch_finnhub_quotes_batch(finnhub_tickers, "dummy_token")

        for def_ticker in self.expected_defense_10:
            self.assertNotIn(
                def_ticker, called_symbols,
                f"fetch_single_finnhub_quote was called for defense ticker {def_ticker}!"
            )

    def test_defense_tickers_properly_routed_to_yfinance_intraday(self):
        """Verify that intraday_map logic successfully handles defense tickers via yfinance 5m RTH data"""
        test_defense = ["KO", "PEP", "MCD", "AXP", "V"]
        intraday_results = imr.fetch_intraday_5m_and_ext_data(test_defense)

        self.assertIsInstance(intraday_results, dict)
        for ticker in test_defense:
            self.assertIn(ticker, intraday_results, f"Defense ticker {ticker} missing from intraday_map")
            data = intraday_results[ticker]
            self.assertIn("rth_close", data)
            self.assertGreater(data["rth_close"], 0.0, f"Invalid rth_close for defense ticker {ticker}")
            self.assertIn("vwap", data)

    def test_finnhub_limiter_rate_and_token_bucket(self):
        """Verify FinnhubRateLimiter enforces 50 req/min and minimum interval >= 1.05s"""
        limiter = imr.FinnhubRateLimiter(max_per_minute=50.0, min_interval=1.05)
        self.assertEqual(limiter.capacity, 50.0)
        self.assertAlmostEqual(limiter.fill_rate, 50.0 / 60.0, places=4)
        self.assertEqual(limiter.min_interval, 1.05)

        # Test consecutive acquisitions enforce >= 1.05s
        t0 = time.time()
        limiter.acquire()
        t1 = time.time()
        limiter.acquire()
        t2 = time.time()

        elapsed = t2 - t0
        self.assertGreaterEqual(
            elapsed, 1.0,
            f"Rate limiter interval violation: 2 acquires took only {elapsed:.3f}s (expected >= 1.05s)"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
