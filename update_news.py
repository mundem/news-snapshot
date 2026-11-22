"""Fetches headlines from news feeds and rewrites news.json.
Uses only Python's built-in libraries, so nothing needs installing."""
import json, re, html, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ---- EDIT HERE: your sources. Each section id must match news.json. ----
FEEDS = {
    "finance": [
        ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ],
    "ai": [
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ],
    "world": [
        ("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("PBS NewsHour", "https://www.pbs.org/newshour/feeds/rss/world"),
    ],
}
PER_SECTION = 5      # stories per topic
SUMMARY_CHARS = 200  # max summary length
# ------------------------------------------------------------------------

def local(tag):
    return tag.rsplit("}", 1)[-1]

def child_text(el, *names):
    for c in el:
        if local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""

def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > SUMMARY_CHARS:
        text = text[:SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text

def parse_date(text):
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text)
    except Exception:
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def parse_feed(data, source):
    items = []
    for el in ET.fromstring(data).iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        link = child_text(el, "link")
        if not link:
            link = next((c.get("href") for c in el
                         if local(c.tag) == "link" and c.get("href")), "")
        title = clean(child_text(el, "title"))
        if not title or not link:
            continue
        when = parse_date(child_text(el, "pubDate", "published", "updated", "date"))
        items.append({
            "title": title,
            "summary": clean(child_text(el, "description", "summary", "content")),
            "when": when,
            "source": source,
            "url": link,
        })
    items.sort(key=lambda i: i["when"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (personal news page)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()

def pick(lists):
    """Take stories from each source in turn so one outlet doesn't dominate."""
    chosen, seen = [], set()
    while len(chosen) < PER_SECTION and any(lists):
        for lst in lists:
            while lst:
                item = lst.pop(0)
                key = item["title"].lower()
                if key not in seen:
                    seen.add(key)
                    chosen.append(item)
                    break
            if len(chosen) >= PER_SECTION:
                break
    return chosen

def main():
    with open("news.json", encoding="utf-8") as f:
        data = json.load(f)
    for section in data["sections"]:
        lists = []
        for name, url in FEEDS.get(section["id"], []):
            try:
                found = parse_feed(fetch(url), name)
                print(f"OK    {section['id']:8} {name}: {len(found)} stories")
                lists.append(found)
            except Exception as e:
                print(f"FAIL  {section['id']:8} {name}: {e}")
        chosen = pick(lists)
        if not chosen:
            print(f"KEEP  {section['id']}: no stories fetched, keeping the old ones")
            continue
        section["items"] = [{
            "title": i["title"],
            "summary": i["summary"],
            "date": f"{i['when']:%b} {i['when'].day}" if i["when"] else "",
            "source": i["source"],
            "url": i["url"],
        } for i in chosen]
    now = datetime.now(timezone(timedelta(hours=8)))
    data["updated"] = f"{now:%b} {now.day}, {now.year}"
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

main()
