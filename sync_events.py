"""Read the publicly rendered Second Life events calendar with Chromium.
Only publish rows actually visible in the calendar. No invented entries.
"""
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

SOURCE = 'https://secondlife.com/events/index.php?lang=en-US'
OUT = Path(__file__).resolve().parent / 'data' / 'events.json'
SLT = ZoneInfo('America/Los_Angeles')
MUSIC = re.compile(r'\b(live music|musician|singer|singing|pianist|piano|concert|guitar|jazz|blues|rock|band|karaoke|acoustic|live performer|live performance)\b', re.I)
DJ = re.compile(r'\b(dj|disc jockey|deejay)\b', re.I)

def category_for(title):
    if DJ.search(title): return 'Live DJ'
    if MUSIC.search(title): return 'Live Music'
    return 'Other / Unclassified'

def parse_date(text, now):
    m = re.search(r'(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})\s*([AP]M)', text, re.I)
    if not m: return None
    month, day, hour, minute = map(int, m.group(1,2,3,4))
    hour = hour % 12 + (12 if m.group(5).upper() == 'PM' else 0)
    for year in (now.year, now.year+1):
        try: d = datetime(year, month, day, hour, minute, tzinfo=SLT)
        except ValueError: continue
        if d >= now - timedelta(hours=2) and d <= now + timedelta(days=370):
            return d
    return None

def extract(html):
    soup = BeautifulSoup(html, 'html.parser')
    now = datetime.now(SLT)
    events = []
    for table in soup.select('table'):
        rows = table.select('tr')
        if not any(all(x in r.get_text(' ',strip=True).lower() for x in ('when','what','where')) for r in rows[:3]):
            continue
        for tr in rows:
            cells = tr.find_all('td', recursive=False)
            if len(cells) < 3: continue
            when, title, venue = [c.get_text(' ',strip=True) for c in cells[:3]]
            date = parse_date(when, now)
            if not date or not title: continue
            link = cells[1].find('a',href=True)
            event_url = urljoin(SOURCE, link['href']) if link else SOURCE
            events.append({'title':title, 'starts_at':date.isoformat(), 'venue':venue,
                           'category':category_for(title), 'description':'',
                           'url':event_url, 'teleport_url':None})
    return list({(e['title'],e['starts_at'],e['venue']):e for e in events}.values())

def main():
    payload = {'status':'unavailable','updated_at':datetime.now(timezone.utc).isoformat(),
               'source':SOURCE,'events':[], 'message':''}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page(locale='en-US', timezone_id='America/Los_Angeles')
            page.goto(SOURCE, wait_until='domcontentloaded', timeout=60000)
            try:
                page.locator('table').filter(has_text='When').filter(has_text='Where').first.wait_for(timeout=30000)
                page.wait_for_timeout(3000)
            except Exception: pass
            events = extract(page.content())
            browser.close()
        if events:
            payload.update(status='ok',events=events,message='')
        else:
            payload['message']='Calendar loaded, but no dated event rows were found. Check the calendar markup.'
    except Exception as exc:
        payload['message']=f'Calendar retrieval failed: {type(exc).__name__}: {str(exc)[:220]}'
    OUT.parent.mkdir(parents=True,exist_ok=True)
    # Keep last verified events on transient failure, rather than erase them.
    if payload['status']!='ok' and OUT.exists():
        try:
            previous=json.loads(OUT.read_text(encoding='utf-8'))
            existing=[e for e in previous.get('events',[]) if datetime.fromisoformat(e['starts_at'])>datetime.now(timezone.utc)]
            if existing: payload['events']=existing;payload['message']+=' (showing previously verified future events)'
        except Exception: pass
    OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(f"status={payload['status']} events={len(payload['events'])} message={payload['message']}")

if __name__=='__main__':main()
