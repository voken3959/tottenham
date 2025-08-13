import os
import requests
import logging
import xml.etree.ElementTree as ET
import tweepy
from datetime import datetime
from typing import List, Dict, Any

# ---------------------------
# CONFIG
# ---------------------------
SOFASCORE_BASE = "https://api.sofascore.com/api/v1"
TEAM_ID = 35  # Tottenham Hotspur
BBC_SPURS_RSS = "https://feeds.bbci.co.uk/sport/football/teams/tottenham-hotspur/rss.xml"

# ---------------------------
# TWITTER AUTH
# ---------------------------
API_KEY = os.getenv("TWITTER_API_KEY")
API_SECRET = os.getenv("TWITTER_API_SECRET")
ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET")

auth = tweepy.OAuth1UserHandler(API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET)
twitter = tweepy.API(auth)

# ---------------------------
# LOGGING
# ---------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------------------------
# FUNCTIONS
# ---------------------------
def get_next_events() -> List[Dict[str, Any]]:
    url = f"{SOFASCORE_BASE}/team/{TEAM_ID}/events/next/0"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    events = data.get("events", [])
    logging.debug(f"Fixtures found (next): {events}")
    return events

def get_last_events() -> List[Dict[str, Any]]:
    url = f"{SOFASCORE_BASE}/team/{TEAM_ID}/events/last/0"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    events = data.get("events", [])
    logging.debug(f"Fixtures found (last): {events}")
    return events

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
    logging.debug(f"News items found: {items}")
    return items

def post_tweet(text: str):
    try:
        twitter.update_status(status=text)
        logging.info(f"Posted tweet: {text}")
    except Exception as e:
        logging.error(f"Error posting tweet: {e}")

# ---------------------------
# MAIN
# ---------------------------
def main():
    logging.info("Fetching Tottenham Hotspur updates...")

    # Fixtures
    next_events = get_next_events()
    last_events = get_last_events()

    # BBC News
    news_items = fetch_bbc_news_items()

    # Example: Post first news item (for testing)
    if news_items:
        first = news_items[0]
        tweet_text = f"📰 {first['title']} {first['link']}"
        post_tweet(tweet_text)
    else:
        logging.info("No news items found to post.")

if __name__ == "__main__":
    main()
