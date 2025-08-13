#!/usr/bin/env python3
"""
Tottenham Hotspur automated X (Twitter) bot — minimal version
Posts:
- Pre-match reminder (60 min before kickoff)
- Live: goals, HT, FT
- Latest BBC Spurs news
Data (free):
- Fixtures & live: SofaScore unofficial endpoints
- News: BBC RSS (Tottenham)
Posting:
- Tweepy (OAuth 1.0a) using env vars
State:
- state.json prevents duplicates
Schedule:
- Run every 5 minutes via GitHub Actions
NOTE: Do not hardcode secrets. Provide via environment variables.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

import requests
import tweepy
import xml.etree.ElementTree as ET

# --- Config ---
TEAM_ID = 17  # Tottenham Hotspur on SofaScore
SOFASCORE_BASE = "https://api.sofascore.com/api/v1"
BBC_SPURS_RSS = "https://feeds.bbci.co.uk/sport/football/teams/tottenham-hotspur/rss.xml"
STATE_PATH = os.environ.get("STATE_PATH", "state.json")
# London timezone offset handling: rely on Europe/London via Python 3.9+ zoneinfo if available, else UTC
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    TZ_LONDON = ZoneInfo("Europe/London")
except Exception:
    TZ_LONDON = timezone(timedelta(hours=0))  # fallback UTC


def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def twitter_client():
    api_key = os.environ.get("TWITTER_API_KEY")
    api_secret = os.environ.get("TWITTER_API_SECRET")
    access_token = os.environ.get("TWITTER_ACCESS_TOKEN")
    access_secret = os.environ.get("TWITTER_ACCESS_SECRET")
    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError("Missing Twitter credentials env vars. Set TWITTER_* in your environment.")
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    return tweepy.API(auth)


def post_tweet(api, text: str) -> Optional[int]:
    text = (text[:279] + "…") if len(text) > 280 else text
    try:
        status = api.update_status(status=text)
        logging.info("Posted tweet id=%s", getattr(status, "id", None))
        return getattr(status, "id", None)
    except Exception as e:
        logging.exception("Failed to post tweet: %s", e)
        return None


# --------- SofaScore helpers ---------
def get_next_events() -> List[Dict[str, Any]]:
    """Upcoming and possibly live events for Spurs."""
    # next/0 returns list of upcoming events (may include today)
    url = f"{SOFASCORE_BASE}/team/{TEAM_ID}/events/next/0"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    return data.get("events", [])


def get_last_events() -> List[Dict[str, Any]]:
    """Recent past events (to identify a currently live or just finished match if next doesn't show it)."""
    url = f"{SOFASCORE_BASE}/team/{TEAM_ID}/events/last/0"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    return data.get("events", [])


def get_event_details(event_id: int) -> Dict[str, Any]:
    url = f"{SOFASCORE_BASE}/event/{event_id}"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json().get("event", {})


def get_event_incidents(event_id: int) -> Dict[str, Any]:
    url = f"{SOFASCORE_BASE}/event/{event_id}/incidents"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()


def is_spurs(home: Dict[str, Any], away: Dict[str, Any]) -> bool:
    return str(home.get("id")) == str(TEAM_ID) or str(away.get("id")) == str(TEAM_ID)


def format_team_vs(ev: Dict[str, Any]) -> str:
    home = ev.get("homeTeam", {}).get("shortName") or ev.get("homeTeam", {}).get("name", "Home")
    away = ev.get("awayTeam", {}).get("shortName") or ev.get("awayTeam", {}).get("name", "Away")
    return f"{home} vs {away}"


def event_kickoff_dt(ev: Dict[str, Any]) -> datetime:
    ts = ev.get("startTimestamp")
    if ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(TZ_LONDON)


def find_current_or_next_match() -> Dict[str, Any]:
    """
    Return dict with keys:
      - mode: 'live' | 'upcoming' | 'none'
      - event: event dict (if any)
    """
    # Check next events first
    try:
        next_events = get_next_events()
    except Exception:
        next_events = []
    # Occasionally, a currently-live event may not appear in "next" – check last events too
    try:
        last_events = get_last_events()
    except Exception:
        last_events = []

    candidates = []
    now = datetime.now(TZ_LONDON)

    # Gather relevant events (Spurs only, within +/- 1 day)
    for ev in (next_events + last_events):
        try:
            if not is_spurs(ev.get("homeTeam", {}), ev.get("awayTeam", {})):
                continue
            ko = event_kickoff_dt(ev)
            if abs((ko - now).total_seconds()) < 60 * 60 * 36:  # within ~36 hours window
                candidates.append(ev)
        except Exception:
            continue

    # Prefer live event
    for ev in candidates:
        status = ev.get("status", {}).get("type")
        if status and status.lower() in {"inprogress", "live"}:
            return {"mode": "live", "event": ev}

    # Else next upcoming by time
    if candidates:
        candidates.sort(key=lambda e: event_kickoff_dt(e))
        upcoming = [e for e in candidates if event_kickoff_dt(e) > now]
        if upcoming:
            return {"mode": "upcoming", "event": upcoming[0]}

    return {"mode": "none", "event": None}


# --------- BBC RSS (News) ---------
def fetch_bbc_news_items(limit: int = 5) -> List[Dict[str, str]]:
    r = requests.get(BBC_SPURS_RSS, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        if title and link:
            items.append({"title": title, "link": link, "id": guid})
        if len(items) >= limit:
            break
    return items


# --------- Posting logic ---------
def maybe_post_prematch(api, state, ev):
    ko = event_kickoff_dt(ev)
    now = datetime.now(TZ_LONDON)
    minutes_to_ko = int((ko - now).total_seconds() // 60)
    event_id = ev.get("id")
    key = f"prematch_posted_{event_id}"

    if 55 <= minutes_to_ko <= 65 and not state.get(key):
        text = (
            f"📅 Next Match (in ~{minutes_to_ko}m)\n"
            f"{format_team_vs(ev)}\n"
            f"Kick-off: {ko.strftime('%H:%M %Z')}\n"
            f"#COYS #THFC"
        )
        if post_tweet(api, text):
            state[key] = True
            return True
    return False


def extract_goal_incident_ids(incidents: Dict[str, Any]) -> List[str]:
    ids = []
    for side in ("homeIncidents", "awayIncidents", "incidents"):
        for inc in incidents.get(side, []) or []:
            if inc.get("type") == "goal":
                # Compose a stable id per incident
                m = inc.get("player", {}).get("id", 0)
                t = inc.get("time", 0)
                pid = f"goal-{m}-{t}-{inc.get('isHome', False)}"
                ids.append(pid)
    return ids


def describe_scoreline(ev_details: Dict[str, Any]) -> str:
    hs = ev_details.get("homeScore", {}).get("current", 0)
    as_ = ev_details.get("awayScore", {}).get("current", 0)
    home = ev_details.get("homeTeam", {}).get("shortName") or ev_details.get("homeTeam", {}).get("name", "Home")
    away = ev_details.get("awayTeam", {}).get("shortName") or ev_details.get("awayTeam", {}).get("name", "Away")
    return f"{home} {hs}–{as_} {away}"


def maybe_post_live_updates(api, state, ev):
    event_id = ev.get("id")
    details = get_event_details(event_id)
    status = details.get("status", {}).get("type", "").lower()

    # HT / FT
    ht_key = f"ht_posted_{event_id}"
    ft_key = f"ft_posted_{event_id}"

    if status in {"inprogress", "live"}:
        # Goals
        inc = get_event_incidents(event_id)
        goal_ids = extract_goal_incident_ids(inc)
        posted = set(state.get(f"posted_goals_{event_id}", []))

        for gid in goal_ids:
            if gid not in posted:
                # Try to craft a goal text from latest incident
                text = f"⚽ GOAL!\n{describe_scoreline(details)}\n#COYS #THFC"
                if post_tweet(api, text):
                    posted.add(gid)
                    state[f"posted_goals_{event_id}"] = list(posted)
                    # Post at most one new goal per run to avoid burst
                    break

        # Halftime detection via status 'halftime' or period 1 ended
        period = details.get("time", {}).get("currentPeriodStartTimestamp")
        # Some events expose status 'halftime'
        if details.get("status", {}).get("description", "").lower() == "halftime" and not state.get(ht_key):
            text = f"⏸️ Halftime: {describe_scoreline(details)}\n#COYS #THFC"
            if post_tweet(api, text):
                state[ht_key] = True

    # Full time
    if status in {"finished", "afterextra", "penalties", "postponed"} and not state.get(ft_key):
        text = f"🔔 Full-time: {describe_scoreline(details)}\n#COYS #THFC"
        if post_tweet(api, text):
            state[ft_key] = True
            # Clear goal cache for this match after FT to prevent future posts
            state.pop(f"posted_goals_{event_id}", None)

    return False


def maybe_post_news(api, state):
    seen = set(state.get("news_ids", []))
    items = fetch_bbc_news_items(limit=3)
    posted = False
    for it in items:
        if it["id"] not in seen:
            text = f"📰 {it['title']}\n{it['link']}"
            if post_tweet(api, text):
                seen.add(it["id"])
                posted = True
                break  # one news per run
    state["news_ids"] = list(seen)
    return posted


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = load_state()
    try:
        api = twitter_client()
    except Exception as e:
        logging.error("Twitter client error: %s", e)
        return

    # Find current or next match
    match_ctx = find_current_or_next_match()
    mode = match_ctx["mode"]
    ev = match_ctx["event"]

    if mode == "upcoming" and ev:
        logging.info("Upcoming match detected: %s", format_team_vs(ev))
        maybe_post_prematch(api, state, ev)

    if mode == "live" and ev:
        logging.info("Live match detected: %s", format_team_vs(ev))
        maybe_post_live_updates(api, state, ev)

    # Always try news if nothing posted this run
    maybe_post_news(api, state)

    save_state(state)


if __name__ == "__main__":
    main()
