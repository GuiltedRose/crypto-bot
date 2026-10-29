"""Pump-owned market feeds used by its web client; anonymous, no wallet login.

OHLCV is explicitly price_usd at 1m. The ATH/pullback uses separately labeled
USD market capitalization, never market cap relabeled as a token price.
"""
import time
from pathlib import Path
from urllib.parse import quote
from meme_evidence import number
from paper_sources import PublicSources, source_error, UnsupportedMarket
from pump_narratives import ROOT, valid_mint

CHAIN='solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp'
SWAP='https://swap-api.pump.fun/v1/coins/'


def pump_history(payload,mint,pool,url,now):
    if payload['served']!={'res':'1m','measure':'price_usd'}:
        raise ValueError('Pump must serve one-minute USD price candles')
    rows,seen=[],set()
    for candle in payload['candles']:
        stamp=number(candle['openTimeMs'])/1000
        if stamp in seen:
            raise ValueError('Duplicate Pump candle')
        seen.add(stamp)
        if candle['final'] is not True or stamp+60>now:
            continue
        rows.append({'timestamp':stamp,'open':number(float(candle['open']),1e-15),
                     'high':number(float(candle['high']),1e-15),'low':number(float(candle['low']),1e-15),
                     'close':number(float(candle['close']),1e-15),'volume_usd':number(float(candle['volumeUsd']))})
    return {'mint':mint,'pool':pool,'source_url':url,'retrieved_at':now,'interval_seconds':60,
            'measure':'price_usd','candles':sorted(rows,key=lambda row:row['timestamp']),
            'provider_has_older':payload['hasOlder'],'raw_candles_received':len(payload['candles']),
            'partial_candles_excluded':len(payload['candles'])-len(rows),'history_scope':'provider token history; not full chain reconstruction'}


class PumpMarketSources(PublicSources):
    modeled_quotes_only=False

    def __init__(self,*args,include_legacy_metrics=True,**kwargs):
        super().__init__(*args,**kwargs)
        self.include_legacy_metrics=include_legacy_metrics
    def observation(self,mint,now=None,pool=None):
        if not valid_mint(mint) or pool is not None and not valid_mint(pool):
            raise ValueError('Invalid mint or pool')
        url=ROOT+'/coins-v3/'+quote(mint,safe='')+'?includeLiveStreamInfo=false'
        coin=self.fetch(url)
        if coin['mint']!=mint or coin.get('chain_id') not in (None,'solana',CHAIN):
            raise ValueError('Pump coin identity mismatch')
        selected=coin.get('pool_address') or coin.get('pump_swap_pool') or coin.get('bonding_curve')
        if not valid_mint(selected) or pool is not None and pool!=selected:
            raise ValueError('Pump market identity changed; reconcile before using another pool')
        pool=selected
        program=coin.get('program')
        if program!='pump':
            raise UnsupportedMarket('Unsupported Pump market program')
        if self.modeled_quotes_only and coin.get('quote_mint') not in ('11111111111111111111111111111111','So11111111111111111111111111111111111111112','EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'):
            raise UnsupportedMarket('Paper execution supports SOL/USDC quotes only')
        candle_url=ROOT+'/candles/'+CHAIN+'/'+quote(mint,safe='')+'?res=1m&measure=price_usd&count=13'
        activity_url=SWAP+mint+'/market-activity?program=pump'
        ath_url=SWAP+mint+'/ath?currency=USD&program=pump'
        holders_url=ROOT+'/token-holders/'+mint+'/count'
        data,errors={},{}
        # Each auxiliary failure leaves the current quote available for held exits.
        auxiliary=[('candles',candle_url),('activity',activity_url)]
        if self.include_legacy_metrics: auxiliary += [('ath',ath_url),('holders',holders_url)]
        for label,source in auxiliary:
            try: data[label]=self.fetch(source)
            except Exception as error: errors[label]=source_error(error)
        now=int(time.time()) if now is None else now
        snapshot={'mint':mint,'pool':pool,'observed_at':now,
                  'symbol':coin.get('symbol')[:80] if isinstance(coin.get('symbol'),str) else None,
                  'price_usd':number(float(coin['price_usd']),1e-15),
                  'market_source_url':url,'market_source':'pump.fun','market_program':program,
                  'market_kind':'pumpswap' if coin.get('complete') is True else 'bonding_curve',
                  'quote_mint':coin.get('quote_mint'),'token_program':coin.get('token_program'),'fixed_cost_per_side_usd':self.fixed_cost,
                  'buy_volume_window_seconds':3600,'source_errors':errors}
        for target,key in (('market_cap_usd','market_cap_usd'),('liquidity_usd','canonical_pool_liquidity_usd')):
            try: snapshot[target]=number(float(coin[key]),1e-15)
            except (KeyError,TypeError,ValueError) as error: errors[target]=source_error(error)
        try:
            if 'candles' in data:
                snapshot['candle_history']=pump_history(data['candles'],mint,pool,candle_url,now)
        except (KeyError,TypeError,ValueError) as error: errors['candles']=source_error(error)
        try:
            if 'activity' in data:
                hour=data['activity']['1h']
                snapshot.update(buy_volume_usd=number(hour['buyVolumeUSD']),
                                sell_volume_usd=number(hour['sellVolumeUSD']),
                                buy_volume_coverage_pct=100,
                                buy_volume_coverage_basis='Pump provider-reported one-hour aggregate',
                                market_activity=data['activity'],trades_source_url=activity_url)
        except (KeyError,TypeError,ValueError) as error: errors['activity']=source_error(error)
        try:
            if 'ath' in data:
                snapshot.update(ath_market_cap_usd=number(data['ath']['athMarketCap'],1e-15),
                                ath_source_kind='pump_provider',ath_checked_at=now,ath_source_url=ath_url,
                                ath_history_complete_since_launch=False)
        except (KeyError,TypeError,ValueError) as error: errors['ath']=source_error(error)
        try:
            if 'holders' in data:
                holders=data['holders']
                if holders['mint']!=mint or holders['chain']!='solana':
                    raise ValueError('Holder count identity mismatch')
                snapshot['holder_count']=number(holders['holderCount'])
        except (KeyError,TypeError,ValueError) as error: errors['holders']=source_error(error)
        if self.evidence_dir:
            import json
            path=self.evidence_dir/(mint+'.json')
            if path.exists():
                proof=json.loads(path.read_text())
                if proof['mint']!=mint or proof['pool']!=pool:
                    raise ValueError('Supplemental proof identity mismatch')
                # Market values/history/ATH are never overridden by proof files.
                for key in ('token_safety_passed','safety_checked_at','evidence','execution'):
                    if key in proof: snapshot[key]=proof[key]
        return snapshot


class ModeledPumpSources(PumpMarketSources):
    """Explicit exploratory-paper assumptions, never live execution evidence."""
    modeled_quotes_only=True

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.sol_quote=None
        self.sol_quote_time=0

    def observation(self,mint,now=None,pool=None):
        row=super().observation(mint,now,pool)
        current=time.time() if now is None else now
        if self.sol_quote is None or current-self.sol_quote_time>60:
            quote_payload=self.fetch(ROOT+'/sol-price')
            if quote_payload.get('stale') is not False:
                raise ValueError('Pump SOL valuation stale or unavailable')
            current=time.time() if now is None else now
            quote_stamp=number(quote_payload['asOfTimestamp'])/1000
            if not 0 <= current-quote_stamp <= 120:
                raise ValueError('Pump SOL valuation expired')
            self.sol_quote=number(quote_payload['solPrice'],1e-15)
            self.sol_quote_time=current
        from meme_dip import SOL_MINT,USDC_MINT
        quote_mint=row.get('quote_mint')
        if quote_mint=='11111111111111111111111111111111':
            quote_mint=SOL_MINT
        supported=quote_mint in (SOL_MINT,USDC_MINT)
        measured_rent=.00203928
        account_prepared=False
        observation_path=Path('state/wallet_observation.json')
        try:
            import json
            observation=json.loads(observation_path.read_text())
            if 0<=current-observation['observed_at']<=86400:
                size='170' if row.get('token_program')=='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb' else '165'
                measured_rent=number(observation['minimum_rent_lamports'][size])/1e9
                if current-observation['observed_at']<=300:
                    from solders.pubkey import Pubkey
                    program=row.get('token_program') or 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
                    expected=str(Pubkey.find_program_address([bytes(Pubkey.from_string(observation['wallet'])),bytes(Pubkey.from_string(program)),bytes(Pubkey.from_string(mint))],Pubkey.from_string('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'))[0])
                    account_prepared=any(account['mint']==mint and account['token_program']==program and account['address']==expected for account in observation['token_accounts'])
        except (OSError,ValueError,KeyError,TypeError,ImportError):
            pass
        row['execution']={'mint':mint,'pool':row['pool'],'observed_at':row['observed_at'],
                          'sol_price_usd':self.sol_quote,'quote_mint':quote_mint,
                          'network_fee_sol':.00002,'fee_per_side':.0125,
                          'fee_assumption_source':'https://pump.fun/docs/fees','entry_rent_sol':0 if account_prepared else measured_rent,'exit_rent_sol':0,
                          'rent_is_refundable':True,'rent_source':'read-only RPC snapshot when fresh, conservative fallback otherwise',
                          'buy_route_available':supported,'sell_route_available':supported,
                          'model_only':True,'assumptions':[
                              'Sampled Pump price plus configured adverse slippage and conservative 1.25% pool fees per side',
                              'Direct execution modeled; no Pump web/app platform fee assumed',
                              '20,000-lamport network allowance per side',
                              'New token-account rent is locked principal; existing verified ATA avoids setup funding',
                              'Simulated closed accounts retain rent until an explicit paper reclaim',
                              'No on-chain executable quote or wallet-account inventory verified']}
        row['safety_unknown']=True
        return row
