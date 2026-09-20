"""Private, versioned GitHub snapshots with optimistic concurrency control."""
from __future__ import annotations

import base64
import copy
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request

from .menus import DEFAULT_MENUS, validate_menus
from .processing import load_transactions, validate_replacement, score_accounts, weekly_leaderboard
from .config import WEEKS


class StorageError(RuntimeError):
    pass


def empty_state():
    return {"schema": 1, "csv": None, "menus": copy.deepcopy(DEFAULT_MENUS),
            "reviews": {}, "upload": None, "weekly_results": {}}


def encode(state):
    return gzip.compress(json.dumps(state, sort_keys=True, allow_nan=False).encode(), mtime=0)


def decode(content):
    # Bounded inflation prevents a corrupt or malicious snapshot exhausting RAM.
    with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
        text = stream.read(30_000_001)
    if len(text) > 30_000_000:
        raise StorageError("Snapshot exceeds the 30 MB safety limit.")
    state = json.loads(text)
    if state.get("schema") != 1:
        raise StorageError("Unsupported snapshot schema.")
    validate_menus(state["menus"])
    if not isinstance(state["reviews"], dict):
        raise StorageError("Invalid review state.")
    state.setdefault("weekly_results", {})
    if not isinstance(state["weekly_results"], dict):
        raise StorageError("Invalid frozen weekly results.")
    if state["csv"] is not None:
        load_transactions(state["csv"].encode())
    return state


def weekly_result(state, result, week, roster=None, competitors=None):
    """Closed results are read from immutable private snapshots, never rescored."""
    frozen = state.get("weekly_results", {}).get(str(week.number))
    if frozen:
        import pandas as pd
        return pd.DataFrame(frozen["board"])
    return weekly_leaderboard(result, week, roster, competitors)


def finalise_week(state, week, today, confirmed_complete=False, roster=None, competitors=None):
    if today <= week.end:
        raise ValueError("The weekly competition has not ended yet.")
    if str(week.number) in state.get("weekly_results", {}):
        raise ValueError("This weekly result is already frozen and cannot be overwritten.")
    if not confirmed_complete:
        raise ValueError("Confirm the complete week's export and review before freezing.")
    if not state.get("csv") or not state.get("upload"):
        raise ValueError("Upload a complete cumulative export first.")
    if date.fromisoformat(state["upload"]["end"]) < week.end:
        raise ValueError("Uploaded data does not cover the end of this week.")
    raw = load_transactions(state["csv"].encode())
    scored = score_accounts(raw, state["menus"], state["reviews"], roster)
    board = weekly_leaderboard(scored, week, roster, competitors)
    if board.empty or board["provisional"].any():
        raise ValueError("Resolve this week's account reviews before freezing its prize result.")
    new = copy.deepcopy(state)
    new.setdefault("weekly_results", {})[str(week.number)] = {
        "board": json.loads(board.to_json(orient="records")),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "period_end": week.end.isoformat(),
        "source_sha256": hashlib.sha256(state["csv"].encode()).hexdigest(),
        "rules": "rolling-habits-v1; shared-category-opportunities",
    }
    return new


def replacement_state(state, payload, allow_reduction=False, today=None):
    raw = load_transactions(payload)
    old = load_transactions(state["csv"].encode()) if state["csv"] else None
    validate_replacement(raw, old, allow_reduction, today)
    # Persist only scoring fields; customer names and payment detail are discarded.
    csv = raw.to_csv(index=False)
    digest = hashlib.sha256(csv.encode()).hexdigest()
    if state.get("upload") and state["upload"]["sha256"] == digest:
        return copy.deepcopy(state), False
    new = copy.deepcopy(state)
    new["csv"] = csv
    new["upload"] = dict(
        sha256=digest, at=datetime.now(timezone.utc).isoformat(),
        start=raw["Date"].min().isoformat(), end=raw["Date"].max().isoformat(),
        rows=len(raw), accounts=int(raw["Account ID"].nunique()),
    )
    return new, True


class GitHubStore:
    """A token must have Contents R/W on this private data repository only."""
    def __init__(self, repo, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise StorageError("Invalid storage repository name.")
        if not token:
            raise StorageError("Private-storage token is not configured.")
        self.repo, self.token = repo, token
        self.root = f"https://api.github.com/repos/{repo}"

    def _request(self, path="", method="GET", data=None, accept=None):
        headers = {"Authorization": f"Bearer {self.token}",
                   "Accept": accept or "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "gd-scoreboard"}
        body = json.dumps(data).encode() if data is not None else None
        if body:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.root + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
            return content if accept else json.loads(content)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET" and path.startswith("/contents/"):
                return None
            if exc.code in (409, 422):
                raise StorageError("Saved data changed in another session. Reload before saving.") from None
            raise StorageError(f"Private storage returned HTTP {exc.code}; check access and configuration.") from None
        except (urllib.error.URLError, TimeoutError):
            raise StorageError("Private storage is unreachable; the previous snapshot has not been replaced.") from None

    def _ensure_private(self):
        repo = self._request()
        if repo.get("private") is not True:
            raise StorageError("Refusing transaction storage in a public repository.")

    def load(self):
        self._ensure_private()
        entry = self._request("/contents/state.json.gz")
        if entry is None:
            return empty_state(), None
        if entry.get("encoding") == "base64":
            content = base64.b64decode(entry["content"])
        else:
            blob = self._request("/git/blobs/" + entry["sha"])
            content = base64.b64decode(blob["content"])
        return decode(content), entry["sha"]

    def save(self, state, expected_version):
        self._ensure_private()
        payload = {"message": "Update private scoreboard snapshot",
                   "content": base64.b64encode(encode(state)).decode()}
        if expected_version:
            payload["sha"] = expected_version
        response = self._request("/contents/state.json.gz", "PUT", payload)
        return response["content"]["sha"]


class LocalStore:
    """Explicit development/test backend; not durable on Streamlit Cloud."""
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return empty_state(), None
        content = self.path.read_bytes()
        return decode(content), hashlib.sha256(content).hexdigest()

    def save(self, state, expected_version):
        _, version = self.load()
        if version != expected_version:
            raise StorageError("Saved data changed; reload before saving.")
        content = encode(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return hashlib.sha256(content).hexdigest()
