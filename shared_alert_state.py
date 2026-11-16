"""Cross-device Discord alert dedupe / heartbeat ledger.

Only pure text keys. Never store webhook URLs or tokens.
"""
import json, os, re, subprocess, time
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_FILE = os.path.join(BASE_DIR, "data_cache", "shared_alert_dedupe.json")
HEARTBEAT_MAX_AGE = 360


def _now():
    return time.time()


def _default():
    return {"dedupe": {}, "device": "", "last_heartbeat_ts": 0, "updated_at": ""}


def load():
    try:
        d = json.load(open(SHARED_FILE, encoding="utf-8"))
        if not isinstance(d, dict):
            d = _default()
        d.setdefault("dedupe", {})
        if not isinstance(d["dedupe"], dict):
            d["dedupe"] = {}
        return d
    except Exception:
        return _default()


def _save(d):
    try:
        os.makedirs(os.path.dirname(SHARED_FILE), exist_ok=True)
        d["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp = SHARED_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SHARED_FILE)
    except Exception:
        pass


def et_day():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def make_key(day, title, ticker=None):
    """Normalize a Discord alert title to ET-day + tickers + signals."""
    title = str(title or "")
    if not ticker:
        tickers = re.findall(r"\b([A-Z]{1,6}(?:-[A-Z]{2,4})?)\b", title)
        ticker = "/".join(dict.fromkeys(tickers)) if tickers else ""
    sigs = []
    if "买点" in title:
        sigs.append("5M_ENTRY")
    if "黄金坑" in title:
        sigs.append("GOLDEN_PIT")
    if "止损" in title or "破位" in title:
        sigs.append("STOP_LOSS")
    if "止盈" in title:
        sigs.append("TAKE_PROFIT")
    if "防御" in title or "Flight" in title:
        sigs.append("DEFENSIVE")
    sig = "+".join(sigs) if sigs else re.sub(r"\s+", "_", title)[:80]
    return f"{day}:{ticker}:{sig}" if ticker else f"{day}:{sig}"


def is_sent(key, max_age=None):
    d = load()
    entry = d.get("dedupe", {}).get(str(key or ""))
    if not entry:
        return False
    if max_age is not None and isinstance(entry, dict):
        try:
            if (_now() - float(entry.get("ts") or 0)) >= float(max_age):
                return False
        except Exception:
            pass
    return True


def mark(key, device=""):
    if not key:
        return False
    d = load()
    d.setdefault("dedupe", {})
    d["dedupe"][key] = {"device": device or "", "ts": _now()}
    _save(d)
    return True


def heartbeat(device):
    d = load()
    d["device"] = str(device or "")
    d["last_heartbeat_ts"] = _now()
    _save(d)


def should_standby(device="ANDROID", max_age=HEARTBEAT_MAX_AGE):
    """Android standby only while PC heartbeat is fresh."""
    if str(device).upper() != "ANDROID":
        return False
    d = load()
    if str(d.get("device", "")).upper() != "PC":
        return False
    try:
        return (_now() - float(d.get("last_heartbeat_ts") or 0)) < float(max_age)
    except Exception:
        return False


def pull_shared(workdir=None, timeout=45):
    """Pull latest shared ledger; suppress output/errors."""
    try:
        subprocess.run(
            ["git", "pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main"],
            cwd=workdir or BASE_DIR, check=False, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=timeout,
        )
        return True
    except Exception:
        return False
