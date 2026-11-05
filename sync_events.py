"""Collect only verifiable events displayed on the official Second Life calendar.

Run using the existing GitHub Actions Playwright environment.
The JSON schema stays compatible with the Eventide SL frontend.
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
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

LOGIN_URL = ('https://id.secondlife.com/openid/login?'
             'return_to=https%3A%2F%2Fsecondlife.com%2Fauth%2Foid_return.php'
             '%3Fredirect%3D%252Fmy&language=en-US')

DATE = re.compile(r'(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)', re.I)


def category_for(title, venue='', description=''):
    """Unknown categories are not inferred from title, venue, or description."""
    return 'Miscellaneous'


def normalize_official_category(value):
    """Return only recognized official event categories in English."""
    value = re.sub(r'\s+', ' ', str(value or '')).strip().rstrip(':')
    return OFFICIAL_CATEGORIES.get(value.casefold())


def category_from_row(row):
    """Read explicit category metadata, never keyword-match an event title."""
    category_id = row.get('data-category-id')
    if category_id in OFFICIAL_CATEGORY_IDS:
        return OFFICIAL_CATEGORY_IDS[category_id]
    for node in [row] + row.select('[data-category], [data-event-category]'):
        for attribute in ('data-category', 'data-event-category'):
            result = normalize_official_category(node.get(attribute))
            if result:
                return result
    for node in row.select('.event-category, .category, [itemprop="eventType"]'):
        result = normalize_official_category(node.get_text(' ', strip=True))
        if result:
            return result
    return None


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


def extract(html, now=None, requested_day=None):
    """Read event tables from the public calendar or its upcoming.php fragment."""
    now = now or datetime.now(SLT)
    soup = BeautifulSoup(html, 'html.parser')
 


    collected = {}
    tables = soup.select('table#event_table, table#event-table, table.event-table')
    if not tables:
        tables = [t for t in soup.select('table') if any(
            all(w in row.get_text(' ', strip=True).lower() for w in ('when', 'what', 'where'))
            for row in t.select('tr')[:4]
        )]
    for table in tables:
        headings = [th.get_text(' ', strip=True).lower() for th in table.select('thead th')]
        if not headings:
            header = table.find('tr')
            headings = [c.get_text(' ', strip=True).lower() for c in header.find_all(['th', 'td'], recursive=False)] if header else []
        def column(words, fallback):
            return next((i for i, name in enumerate(headings) if any(w in name for w in words)), fallback)
        when_i, title_i, venue_i = column(('when', 'time', 'date'), 0), column(('what', 'event', 'title'), 1), column(('where', 'location', 'venue'), 2)
        for row in table.select('tr'):
            cells = row.find_all('td', recursive=False)
            if len(cells) <= max(when_i, title_i, venue_i):
                continue
            when, title, venue = (cells[i].get_text(' ', strip=True) for i in (when_i, title_i, venue_i))
            date = parse_date(when, now)
            if not date and requested_day:
                # Daily responses sometimes show just the time, not MM/DD.
                tm = re.search(r'\b(1[0-2]|0?[1-9]):([0-5]\d)\s*(AM|PM)\b', when, re.I)
                if tm:
                    hour = int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == 'PM' else 0)
                    date = datetime(requested_day.year, requested_day.month, requested_day.day, hour, int(tm.group(2)), tzinfo=SLT)
            if not date or not title:
                continue
            if requested_day and date.date() != requested_day:
                continue
            link = cells[title_i].find('a', href=True)
            url = urljoin(SOURCE, link['href']) if link else SOURCE
            if urlparse(url).scheme not in ('http', 'https'):
                url = SOURCE
            event = {'title': title, 'starts_at': date.isoformat(), 'venue': venue,
                     'category': category_from_row(row) or 'Miscellaneous', 'description': '',
                     'url': url, 'teleport_url': None}
            event['official_category'] = bool(category_from_row(row))
            key = (title.casefold(), date.isoformat(), venue.casefold())
            collected[key] = event

    return sorted(
        collected.values(),
        key=lambda e: (e['starts_at'], e['title'].casefold())
    )

# Numeric IDs are supplied by the official calendar's category selector.
OFFICIAL_CATEGORY_IDS = {
    '27': 'Arts & Culture', '28': 'Charity & Support Groups',
    '22': 'Commercial', '18': 'Discussion', '26': 'Education',
    '24': 'Games & Contests', '30': 'Live DJ', '20': 'Live Music',
    '29': 'Miscellaneous', '23': 'Nightlife & Entertainment',
    '25': 'Pageants', '31': 'Spirituality', '19': 'Sports',
}

OFFICIAL_CATEGORIES = {
    'arts and culture': 'Arts & Culture', 'arts & culture': 'Arts & Culture',
    'charity/support groups': 'Charity & Support Groups',
    'commercial': 'Commercial', 'discussion': 'Discussion', 'education': 'Education',
    'games/contests': 'Games & Contests', 'live dj': 'Live DJ',
    'live music': 'Live Music', 'miscellaneous': 'Miscellaneous',
    'nightlife/entertainment': 'Nightlife & Entertainment',
    'performances': 'Performances', 'spirituality': 'Spirituality', 'sports': 'Sports',
    'pageants': 'Pageants',
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
        verified = normalize_official_category(category)
        if verified:
            event['category'] = verified
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



def retrieve_official_categories(events, page, previous=None):
    """Verify category from official event detail pages using the signed-in session.

    Only the category label is read. Do not copy descriptions or photographs.
    Failed lookups remain Miscellaneous and are not guessed from titles.
    """
    previous = previous or {}
    cached = {}
    for old in previous.get('events', []):
        if old.get('official_category') is True:
            category = normalize_official_category(old.get('category'))
            if category:
                cached[old.get('url')] = category

    unresolved = {}
    verified_rows = 0
    cached_rows = 0
    for event in events:
        existing = normalize_official_category(event.get('category'))
        if event.get('official_category') is True and existing:
            verified_rows += 1
            continue
        category = cached.get(event.get('url'))
        if category:
            event['category'] = category
            event['official_category'] = True
            cached_rows += 1
            continue
        event['category'] = 'Miscellaneous'
        event['official_category'] = False
        url = event.get('url', '')
        parsed = urlparse(url)
        if (parsed.scheme == 'https'
                and parsed.hostname in ('secondlife.com', 'www.secondlife.com')
                and parsed.path.endswith('/event.php')
                and re.search(r'(?:^|&)id=\d+(?:&|$)', parsed.query)):
            unresolved.setdefault(url, []).append(event)

    # Requests sessions cannot safely share a Playwright request context across
    # threads. Export the session cookies in memory only; never print or save them.
    cookie_data = page.context.cookies(['https://secondlife.com'])

    def fetch(item):
        url, matches = item
        try:
            with requests.Session() as session:
                session.headers.update({'User-Agent': 'Mozilla/5.0',
                                        'Referer': SOURCE})
                for cookie in cookie_data:
                    session.cookies.set(cookie['name'], cookie['value'],
                                        domain=cookie.get('domain', 'secondlife.com'),
                                        path=cookie.get('path', '/'))
                response = session.get(url, timeout=12, allow_redirects=True)
                if response.status_code != 200:
                    return url, None
                if urlparse(response.url).hostname not in ('secondlife.com', 'www.secondlife.com'):
                    return url, None
                soup = BeautifulSoup(response.text, 'html.parser')
                value, _ = detail_field(soup, 'Category')
                category = normalize_official_category(value)
                if not category:
                    for node in soup.select('[data-event-category], [data-category]'):
                        category = normalize_official_category(
                            node.get('data-event-category') or node.get('data-category'))
                        if category:
                            break
                return url, category
        except (requests.RequestException, ValueError):
            return url, None

    found = 0
    if unresolved:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for url, category in pool.map(fetch, unresolved.items()):
                if category:
                    for event in unresolved[url]:
                        event['category'] = category
                        event['official_category'] = True
                        found += 1
    print('official_categories:',
          f'verified_in_list={verified_rows}',
          f'cached={cached_rows}',
          f'verified_from_details={found}',
          f'unknown={sum(not e.get("official_category") for e in events)}',
          f'detail_pages_checked={len(unresolved)}')
    return events

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


def collect_calendar_pages(page, max_rounds=60):
    """Collect rows from dynamically loaded calendar pages; stop when no new rows appear."""
    found = {}
    seen_states = set()
    for round_number in range(max_rounds):
        for event in extract(page.content()):
            key = (event['title'].casefold(), event['starts_at'], event['venue'].casefold())
            found[key] = event
        before = len(found)
        # Some calendar implementations append results as the user scrolls.
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        page.wait_for_timeout(1300)
        for event in extract(page.content()):
            key = (event['title'].casefold(), event['starts_at'], event['venue'].casefold())
            found[key] = event
        if len(found) > before:
            continue
        # Others use a next-page or load-more button.
        moved = False
        for selector in ('button:has-text("Load more")', 'button:has-text("Show more")',
                         'a:has-text("Next")', 'button:has-text("Next")',
                         '[aria-label="Next page"]', '[rel="next"]'):
            loc = page.locator(selector)
            try:
                if loc.count() and loc.first.is_visible() and loc.first.is_enabled():
                    loc.first.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    moved = True
                    break
            except Exception:
                pass
        state = (len(found), page.url)
        if not moved or state in seen_states:
            break
        seen_states.add(state)
    print(f'calendar_collection: pages_or_scroll_rounds={round_number + 1} events={len(found)}')
    return sorted(found.values(), key=lambda e: (e['starts_at'], e['title'].casefold()))


def collect_dates(days=90):
    """Query each date and report exactly why a date returned no parsed events."""
    start = datetime.now(SLT).date()
    dates = [start + timedelta(days=n) for n in range(days)]
    def fetch_day(day):
        url = 'https://secondlife.com/events/upcoming.php'
        try:
            response = requests.get(url, params={
                'lang': 'en-US', 'per_page': 200, 'date': day.isoformat()
            }, timeout=25, headers={'User-Agent': 'Mozilla/5.0', 'Referer': SOURCE})
            response.raise_for_status()
            reference = datetime(day.year, day.month, day.day, tzinfo=SLT)
            items = extract(response.text, now=reference, requested_day=day)
            if not items:
                soup = BeautifulSoup(response.text, 'html.parser')
                tables = soup.select('table')
                first_row = next((r.get_text(' ', strip=True)[:180] for t in tables for r in t.select('tr') if r.find('td')), '')
                reason = f'no rows parsed; tables={len(tables)} first_row={first_row!r} bytes={len(response.content)} final_url={response.url}'
                return day.isoformat(), [], reason
            return day.isoformat(), items, None
        except Exception as exc:
            return day.isoformat(), [], f'{type(exc).__name__}: {str(exc)[:180]}'
    unique, errors, populated = {}, [], 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for day, items, error in pool.map(fetch_day, dates):
            if error:
                errors.append(day + ': ' + error)
            if items:
                populated += 1
            for event in items:
                key = (event['url'], event['starts_at'], event['title'].casefold())
                unique[key] = event
    print(f'daily_calendar: dates={len(dates)} days_with_events={populated} events={len(unique)} empty_or_errors={len(errors)}')
    for error in errors[:8]:
        print('daily_calendar_diagnostic:', error)
    return list(unique.values())



def login_secondlife(page):
    """Attempt normal login. No CAPTCHA/2FA bypass and no secrets in logs."""
    username = os.environ.get('SL_USERNAME', '').strip()
    password = os.environ.get('SL_PASSWORD', '')
    if not username or not password:
        print('sl_login: credentials_not_configured')
        return False
    try:
        page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=45000)
        if urlparse(page.url).hostname not in ('id.secondlife.com', 'secondlife.com', 'www.secondlife.com'):
            print('sl_login: unexpected_host')
            return False
        def visible(selectors):
            for selector in selectors:
                item = page.locator(selector).first
                if item.count() and item.is_visible():
                    return item
            return None
        username_field = visible(['input[name="first_name"]', 'input[name="username"]',
                                  'input[name="login"]', 'input[autocomplete="username"]',
                                  'input[type="text"]'])
        password_field = visible(['input[name="password"]', 'input[type="password"]'])
        if username_field is None or password_field is None:
            print('sl_login: fields_not_found')
            return False
        username_field.fill(username)
        password_field.fill(password)
        submit = visible(['button[type="submit"]', 'input[type="submit"]',
                          'button:has-text("Log In")', 'button:has-text("Sign In")'])
        if submit is None:
            print('sl_login: submit_not_found')
            return False
        submit.click(timeout=15000)
        try:
            page.wait_for_url(re.compile(r'https://(?:www\.)?secondlife\.com/(?:my|auth)/.*'), timeout=20000)
        except Exception:
            pass
        page.goto('https://secondlife.com/my/', wait_until='domcontentloaded', timeout=45000)
        host = urlparse(page.url).hostname
        body = page.locator('body').inner_text(timeout=10000)[:3000].lower()
        probable = (host in ('secondlife.com', 'www.secondlife.com')
                    and not re.search(r'/(?:openid/login|login)(?:[/?]|$)', page.url)
                    and 'sign in to second life' not in body
                    and 'log in to second life' not in body)
        print('sl_login:', 'possible_authenticated' if probable else 'not_confirmed')
        return probable
    except Exception as exc:
        print('sl_login: failed', type(exc).__name__)
        return False


def explicit_maturity(row):
    """Only trust an explicit maturity label on the event row itself."""
    labels = []
    for node in [row] + row.select('[title], [aria-label], [data-maturity], [data-rating]'):
        for attr in ('data-maturity', 'data-rating', 'title', 'aria-label'):
            value = node.get(attr)
            if value:
                labels.append(str(value))
    labels.extend(x.get_text(' ', strip=True) for x in row.select('.maturity, .rating, .event-maturity'))
    raw = ' '.join(labels)
    if re.search(r'\b(adult|mature-adult|18\+)\b', raw, re.I):
        return 'Adult'
    if re.search(r'\b(moderate|mature)\b', raw, re.I):
        return 'Moderate'
    if re.search(r'\b(general|pg)\b', raw, re.I):
        return 'General'
    return None


def official_next_page(soup, current_url, day):
    """Follow only an explicit same-day pagination link from the official listing.

    Do not guess undocumented page parameters or traverse to other dates.
    """
    candidates = soup.select('a[rel="next"][href], a.next[href], a.qp_prev[href], .pagination a[href], .pager a[href]')
    for link in candidates:
        label = link.get_text(' ', strip=True).lower()
        rel = link.get('rel', [])
        classes = link.get('class', [])
        if not (('next' in rel) or ('next' in classes) or ('qp_prev' in classes) or
                label in ('next', 'next page', '>', '›', '»')):
            continue
        target = urljoin(current_url, link['href'])
        parsed = urlparse(target)
        if parsed.scheme != 'https' or parsed.hostname not in ('secondlife.com', 'www.secondlife.com'):
            continue
        if parsed.path != '/events/upcoming.php':
            continue
        params = parse_qs(parsed.query)
        if params.get('date', [day.isoformat()])[0] != day.isoformat():
            continue
        if 'date' not in params:
            params['date'] = [day.isoformat()]
            target = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
        if target != current_url:
            return target
    return None


def collect_authenticated_offset_pages(page, days=90, max_pages=150):
    """Traverse the official upcoming.php offset links as observed in the browser.

    The paginator uses .qp_prev for the next chronological batch despite its
    misleading class name. Stop at the official end or on repeated content.
    """
    start = datetime.now(SLT)
    end = start + timedelta(days=days)
    current_url = 'https://secondlife.com/events/upcoming.php?lang=en-US&per_page=15'
    seen_urls, seen_signatures, collected = set(), set(), {}
    pages = 0
    while current_url and current_url not in seen_urls and pages < max_pages:
        seen_urls.add(current_url)
        try:
            response = page.request.get(current_url, timeout=30000)
            if not response.ok:
                print(f'offset_calendar: stopped_http={response.status} pages={pages}')
                break
            soup = BeautifulSoup(response.text(), 'html.parser')
            rows = soup.select('table#event_table tr[data-event-id]')
            signature = tuple(row.get('data-event-id') for row in rows)
            if not signature or signature in seen_signatures:
                break
            seen_signatures.add(signature)
            pages += 1
            for event in extract(str(soup), now=start):
                when = datetime.fromisoformat(event['starts_at'])
                if start - timedelta(hours=2) <= when < end:
                    key = (event['url'], event['starts_at'], event['title'].casefold())
                    collected[key] = event
            # The captured HTML calls the forward chronological link qp_prev.
            link = soup.select_one('#WidgetUpcomingEventsPagination a.qp_prev[href]')
            if link is None:
                break
            target = urljoin(current_url, link['href'])
            parsed = urlparse(target)
            if parsed.scheme != 'https' or parsed.hostname not in ('secondlife.com', 'www.secondlife.com') or parsed.path != '/events/upcoming.php':
                break
            params = parse_qs(parsed.query)
            if not params.get('offset', [''])[0].isdigit():
                break
            current_url = target
        except Exception as exc:
            print('offset_calendar: stopped_exception=' + type(exc).__name__)
            break
    print(f'offset_calendar: pages={pages} events={len(collected)}')
    return list(collected.values())


def collect_authenticated_dates(page, days=90):
    """Read all explicitly linked listing pages for each day, without guessing dates."""
    start = datetime.now(SLT).date()
    collected = {}
    responses_ok = rows_seen = parsed_count = failures = missing_dates = 0
    pagination_pages = 0
    capped_days = 0
    consecutive_failures = 0
    browser_mode = False
    MAX_PAGES_PER_DAY = 12

    for offset in range(days):
        day = start + timedelta(days=offset)
        first_url = ('https://secondlife.com/events/upcoming.php?'
                     + urlencode({'lang': 'en-US', 'per_page': 200, 'date': day.isoformat()}))
        current_url = first_url
        seen_urls = set()
        seen_signatures = set()
        day_rows = day_parsed = day_pages = 0
        day_failed = False

        while current_url and day_pages < MAX_PAGES_PER_DAY and current_url not in seen_urls:
            seen_urls.add(current_url)
            try:
                response = None
                if not browser_mode:
                    response = page.request.get(current_url, timeout=30000)
                if browser_mode or (response is not None and response.status == 403 and offset == 0 and day_pages == 0):
                    # One normal navigation is permitted. Stop on denial; no bypass.
                    browser_response = page.goto(current_url, wait_until='domcontentloaded', timeout=30000)
                    if browser_response and browser_response.status == 200:
                        if not browser_mode:
                            print('authenticated_diagnostic: browser_navigation_available')
                        browser_mode = True
                        status = 200
                        body = page.content()
                    else:
                        print('authenticated_diagnostic: access_denied_browser_too')
                        status = browser_response.status if browser_response else 0
                        body = None
                else:
                    status = response.status if response else 0
                    body = response.text() if response and response.ok else None

                if body is None:
                    failures += 1
                    day_failed = True
                    if failures <= 3:
                        print(f'authenticated_diagnostic: day_offset={offset} http_status={status}')
                    break

                soup = BeautifulSoup(body, 'html.parser')
                tables = soup.select('table#event_table, table#event-table, table.event-table')
                rows = [row for table in tables for row in table.select('tr')
                        if len(row.find_all('td', recursive=False)) >= 3]
                # Prevent looping over the same 20 results through different URLs.
                signature = tuple((row.get('data-event-id') or
                                   (row.select_one('[data-event-id]') or {}).get('data-event-id') or
                                   row.get_text(' ', strip=True)) for row in rows)
                if signature and signature in seen_signatures:
                    print(f'authenticated_diagnostic: repeated_page day_offset={offset} page={day_pages + 1}')
                    break
                seen_signatures.add(signature)
                responses_ok += 1
                day_pages += 1
                if day_pages > 1:
                    pagination_pages += 1
                reference = datetime(day.year, day.month, day.day, tzinfo=SLT)

                for table in tables:
                    header = table.select_one('thead') or table.find('tr')
                    for row in table.select('tr'):
                        if len(row.find_all('td', recursive=False)) < 3:
                            continue
                        rows_seen += 1
                        day_rows += 1
                        fragment = ('<table class="event-table">'
                                    + (str(header) if header else '')
                                    + str(row) + '</table>')
                        for event in extract(fragment, now=reference, requested_day=day):
                            event_id = row.get('data-event-id')
                            if not event_id:
                                node = row.select_one('[data-event-id]')
                                event_id = node.get('data-event-id') if node else None
                            if event_id and re.fullmatch(r'\d+', str(event_id)):
                                event['url'] = ('https://secondlife.com/my/community/events/'
                                                f'event.php?id={event_id}')
                            elif event['url'] == SOURCE:
                                continue
                            event['description'] = ''
                            event.pop('image_url', None)
                            event['teleport_url'] = None
                            key = (event['url'], event['starts_at'], event['title'].casefold())
                            collected[key] = event
                            parsed_count += 1
                            day_parsed += 1
                current_url = official_next_page(soup, current_url, day)
            except Exception as exc:
                failures += 1
                day_failed = True
                if failures <= 3:
                    print(f'authenticated_diagnostic: day_offset={offset} exception={type(exc).__name__}')
                break

        if day_failed:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                print('authenticated_diagnostic: stopped_after_three_consecutive_failures')
                break
        else:
            consecutive_failures = 0
        if day_rows == 20 and day_pages == 1:
            capped_days += 1
        if day_rows and not day_parsed:
            missing_dates += 1
        if offset < 3:
            print('authenticated_diagnostic:', f'day_offset={offset}',
                  f'pages={day_pages}', f'rows={day_rows}', f'parsed_for_day={day_parsed}')

    print('pagination_diagnostic:', f'extra_pagination_pages={pagination_pages}', f'possible_capped_days={capped_days}',
          'note=20-row days may be incomplete without a discoverable next page')
    print('authenticated_diagnostic:', f'responses_ok={responses_ok}',
          f'rows={rows_seen}', f'parsed={parsed_count}',
          f'no_matching_dates={missing_dates}', f'failures={failures}')
    print(f'authenticated_calendar: unique_events={len(collected)}')
    return list(collected.values())



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
                events = collect_calendar_pages(page)
                daily = collect_dates(days=90)
                authenticated = []
                offset_events = []
                if login_secondlife(page):
                    offset_events = collect_authenticated_offset_pages(page, days=90)
                    authenticated = collect_authenticated_dates(page, days=90)
                merged = {}
                for event in events + daily + authenticated + offset_events:
                    key = (event['url'], event['starts_at'], event['title'].casefold())
                    prior = merged.get(key)
                    if prior and prior.get('official_category') and not event.get('official_category'):
                        event['category'] = prior['category']
                        event['official_category'] = True
                    merged[key] = event
                events = list(merged.values())
                events = sorted(events, key=lambda e: (e['starts_at'], e['title']))
                # Official event categories only. Never guess from the event title.
                previous_for_categories = {}
                if OUT.exists():
                    try:
                        previous_for_categories = json.loads(OUT.read_text(encoding='utf-8'))
                    except (OSError, ValueError, TypeError):
                        pass
                events = retrieve_official_categories(events, page, previous_for_categories)
                print(f'calendar_total: {len(events)} events across 90 queried dates')
                # Detail and venue requests can be very expensive for large feeds.
                # Prioritize complete event coverage over optional image enrichment.
                if events and len(events) <= 100:
                    events = attach_venue_photos(enrich_events(events))
            finally:
                browser.close()
        if events:
            payload.update(status='ok', events=events)
        else:
            payload['message'] = 'Calendar loaded, but no verified dated event rows were found.'
    except Exception as exc:
        payload['message'] = f'Calendar retrieval failed: {type(exc).__name__}: {str(exc)[:220]}'

    # Never discard known future events just because a daily request failed.
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text(encoding='utf-8'))
            old = future_verified_events(previous)
            for old_event in old:
                if old_event.get('official_category') is not True:
                    old_event['category'] = 'Miscellaneous'
                    old_event['official_category'] = False
            merged = {(e.get('url'), e.get('starts_at'), e.get('title', '').casefold()): e for e in old}
            for e in payload['events']:
                key = (e.get('url'), e.get('starts_at'), e.get('title', '').casefold())
                old_event = merged.get(key, {})
                # A failed detail lookup must not erase a previously verified
                # official category or downgrade it to Miscellaneous.
                if old_event.get('official_category') is True and not e.get('official_category'):
                    previous_category = normalize_official_category(old_event.get('category'))
                    if previous_category:
                        e['category'] = previous_category
                        e['official_category'] = True
                merged[key] = {**old_event, **e}
            payload['events'] = sorted(merged.values(), key=lambda e: (e['starts_at'], e['title']))
            if payload['events'] and payload['status'] != 'ok':
                payload['message'] += ' Previously collected future events retained.'
        except (OSError, ValueError, TypeError):
            pass
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
    print('retained_events:', f'final_count={len(payload["events"])}', f'official_categories={sum(e.get("official_category") is True for e in payload["events"])}')
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"status={payload['status']} events={len(payload['events'])} message={payload['message']}")


if __name__ == '__main__':
    main()
