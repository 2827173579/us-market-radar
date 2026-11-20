# -*- coding: utf-8 -*-
"""Offline checks for fork-safe cloud sync. No network and no real credentials."""

import os
import unittest
from unittest import mock

import radar_cloud_sync as sync

FORK = "yunnnnn1/us-market-radar"
UPSTREAM = "2827173579/us-market-radar"
SHA_INHERITED = "f3c07922c37cef735b8b352f882a1d357e02ce15"
SHA_LOCAL = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NOW = 1_700_000_000


class SelectLocalClientTests(unittest.TestCase):
    def test_inherited_upstream_commit_does_not_yield_on_a_fork(self):
        # Morning run: tip authored by the upstream daemon 528s earlier, but the
        # SHA already exists on the parent. That is the fork snapshot, not a
        # local client on this repo.
        hit = sync.select_local_client_commit(
            [("a18669358277@gmail.com", NOW - 528, SHA_INHERITED)],
            NOW,
            "fork",
            UPSTREAM,
            lambda parent, sha: sha == SHA_INHERITED,
        )
        self.assertIsNone(hit)

    def test_upstream_commit_pulled_into_this_repo_does_not_yield(self):
        hit = sync.select_local_client_commit(
            [("a18669358277@gmail.com", NOW - 45, SHA_INHERITED)],
            NOW,
            "fork",
            UPSTREAM,
            lambda parent, sha: True,
        )
        self.assertIsNone(hit)

    def test_real_local_commit_on_the_fork_yields(self):
        hit = sync.select_local_client_commit(
            [("owner@example.com", NOW - 100, SHA_LOCAL)],
            NOW,
            "fork",
            UPSTREAM,
            lambda parent, sha: False,
        )
        self.assertEqual(hit["email"], "owner@example.com")
        self.assertEqual(hit["age_sec"], 100)

    def test_actions_commit_never_yields(self):
        hit = sync.select_local_client_commit(
            [("actions@github.com", NOW - 10, SHA_LOCAL)],
            NOW,
            "fork",
            UPSTREAM,
            lambda parent, sha: False,
        )
        self.assertIsNone(hit)

    def test_non_fork_still_yields_to_its_own_recent_commit(self):
        hit = sync.select_local_client_commit(
            [("a18669358277@gmail.com", NOW - 30, SHA_LOCAL)],
            NOW,
            "repo",
            "",
            lambda parent, sha: True,
        )
        self.assertIsNotNone(hit)

    def test_unresolved_fork_does_not_treat_history_as_local(self):
        hit = sync.select_local_client_commit(
            [("a18669358277@gmail.com", NOW - 30, SHA_INHERITED)],
            NOW,
            "fork-unresolved",
            "",
            lambda parent, sha: None,
        )
        self.assertIsNone(hit)

    def test_stale_commit_does_not_yield(self):
        hit = sync.select_local_client_commit(
            [("owner@example.com", NOW - 5000, SHA_LOCAL)],
            NOW,
            "repo",
            "",
            lambda parent, sha: False,
        )
        self.assertIsNone(hit)

    def test_actions_tip_does_not_hide_an_older_local_commit(self):
        hit = sync.select_local_client_commit(
            [
                ("actions@github.com", NOW - 20, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
                ("owner@example.com", NOW - 120, SHA_LOCAL),
            ],
            NOW,
            "fork",
            UPSTREAM,
            lambda parent, sha: False,
        )
        self.assertEqual(hit["sha"], SHA_LOCAL)


class OriginPlanTests(unittest.TestCase):
    def test_cloud_run_targets_this_repository_not_upstream(self):
        plan = sync.sync_origin_plan(True, FORK, UPSTREAM, True)
        self.assertEqual(plan["target_slug"], FORK)
        self.assertTrue(plan["rewrite"])
        self.assertFalse(plan["embed_token"])
        self.assertTrue(plan["allow_pull_push"])
        self.assertNotEqual(plan["target_slug"], UPSTREAM)

    def test_cloud_run_without_repository_refuses_pull_push(self):
        plan = sync.sync_origin_plan(True, "", UPSTREAM, True)
        self.assertEqual(plan["target_slug"], "")
        self.assertFalse(plan["allow_pull_push"])
        self.assertFalse(plan["embed_token"])

    def test_local_daemon_keeps_its_own_origin(self):
        plan = sync.sync_origin_plan(False, "", FORK, True)
        self.assertEqual(plan["target_slug"], FORK)
        self.assertFalse(plan["rewrite"])
        self.assertTrue(plan["embed_token"])

    def test_token_url_slug_drops_userinfo(self):
        url = "https://x-access-token:super-secret-value@github.com/yunnnnn1/us-market-radar.git"
        self.assertEqual(sync.origin_url_repo_slug(url), FORK)
        self.assertNotIn("super-secret-value", sync.origin_url_repo_slug(url))
        self.assertTrue(sync.origin_url_has_userinfo(url))

    def test_ssh_and_plain_https_slugs(self):
        self.assertEqual(
            sync.origin_url_repo_slug("git@github.com:yunnnnn1/us-market-radar.git"),
            FORK,
        )
        self.assertEqual(
            sync.origin_url_repo_slug("https://github.com/yunnnnn1/us-market-radar"),
            FORK,
        )


class RedactTests(unittest.TestCase):
    def test_redact_tokens_and_env_values(self):
        raw = "https://x-access-token:super-secret-value@github.com/yunnnnn1/us-market-radar.git"
        with mock.patch.dict(os.environ, {"FINNHUB_API_KEY": "finnhub-secret-value"}, clear=False):
            text = sync.redact_secrets(raw + " finnhub-secret-value ghp_" + ("a" * 36))
        self.assertNotIn("super-secret-value", text)
        self.assertNotIn("finnhub-secret-value", text)
        self.assertNotIn("ghp_" + ("a" * 36), text)
        self.assertIn("REDACTED", text)


class PagesGateTests(unittest.TestCase):
    def test_ignores_the_running_radar_job_and_waits_on_pages(self):
        runs = [
            {"id": 111, "name": "US Market Radar 24-7 Engine", "status": "in_progress"},
            {"id": 222, "name": "pages-build-deployment", "status": "in_progress", "path": "dynamic/pages/pages-build-deployment"},
        ]
        self.assertEqual(sync.pages_gate_decision(runs, "111"), "wait")

    def test_completed_pages_does_not_block(self):
        runs = [
            {"id": 222, "name": "pages-build-deployment", "status": "completed"},
        ]
        self.assertEqual(sync.pages_gate_decision(runs, "111"), "go")

    def test_non_pages_activity_does_not_block(self):
        runs = [
            {"id": 333, "name": "US Market Radar 24-7 Engine", "status": "in_progress"},
        ]
        self.assertEqual(sync.pages_gate_decision(runs, "999"), "go")


class ParentContextTests(unittest.TestCase):
    def test_explicit_parent_marks_a_fork(self):
        mode, parent = sync.parent_context_from_env(
            {"RADAR_FORK_PARENT": UPSTREAM, "GITHUB_REPOSITORY": FORK, "RADAR_REPO_IS_FORK": "true"},
            lambda: None,
        )
        self.assertEqual((mode, parent), ("fork", UPSTREAM))

    def test_not_a_fork_keeps_repo_mode(self):
        mode, parent = sync.parent_context_from_env(
            {"RADAR_REPO_IS_FORK": "false", "GITHUB_REPOSITORY": UPSTREAM, "GITHUB_ACTIONS": "true"},
            lambda: "should-not-be-called",
        )
        self.assertEqual((mode, parent), ("repo", ""))

    def test_fork_flag_without_parent_uses_lookup(self):
        mode, parent = sync.parent_context_from_env(
            {"RADAR_REPO_IS_FORK": "true", "GITHUB_REPOSITORY": FORK, "GITHUB_ACTIONS": "true"},
            lambda: UPSTREAM,
        )
        self.assertEqual((mode, parent), ("fork", UPSTREAM))

    def test_lookup_failure_does_not_become_a_local_client(self):
        mode, parent = sync.parent_context_from_env(
            {"RADAR_REPO_IS_FORK": "true", "GITHUB_REPOSITORY": FORK},
            lambda: None,
        )
        self.assertEqual((mode, parent), ("fork-unresolved", ""))


class WorkflowSourceTests(unittest.TestCase):
    def test_cloud_sync_code_is_not_hardcoded_to_upstream(self):
        root = os.path.dirname(os.path.abspath(__file__))
        for name in ("integrated_market_radar.py", ".github/workflows/market_radar.yml", "radar_cloud_sync.py"):
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("2827173579/us-market-radar.git", text, name)
            self.assertNotIn("repos/2827173579/us-market-radar/actions", text, name)

    def test_workflow_uses_optional_pat_or_github_token(self):
        root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, ".github/workflows/market_radar.yml"), encoding="utf-8") as handle:
            text = handle.read()
        with open(os.path.join(root, "requirements.txt"), encoding="utf-8") as handle:
            requirements = handle.read()
        self.assertIn("secrets.GITHUB_TOKEN", text)
        self.assertNotIn("secrets.GH_TOKEN", text)
        self.assertIn("pages: write", text)
        self.assertIn("radar_cloud_sync.py --check-local-client", text)
        self.assertIn("pip install -r requirements.txt", text)
        self.assertIn("pyarrow", requirements)


class GitOriginRewriteTests(unittest.TestCase):
    def test_actions_retargets_hardcoded_upstream_back_to_this_repo(self):
        import shutil
        import subprocess
        import tempfile

        tmp = tempfile.mkdtemp(prefix="radar-origin-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        subprocess.check_call(["git", "init", "-q"], cwd=tmp)
        subprocess.check_call(
            ["git", "remote", "add", "origin", f"https://x-access-token:local-test-token@github.com/{UPSTREAM}.git"],
            cwd=tmp,
        )
        old = os.getcwd()
        os.chdir(tmp)
        self.addCleanup(lambda: os.chdir(old))
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_REPOSITORY": FORK,
                "GH_TOKEN": "local-test-token",
                "GIT_TERMINAL_PROMPT": "0",
            },
            clear=False,
        ):
            allowed = sync.ensure_sync_origin()
            url = sync.read_origin_url()
        self.assertTrue(allowed)
        self.assertEqual(sync.origin_url_repo_slug(url), FORK)
        self.assertNotEqual(sync.origin_url_repo_slug(url), UPSTREAM)
        self.assertFalse(sync.origin_url_has_userinfo(url), "stored origin still has userinfo")


class PagesPublishTests(unittest.TestCase):
    def test_legacy_fork_uses_this_repo_pages_build_api(self):
        plan = sync.pages_publish_plan(True, FORK, True, "legacy")
        self.assertEqual(plan["method"], "branch-build")
        self.assertEqual(plan["repo"], FORK)
        self.assertEqual(plan["url"], f"https://api.github.com/repos/{FORK}/pages/builds")
        self.assertNotIn(UPSTREAM, plan["url"])

    def test_upstream_is_not_substituted_for_a_fork(self):
        plan = sync.pages_publish_plan(True, FORK, True, "unknown")
        self.assertEqual(plan["repo"], FORK)
        self.assertNotEqual(plan["repo"], UPSTREAM)

    def test_workflow_source_uses_artifact_deploy_not_a_pat_url(self):
        plan = sync.pages_publish_plan(True, FORK, True, "workflow")
        self.assertEqual(plan["method"], "artifact")
        self.assertEqual(plan["url"], "")

    def test_refuses_without_actions_or_token(self):
        self.assertEqual(sync.pages_publish_plan(False, FORK, True, "legacy")["method"], "none")
        self.assertEqual(sync.pages_publish_plan(True, FORK, False, "legacy")["method"], "none")
        self.assertEqual(sync.pages_publish_plan(True, "", True, "legacy")["method"], "none")

    def test_build_request_posts_to_this_repo_and_hides_the_token(self):
        seen = {}

        class _Resp:
            status = 201

            def read(self, _n=None):
                return b'{"status":"queued"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(req, timeout=10):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["auth_present"] = bool(req.get_header("Authorization"))
            return _Resp()

        result = sync.request_branch_pages_build(FORK, "local-test-token", opener=opener)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 201)
        self.assertEqual(seen["url"], f"https://api.github.com/repos/{FORK}/pages/builds")
        self.assertEqual(seen["method"], "POST")
        self.assertTrue(seen["auth_present"])
        self.assertNotIn("local-test-token", str(result))
        self.assertNotIn(UPSTREAM, seen["url"])

    def test_inherited_commit_still_does_not_block_a_fork_deploy(self):
        hit = sync.select_local_client_commit(
            [("a18669358277@gmail.com", NOW - 528, SHA_INHERITED)],
            NOW,
            "fork",
            UPSTREAM,
            lambda parent, sha: sha == SHA_INHERITED,
        )
        plan = sync.pages_publish_plan(True, FORK, True, "legacy")
        self.assertIsNone(hit)
        self.assertEqual(plan["method"], "branch-build")


class CheckCliTests(unittest.TestCase):
    def test_schedule_skip_output(self):
        sync.reset_runtime_cache()
        with mock.patch.object(
            sync,
            "actions_local_client_hit",
            return_value={"email": "owner@example.com", "age_sec": 12, "sha": SHA_LOCAL},
        ):
            with mock.patch.dict(
                os.environ,
                {"GITHUB_EVENT_NAME": "schedule", "GITHUB_OUTPUT": ""},
                clear=False,
            ):
                fd_path = os.path.join(self._tmp(), "out.txt")
                with open(fd_path, "w", encoding="utf-8") as handle:
                    handle.write("")
                with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": fd_path}, clear=False):
                    code = sync.cmd_check_local_client()
                with open(fd_path, encoding="utf-8") as handle:
                    body = handle.read()
        self.assertEqual(code, 0)
        self.assertEqual(body.strip(), "skip_run=true")

    def _tmp(self):
        import tempfile
        path = tempfile.mkdtemp(prefix="radar-sync-")
        self.addCleanup(lambda: __import__("shutil").rmtree(path, ignore_errors=True))
        return path


if __name__ == "__main__":
    unittest.main()
