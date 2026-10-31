"""Persistent incremental, event-time USD aggregates. No network or model updates."""
from decimal import Decimal
import json
from onchain_market import OnchainMarket
from onchain_store import canonical

PERIODS = (60, 300, 900, 3600)


class CandleEngine:
    def __init__(self, store):
        self.store = store
        if store.readonly:
            return
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS candle_markets(mint TEXT,pool TEXT,PRIMARY KEY(mint,pool));
                CREATE TABLE IF NOT EXISTS candle_dirty(minute INTEGER PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS candle_cache(mint TEXT,pool TEXT,period INTEGER,start INTEGER,payload TEXT,
                    PRIMARY KEY(mint,pool,period,start));
                CREATE TRIGGER IF NOT EXISTS candle_event_insert AFTER INSERT ON events BEGIN
                    INSERT INTO candle_dirty(minute) VALUES(NEW.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1; END;
                CREATE TRIGGER IF NOT EXISTS candle_event_update AFTER UPDATE ON events BEGIN
                    INSERT INTO candle_dirty(minute) VALUES(OLD.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1;
                    INSERT INTO candle_dirty(minute) VALUES(NEW.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1; END;
                CREATE TRIGGER IF NOT EXISTS candle_event_delete AFTER DELETE ON events BEGIN
                    INSERT INTO candle_dirty(minute) VALUES(OLD.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1; END;
                CREATE TRIGGER IF NOT EXISTS candle_block_insert AFTER INSERT ON blocks BEGIN
                    INSERT INTO candle_dirty(minute) VALUES(NEW.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1;
                    INSERT INTO candle_dirty(minute) VALUES(NEW.time/60*60-60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1; END;
                CREATE TRIGGER IF NOT EXISTS candle_conversion_update AFTER UPDATE ON conversions BEGIN
                    INSERT INTO candle_dirty(minute) VALUES(OLD.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1;
                    INSERT INTO candle_dirty(minute) VALUES(OLD.time/60*60+60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1;
                    INSERT INTO candle_dirty(minute) VALUES(NEW.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1;
                    INSERT INTO candle_dirty(minute) VALUES(NEW.time/60*60+60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1; END;
                CREATE TRIGGER IF NOT EXISTS candle_conversion_delete AFTER DELETE ON conversions BEGIN
                    INSERT INTO candle_dirty(minute) VALUES(OLD.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1;
                    INSERT INTO candle_dirty(minute) VALUES(OLD.time/60*60+60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1; END;
                CREATE TRIGGER IF NOT EXISTS candle_conversion_insert AFTER INSERT ON conversions BEGIN
                    INSERT INTO candle_dirty(minute) VALUES(NEW.time/60*60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1;
                    INSERT INTO candle_dirty(minute) VALUES(NEW.time/60*60+60) ON CONFLICT(minute) DO UPDATE SET revision=revision+1; END;
            ''')

    def watch(self, mint, pool):
        with self.store.connect() as db:
            added = db.execute('INSERT OR IGNORE INTO candle_markets VALUES(?,?)', (mint, pool)).rowcount
            watermark = db.execute('SELECT max(time) FROM blocks').fetchone()[0]
            if added and watermark is not None:
                db.executemany('INSERT INTO candle_dirty(minute) VALUES(?) ON CONFLICT(minute) DO UPDATE SET revision=revision+1',
                    ((t,) for t in range(watermark//60*60-3600,watermark//60*60+60,60)))

    def invalidate(self, start, end):
        """Required for approved historical corrections, including quote corrections."""
        with self.store.connect() as db:
            db.executemany('INSERT INTO candle_dirty(minute) VALUES(?) ON CONFLICT(minute) DO UPDATE SET revision=revision+1',
                           ((stamp,) for stamp in range(start//60*60, end, 60)))

    def available(self):
        with self.store.connect() as db:
            return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='candle_cache'").fetchone())

    def read(self, mint, pool, period, start, check_dirty=True):
        if period not in PERIODS or start % period:
            raise ValueError('Unsupported or unaligned candle')
        with self.store.connect() as db:
            if check_dirty and db.execute('SELECT 1 FROM candle_dirty WHERE minute>=? AND minute<? LIMIT 1', (start,start+period)).fetchone():
                return {'status':'missing','bar':None}
            row = db.execute('SELECT payload FROM candle_cache WHERE mint=? AND pool=? AND period=? AND start=?',
                             (mint, pool, period, start)).fetchone()
        value = json.loads(row[0]) if row else {'status': 'missing', 'bar': None}
        if value['status'] in ('complete', 'zero') and not self.store.coverage(start, start+period)['complete']:
            return {'status': 'missing', 'bar': None}
        return value

    def minute(self, mint, pool, stamp):
        market = OnchainMarket(self.store)
        rows = self.store.events(mint, pool, stamp, stamp+60, verified_only=True)
        prices, buy, sell, sources = [], Decimal(0), Decimal(0), set()
        error = None
        for event in rows:
            if event['kind'] != 'swap':
                continue
            try:
                price, amount, source, _ = market.valued(event)
                sources.add(source); prices.append(price)
                if event['direction'] == 'buy': buy += amount
                else: sell += amount
            except ValueError as exc:
                error = str(exc)
        complete = self.store.coverage(stamp, stamp+60)['complete'] and error is None
        watermark = self.store.watermark()
        status = ('complete' if prices else 'zero') if complete else (
            'provisional' if watermark is not None and stamp <= watermark < stamp+60 and not error else 'missing')
        bar = None
        if prices:
            bar = {'timestamp': stamp, 'open': float(prices[0]), 'high': float(max(prices)),
                   'low': float(min(prices)), 'close': float(prices[-1]),
                   'volume_usd': float(buy+sell), 'final': complete}
        return {'status': status, 'bar': bar, 'buy_usd': str(buy), 'sell_usd': str(sell),
                'trades': len(prices), 'sources': sorted(sources), 'error': error}

    def higher(self, mint, pool, period, start):
        with self.store.connect() as db:
            saved={r[0]:json.loads(r[1]) for r in db.execute('SELECT start,payload FROM candle_cache WHERE mint=? AND pool=? AND period=60 AND start>=? AND start<?',(mint,pool,start,start+period))}
        minutes=[saved.get(t,{'status':'missing','bar':None}) for t in range(start,start+period,60)]
        valid = all(row['status'] in ('complete', 'zero') for row in minutes)
        bars = [row['bar'] for row in minutes if row.get('bar')]
        buy = sum((Decimal(row.get('buy_usd','0')) for row in minutes), Decimal(0))
        sell = sum((Decimal(row.get('sell_usd','0')) for row in minutes), Decimal(0))
        bar = None
        if bars:
            bar = dict(timestamp=start, open=bars[0]['open'], close=bars[-1]['close'],
                       high=max(b['high'] for b in bars), low=min(b['low'] for b in bars),
                       volume_usd=float(buy+sell), final=valid)
        return {'status': ('complete' if bars else 'zero') if valid else 'missing',
                'bar': bar, 'buy_usd': str(buy), 'sell_usd': str(sell),
                'trades': sum(row.get('trades',0) for row in minutes),
                'sources': sorted({s for row in minutes for s in row.get('sources',[])})}

    def sync(self, limit=256):
        if not 1 <= limit <= 4096:
            raise ValueError('Bounded candle batch required')
        watermark = self.store.watermark()
        if watermark is None:
            return {'processed_minutes': 0, 'pending_minutes': 0}
        with self.store.connect() as db:
            row = db.execute("SELECT value FROM checkpoints WHERE name='candle_minute'").fetchone()
            cursor = row[0] if row else watermark//60*60-3600
            end = min(watermark//60*60+60,cursor+limit*60)
            db.executemany('INSERT INTO candle_dirty(minute) VALUES(?) ON CONFLICT(minute) DO UPDATE SET revision=revision+1', ((t,) for t in range(cursor,end,60)))
            db.execute("INSERT OR REPLACE INTO checkpoints VALUES('candle_minute',?)",(end,))
            markets = [tuple(row) for row in db.execute('SELECT mint,pool FROM candle_markets')]
            dirty = [tuple(r) for r in db.execute('SELECT minute,revision FROM candle_dirty ORDER BY minute LIMIT ?', (limit,))]
        for stamp,revision in dirty:
            for mint, pool in markets:
                minute = self.minute(mint, pool, stamp)
                with self.store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO candle_cache VALUES(?,?,?,?,?)', (mint,pool,60,stamp,canonical(minute)))
                for period in PERIODS[1:]:
                    start = stamp//period*period
                    value = self.higher(mint,pool,period,start)
                    with self.store.connect() as db:
                        db.execute('INSERT OR REPLACE INTO candle_cache VALUES(?,?,?,?,?)', (mint,pool,period,start,canonical(value)))
            with self.store.connect() as db:
                db.execute('DELETE FROM candle_dirty WHERE minute=? AND revision=?', (stamp,revision))
        with self.store.connect() as db:
            pending = db.execute('SELECT count(*) FROM candle_dirty').fetchone()[0]
        return {'processed_minutes': len(dirty), 'pending_minutes': pending, 'watermark': watermark}

    def rolling(self, mint, pool, start, end):
        if type(start) is not int or type(end) is not int or end <= start:
            raise ValueError('Valid rolling interval required')
        if not self.store.coverage(start, end)['complete']:
            return Decimal(0), Decimal(0), set(), ['rolling window coverage incomplete']
        buy,sell,sources,errors=Decimal(0),Decimal(0),set(),[]
        market=OnchainMarket(self.store)
        for stamp in range(start//60*60,(end+59)//60*60,60):
            if start<=stamp and stamp+60<=end:
                row=self.read(mint,pool,60,stamp)
                if row['status'] not in ('complete','zero'):
                    errors.append('cached minute incomplete')
                    continue
                buy+=Decimal(row.get('buy_usd','0'));sell+=Decimal(row.get('sell_usd','0'))
                sources.update(row.get('sources',[]))
            else:
                for event in self.store.events(mint,pool,max(start,stamp),min(end,stamp+60),verified_only=True):
                    if event['kind']!='swap':continue
                    try:
                        _,amount,source,_=market.valued(event);sources.add(source)
                        if event['direction']=='buy':buy+=amount
                        else:sell+=amount
                    except ValueError as error:errors.append(str(error))
        return buy,sell,sources,errors
