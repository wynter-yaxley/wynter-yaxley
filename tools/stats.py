#!/usr/bin/env python3
"""Fetch the numbers for the stats card into assets/stats.json.

Only public endpoints are used. GITHUB_TOKEN is sent when it is set (as in
Actions) to lift the rate limit. A number that cannot be fetched keeps its last
value, so a flaky network never blanks the card.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

USER = 'wynter-yaxley'
OUT = Path(__file__).resolve().parent.parent / 'assets' / 'stats.json'


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER, 'Accept': 'application/vnd.github+json'})
    if os.environ.get('GITHUB_TOKEN') and url.startswith('https://api.github.com/'):
        req.add_header('Authorization', f'Bearer {os.environ["GITHUB_TOKEN"]}')
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8')


def stars_and_repos():
    repos = [r for r in json.loads(get(f'https://api.github.com/users/{USER}/repos?per_page=100&type=owner'))
             if not r['fork']]
    return {'stars': sum(r['stargazers_count'] for r in repos), 'repositories': len(repos)}


def pull_requests():
    found = json.loads(get(f'https://api.github.com/search/issues?q=author:{USER}+type:pr&per_page=1'))
    return {'pull_requests': found['total_count']}


def contributions():
    # the same calendar the profile shows, one tooltip per day
    days = re.findall(r'(\d[\d,]*) contributions? on', get(f'https://github.com/users/{USER}/contributions'))
    if not days:
        raise ValueError('no days found in the contribution calendar')
    return {'contributions': sum(int(d.replace(',', '')) for d in days)}


def main():
    stats = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    old = dict(stats)
    for fetch in (stars_and_repos, pull_requests, contributions):
        try:
            stats.update(fetch())
        except Exception as e:  # keep the last good value
            print(f'warning: {fetch.__name__}: {e}', file=sys.stderr)
    numbers = {k: v for k, v in stats.items() if k != 'updated'}
    if numbers != {k: v for k, v in old.items() if k != 'updated'} or 'updated' not in stats:
        stats['updated'] = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
    OUT.write_text(json.dumps(stats, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
