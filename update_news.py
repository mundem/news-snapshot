"""Fetches headlines from news feeds and rewrites news.json.
Uses only Python's built-in libraries, so nothing needs installing."""
import os, json, re, html, urllib.request, urllib.error
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
CANDIDATES = 30      # headlines the AI chooses from, per topic

# ---- AI settings. If the AI step fails with a "model" error, change MODEL. ----
MODEL = "grok-4.7"
API_URL = "https://api.x.ai/v1/chat/completions"
TOPICS = {
    "finance": "finance and markets: stock and bond markets, the economy, central banks, major companies and commodities",
    "ai": "AI and technology: AI models and companies, chips, major tech products and tech policy",
    "world": "world news: major international events, conflicts, diplomacy, disasters and the global economy",
}
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

def pick(lists, limit=PER_SECTION):
    """Take stories from each source in turn so one outlet doesn't dominate."""
    chosen, seen = [], set()
    while len(chosen) < limit and any(lists):
        for lst in lists:
            while lst:
                item = lst.pop(0)
                key = item["title"].lower()
                if key not in seen:
                    seen.add(key)
                    chosen.append(item)
                    break
            if len(chosen) >= limit:
                break
    return chosen

def ask_ai(section_id, pool):
    """Asks the AI to choose the most important stories and summarise them."""
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no XAI_API_KEY found")
    lines = "\n".join(f"{n}. {i['title']} | {i['summary']} ({i['source']})"
                      for n, i in enumerate(pool, 1))
    prompt = (
        f"You are the editor of a personal news page about {TOPICS.get(section_id, section_id)}.\n"
        f"From the numbered list below, choose the {PER_SECTION} most important and newsworthy stories "
        "for a general reader. Skip celebrity gossip, lifestyle, shopping deals and minor stories. "
        "Prefer a mix of sources and avoid near-duplicates.\n"
        "For each, write a neutral summary of 1-2 sentences (under 200 characters) using ONLY "
        "information in its headline and snippet. Do not add facts.\n"
        'Reply with JSON only, in this shape: {"picks":[{"id":1,"summary":"..."}]}\n\n' + lines
    )
    body = json.dumps({
        "model": MODEL, "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are a careful news editor. Reply with JSON only."},
            {"role": "user", "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key,
        "User-Agent": "Mozilla/5.0 (personal news page)",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            reply = json.load(r)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")
    picks = json.loads(re.search(r"\{.*\}", reply, re.S).group(0))["picks"]
    chosen, used = [], set()
    for p in picks:
        n = int(p["id"])
        if 1 <= n <= len(pool) and n not in used:
            used.add(n)
            item = dict(pool[n - 1])
            summary = clean(str(p.get("summary", "")))
            if summary:
                item["summary"] = summary
            chosen.append(item)
    if len(chosen) < 3:
        raise RuntimeError("the AI returned too few usable picks")
    return chosen[:PER_SECTION]

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
        pool = pick(lists, CANDIDATES)
        chosen = pool[:PER_SECTION]  # fallback: the newest stories
        if pool:
            try:
                chosen = ask_ai(section["id"], pool)
                print(f"AI    {section['id']:8} picked {len(chosen)} of {len(pool)}")
            except Exception as e:
                print(f"AIFAIL {section['id']:8} {e} -- using the newest stories instead")
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
