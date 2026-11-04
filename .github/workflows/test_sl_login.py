"""Isolated Second Life login diagnostic. Never publishes event data or session tokens."""
import os
import re
import sys
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

LOGIN = 'https://id.secondlife.com/openid/login?return_to=https%3A%2F%2Fsecondlife.com%2Fauth%2Foid_return.php%3Fredirect%3D%252Fmy&language=en-US'
CALENDAR = 'https://secondlife.com/events/index.php?lang=en-US'
DAY = os.getenv('SL_TEST_DATE', '2026-11-20')

def safe_host(url):
    return urlparse(url).hostname or '(unknown)'

def first_visible(page, selectors):
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            pass
    return None

def calendar_count(page):
    page.goto(CALENDAR, wait_until='domcontentloaded', timeout=45000)
    try:
        response = page.request.get('https://secondlife.com/events/upcoming.php', params={
            'lang': 'en-US', 'per_page': '200', 'date': DAY
        }, timeout=30000)
        body = response.text()
        # Count actual unique event identifiers. Never print response contents.
        ids = set(re.findall(r'data-event-id\s*=\s*["\'](\d+)["\']', body))
        return response.status, len(ids), len(body)
    except Exception as exc:
        print('CALENDAR_TEST_ERROR:', type(exc).__name__)
        return None, None, None

def main():
    username = os.getenv('SL_USERNAME', '').strip()
    password = os.getenv('SL_PASSWORD', '')
    if not username or not password:
        print('RESULT: missing SL_USERNAME or SL_PASSWORD secret')
        return 2
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            context = browser.new_context(locale='en-US', timezone_id='America/Los_Angeles')
            page = context.new_page()
            print('TEST_DATE:', DAY)
            before = calendar_count(page)
            print('ANONYMOUS: status=%s event_ids=%s bytes=%s' % before)
            page.goto(LOGIN, wait_until='domcontentloaded', timeout=45000)
            print('LOGIN_PAGE_HOST:', safe_host(page.url))
            if safe_host(page.url) not in ('id.secondlife.com', 'secondlife.com', 'www.secondlife.com'):
                print('RESULT: unexpected login host; stopped')
                return 3
            user = first_visible(page, ['input[name="first_name"]', 'input[name="username"]', 'input[name="login"]', 'input[autocomplete="username"]', 'input[type="text"]'])
            pwd = first_visible(page, ['input[name="password"]', 'input[type="password"]'])
            if not user or not pwd:
                print('RESULT: login fields not detected (or extra verification required)')
                return 4
            # Never log field values or a browser trace/screenshot.
            user.fill(username)
            pwd.fill(password)
            submit = first_visible(page, ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Log In")', 'button:has-text("Sign In")'])
            if not submit:
                print('RESULT: submit button not detected')
                return 5
            submit.click(timeout=15000)
            try:
                page.wait_for_url(re.compile(r'https://(?:www\.)?secondlife\.com/(?:my|auth)/.*'), timeout=20000)
            except PlaywrightTimeout:
                pass
            print('AFTER_LOGIN_HOST:', safe_host(page.url))
            # A successful authenticated /my page is stronger evidence than a redirect alone.
            try:
                page.goto('https://secondlife.com/my/', wait_until='domcontentloaded', timeout=45000)
                current_host = safe_host(page.url)
                page_text = page.locator('body').inner_text(timeout=10000)[:3000].lower()
                authenticated = current_host in ('secondlife.com', 'www.secondlife.com') and not re.search(r'/(?:openid/login|login)(?:[/?]|$)', page.url) and not ('sign in to second life' in page_text or 'log in to second life' in page_text)
            except Exception:
                authenticated = False
            print('AUTH_CHECK:', 'possible_authenticated' if authenticated else 'not_confirmed')
            after = calendar_count(page)
            print('AFTER_LOGIN: status=%s event_ids=%s bytes=%s' % after)
            if before[1] is not None and after[1] is not None:
                print('EVENT_COUNT_DIFFERENCE:', after[1] - before[1])
            print('RESULT:', 'login_test_completed' if authenticated else 'login_not_confirmed')
            return 0 if authenticated else 6
        finally:
            browser.close()

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Do not print exception message: URLs or page data might contain sensitive values.
        print('RESULT: unexpected error:', type(exc).__name__)
        sys.exit(1)
