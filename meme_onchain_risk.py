"""Bounded, keyless entry-risk observations; never certifies human buyers.

Top-account concentration and recent trade clustering are measured separately
from unavailable full launch/funding/bundle coverage. Unknown is never zero.
"""
import base64
import copy
from collections import Counter
import time
from solders.pubkey import Pubkey
from wallet_watch import classify_transaction


class OnchainRisk:
    def __init__(self,rpc,clock=time.time):
        self.rpc,self.clock=rpc,clock
        self.cache={}

    def assess(self,adapter,market):
        key=(str(adapter.mint),str(adapter.pool))
        cached=self.cache.get(key)
        if cached and 0<=self.clock()-cached['observed_at']<=30:
            return copy.deepcopy(cached)
        result=self._observe(adapter,market)
        if len(self.cache)>=128: self.cache.clear()
        self.cache[key]=copy.deepcopy(result)
        return result

    def _observe(self,adapter,market):
        now=self.clock()
        report={'mint':str(adapter.mint),'pool':str(adapter.pool),'observed_at':now,
                'source':'confirmed_solana_rpc','status':'unknown','passed':False,'reasons':[],
                'launch_history_complete':False,'bundled_supply_pct':None,'bot_buy_volume_pct':None,
                'independent_human_verified':False,'coverage':'top 20 token accounts and up to 8 recent mint transactions'}
        try:
            mint_data=base64.b64decode(market['records'][3]['data'][0],validate=True)
            supply=int.from_bytes(mint_data[36:44],'little')
            if supply<=0: raise ValueError('Mint supply missing')
            largest=self.rpc.call('getTokenLargestAccounts',[str(adapter.mint),{'commitment':'confirmed'}])['value']
            if not isinstance(largest,list) or not largest or len(largest)>20: raise ValueError('Holder sample unavailable')
            addresses=[row['address'] for row in largest]
            if len(set(addresses))!=len(addresses): raise ValueError('Duplicate holder accounts')
            accounts,_=adapter.accounts([Pubkey.from_string(a) for a in addresses],market['slot'])
            balances=Counter();sample_units=0;excluded=0
            for address,account in zip(addresses,accounts):
                if account is None or account['owner']!=str(market['token_program']): raise ValueError('Holder account unavailable')
                raw=base64.b64decode(account['data'][0],validate=True)
                if len(raw)<165 or raw[:32]!=bytes(adapter.mint): raise ValueError('Holder mint mismatch')
                units=int.from_bytes(raw[64:72],'little');sample_units+=units
                if address==str(market['base_vault']): excluded+=units;continue
                balances[str(Pubkey.from_bytes(raw[32:64]))]+=units
            if sample_units>supply: raise ValueError('Holder sample exceeds supply')
            top=sorted(balances.values(),reverse=True)
            report.update(holder_sample_supply_pct=sample_units/supply*100,pool_vault_supply_pct=excluded/supply*100,
                          largest_sampled_owner_pct=(top[0] if top else 0)/supply*100,
                          top10_sampled_owners_pct=sum(top[:10])/supply*100)
            if report['largest_sampled_owner_pct']>10: report['reasons'].append('sampled non-pool owner exceeds 10% of supply')
            if report['top10_sampled_owners_pct']>50: report['reasons'].append('sampled top ten non-pool owners exceed 50% of supply')
            signatures=self.rpc.call('getSignaturesForAddress',[str(adapter.mint),{'limit':8,'commitment':'confirmed'}])
            if not isinstance(signatures,list): raise ValueError('Trade sample unavailable')
            buyers=[];slots=[];seen=set();inspected=classified=missing=0
            for row in signatures:
                signature=row['signature']
                if signature in seen: continue
                seen.add(signature)
                if row.get('err') is not None or type(row.get('blockTime')) is not int or not 0<=now-row['blockTime']<=300: continue
                tx=self.rpc.call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':1}])
                if tx is None: missing+=1;continue
                inspected+=1;keys=tx['transaction']['message']['accountKeys']
                wallets=[key['pubkey'] for key in keys if key.get('signer') is True]
                matched=[]
                for wallet in wallets:
                    event=classify_transaction(tx,wallet,signature,now)
                    matched.extend((wallet,signal) for signal in event['signals'] if signal['mint']==str(adapter.mint))
                if len(matched)!=1: continue
                classified+=1;wallet,signal=matched[0]
                if signal['action']=='buy': buyers.append(wallet);slots.append(tx.get('slot'))
            counts=Counter(buyers);slot_counts=Counter(slot for slot in slots if type(slot) is int)
            report.update(transactions_inspected=inspected,classified_trades=classified,unavailable_transactions=missing,
                          sampled_buys=len(buyers),distinct_sampled_buyers=len(counts),
                          busiest_buyer_share=(max(counts.values())/len(buyers) if buyers else None),
                          busiest_slot_buy_share=(max(slot_counts.values())/len(buyers) if slot_counts and buyers else None))
            if len(buyers)>=6 and report['busiest_buyer_share']>=.7:
                report['reasons'].append('one wallet dominates the recent buy sample')
            if len(buyers)>=4 and len(counts)>=3 and (report['busiest_slot_buy_share'] or 0)>=.75:
                report['reasons'].append('recent buys cluster in one slot; possible coordination')
            # An empty/thin/failed sample cannot establish organic demand.
            if inspected<3 or classified<3 or len(counts)<2 or missing:
                report['status']='insufficient_coverage';report['reasons'].append('insufficient classified recent trade coverage')
            else: report['status']='flagged' if report['reasons'] else 'bounded_checks_passed'
            report['passed']=report['status']=='bounded_checks_passed'
        except (KeyError,TypeError,ValueError,RuntimeError,OSError) as error:
            report['status']='unavailable';report['error_type']=type(error).__name__
            if type(getattr(error,'code',None)) is int: report['provider_error_code']=error.code
            report['reasons'].append('on-chain risk observations unavailable or invalid')
        return report
