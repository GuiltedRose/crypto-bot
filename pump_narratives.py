"""Free, read-only pump.fun narrative leads. No wallet, signing or trade access.

Creator metadata is a claim, never independent social endorsement or human
buyer evidence. X links are recorded, not scraped behind login challenges.
The public frontend endpoint is undocumented and may stop working.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = 'https://frontend-api-v3.pump.fun'
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def valid_mint(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return False
    try:
        n = 0
        for char in value:
            n = n * 58 + ALPHABET.index(char)
        length = (n.bit_length() + 7) // 8 + len(value) - len(value.lstrip('1'))
        return length == 32
    except ValueError:
        return False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Unexpected provider redirect; review source before retrying')


def fetch_json(url):
    from data_policy import require_permitted_url
    require_permitted_url(url)
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'frontend-api-v3.pump.fun':
        raise ValueError('Unreviewed source host')
    request = Request(url, headers={'User-Agent': 'SolanaNarrativeResearch/0.1', 'Accept': 'application/json'})
    with build_opener(NoRedirect).open(request, timeout=15) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Provider response too large')
    return json.loads(raw)


def x_link(value):
    if not isinstance(value, str) or len(value) > 500:
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme != 'https' or parsed.netloc.lower() not in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'):
        return None
    parts = parsed.path.strip('/').split('/')
    # Communities and posts are different anchors from their profile owner.
    if len(parts) == 3 and parts[:2] == ['i', 'communities'] and parts[2].isdigit():
        return {'kind': 'community', 'url': 'https://x.com/i/communities/' + parts[2]}
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', parts[0]) or parts[0].lower() in ('home', 'search', 'intent', 'i', 'share', 'explore'):
        return None
    if len(parts) == 1:
        return {'kind': 'profile', 'url': 'https://x.com/' + parts[0].lower()}
    if len(parts) == 3 and parts[1] == 'status' and parts[2].isdigit():
        return {'kind': 'post', 'url': 'https://x.com/' + parts[0].lower() + '/status/' + parts[2]}
    return None


def brand_key(value):
    """Comparison hint only; does not resolve visually confusable alphabets."""
    text = unicodedata.normalize('NFKC', value).casefold()
    return ''.join(char for char in text if char.isalnum())


def duplicate_groups(leads):
    groups = {}
    for lead in leads:
        features = [('name', brand_key(lead.get('name', ''))),
                    ('symbol', brand_key(lead.get('symbol', ''))),
                    ('description', lead.get('description_fingerprint')),
                    ('x_anchor', (lead.get('x_anchor') or {}).get('url'))]
        for kind, value in features:
            if value:
                groups.setdefault((kind, value), set()).add(lead['mint'])
    return [{'signal': kind, 'value': value, 'mints': sorted(mints),
             'canonical_mint': None, 'status': 'requires_originality_verification'}
            for (kind, value), mints in sorted(groups.items()) if len(mints) > 1]


def parse_coin(coin, now, expected_mint=None):
    mint = coin['mint']
    if not valid_mint(mint) or expected_mint is not None and expected_mint != mint:
        raise ValueError('Invalid or mismatched metadata mint')
    fields = {}
    for key in ('name', 'symbol', 'description'):
        value = coin.get(key, '')
        if not isinstance(value, str) or len(value) > 10000:
            raise ValueError('Invalid metadata text')
        fields[key] = value
    anchor = x_link(coin.get('twitter'))
    # Hashes detect copied claims; no positive sentiment or safety inference.
    text = re.sub(r'\s+', ' ', fields['description'].casefold()).strip()
    return {'mint': mint, **fields, 'observed_at': now,
            'source_url': ROOT + '/coins/' + mint,
            'pump_url': 'https://pump.fun/coin/' + mint,
            'x_anchor': anchor,
            'description_fingerprint': hashlib.sha256(text.encode()).hexdigest() if text else None,
            'hashtags': sorted(set(re.findall(r'(?<!\w)#([\w]{2,40})', text))),
            'source_kind': 'creator_supplied_metadata',
            'mint_identity_chain_verified': False,
            'independent_endorsement': False, 'human_buying_verified': False,
            'bundle_analysis': 'unknown', 'originality_status': 'unverified',
            'canonical_mint': None, 'live_entry_authorized': False}


class NarrativeStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS leads(mint TEXT PRIMARY KEY, first_seen REAL, last_seen REAL, data TEXT)')
        self.db.commit()

    def ingest(self, lead):
        with self.db:
            self.db.execute('INSERT INTO leads VALUES(?,?,?,?) ON CONFLICT(mint) DO UPDATE SET last_seen=excluded.last_seen,data=excluded.data',
                            (lead['mint'], lead['observed_at'], lead['observed_at'], json.dumps(lead)))

    def report(self, now):
        rows = self.db.execute('SELECT first_seen,data FROM leads WHERE last_seen>=?', (now - 3600,)).fetchall()
        leads = [dict(json.loads(data), first_seen=first) for first, data in rows]
        # Include old cached identities: a copycat must not become unique
        # merely because the earlier coin left the recent-leads window.
        known = [json.loads(row[0]) for row in self.db.execute('SELECT data FROM leads')]
        duplicates = duplicate_groups(known)
        for lead in leads:
            lead['duplicate_signals'] = [group for group in duplicates if lead['mint'] in group['mints']]
        anchors = {}
        fingerprints = {}
        for lead in leads:
            if lead['x_anchor']:
                anchors.setdefault(lead['x_anchor']['url'], []).append(lead['mint'])
            if lead['description_fingerprint']:
                fingerprints.setdefault(lead['description_fingerprint'], []).append(lead['mint'])
        return {'mode': 'research_only', 'observed_at': now, 'leads': leads,
                'shared_x_anchors': [{'url': url, 'mints': sorted(mints), 'interpretation': 'shared claimed association; possible copycats'}
                                     for url, mints in sorted(anchors.items()) if len(mints) > 1],
                'copied_descriptions': [sorted(mints) for mints in fingerprints.values() if len(mints) > 1],
                'duplicate_candidates': duplicates,
                'live_entry_authorized': False,
                'limitations': ['Metadata may impersonate unrelated accounts or posts.',
                                'Matching branding flags candidates; first seen, age and volume do not establish the original.',
                                'Linked X content, account ownership and independent demand are not verified.',
                                'Public frontend endpoint is undocumented; errors mean unavailable coverage.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mint', action='append', default=[])
    parser.add_argument('--recent', action='store_true', help='Sample at most 20 recent launches from public frontend')
    parser.add_argument('--input', type=Path, help='Offline JSON object or array of pump.fun metadata')
    parser.add_argument('--state-dir', type=Path, default=Path('state/narratives'))
    args = parser.parse_args()
    if not (args.mint or args.recent or args.input):
        parser.error('Choose --mint, --recent, or --input')
    if any(not valid_mint(mint) for mint in args.mint):
        parser.error('Each --mint must be a Solana public key')
    store = NarrativeStore(args.state_dir / 'leads.sqlite')
    errors = []
    payloads = []
    if args.input:
        data = json.loads(args.input.read_text())
        payloads.append((data if isinstance(data, list) else [data], None))
    requests = [(ROOT + '/coins/' + mint, mint) for mint in args.mint]
    if args.recent:
        requests.append((ROOT + '/coins?' + urlencode({'offset': 0, 'limit': 20, 'sort': 'created_timestamp', 'order': 'DESC', 'includeNsfw': 'false'}), None))
    for i, (url, expected) in enumerate(requests):
        if i:
            time.sleep(2)
        try:
            data = fetch_json(url)
            if not isinstance(data, (list, dict)):
                raise ValueError('Unexpected provider schema')
            payloads.append((data if isinstance(data, list) else [data], expected))
        except Exception as error:
            errors.append({'source': url, 'error_type': type(error).__name__, 'status': 'unavailable'})
    for coins, expected in payloads:
        for coin in coins[:100]:
            try:
                store.ingest(parse_coin(coin, int(time.time()), expected))
            except (KeyError, TypeError, ValueError):
                errors.append({'status': 'invalid metadata rejected'})
    report = store.report(int(time.time()))
    report['errors'] = errors
    report['coverage'] = 'incomplete' if errors else 'sample_only'
    target = args.state_dir / 'status.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(target)
    store.db.close()
    print(json.dumps({'report': str(target), 'leads': len(report['leads']), 'errors': errors, 'mode': 'research_only'}))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
