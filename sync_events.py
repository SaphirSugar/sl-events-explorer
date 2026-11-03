"""Best-effort importer of the public Second Life calendar HTML.
No undocumented API is assumed. Fail closed if markup changes.
"""
import json, re, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

SOURCE='https://secondlife.com/events/'
OUT=Path(__file__).parent/'data'/'events.json'
MUSIC=re.compile(r'\b(music|concert|singer|piano|pianist|dj|jazz|blues|rock|band|karaoke)\b',re.I)

def main():
    payload={'status':'unavailable','updated_at':datetime.now(timezone.utc).isoformat(),'source':SOURCE,'events':[], 'message':'Event extraction has not been validated against the current calendar markup.'}
    try:
        r=requests.get(SOURCE,timeout=25,headers={'User-Agent':'SLEventsExplorer/0.1 (community calendar index; respectful hourly polling)'})
        r.raise_for_status()
        soup=BeautifulSoup(r.text,'html.parser')
        # The public page exposes a "When | What | Where" table in its text rendering.
        # Do not publish unverified times: source markup and timezone must be confirmed first.
        rows=[]
        for table in soup.select('table'):
            headers=[x.get_text(' ',strip=True).lower() for x in table.select('th')]
            if not ('when' in headers and 'what' in headers and 'where' in headers):continue
            for tr in table.select('tr'):
                cells=tr.find_all('td',recursive=False)
                if len(cells)<3:continue
                when,title,venue=[c.get_text(' ',strip=True) for c in cells[:3]]
                link=cells[1].find('a',href=True)
                if not link or not title:continue
                # Require explicit year/timezone in event metadata to avoid fabricated dates.
                time_tag=tr.find('time',datetime=True)
                if not time_tag:continue
                raw=time_tag['datetime']
                try:
                    parsed=datetime.fromisoformat(raw.replace('Z','+00:00'))
                    if parsed.tzinfo is None:continue
                except ValueError:continue
                if parsed<=datetime.now(timezone.utc):continue
                rows.append({'title':title,'starts_at':parsed.isoformat(),'venue':venue,'category':'Live Music' if MUSIC.search(title) else 'Other','description':'','url':urljoin(SOURCE,link['href']),'teleport_url':None})
        if rows:
            payload.update(status='ok',events=list({(x['url'],x['starts_at']):x for x in rows}.values()),message='')
        else:
            payload['message']='No events with verified dates were extractable. The page structure may require an adapter.'
    except Exception as e:
        payload['message']=f'Fetch failed ({type(e).__name__}); check the source and access policy.'
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(payload['status'],len(payload['events']),payload['message'])

if __name__=='__main__':main()
