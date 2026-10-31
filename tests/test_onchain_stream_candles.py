import json
from pathlib import Path
import unittest
import asyncio
from unittest.mock import patch, AsyncMock
import test_onchain_indexer as fixtures
from onchain_candles import CandleEngine
from onchain_market import OnchainMarket
from onchain_store import EventStore
from onchain_stream import StreamHints


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.IndexerTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.store=self.helper.store;self.market=self.helper.populated()

    def engine(self):
        engine=CandleEngine(self.store);engine.watch(fixtures.MINT,fixtures.POOL);engine.sync()
        return engine

    def test_all_timeframes_and_exact_rolling_are_local_and_restartable(self):
        engine=self.engine()
        for period in (60,300,900,3600):
            stamp=3540//period*period
            row=engine.read(fixtures.MINT,fixtures.POOL,period,stamp)
            self.assertEqual(row['status'],'complete')
            self.assertTrue(row['bar']['final'])
        buy,sell,_,errors=engine.rolling(fixtures.MINT,fixtures.POOL,0,3600)
        self.assertEqual((buy,sell,errors),(600,300,[]))
        self.assertEqual(engine.sync()['processed_minutes'],0)
        reopened=CandleEngine(EventStore(self.store.path,readonly=True))
        self.assertEqual(reopened.read(fixtures.MINT,fixtures.POOL,3600,0)['trades'],3)
        self.assertEqual(self.market.observation(fixtures.MINT,now=3600)['buy_volume_usd'],600)
        from onchain_market import OnchainPaperSources
        from meme_dip import DipRules
        from paper_runtime import PaperSession
        session=PaperSession(Path(self.helper.temp.name)/'paper',6,DipRules(evidence_mode='market_only',entry_style='volume_candles'))
        try:
            with patch('onchain_market.time.time',return_value=3600), patch('pump_market.time.time',return_value=3600), patch('paper_sources.build_opener') as http:
                observed=OnchainPaperSources(self.store.path).observation(fixtures.MINT,now=3600)
                self.assertIsNotNone(session.process(observed))
                self.assertFalse(http.called)
                self.assertEqual(session.trader.all_positions(),[])
        finally:session.close()

    def test_zero_missing_and_provisional_are_distinct_without_fake_bars(self):
        engine=self.engine()
        self.assertEqual(engine.read(fixtures.MINT,fixtures.POOL,60,0)['status'],'zero')
        self.assertIsNone(engine.read(fixtures.MINT,fixtures.POOL,60,0)['bar'])
        self.assertEqual(engine.read(fixtures.MINT,fixtures.POOL,60,-60)['status'],'missing')
        self.assertEqual(engine.read(fixtures.MINT,fixtures.POOL,60,3600)['status'],'provisional')

    def test_dirty_rebuild_and_missing_conversion_close_gates(self):
        engine=self.engine()
        with self.store.connect() as db:db.execute('DELETE FROM conversions')
        engine.invalidate(0,3600)
        self.assertEqual(engine.read(fixtures.MINT,fixtures.POOL,3600,0)['status'],'missing')
        engine.sync()
        self.assertEqual(engine.read(fixtures.MINT,fixtures.POOL,60,3540)['status'],'missing')
        for stamp in (3420,3480,3540):self.store.add_conversion(fixtures.SOL,stamp,100,'fixture',{'test':True})
        engine.sync()
        self.assertEqual(engine.read(fixtures.MINT,fixtures.POOL,3600,0)['buy_usd'],'600')

    def test_racing_event_revision_is_not_lost(self):
        engine=self.engine();engine.invalidate(3540,3600)
        original=engine.minute
        def racing(*args):
            row=original(*args);engine.invalidate(3540,3600);return row
        engine.minute=racing
        result=engine.sync(limit=1)
        self.assertGreater(result['pending_minutes'],0)

    def test_partial_minute_rolling_does_not_round_to_candle_boundaries(self):
        engine=self.engine()
        buy,sell,_,errors=engine.rolling(fixtures.MINT,fixtures.POOL,3510,3570)
        self.assertEqual((buy,sell,errors),(400,0,[]))

    def test_disconnect_reconnect_is_bounded_and_never_inserts_trade_events(self):
        from onchain_stream import listen
        hints=StreamHints(self.store,'fixture',capacity=10)
        self.store.register_source('stream-fixture',{**fixtures.POLICY,'stream_verified':True})
        hints=StreamHints(self.store,'stream-fixture',capacity=10)
        class Socket:
            async def __aenter__(self):
                from solders.signature import Signature
                self.messages=iter([
                    {'jsonrpc':'2.0','id':1,'result':11},
                    {'jsonrpc':'2.0','id':2,'result':12},
                    {'jsonrpc':'2.0','method':'logsNotification','params':{'subscription':11,'result':{'context':{'slot':20},'value':{'signature':str(Signature.default()),'err':None,'logs':[]}}}}])
                return self
            async def __aexit__(self,*args):pass
            async def send(self,value):pass
            async def recv(self):
                value=next(self.messages,None)
                if value is None:raise OSError('disconnect')
                return json.dumps(value)
        before=self.store.metrics()['events']
        async def run():
            with patch('onchain_stream.websockets.connect',return_value=Socket()) as connect, patch('onchain_stream.asyncio.to_thread',new_callable=AsyncMock), patch('onchain_stream.asyncio.sleep',new_callable=AsyncMock):
                with self.assertRaises(ConnectionError):
                    await asyncio.wait_for(listen({'ws_url':'wss://example.invalid'},hints,asyncio.Event(),max_failures=2),2)
                self.assertEqual(connect.call_count,2)
        asyncio.run(run())
        self.assertEqual(self.store.metrics()['events'],before)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM stream_hints').fetchone()[0],1)
            self.assertIsNotNone(db.execute("SELECT value FROM stream_state WHERE name='gap'").fetchone())

    def test_stream_dedup_capacity_restart_and_finalized_ack(self):
        hints=StreamHints(self.store,'fixture',capacity=1)
        def message(signature):return {'method':'logsNotification','params':{'result':{'context':{'slot':10},'value':{'signature':signature,'err':None}}}}
        self.assertTrue(hints.accept(message('one'),now=10))
        self.assertFalse(hints.accept(message('one'),now=11))
        self.assertFalse(hints.accept(message('overflow'),now=12))
        restarted=StreamHints(self.store,'fixture',capacity=1)
        self.assertFalse(restarted.accept(message('one')))
        with self.store.connect() as db:
            self.assertIsNotNone(db.execute("SELECT value FROM stream_state WHERE name='gap'").fetchone())
        # The existing finalized cursor is 6, so slot 10 must remain pending.
        restarted.recover()
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM stream_hints').fetchone()[0],1)


if __name__=='__main__':unittest.main()
