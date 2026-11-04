"""Collect only verifiable events displayed on the official Second Life calendar.

Run using the existing GitHub Actions Playwright environment.
The JSON schema stays compatible with the Eventide SL frontend.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

SOURCE = 'https://secondlife.com/events/index.php?lang=en-US'
OUT = Path(__file__).resolve().parent / 'data' / 'events.json'
SLT = ZoneInfo('America/Los_Angeles')
MUSIC = re.compile(r'\b(live music|musician|singer|singing|pianist|piano|concert|guitar|jazz|blues|rock|band|karaoke|acoustic|live performer|live performance|classical|gig|music|live at|live show|live performance)\b', re.I)
DJ = re.compile(r'\b(dj|disc jockey|deejay|dj set)\b', re.I)
SPIRITUAL = re.compile(r'\b(prayer|devotion|worship|church|meditation|spiritual)\b', re.I)
SHOPPING = re.compile(r'\b(shop|shopping|collection|fashion|sale|market|boutique)\b', re.I)
ARTS = re.compile(r'\b(art|gallery|exhibition|photography|museum)\b', re.I)
EDUCATION = re.compile(r'\b(class|workshop|lecture|discussion|seminar)\b', re.I)

DATE = re.compile(r'(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)', re.I)


def category_for(title, venue='', description=''):
    text = ' '.join((title or '', venue or '', description or ''))
    # Use explicit clues, never invent a category for ambiguous events.
    if DJ.search(text):
        return 'Live DJ'
    if MUSIC.search(text) or re.search(r'\bLIVE\s+at\b', title, re.I):
        return 'Live Music'
    if SPIRITUAL.search(text):
        return 'Spiritual & Community'
    if SHOPPING.search(text):
        return 'Shopping & Fashion'
    if ARTS.search(text):
        return 'Arts & Culture'
    if EDUCATION.search(text):
        return 'Education & Talks'
    return 'Other Events'


def parse_date(text, now):
    match = DATE.search(text)
    if not match:
        return None
    month, day, hour, minute = (int(match.group(k)) for k in ('month', 'day', 'hour', 'minute'))
    if not 1 <= hour <= 12:
        return None
    hour = hour % 12 + (12 if match.group('ampm').upper() == 'PM' else 0)
    # The official calendar displays month/day without a year.
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            date = datetime(year, month, day, hour, minute, tzinfo=SLT)
        except ValueError:
            continue
        if now - timedelta(hours=2) <= date <= now + timedelta(days=370):
            return date
    return None


def extract(html, now=None):
    now = now or datetime.now(SLT)
    soup = BeautifulSoup(html, 'html.parser')
    collected = {}
    for table in soup.select('table'):
        rows = table.select('tr')
        if not any(all(word in row.get_text(' ', strip=True).lower() for word in ('when', 'what', 'where')) for row in rows[:3]):
            continue
        for row in rows:
            cells = row.find_all('td', recursive=False)
            if len(cells) < 3:
                continue
            when, title, venue = (cell.get_text(' ', strip=True) for cell in cells[:3])
            date = parse_date(when, now)
            if not date or not title:
                continue
            link = cells[1].find('a', href=True)
            url = urljoin(SOURCE, link['href']) if link else SOURCE
            if urlparse(url).scheme not in ('http', 'https'):
                url = SOURCE
            event = {
                'title': title, 'starts_at': date.isoformat(), 'venue': venue,
                'category': category_for(title, venue), 'description': '',
                'url': url, 'teleport_url': None,
            }
            key = (title.casefold(), date.isoformat(), venue.casefold())
            collected[key] = event
    return sorted(collected.values(), key=lambda event: (event['starts_at'], event['title'].casefold()))


OFFICIAL_CATEGORIES = {
    'arts and culture': 'Arts & Culture', 'arts & culture': 'Arts & Culture',
    'charity/support groups': 'Charity & Support Groups',
    'commercial': 'Commercial', 'discussion': 'Discussion', 'education': 'Education',
    'games/contests': 'Games & Contests', 'live dj': 'Live DJ',
    'live music': 'Live Music', 'miscellaneous': 'Miscellaneous',
    'nightlife/entertainment': 'Nightlife & Entertainment',
    'performances': 'Performances', 'spirituality': 'Spirituality', 'sports': 'Sports',
}


def detail_field(soup, field):
    """Find a labeled field in either a table or a definition-style detail block."""
    label = field.lower().rstrip(':')
    for cell in soup.select('td, th, dt, strong, b, label'):
        if cell.get_text(' ', strip=True).lower().rstrip(':') != label:
            continue
        if cell.name in ('td', 'th'):
            sibling = cell.find_next_sibling(['td', 'th'])
        elif cell.name == 'dt':
            sibling = cell.find_next_sibling('dd')
        else:
            sibling = cell.find_next_sibling()
            if sibling is None and cell.parent:
                sibling = cell.parent.find_next_sibling()
        if sibling:
            return sibling.get_text(' ', strip=True), sibling
    return '', None


def detail_image(soup, base_url):
    """Select the single event-detail photo, excluding logos, avatars and icons."""
    candidates = []
    for img in soup.select('img'):
        src = img.get('src') or img.get('data-src') or img.get('data-original')
        if not src:
            continue
        url = urljoin(base_url, src)
        if urlparse(url).scheme not in ('https', 'http'):
            continue
        hint = ' '.join([src, img.get('class', '') if isinstance(img.get('class'), str) else ' '.join(img.get('class', [])), img.get('alt', '')]).lower()
        if any(x in hint for x in ('logo', 'avatar', 'icon', 'spinner', 'badge', 'tracking', 'pixel')):
            continue
        width = int(img.get('width', 0)) if str(img.get('width', '')).isdigit() else 0
        height = int(img.get('height', 0)) if str(img.get('height', '')).isdigit() else 0
        if width and width < 100 or height and height < 100:
            continue
        parent = img.find_parent(['main', 'article', 'section', 'div'])
        parent_hint = ' '.join(parent.get('class', [])) if parent else ''
        score = (4 if re.search(r'event|detail|content|picture|photo', parent_hint, re.I) else 0)
        score += (3 if re.search(r'event|picture|photo|image', hint, re.I) else 0)
        score += (2 if width >= 180 or height >= 180 else 0)
        candidates.append((score, url))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1] if candidates[0][0] >= 2 or len(candidates) == 1 else None


def enrich_event(event):
    url = event['url']
    if url == SOURCE or urlparse(url).netloc not in ('secondlife.com', 'www.secondlife.com'):
        return event
    try:
        response = requests.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0 (compatible; EventideSL/1.0)'})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        category, _ = detail_field(soup, 'Category')
        if category:
            event['category'] = OFFICIAL_CATEGORIES.get(category.casefold(), category)
            event['official_category'] = True
        host, _ = detail_field(soup, 'Host')
        if host:
            event['host'] = host
        duration, _ = detail_field(soup, 'Duration')
        if duration:
            event['duration'] = duration
        location, node = detail_field(soup, 'Location')
        if location:
            event['venue'] = location
        if node:
            link = node.find('a', href=True)
            if link:
                teleport = urljoin(url, link['href'])
                if urlparse(teleport).scheme in ('https', 'http', 'secondlife'):
                    event['teleport_url'] = teleport
        # Venue images are verified separately against the public parcel name.
        # The official event description normally follows the detail fields and image.
        content = soup.select_one('.event-description, #event-description, .description, #description')
        if content:
            description = content.get_text(' ', strip=True)
            if description and len(description) < 10000:
                event['description'] = description
    except (requests.RequestException, ValueError) as exc:
        print(f"detail skipped for {url}: {type(exc).__name__}")
    return event


def enrich_events(events):
    # Parallel requests keep the scheduled sync reasonably fast. Failures do not hide events.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(enrich_event, event) for event in events]
        return [future.result() for future in futures]



# Public parcel photos. These endpoints are unofficial and may change.
# A venue name alone is not enough: accept only an exact parcel-name match.
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.I)
PARCEL_LINK = re.compile(r"(?:https?://)?world\.secondlife\.com/place/([0-9a-f-]{36})", re.I)
IMAGE_HOST = 'https://picture-service.secondlife.com'
BAD_IMAGE_HOSTS = ('cookielaw.org', 'onetrust.com', 'cookiebot.com')


def normalize_place(value):
    return re.sub(r'[^\w]+', ' ', (value or '').casefold(), flags=re.UNICODE).strip()


def photo_from_parcel(parcel_id, expected_venue, session):
    if not UUID_PATTERN.fullmatch(parcel_id):
        return None
    try:
        response = session.get(f'https://world.secondlife.com/place/{parcel_id}', timeout=12)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        meta = lambda key: soup.find('meta', attrs={'name': key})
        parcel_name = meta('parcel')
        # World API often leaves meta[name=parcel] empty. The visible page
        # heading/title is the parcel name in that case. Verify exact match.
        candidates = [parcel_name.get('content', '').strip() if parcel_name else '']
        heading = soup.find('h1')
        if heading:
            candidates.append(heading.get_text(' ', strip=True))
        if soup.title:
            candidates.append(soup.title.get_text(' ', strip=True))
        if normalize_place(expected_venue) not in {normalize_place(x) for x in candidates if x}:
            return None
        image = meta('imageid')
        uuid = image.get('content', '').strip() if image else ''
        if not UUID_PATTERN.fullmatch(uuid) or uuid == '00000000-0000-0000-0000-000000000000':
            return None
        return f'{IMAGE_HOST}/{uuid}/256x192.jpg'
    except requests.RequestException:
        return None


def venue_photo(venue, session):
    """Search public Second Life places, verify parcel name, then read imageid.

    Search API is unofficial and may not return any public results. No false images.
    """
    if not venue or len(venue.strip()) < 4:
        return None
    try:
        response = session.get('https://search.secondlife.com/', params={
            'query_term': venue, 'search_type': 'standard',
            'collection_chosen': 'places', 'maturity': 'g',
        }, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        # Only parcel UUIDs in actual search-result links, never arbitrary image UUIDs.
        ids = []
        for a in soup.select('a[href]'):
            match = PARCEL_LINK.search(a.get('href', ''))
            if match and match.group(1) not in ids:
                ids.append(match.group(1))
        for parcel_id in ids[:8]:
            photo = photo_from_parcel(parcel_id, venue, session)
            if photo:
                return photo
    except requests.RequestException:
        pass
    return None


def attach_venue_photos(events):
    """One lookup per distinct venue; reuse the photo across its events."""
    venues = sorted({e.get('venue', '').strip() for e in events if e.get('venue', '').strip()})
    def lookup(venue):
        with requests.Session() as session:
            session.headers.update({'User-Agent': 'Mozilla/5.0 (EventideSL public venue image lookup)'})
            return venue, venue_photo(venue, session)
    photos = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for venue, photo in pool.map(lookup, venues):
            if photo:
                photos[venue] = photo
    for event in events:
        # Don't overwrite a valid photo already associated with this venue.
        photo = photos.get(event.get('venue', '').strip())
        if photo:
            event['image_url'] = photo
            event['image_source'] = 'public_parcel_photo'
        else:
            # Avoid persisting a cookie banner or unverified image.
            old = event.get('image_url', '')
            if not old or any(host in old.lower() for host in BAD_IMAGE_HOSTS):
                event.pop('image_url', None)
    print(f'venue_photo_lookup: venues={len(venues)} verified_images={len(photos)} events_with_images={sum(bool(e.get("image_url")) for e in events)}')
    return events


def future_verified_events(previous):
    now = datetime.now(timezone.utc)
    result = []
    for event in previous.get('events', []):
        try:
            date = datetime.fromisoformat(event['starts_at'])
            if date.tzinfo is not None and date.astimezone(timezone.utc) > now and event.get('title'):
                result.append(event)
        except (ValueError, TypeError, KeyError):
            continue
    return result


def main():
    payload = {
        'status': 'unavailable', 'updated_at': datetime.now(timezone.utc).isoformat(),
        'source': SOURCE, 'events': [], 'message': '',
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            try:
                page = browser.new_page(locale='en-US', timezone_id='America/Los_Angeles')
                page.goto(SOURCE, wait_until='domcontentloaded', timeout=60000)
                try:
                    page.locator('table').filter(has_text='When').filter(has_text='Where').first.wait_for(timeout=30000)
                    page.wait_for_timeout(2500)
                except Exception:
                    pass
                events = extract(page.content())
                if events:
                    events = attach_venue_photos(enrich_events(events))
            finally:
                browser.close()
        if events:
            payload.update(status='ok', events=events)
        else:
            payload['message'] = 'Calendar loaded, but no verified dated event rows were found.'
    except Exception as exc:
        payload['message'] = f'Calendar retrieval failed: {type(exc).__name__}: {str(exc)[:220]}'

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if payload['status'] != 'ok' and OUT.exists():
        try:
            previous = json.loads(OUT.read_text(encoding='utf-8'))
            existing = future_verified_events(previous)
            if existing:
                payload['events'] = existing
                payload['message'] += ' Showing previously verified upcoming events.'
        except (OSError, ValueError, TypeError):
            pass
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"status={payload['status']} events={len(payload['events'])} message={payload['message']}")


if __name__ == '__main__':
    main()
