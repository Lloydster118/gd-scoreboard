"""Bounded in-memory caches for public reruns; never persist private data to disk."""
import streamlit as st

from .storage import GitHubStore
from .processing import load_transactions, score_accounts


@st.cache_data(ttl=60, max_entries=4, show_spinner=False)
def saved_snapshot(repo, token):
    """Credential-scoped cache; rotated credentials never reuse old access.

    A new interaction checks GitHub after at most 60 seconds. Explicit refresh
    and successful admin saves clear this cache immediately. Errors propagate:
    expired entries are not used as a silent stale fallback.
    """
    return GitHubStore(repo, token).load()


@st.cache_data(max_entries=4, show_spinner=False)
def scored_snapshot(csv, menus, reviews, roster):
    """Content-keyed result: every scoring input participates in invalidation.

    cache_data returns isolated copies, so one session cannot mutate another
    session's raw transactions, reviewed scores or category DataFrames.
    """
    raw = load_transactions(csv.encode())
    return raw, score_accounts(raw, menus, reviews, roster)
