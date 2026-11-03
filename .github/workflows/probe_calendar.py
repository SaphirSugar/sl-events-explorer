"""Inspect public SL calendar network calls and rendered DOM; do not collect personal cookies."""
import asyncio, json, re
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

URL='https://secondlife.com/events/index.php?lang=en-US'
async def main():
    report={'source':URL,'requests':[],'tables':[],'frames':[],'errors':[]}
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1440,'height':900})
        async def response(resp):
            if len(report['requests'])>=100: return
            u=resp.url
            if not u.startswith('https://'):return
            # No request headers, cookies, query strings or response bodies saved.
            parsed=urlparse(u)
            report['requests'].append({'host':parsed.netloc,'path':parsed.path,'status':resp.status,'type':resp.request.resource_type})
        page.on('response',response)
        page.on('pageerror',lambda e: report['errors'].append(str(e)[:250]))
        try:
            await page.goto(URL,wait_until='domcontentloaded',timeout=45000)
            await page.wait_for_timeout(12000)
            for frame in page.frames:
                report['frames'].append({'host':urlparse(frame.url).netloc,'path':urlparse(frame.url).path})
                try:
                    rows=await frame.locator('tr').all_text_contents()
                    if rows:report['tables'].append({'frame':urlparse(frame.url).netloc,'sample_rows':[r.strip()[:180] for r in rows[:15]]})
                except Exception as e:report['errors'].append(type(e).__name__)
            report['page_excerpt']=(await page.locator('body').inner_text())[:2500]
        except Exception as e:report['errors'].append(str(e)[:500])
        await browser.close()
    Path('calendar_diagnostic.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Calendar probe complete. See calendar_diagnostic.json artifact.')
    print('Frames:',report['frames'])
    print('Rows found:',sum(len(x['sample_rows']) for x in report['tables']))
    print('Errors:',report['errors'])
asyncio.run(main())
