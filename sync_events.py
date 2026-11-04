
"""Sync official Second Life events for Eventide SL."""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


SOURCE = "https://secondlife.com/events/index.php?lang=en-US"
UPCOMING = "https://secondlife.com/events/upcoming.php"
OUT = Path(__file__).resolve().parent / "data" / "events.json"
SLT = ZoneInfo("America/Los_Angeles")

LOGIN_URL = (
    "https://id.secondlife.com/openid/login?"
    "return_to=https%3A%2F%2Fsecondlife.com%2Fauth%2Foid_return.php"
    "%3Fredirect%3D%252Fmy&language=en-US"
)

DATE_RE = re.compile(
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*"
    r"(?P<ampm>AM|PM)",
    re.I,
)

TIME_RE = re.compile(
    r"\b(1[0-2]|0?[1-9]):([0-5]\d)\s*(AM|PM)\b",
    re.I,
)

CATEGORIES = {
    "arts and culture": "Arts & Culture",
    "arts & culture": "Arts & Culture",
    "charity/support groups": "Charity & Support Groups",
    "commercial": "Commercial",
    "discussion": "Discussion",
    "education": "Education",
    "games/contests": "Games & Contests",
    "live dj": "Live DJ",
    "live music": "Live Music",
    "miscellaneous": "Miscellaneous",
    "nightlife/entertainment": "Nightlife & Entertainment",
    "performances": "Performances",
    "spirituality": "Spirituality",
    "sports": "Sports",
}


def category_for(title, venue=""):
    text = f"{title} {venue}".lower()

    patterns = (
        (r"\b(dj|deejay|disc jockey)\b", "Live DJ"),
        (
            r"\b(music|concert|singer|piano|pianist|"
            r"jazz|blues|rock|band|karaoke|guitar)\b",
            "Live Music",
        ),
        (
            r"\b(church|prayer|meditation|worship)\b",
            "Spiritual & Community",
        ),
        (
            r"\b(shopping|fashion|boutique|market)\b",
            "Shopping & Fashion",
        ),
        (
            r"\b(gallery|exhibition|photography|museum)\b",
            "Arts & Culture",
        ),
        (
            r"\b(workshop|lecture|seminar|class)\b",
            "Education & Talks",
        ),
    )

    for pattern, category in patterns:
        if re.search(pattern, text, re.I):
            return category

    return "Other Events"


def parse_date(text, now, requested_day=None):
    match = DATE_RE.search(text)

    if match:
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))

        if not 1 <= hour <= 12:
            return None

        hour = hour % 12
        if match.group("ampm").upper() == "PM":
            hour += 12

        for year in (now.year - 1, now.year, now.year + 1):
            try:
                date = datetime(
                    year, month, day, hour, minute, tzinfo=SLT
                )
            except ValueError:
                continue

            if requested_day:
                if date.date() == requested_day:
                    return date
            elif now - timedelta(hours=2) <= date <= (
                now + timedelta(days=370)
            ):
                return date

    if requested_day:
        match = TIME_RE.search(text)

        if match:
            hour = int(match.group(1)) % 12
            minute = int(match.group(2))

            if match.group(3).upper() == "PM":
                hour += 12

            return datetime(
                requested_day.year,
                requested_day.month,
                requested_day.day,
                hour,
                minute,
                tzinfo=SLT,
            )

    return None


def event_key(event):
    return (
        event.get("url", ""),
        event.get("starts_at", ""),
        event.get("title", "").casefold(),
    )


def extract(html, now=None, requested_day=None):
    now = now or datetime.now(SLT)
    soup = BeautifulSoup(html, "html.parser")
    collected = {}

    tables = soup.select("table#event-table, table.event-table")

    if not tables:
        tables = [
            table
            for table in soup.select("table")
            if any(
                all(
                    word in row.get_text(" ", strip=True).lower()
                    for word in ("when", "what", "where")
                )
                for row in table.select("tr")[:4]
            )
        ]

    for table in tables:
        headings = [
            th.get_text(" ", strip=True).lower()
            for th in table.select("thead th")
        ]

        if not headings:
            header = table.find("tr")
            headings = (
                [
                    cell.get_text(" ", strip=True).lower()
                    for cell in header.find_all(
                        ["th", "td"], recursive=False
                    )
                ]
                if header
                else []
            )

        def column(words, fallback):
            return next(
                (
                    i
                    for i, heading in enumerate(headings)
                    if any(word in heading for word in words)
                ),
                fallback,
            )

        when_i = column(("when", "time", "date"), 0)
        title_i = column(("what", "event", "title"), 1)
        venue_i = column(("where", "location", "venue"), 2)

        for row in table.select("tr"):
            cells = row.find_all("td", recursive=False)

            if len(cells) <= max(when_i, title_i, venue_i):
                continue

            when = cells[when_i].get_text(" ", strip=True)
            title = cells[title_i].get_text(" ", strip=True)
            venue = cells[venue_i].get_text(" ", strip=True)

            date = parse_date(when, now, requested_day)

            if not date or not title:
                continue

            if requested_day and date.date() != requested_day:
                continue

            link = cells[title_i].find("a", href=True)
            url = (
                urljoin(SOURCE, link["href"])
                if link
                else SOURCE
            )

            if urlparse(url).scheme not in ("http", "https"):
                url = SOURCE

            event = {
                "title": title,
                "starts_at": date.isoformat(),
                "venue": venue,
                "category": category_for(title, venue),
                "description": "",
                "url": url,
                "teleport_url": None,
            }

            collected[event_key(event)] = event

    return sorted(
        collected.values(),
        key=lambda e: (
            e["starts_at"],
            e["title"].casefold(),
        ),
    )


def explicit_maturity(row):
    labels = []

    nodes = [row] + row.select(
        "[title], [aria-label], [data-maturity], [data-rating]"
    )

    for node in nodes:
        for attr in (
            "data-maturity",
            "data-rating",
            "title",
            "aria-label",
        ):
            value = node.get(attr)
            if value:
                labels.append(str(value))

    for node in row.select(
        ".maturity, .rating, .event-maturity"
    ):
        labels.append(node.get_text(" ", strip=True))

    raw = " ".join(labels)

    if re.search(r"\b(adult|18\+)\b", raw, re.I):
        return "Adult"

    if re.search(r"\b(moderate|mature)\b", raw, re.I):
        return "Moderate"

    if re.search(r"\b(general|pg)\b", raw, re.I):
        return "General"

    return None


def collect_calendar_pages(page, max_rounds=60):
    found = {}
    seen = set()

    for round_number in range(max_rounds):
        for event in extract(page.content()):
            found[event_key(event)] = event

        before = len(found)

        page.evaluate(
            "window.scrollTo(0, document.body.scrollHeight)"
        )
        page.wait_for_timeout(1300)

        for event in extract(page.content()):
            found[event_key(event)] = event

        if len(found) > before:
            continue

        moved = False

        selectors = (
            'button:has-text("Load more")',
            'button:has-text("Show more")',
            'a:has-text("Next")',
            'button:has-text("Next")',
            '[aria-label="Next page"]',
            '[rel="next"]',
        )

        for selector in selectors:
            try:
                item = page.locator(selector).first

                if (
                    item.count()
                    and item.is_visible()
                    and item.is_enabled()
                ):
                    item.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    moved = True
                    break

            except Exception:
                pass

        state = (len(found), page.url)

        if not moved or state in seen:
            break

        seen.add(state)

    print(
        "calendar_collection:",
        f"pages_or_scroll_rounds={round_number + 1}",
        f"events={len(found)}",
    )

    return list(found.values())


def collect_dates(days=90):
    start = datetime.now(SLT).date()
    dates = [
        start + timedelta(days=offset)
        for offset in range(days)
    ]

    def fetch_day(day):
        try:
            response = requests.get(
                UPCOMING,
                params={
                    "lang": "en-US",
                    "per_page": 200,
                    "date": day.isoformat(),
                },
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": SOURCE,
                },
                timeout=25,
            )

            response.raise_for_status()

            reference = datetime(
                day.year,
                day.month,
                day.day,
                tzinfo=SLT,
            )

            items = extract(
                response.text,
                now=reference,
                requested_day=day,
            )

            if not items:
                soup = BeautifulSoup(
                    response.text, "html.parser"
                )

                return (
                    day,
                    [],
                    f"no rows parsed; "
                    f"tables={len(soup.select('table'))} "
                    f"bytes={len(response.content)}",
                )

            return day, items, None

        except Exception as exc:
            return day, [], type(exc).__name__

    found = {}
    errors = []
    populated = 0

    with ThreadPoolExecutor(max_workers=4) as pool:
        for day, items, error in pool.map(fetch_day, dates):
            if items:
                populated += 1

            if error:
                errors.append(f"{day}: {error}")

            for event in items:
                found[event_key(event)] = event

    print(
        "daily_calendar:",
        f"dates={len(dates)}",
        f"days_with_events={populated}",
        f"events={len(found)}",
        f"empty_or_errors={len(errors)}",
    )

    for error in errors[:8]:
        print("daily_calendar_diagnostic:", error)

    return list(found.values())


def login_secondlife(page):
    username = os.environ.get("SL_USERNAME", "").strip()
    password = os.environ.get("SL_PASSWORD", "")

    if not username or not password:
        print("sl_login: credentials_not_configured")
        return False

    try:
        page.goto(
            LOGIN_URL,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        if urlparse(page.url).hostname not in (
            "id.secondlife.com",
            "secondlife.com",
            "www.secondlife.com",
        ):
            print("sl_login: unexpected_host")
            return False

        def visible(selectors):
            for selector in selectors:
                item = page.locator(selector).first

                if item.count() and item.is_visible():
                    return item

            return None

        username_field = visible(
            [
                'input[name="first_name"]',
                'input[name="username"]',
                'input[name="login"]',
                'input[autocomplete="username"]',
                'input[type="text"]',
            ]
        )

        password_field = visible(
            [
                'input[name="password"]',
                'input[type="password"]',
            ]
        )

        if username_field is None or password_field is None:
            print("sl_login: fields_not_found")
            return False

        username_field.fill(username)
        password_field.fill(password)

        submit = visible(
            [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Log In")',
                'button:has-text("Sign In")',
            ]
        )

        if submit is None:
            print("sl_login: submit_not_found")
            return False

        submit.click(timeout=15000)

        try:
            page.wait_for_url(
                re.compile(
                    r"https://(?:www\.)?secondlife\.com/"
                    r"(?:my|auth)/.*"
                ),
                timeout=20000,
            )
        except Exception:
            pass

        page.goto(
            "https://secondlife.com/my/",
            wait_until="domcontentloaded",
            timeout=45000,
        )

        host = urlparse(page.url).hostname

        body = page.locator("body").inner_text(
            timeout=10000
        )[:3000].lower()

        probable = (
            host in ("secondlife.com", "www.secondlife.com")
            and not re.search(
                r"/(?:openid/login|login)(?:[/?]|$)",
                page.url,
            )
            and "sign in to second life" not in body
            and "log in to second life" not in body
        )

        print(
            "sl_login:",
            "possible_authenticated"
            if probable
            else "not_confirmed",
        )

        return probable

    except Exception as exc:
        print("sl_login: failed", type(exc).__name__)
        return False


def collect_authenticated_dates(page, days=90):
    start = datetime.now(SLT).date()
    collected = {}

    responses_ok = 0
    tables_seen = 0
    rows_seen = 0
    no_table_responses = 0
    parsed = 0
    skipped_adult = 0
    skipped_unclassified = 0
    failures = 0

    for offset in range(days):
        day = start + timedelta(days=offset)

        try:
            response = page.request.get(
                UPCOMING,
                params={
                    "lang": "en-US",
                    "per_page": "200",
                    "date": day.isoformat(),
                },
                timeout=30000,
            )

            if not response.ok:
                failures += 1

                if failures <= 3:
                    print(
                        "authenticated_diagnostic:",
                        f"day_offset={offset}",
                        f"http_status={response.status}",
                    )

                continue

            responses_ok += 1

            html = response.text()
            soup = BeautifulSoup(html, "html.parser")

            all_tables = soup.select("table")

            if offset < 3:
                print(
                    "authenticated_structure:",
                    f"day_offset={offset}",
                    f"title_present={bool(soup.title)}",
                    f"event_ids={len(soup.select('[data-event-id]'))}",
                    f"all_tables={len(all_tables)}",
                    f"forms={len(soup.select('form'))}",
                    f"links={len(soup.select('a[href]'))}",
                    f"login_form={bool(soup.select('input[type=password]'))}",
                )

                for index, table in enumerate(all_tables[:2]):
                    first_row = table.select_one("tr:has(td)")
                    first_event = table.select_one(
                        "[data-event-id]"
                    )

                    print(
                        "authenticated_table_shape:",
                        f"day_offset={offset}",
                        f"table_index={index}",
                        f"table_id={table.get('id', '')!r}",
                        f"table_classes={table.get('class', [])!r}",
                        f"rows={len(table.select('tr'))}",
                        f"data_rows={len(table.select('tr:has(td)'))}",
                        f"first_row_cells="
                        f"{len(first_row.find_all('td', recursive=False)) if first_row else 0}",
                        f"event_node_tag="
                        f"{first_event.name if first_event else 'none'}",
                        f"event_node_classes="
                        f"{first_event.get('class', []) if first_event else []!r}",
                        f"maturity_markers="
                        f"{len(table.select('[data-maturity], [data-rating], .maturity, .rating, .event-maturity'))}",
                    )

            # Keep the current strict parser until the actual
            # authenticated table structure has been verified.
            tables = soup.select(
                "table#event-table, table.event-table"
            )

            tables_seen += len(tables)

            if not tables:
                no_table_responses += 1

            day_rows = 0

            for table in tables:
                for row in table.select("tr"):
                    if not row.find("td"):
                        continue

                    day_rows += 1
                    rows_seen += 1

                    rating = explicit_maturity(row)

                    if rating == "Adult":
                        skipped_adult += 1
                        continue

                    if rating is None:
                        skipped_unclassified += 1
                        continue

                    header = (
                        table.select_one("thead")
                        or table.find("tr")
                    )

                    fragment = (
                        '<table class="event-table">'
                        + (str(header) if header else "")
                        + str(row)
                        + "</table>"
                    )

                    reference = datetime(
                        day.year,
                        day.month,
                        day.day,
                        tzinfo=SLT,
                    )

                    events = extract(
                        fragment,
                        now=reference,
                        requested_day=day,
                    )

                    for event in events:
                        event["maturity"] = rating
                        collected[event_key(event)] = event
                        parsed += 1

            if offset < 3:
                content_type = response.headers.get(
                    "content-type", ""
                ).split(";")[0]

                print(
                    "authenticated_diagnostic:",
                    f"day_offset={offset}",
                    f"http_status={response.status}",
                    f"content_type={content_type}",
                    f"bytes={len(html)}",
                    f"tables={len(tables)}",
                    f"rows={day_rows}",
                )

        except Exception as exc:
            failures += 1

            if failures <= 3:
                print(
                    "authenticated_diagnostic:",
                    f"day_offset={offset}",
                    f"exception={type(exc).__name__}",
                )

    print(
        "authenticated_diagnostic:",
        f"responses_ok={responses_ok}",
        f"tables={tables_seen}",
        f"rows={rows_seen}",
        f"no_table_responses={no_table_responses}",
    )

    print(
        "authenticated_calendar:",
        f"publishable={len(collected)}",
        f"parsed={parsed}",
        f"skipped_adult={skipped_adult}",
        f"skipped_unclassified={skipped_unclassified}",
        f"failures={failures}",
    )

    return list(collected.values())


def future_events(previous):
    now = datetime.now(timezone.utc)
    result = []

    for event in previous.get("events", []):
        try:
            date = datetime.fromisoformat(
                event["starts_at"]
            )

            if (
                date.tzinfo is not None
                and date.astimezone(timezone.utc) > now
                and event.get("title")
            ):
                result.append(event)

        except (KeyError, ValueError, TypeError):
            continue

    return result


def main():
    payload = {
        "status": "unavailable",
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": SOURCE,
        "events": [],
        "message": "",
    }

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox"],
            )

            try:
                page = browser.new_page(
                    locale="en-US",
                    timezone_id="America/Los_Angeles",
                )

                page.goto(
                    SOURCE,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                try:
                    page.locator("table").filter(
                        has_text="When"
                    ).filter(
                        has_text="Where"
                    ).first.wait_for(timeout=30000)

                    page.wait_for_timeout(2500)

                except Exception:
                    pass

                public_events = collect_calendar_pages(page)
                daily_events = collect_dates(days=90)

                authenticated_events = []

                if login_secondlife(page):
                    authenticated_events = (
                        collect_authenticated_dates(
                            page,
                            days=90,
                        )
                    )

                merged = {}

                for event in (
                    public_events
                    + daily_events
                    + authenticated_events
                ):
                    merged[event_key(event)] = event

                events = sorted(
                    merged.values(),
                    key=lambda e: (
                        e["starts_at"],
                        e["title"].casefold(),
                    ),
                )

                print(
                    "calendar_total:",
                    f"{len(events)} events",
                    "across 90 queried dates",
                )

            finally:
                browser.close()

        if events:
            payload["status"] = "ok"
            payload["events"] = events
        else:
            payload["message"] = (
                "No verified dated events found."
            )

    except Exception as exc:
        payload["message"] = (
            "Calendar retrieval failed: "
            + type(exc).__name__
        )

    # Preserve previously collected future events.
    if OUT.exists():
        try:
            previous = json.loads(
                OUT.read_text(encoding="utf-8")
            )

            merged = {
                event_key(event): event
                for event in future_events(previous)
            }

            for event in payload["events"]:
                key = event_key(event)
                merged[key] = {
                    **merged.get(key, {}),
                    **event,
                }

            payload["events"] = sorted(
                merged.values(),
                key=lambda e: (
                    e["starts_at"],
                    e["title"].casefold(),
                ),
            )

            if (
                payload["events"]
                and payload["status"] != "ok"
            ):
                payload["status"] = "ok"
                payload["message"] = (
                    "Previously collected future events retained."
                )

        except (OSError, ValueError, TypeError):
            pass

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUT.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"status={payload['status']}",
        f"events={len(payload['events'])}",
        f"message={payload['message']}",
    )


if __name__ == "__main__":
    main()
