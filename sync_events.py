first_row = table.select_one('tr:has(td)')
                    print(
                        'authenticated_table_shape:',
                        f'day_offset={offset}',
                        f'table_index={index}',
                        f'table_id={table.get("id", "")!r}',
                        f'table_classes={table.get("class", [])!r}',
                        f'rows={len(table.select("tr"))}',
                        f'data_rows={len(table.select("tr:has(td)"))}',
                        f'first_row_cells={len(first_row.find_all("td", recursive=False)) if first_row else 0}',
                        f'event_node_tag={first_event.name if first_event else "none"}',
                        f'event_node_classes={first_event.get("class", []) if first_event else []!r}',
                        f'maturity_markers={len(table.select("[data-maturity], [data-rating], .maturity, .rating, .event-maturity"))}',
                    )
            tables = soup.select('table#event-table, table.event-table')
            tables_seen += len(tables)
            if not tables:
                no_table_responses += 1
            day_rows = 0
            for table in tables:
                for row in table.select('tr'):
                    if not row.find('td'):
                        continue
                    day_rows += 1
                    rows_seen += 1
                    rating = explicit_maturity(row)
                    if rating == 'Adult':
                        skipped_adult += 1
                        continue
                    if rating is None:
                        skipped_unclassified += 1
                        continue
                    header = table.select_one('thead') or table.find('tr')
                    fragment = '<table class="event-table">' + (str(header) if header else '') + str(row) + '</table>'
                    reference = datetime(day.year, day.month, day.day, tzinfo=SLT)
                    for event in extract(fragment, now=reference, requested_day=day):
                        total_seen += 1
                        event['maturity'] = rating
                        key = (event['url'], event['starts_at'], event['title'].casefold())
                        collected[key] = event

            if offset < 3 or (day_rows and not total_seen and offset < 7):
                content_type = response.headers.get('content-type', '').split(';')[0][:45]
                print(f'authenticated_diagnostic: day_offset={offset} http_status={status} '
                      f'content_type={content_type} bytes={len(html)} '
                      f'tables={len(tables)} rows={day_rows}')
        except Exception as exc:
            failures += 1
            consecutive_failures += 1
            if failures <= 3:
                print(f'authenticated_diagnostic: day_offset={offset} exception={type(exc).__name__}')
            if consecutive_failures >= 3:
                print('authenticated_diagnostic: stopped_after_three_consecutive_exceptions')
                break

    print(f'authenticated_diagnostic: responses_ok={successful_responses} '
          f'tables={tables_seen} rows={rows_seen} no_table_responses={no_table_responses}')
    print(f'authenticated_calendar: publishable={len(collected)} parsed={total_seen} '
          f'skipped_adult={skipped_adult} skipped_unclassified={skipped_unclassified} failures={failures}')
    if not collected:
        print('authenticated_diagnostic: no_authenticated_events_published; '
              'check HTTP status, table count and explicit maturity labels')
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
                if login_secondlife(page):
                    authenticated = collect_authenticated_dates(page, days=90)
                merged = {}
                for event in events + daily + authenticated:
                    key = (event['url'], event['starts_at'], event['title'].casefold())
                    merged[key] = event
                events = sorted(merged.values(), key=lambda e: (e['starts_at'], e['title']))
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
            merged = {(e.get('url'), e.get('starts_at'), e.get('title', '').casefold()): e for e in old}
            for e in payload['events']:
                key = (e.get('url'), e.get('starts_at'), e.get('title', '').casefold())
                merged[key] = {**merged.get(key, {}), **e}
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
