# -*- coding: utf-8 -*-
"""Cloud git sync for the radar daemon.

Actions must push to the repository that is running the workflow
(``GITHUB_REPOSITORY``). A fork must not retarget ``origin`` at an upstream
owner, rebase that upstream during a cloud run, or treat commits inherited
from the parent repository as a live local client.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

LOCAL_CLIENT_WINDOW_SEC = 900

# Produced by `ml_predictor_5y.py --refresh`. The Actions commit must stage
# these or the next job checks out a stale panel and macro_weather.latest_date stalls.
DAILY_PANEL_GIT_PATHS = (
    "predict_radar_5y.html",
    "data_cache/latest_ai_predictions.json",
    "data_cache/ml_panel_5y.parquet",
    "data_cache/ml_extended_panel.parquet",
    "data_cache/.ml_panel_max_date",
    "data_cache/.ml_btc_utc_date",
    "data_cache/.last_daily_ml_refresh",
)

RADAR_PUBLISH_PATHS = DAILY_PANEL_GIT_PATHS + (
    "index.html",
    "futu_pro_terminal.html",
    "macro_radar.html",
    "stocks_radar.html",
    "data_cache/shared_alert_dedupe.json",
    ".terminal_notifications.json",
    ".options_cache.json",
    ".radar_alert_history.json",
    ".gitignore",
)
_SECRET_ENV_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "FINNHUB_API_KEY",
    "PUSHPLUS_TOKEN",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_CLAW_WEBHOOK_URL",
    "DISCORD_DEFENSIVE_FLIGHT_WEBHOOK_URL",
    "DISCORD_ALERT_WEBHOOK_URL",
)
_PARENT_OK = {"set": False, "mode": "repo", "slug": ""}
_SHA_CACHE = {}

_ACTIONS_EMAILS = {
    "actions@github.com",
    "41898282+github-actions[bot]@users.noreply.github.com",
}


def daily_ml_refresh_succeeded(returncode, marker, pred_day, page_day, expected_day) -> bool:
    """True only when the predictor exited 0 and an artifact reached the closed session.

    A non-zero child (missing pyarrow, incomplete download, exit 2) is never success,
    even if an older snapshot already contains today's date string.
    """
    if returncode != 0:
        return False
    expected = str(expected_day or "")[:10]
    if not expected:
        return False
    for value in (marker, pred_day, page_day):
        got = str(value or "")[:10]
        if got and got >= expected:
            return True
    return False


def reset_runtime_cache():
    _PARENT_OK["set"] = False
    _PARENT_OK["mode"] = "repo"
    _PARENT_OK["slug"] = ""
    _SHA_CACHE.clear()


def redact_secrets(text):
    """Remove credentials that git or the GitHub API might echo back."""
    if not text:
        return ""
    out = str(text)
    out = re.sub(r"x-access-token:[^@\s]+", "x-access-token:REDACTED", out, flags=re.I)
    out = re.sub(
        r"https://[^:\s/@]+:[^@\s]+@github\.com",
        "https://REDACTED@github.com",
        out,
        flags=re.I,
    )
    out = re.sub(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9._-]{10,}", "REDACTED_TOKEN", out)
    out = re.sub(r"\bgithub_pat_[A-Za-z0-9_]{20,}", "REDACTED_TOKEN", out)
    for key in _SECRET_ENV_KEYS:
        val = os.environ.get(key) or ""
        if len(val) >= 8 and val in out:
            out = out.replace(val, "REDACTED")
    return out


def valid_repo_slug(slug):
    if not slug or not isinstance(slug, str):
        return False
    text = slug.strip()
    if text.count("/") != 1 or ".." in text or " " in text:
        return False
    owner, name = text.split("/", 1)
    if not owner or not name or owner.startswith(".") or name.startswith("."):
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    return all(ch in allowed for ch in owner + name)


def valid_commit_sha(sha):
    if not sha or not isinstance(sha, str):
        return False
    text = sha.strip()
    if len(text) < 7 or len(text) > 40:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in text)


def is_actions_author_email(email):
    text = (email or "").strip().lower()
    if text in _ACTIONS_EMAILS:
        return True
    return "github-actions[bot]" in text and text.endswith("@users.noreply.github.com")


def origin_url_repo_slug(url):
    """Return ``owner/repo`` from a git remote URL, ignoring any userinfo."""
    raw = (url or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    path = ""
    for marker in ("github.com/", "github.com:"):
        idx = lower.find(marker)
        if idx >= 0:
            path = raw[idx + len(marker):]
            break
    if not path:
        return ""
    path = path.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return ""
    slug = f"{parts[0]}/{parts[1]}"
    return slug if valid_repo_slug(slug) else ""


def origin_url_has_userinfo(url):
    lower = (url or "").lower()
    return "@github.com" in lower or "x-access-token:" in lower


def sync_origin_plan(actions, github_repository, origin_slug, token_present):
    """Decide which repository a pull/push may use.

    Cloud runs always target ``github_repository`` and never embed a token in
    the remote URL (checkout already installed credentials). Local daemons keep
    whatever origin they cloned and may embed a PAT for that same slug.
    """
    repo = (github_repository or "").strip()
    origin = (origin_slug or "").strip()
    repo_ok = valid_repo_slug(repo)
    origin_ok = valid_repo_slug(origin)
    if actions:
        return {
            "target_slug": repo if repo_ok else "",
            "rewrite": bool(repo_ok and origin != repo),
            "embed_token": False,
            "allow_pull_push": repo_ok,
        }
    return {
        "target_slug": origin if origin_ok else "",
        "rewrite": False,
        "embed_token": bool(token_present and origin_ok),
        "allow_pull_push": origin_ok,
    }


def select_local_client_commit(commits, now, mode, parent_slug, parent_has_commit, window=LOCAL_CLIENT_WINDOW_SEC):
    """Return a recent non-Actions commit that should make cloud yield.

    ``mode`` is ``repo`` (not a fork: any recent non-Actions commit counts),
    ``fork`` (only a commit that is absent from the parent repository counts),
    or ``fork-unresolved`` (do not yield; an inherited commit must not look
    like a live local client).
    ``parent_has_commit(parent, sha)`` returns True, False, or None.
    """
    for email, epoch, sha in commits or []:
        if is_actions_author_email(email):
            continue
        try:
            diff = float(now) - float(epoch)
        except (TypeError, ValueError):
            continue
        if diff < 0 or diff >= window:
            continue
        if mode == "repo":
            return {"email": email, "age_sec": int(diff), "sha": sha}
        if mode == "fork-unresolved":
            continue
        if mode != "fork" or not valid_repo_slug(parent_slug):
            continue
        try:
            exists = parent_has_commit(parent_slug, sha)
        except Exception:
            exists = None
        if exists is False:
            return {"email": email, "age_sec": int(diff), "sha": sha}
    return None


def parent_context_from_env(env, repo_lookup):
    """Return ``(mode, parent_slug)`` without caching failures."""
    flag = str((env or {}).get("RADAR_REPO_IS_FORK") or "").strip().lower()
    explicit = str((env or {}).get("RADAR_FORK_PARENT") or "").strip()
    this_repo = str((env or {}).get("GITHUB_REPOSITORY") or "").strip().lower()
    if valid_repo_slug(explicit) and explicit.lower() != this_repo:
        return "fork", explicit
    if flag in {"false", "0", "no"}:
        return "repo", ""
    actions = str((env or {}).get("GITHUB_ACTIONS") or "").strip().lower() == "true"
    if flag in {"true", "1", "yes"} or actions:
        try:
            looked = repo_lookup()
        except Exception:
            looked = None
        if looked is None:
            return "fork-unresolved", ""
        if looked == "":
            return "repo", ""
        if valid_repo_slug(looked) and looked.lower() != this_repo:
            return "fork", looked
        return "fork-unresolved", ""
    return "repo", ""


# How often a cloud run may ask Pages to rebuild the default branch.
# Each radar round is already slower than this; the cap avoids a 4-hour
# job enqueueing a build on every 15-second retry.
PAGES_BUILD_MIN_INTERVAL_SEC = 90
_LAST_PAGES_BUILD_TS = {"value": 0.0}


def reset_pages_build_throttle():
    _LAST_PAGES_BUILD_TS["value"] = 0.0


def pages_publish_plan(actions, github_repository, token_present, build_type):
    """Choose the official Pages publish path for this repository.

    ``legacy`` (Deploy from a branch) uses POST /repos/{repo}/pages/builds.
    That call uses the workflow GITHUB_TOKEN with pages:write and does not
    need a PAT. ``workflow`` (GitHub Actions source) is published by
    actions/upload-pages-artifact and actions/deploy-pages in the workflow.
    Neither path may target an upstream owner.
    """
    repo = (github_repository or "").strip()
    repo_ok = bool(actions and valid_repo_slug(repo) and token_present)
    kind = (build_type or "").strip().lower()
    if not repo_ok:
        return {"method": "none", "repo": repo if valid_repo_slug(repo) else "", "url": ""}
    if kind == "workflow":
        return {"method": "artifact", "repo": repo, "url": ""}
    return {
        "method": "branch-build",
        "repo": repo,
        "url": f"https://api.github.com/repos/{repo}/pages/builds",
    }


def should_request_pages_build(last_epoch, now, min_interval=PAGES_BUILD_MIN_INTERVAL_SEC, force=False):
    if force or not last_epoch:
        return True
    try:
        return (float(now) - float(last_epoch)) >= float(min_interval)
    except (TypeError, ValueError):
        return True


def request_branch_pages_build(repo, token, opener=None):
    """POST the official Pages build API. The token is never returned or logged."""
    plan = pages_publish_plan(True, repo, bool(token), "legacy")
    if plan["method"] != "branch-build" or not plan["url"]:
        return {"ok": False, "status": 0, "detail": "refused"}
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "us-market-radar-pages",
        "X-GitHub-Api-Version": "2026-03-10",
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(plan["url"], data=b"{}", headers=headers, method="POST")
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=20) as resp:
            raw = resp.read(1500)
            status = int(getattr(resp, "status", 201) or 201)
    except urllib.error.HTTPError as exc:
        raw = exc.read(800)
        status = int(getattr(exc, "code", 0) or 0)
        detail = redact_secrets(raw.decode("utf-8", errors="replace"))
        return {"ok": False, "status": status, "detail": detail[:180]}
    except Exception as exc:
        return {"ok": False, "status": 0, "detail": redact_secrets(str(exc))[:180]}
    detail = ""
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        if isinstance(payload, dict):
            detail = str(payload.get("status") or "")
    except Exception:
        detail = ""
    return {"ok": status in (200, 201, 202, 204), "status": status, "detail": detail[:80]}


def maybe_request_branch_pages_build(now=None, force=False, opener=None):
    """Request a Pages rebuild of THIS repo after a cloud push. No PAT."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return {"ok": False, "status": 0, "detail": "not-actions"}
    build_type = (os.environ.get("RADAR_PAGES_BUILD_TYPE") or "legacy").strip().lower()
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    plan = pages_publish_plan(True, repo, bool(token), build_type)
    if plan["method"] == "artifact":
        return {"ok": True, "status": 0, "detail": "artifact"}
    if plan["method"] != "branch-build":
        return {"ok": False, "status": 0, "detail": "refused"}
    current = time.time() if now is None else float(now)
    if not should_request_pages_build(_LAST_PAGES_BUILD_TS["value"], current, force=force):
        return {"ok": True, "status": 0, "detail": "throttled"}
    result = request_branch_pages_build(repo, token, opener=opener)
    if result.get("ok") or result.get("status") in (409, 422, 429):
        _LAST_PAGES_BUILD_TS["value"] = current
    return result


def pages_gate_decision(runs, current_run_id):
    """``wait`` only when the newest Pages deploy (not this run) is still active."""
    current = str(current_run_id or "")
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        if current and str(run.get("id") or "") == current:
            continue
        name = str(run.get("name") or "")
        path = str(run.get("path") or "")
        if name != "pages-build-deployment" and not path.endswith("pages-build-deployment"):
            continue
        status = str(run.get("status") or "")
        if status in {"in_progress", "queued", "waiting", "pending", "requested"}:
            return "wait"
        return "go"
    return "go"


def _git(args, capture=False):
    kwargs = {"check": False}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000
    if capture:
        kwargs.update(capture_output=True, text=True, encoding="utf-8", errors="replace")
    else:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.run(["git", *args], **kwargs)


def read_origin_url():
    """Stored origin URL, without insteadOf credential rewriting."""
    res = _git(["config", "--local", "--get", "remote.origin.url"], capture=True)
    stored = (res.stdout or "").strip() if res.returncode == 0 else ""
    if stored:
        return stored
    res = _git(["remote", "get-url", "origin"], capture=True)
    if res.returncode != 0:
        return ""
    return (res.stdout or "").strip()


def read_origin_slug():
    return origin_url_repo_slug(read_origin_url())


def read_commits(rev, limit=10):
    res = _git(["log", "-n", str(int(limit)), "--format=%ae|%ct|%H", rev], capture=True)
    if res.returncode != 0:
        return []
    commits = []
    for line in (res.stdout or "").splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        email, epoch, sha = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if valid_commit_sha(sha):
            commits.append((email, epoch, sha))
    return commits


def _api_token(actions):
    if actions:
        return (os.environ.get("GITHUB_TOKEN") or "").strip()
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def _api_request(url, token, opener=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "us-market-radar-cloud-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers, method="GET")
    open_fn = opener or urllib.request.urlopen
    return open_fn(req, timeout=10)


def http_repo_parent(repo_slug, token, opener=None):
    """Return parent slug, ``''`` when this repo is not a fork, or None on failure."""
    if not valid_repo_slug(repo_slug):
        return None
    url = f"https://api.github.com/repos/{repo_slug}"
    try:
        with _api_request(url, token, opener=opener) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not payload.get("fork"):
        return ""
    parent = ((payload.get("parent") or {}).get("full_name") or "").strip()
    if valid_repo_slug(parent):
        return parent
    return None


def http_parent_has_commit(parent_slug, sha, token, opener=None):
    if not valid_repo_slug(parent_slug) or not valid_commit_sha(sha):
        return None
    url = f"https://api.github.com/repos/{parent_slug}/commits/{sha}"
    try:
        with _api_request(url, token, opener=opener) as resp:
            resp.read(64)
            status = getattr(resp, "status", 200)
            return 200 <= int(status) < 300
    except urllib.error.HTTPError as exc:
        if getattr(exc, "code", None) == 404:
            return False
        return None
    except Exception:
        return None


def cached_parent_has_commit(parent_slug, sha, token, opener=None):
    key = (parent_slug, sha)
    if key in _SHA_CACHE:
        return _SHA_CACHE[key]
    result = http_parent_has_commit(parent_slug, sha, token, opener=opener)
    if result is not None:
        _SHA_CACHE[key] = result
    return result


def resolve_parent_context(env=None, lookup=None):
    if _PARENT_OK["set"]:
        return _PARENT_OK["mode"], _PARENT_OK["slug"]
    env = os.environ if env is None else env
    actions = str(env.get("GITHUB_ACTIONS") or "").strip().lower() == "true"
    repo = (env.get("GITHUB_REPOSITORY") or "").strip()

    def default_lookup():
        return http_repo_parent(repo, _api_token(actions))

    mode, slug = parent_context_from_env(env, lookup or default_lookup)
    if mode != "fork-unresolved":
        _PARENT_OK["set"] = True
        _PARENT_OK["mode"] = mode
        _PARENT_OK["slug"] = slug
    return mode, slug


def ensure_sync_origin():
    """Point a cloud run at ``GITHUB_REPOSITORY``. Never retarget a local clone."""
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    origin_url = read_origin_url()
    origin = origin_url_repo_slug(origin_url)
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    token_present = bool((os.environ.get("GH_TOKEN") or "").strip() or (os.environ.get("GITHUB_TOKEN") or "").strip())
    plan = sync_origin_plan(actions, repo, origin, token_present)
    target = plan["target_slug"]
    if not target or not plan["allow_pull_push"]:
        return False
    if actions:
        if plan["rewrite"] or origin_url_has_userinfo(origin_url):
            _git(["remote", "set-url", "origin", f"https://github.com/{target}.git"])
        return read_origin_slug() == target
    if plan["embed_token"]:
        token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
        if token:
            _git(["remote", "set-url", "origin", f"https://x-access-token:{token}@github.com/{target}.git"])
            return read_origin_slug() == target
    return read_origin_slug() == target


def actions_local_client_hit(now=None):
    """Recent non-Actions commit on THIS repo that did not come from the parent."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    allowed = ensure_sync_origin()
    rev = "HEAD"
    if allowed:
        fetched = _git(["fetch", "origin", "main", "--quiet"], capture=True)
        if fetched.returncode == 0 and read_commits("origin/main", limit=1):
            rev = "origin/main"
    commits = read_commits(rev)
    if not commits and rev != "HEAD":
        commits = read_commits("HEAD")
    mode, parent = resolve_parent_context()
    token = _api_token(True)

    def has_commit(parent_slug, sha):
        return cached_parent_has_commit(parent_slug, sha, token)

    return select_local_client_commit(commits, time.time() if now is None else now, mode, parent, has_commit)


def pull_rebase_origin_main():
    return _git(["pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main"], capture=True)


def wait_for_pages_idle(sleep_fn=None, max_wait=240, opener=None):
    """Wait for this repository's Pages deploy. Failures let the push proceed."""
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip() if actions else read_origin_slug()
    if not valid_repo_slug(repo):
        repo = read_origin_slug()
    if not valid_repo_slug(repo):
        return
    token = _api_token(actions)
    if not token:
        print("[+] ⚡ 未配置可用于查询的 token，跳过 Pages 守门员并直接推送")
        return
    current = (os.environ.get("GITHUB_RUN_ID") or "").strip()
    url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=20&exclude_pull_requests=true"
    sleep = sleep_fn or time.sleep
    started = time.time()
    waited = False
    while True:
        try:
            with _api_request(url, token, opener=opener) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        except Exception as exc:
            print("[!] Pages 守门员查询失败，直接放行推送: " + redact_secrets(str(exc))[:160])
            return
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        decision = pages_gate_decision(runs, current)
        if decision != "wait":
            if waited:
                print(f"[+] 🎯 当前仓库 Pages 部署已就绪 (等待 {int(time.time() - started)}s)，放行推送")
            else:
                print("[+] ⚡ 当前仓库 Pages 空闲，直接放行推送")
            return
        waited = True
        elapsed = time.time() - started
        if elapsed >= max_wait:
            print(f"[!] Pages 守门员已等待 {int(elapsed)}s，放行推送")
            return
        active = None
        for run in runs or []:
            if isinstance(run, dict) and str(run.get("name") or "") == "pages-build-deployment":
                active = run
                break
        run_id = active.get("id") if isinstance(active, dict) else ""
        status = active.get("status") if isinstance(active, dict) else ""
        print(f"[*] ⏳ 当前仓库 Pages 正在部署 (Run {run_id} / {status})，已等 {int(elapsed)}s")
        sleep(8)


def cmd_check_local_client():
    hit = actions_local_client_hit()
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    skip = bool(hit) and event == "schedule"
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"skip_run={'true' if skip else 'false'}\n")
    if hit and skip:
        print(
            f"::notice::检测到本仓库本地客户端活跃提交 ({hit['email']}, {hit['age_sec']}s 前)，云端 Actions 优雅避让"
        )
    elif hit:
        print(
            f"检测到本仓库本地客户端活跃提交 ({hit['email']}, {hit['age_sec']}s 前)；手动触发不在预检阶段跳过"
        )
    else:
        print("未发现需要避让的本仓库本地提交（上游继承或从上游同步的提交已忽略）")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--check-local-client" in args:
        return cmd_check_local_client()
    print("usage: python radar_cloud_sync.py --check-local-client", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
