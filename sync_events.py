"""Collect only verifiable events displayed on the official Second Life calendar.

Run using the existing GitHub Actions Playwright environment.
The JSON schema stays compatible with the Eventide SL frontend.
"""
import json
import re
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
    'musique en direct': 'Live Music', 'musique live': 'Live Music',
    'arts et culture': 'Arts & Culture', 'associations caritatives et groupes de soutien': 'Charity & Support Groups',
    'jeux/concours': 'Games & Contests', 'jeux et concours': 'Games & Contests',
    'divers': 'Miscellaneous', 'vie nocturne/divertissement': 'Nightlife & Entertainment',
    'spectacles': 'Performances', 'éducation': 'Education',
}


def detail_field(soup, field):
    """Read detail labels in table cells, definition lists and inline text."""
    label = field.casefold()
    for cell in soup.select('td, th, dt, strong, b, label'):
        raw = cell.get_text(' ', strip=True)
        if raw.casefold().rstrip(':').strip() != label:
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
        if cell.parent:
            text = cell.parent.get_text(' ', strip=True)
            match = re.match(r'^' + re.escape(field) + r'\s*:\s*(.+)$', text, re.I)
            if match:
                return match.group(1).strip(), cell.parent
    # Some Second Life layouts use "Category: Live Music" in a single element.
    for node in soup.select('p, li, dd, div'):
        if node.find(['p', 'li', 'dd', 'div', 'table']):
            continue
        text = node.get_text(' ', strip=True)
        match = re.match(r'^' + re.escape(field) + r'\s*:\s*(.{1,150})$', text, re.I)
        if match:
            return match.group(1).strip(), node
    return '', None


def detail_image(soup, base_url):
    """Find a plausible event/venue picture, never a cookie banner or UI icon.

    Prefer an image in the event content. If the page has no identifiable
    picture, return None rather than publishing an unrelated site asset.
    """
    blocked_hosts = ('cookielaw.org', 'onetrust.com', 'cookiebot.com',
                     'googletagmanager.com', 'google-analytics.com')
    blocked_words = ('cookie', 'onetrust', 'ot_close', 'logo', 'avatar',
                     'icon', 'spinner', 'badge', 'tracking', 'pixel',
                     'button', 'favicon', 'social', 'facebook', 'twitter',
                     'youtube', 'instagram', 'banner-consent')

    def valid_url(raw):
        if not raw or raw.startswith(('data:', 'blob:')):
            return None
        url = urljoin(base_url, raw.strip())
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return None
        host = (parsed.hostname or '').lower()
        if any(host == name or host.endswith('.' + name) for name in blocked_hosts):
            return None
        if any(word in (parsed.path + '?' + parsed.query).lower() for word in blocked_words):
            return None
        if parsed.path.lower().endswith('.svg'):
            return None
        return url

    candidates = []
    for img in soup.select('img'):
        raw = img.get('data-src') or img.get('data-original') or img.get('src')
        if not raw and img.get('srcset'):
            raw = img['srcset'].split(',')[-1].strip().split(' ')[0]
        url = valid_url(raw)
        if not url:
            continue
        hint = ' '.join([str(raw), ' '.join(img.get('class', [])),
                         img.get('alt', ''), img.get('id', '')]).lower()
        if any(word in hint for word in blocked_words):
            continue
        width = int(img.get('width', 0)) if str(img.get('width', '')).isdigit() else 0
        height = int(img.get('height', 0)) if str(img.get('height', '')).isdigit() else 0
        if (width and width < 120) or (height and height < 100):
            continue
        # A picture should be associated with event details, not site chrome.
        context = ' '.join(' '.join(ancestor.get('class', [])) + ' ' + ancestor.get('id', '')
                           for ancestor in list(img.parents)[:5] if getattr(ancestor, 'attrs', None))
        context = context.lower()
        if any(word in context for word in ('cookie', 'onetrust', 'footer', 'header', 'navbar', 'navigation')):
            continue
        score = 0
        if re.search(r'event|detail|location|venue|picture|photo|image', context):
            score += 5
        if re.search(r'event|location|venue|picture|photo', hint):
            score += 4
        if width >= 180 and height >= 120:
            score += 3
        if img.find_parent(['main', 'article']):
            score += 2
        if score >= 3:
            candidates.append((score, url))

    for node in soup.select('[style*="background-image"]'):
        style = node.get('style', '')
        match = re.search(r'background-image\s*:\s*url\(\s*["\']?([^\)"\']+)', style, re.I)
        if not match:
            continue
        url = valid_url(match.group(1))
        if not url:
            continue
        context = (' '.join(node.get('class', [])) + ' ' + node.get('id', '')).lower()
        if any(word in context for word in blocked_words):
            continue
        if re.search(r'event|location|venue|detail|photo|image', context):
            candidates.append((6, url))

    # Social previews are deliberately excluded: they may point to generic
    # Second Life or cookie-consent images instead of the event picture.
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def enrich_event(event, html):
    """Enrich from the same browser session that loaded the public calendar."""
    soup = BeautifulSoup(html, 'html.parser')
    url = event['url']
    category, _ = detail_field(soup, 'Category')
    if category:
        category = re.sub(r'\s+', ' ', category).strip()
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
    found_image = detail_image(soup, url)
    if found_image:
        event['image_url'] = found_image
    content = soup.select_one('.event-description, #event-description, .description, #description')
    if content:
        description = content.get_text(' ', strip=True)
        if description and len(description) < 10000:
            event['description'] = description
    return event


def enrich_events(browser, events):
    """Use Chromium for details too: HTTP requests may receive a different page."""
    page = browser.new_page(locale='en-US', timezone_id='America/Los_Angeles')
    images = 0
    official = 0
    for index, event in enumerate(events, 1):
        url = event['url']
        if url == SOURCE or urlparse(url).netloc not in ('secondlife.com', 'www.secondlife.com'):
            continue
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=20000)
            page.wait_for_timeout(450)
            enrich_event(event, page.content())
            if event.get('image_url'):
                images += 1
            if event.get('official_category'):
                official += 1
        except Exception as exc:
            print(f"detail {index}/{len(events)} failed: {url} ({type(exc).__name__})")
    page.close()
    print(f"detail enrichment: {official}/{len(events)} official categories, {images}/{len(events)} images")
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
                    events = enrich_events(browser, events)
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