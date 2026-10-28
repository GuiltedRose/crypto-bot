"""One-transaction Orca exact-output buy + PumpSwap exact-input sell.

A cycle is never split across sends. Exact token quantities avoid residual
inventory. The second leg's on-chain minimum locks in USDC principal/costs/
profit; simulation and message binding precede local signing.
"""
import base64
import hashlib
import json
import math
import time
from dataclasses import asdict
from decimal import Decimal,ROUND_CEILING
from pathlib import Path
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.compute_budget import set_compute_unit_limit,set_compute_unit_price
from solders.hash import Hash
from solders.instruction import Instruction,AccountMeta
from solders.message import MessageV0,to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction
from bot import SOL_MINT
from market import USDC_MINT
from pumpswap import TOKEN,ata,token_account
from orca import Orca
from arbitrage import BuyQuote,SellQuote,admit

STRATEGY_ID='atomic-orca-pumpswap-v1'


def message_hash(message):return hashlib.sha256(to_bytes_versioned(message)).hexdigest()


def lookup_tables(rpc):
    ids=json.loads((Path(__file__).parent/'vendor/solana_address_tables.json').read_text())['tables']
    response=rpc.call('getMultipleAccounts',[ids,{'encoding':'base64','commitment':'confirmed'}])
    result=[]
    for key,account in zip(ids,response['value']):
        if not account or account['owner']!='AddressLookupTab1e1111111111111111111111111':raise ValueError('Atomic lookup owner mismatch')
        raw=base64.b64decode(account['data'][0],validate=True)
        if len(raw)<56 or (len(raw)-56)%32 or int.from_bytes(raw[:4],'little')!=1 or int.from_bytes(raw[4:12],'little')!=2**64-1:
            raise ValueError('Atomic lookup unavailable/deactivating')
        keys=[Pubkey.from_bytes(raw[i:i+32]) for i in range(56,len(raw),32)]
        if int.from_bytes(raw[12:20],'little')>=response['context']['slot']:keys=keys[:raw[20]]
        result.append(AddressLookupTableAccount(Pubkey.from_string(key),keys))
    return result


def decompile_single(unsigned):
    message=unsigned.message
    if len(message.instructions)!=1 or message.address_table_lookups:raise ValueError('Unexpected locally built PumpSwap message')
    ix=message.instructions[0]
    return Instruction(message.account_keys[ix.program_id_index],bytes(ix.data),
        [AccountMeta(message.account_keys[i],message.is_signer(i),message.is_maybe_writable(i)) for i in ix.accounts])


class AtomicExecution:
    def __init__(self,coordinator,orders,pump):
        self.coordinator,self.orders,self.pump=coordinator,orders,pump
        self.rpc,self.user=pump.rpc,pump.user
        self.orca=Orca(self.rpc,self.user)

    def allocation(self,equity):
        from bot import Risk,size
        state=self.coordinator.read() if self.coordinator else {}
        history=state.get('asset_history',{}).get(SOL_MINT,{}).get(STRATEGY_ID,[])
        cap=max(0,min(equity*.01,equity-1))
        if len(history)>=Risk().min_samples:
            cap=min(cap,size(equity,history)[0])
        return cap

    def prepare(self,equity,probe=False):
        if not math.isfinite(equity) or equity<=1:raise ValueError('Invalid atomic equity')
        initial=self.pump.snapshot()
        allocation=self.allocation(equity)
        if allocation<=0:raise ValueError('Atomic Kelly budget unavailable')
        units=int(allocation*.98/initial['price']*1e9)
        first,quote=self.orca.exact_output_buy(units)
        snapshot=self.pump.snapshot()
        sol_index=0 if str(snapshot['base'])==SOL_MINT else 1
        balances=list(snapshot['user_balances'])
        if balances[1-sol_index]<quote['max_usdc_units']:raise ValueError('Atomic USDC funds insufficient')
        if snapshot['native_lamports']<40000:raise ValueError('Atomic fee reserve insufficient')
        # The local second-leg builder needs the balance delivered by leg one;
        # only the combined unsigned transaction is simulated/signed below.
        anticipated={**snapshot,'user_balances':balances[:]}
        anticipated['user_balances'][sol_index]+=units
        second_unsigned,second_context=self.pump.build('sell',units,slippage_bps=10,snapshot=anticipated)
        second=decompile_single(second_unsigned)
        fee_units=int((Decimal(20000)*Decimal(str(snapshot['price']))/Decimal(1000)).to_integral_value(rounding=ROUND_CEILING))
        now=time.time()
        buy=BuyQuote('orca',SOL_MINT,units,quote['max_usdc_units'],quote['quoted_at'])
        sell=SellQuote('pumpswap',SOL_MINT,units,second_context['min_output'],snapshot['read_at'])
        accepted=True;reason='eligible atomic cycle'
        try:plan=admit(buy,sell,equity,fee_units,now=now)
        except ValueError as error:
            if not probe:raise
            accepted=False;reason=str(error);plan=None
        instructions=[set_compute_unit_limit(1400000),set_compute_unit_price(0),first,second]
        blockhash=self.rpc.call('getLatestBlockhash',[{'commitment':'confirmed'}])['value']
        message=MessageV0.try_compile(self.user,instructions,lookup_tables(self.rpc),Hash.from_string(blockhash['blockhash']))
        unsigned=VersionedTransaction.populate(message,[Signature.default()])
        if len(bytes(unsigned))>1232 or message.header.num_required_signatures!=1:
            raise ValueError('Atomic transaction exceeds supported packet/signers')
        if any(meta.is_signer and meta.pubkey!=self.user for ix in (first,second) for meta in ix.accounts):
            raise ValueError('Unexpected atomic signing authority')
        context={'accepted':accepted,'reason':reason,'units':units,'maximum':quote['max_usdc_units'],
            'minimum':second_context['min_output'],'before_sol':balances[sol_index],
            'before_usdc':balances[1-sol_index],'native':snapshot['native_lamports'],
            'sol_price':snapshot['price'],'read_at':min(quote['quoted_at'],snapshot['read_at']),
            'message_hash':message_hash(message),'plan':asdict(plan) if plan else None}
        return unsigned,context

    def simulate(self,unsigned,context):
        if message_hash(unsigned.message)!=context['message_hash']:raise ValueError('Atomic message modified')
        if time.time()-context['read_at']>10:raise ValueError('Atomic quote expired')
        fee=self.rpc.call('getFeeForMessage',[base64.b64encode(to_bytes_versioned(unsigned.message)).decode(),{'commitment':'confirmed'}])['value']
        if fee is None or fee>20000:raise ValueError('Atomic network fee exceeds limit')
        addresses=[ata(self.user,Pubkey.from_string(SOL_MINT)),ata(self.user,Pubkey.from_string(USDC_MINT)),self.user]
        result=self.rpc.call('simulateTransaction',[base64.b64encode(bytes(unsigned)).decode(),{'encoding':'base64','sigVerify':False,
            'commitment':'confirmed','accounts':{'encoding':'base64','addresses':[str(a) for a in addresses]}}])['value']
        if result['err'] is not None:raise ValueError('Atomic simulation rejected; neither leg signed')
        accounts=result.get('accounts')
        if not accounts or len(accounts)!=3:raise ValueError('Atomic simulation missing wallet accounts')
        sol=token_account(accounts[0],Pubkey.from_string(SOL_MINT),self.user)
        usdc=token_account(accounts[1],Pubkey.from_string(USDC_MINT),self.user)
        native_spent=context['native']-accounts[2]['lamports']
        if (sol!=context['before_sol'] or usdc-context['before_usdc']<context['minimum']-context['maximum'] or
            native_spent>fee or accounts[2]['owner']!='11111111111111111111111111111111' or accounts[2]['lamports']<20000):
            raise ValueError('Atomic simulated balances violate cycle bounds')
        context.update(simulated_hash=context['message_hash'],fee=fee,simulated_usdc_change=usdc-context['before_usdc'])
        return fee

    def sign(self,unsigned,context):
        from venue_policy import entry_allowed
        if not all(entry_allowed(v) for v in ('orca','pumpswap')):
            raise ValueError('Atomic venue eligibility changed')
        if not context['accepted'] or context.get('simulated_hash')!=context['message_hash']:
            raise ValueError('Only admitted, simulated atomic cycles may be signed')
        if message_hash(unsigned.message)!=context['message_hash'] or time.time()-context['read_at']>10:
            raise ValueError('Atomic message modified or quote expired')
        return VersionedTransaction(unsigned.message,[self.pump.wallet])

    def reserve(self,context,equity,now):
        import uuid
        from venue_policy import entry_allowed
        if not context['accepted'] or not all(entry_allowed(v) for v in ('orca','pumpswap')):
            return None,'atomic venue/profit gate blocked'
        with self.coordinator.db:
            self.coordinator.db.execute('BEGIN IMMEDIATE')
            state=self.coordinator.read()
            peak=max(state['peak_equity'] or equity,equity)
            state['peak_equity']=peak
            state['halted']=state['halted'] or equity<=peak*.9
            if state['pending'] or state['positions'] or state['halted'] or state['review_halted'] or state.get('arbitrage_halted'):
                self.coordinator._write(state)
                return None,'portfolio slot/pending order/halt blocks atomic cycle'
            if context['maximum']/1e6>self.allocation(equity):
                return None,'atomic allocation changed; re-evaluate'
            intent={'kind':'atomic_arbitrage','side':'cycle','mint':SOL_MINT,'strategy_id':STRATEGY_ID,
                'order_id':str(uuid.uuid4()),'created_at':now,'max_usdc_units':context['maximum'],
                'min_usdc_units':context['minimum'],'sol_units':context['units'],
                'fee_budget_lamports':20000,'sol_price':context['sol_price']}
            state['pending']=intent;self.coordinator._write(state)
            return intent,'atomic cycle reserved'

    def submit(self,unsigned,context,equity,now):
        # Simulate BEFORE reservation; signing/send still require a globally
        # reserved intent and an identical admitted message.
        self.simulate(unsigned,context)
        intent,reason=self.reserve(context,equity,now)
        if not intent:return reason
        try:signed=self.sign(unsigned,context)
        except Exception:
            self.coordinator.record_failed(intent['order_id']);raise
        self.orders.submit(intent['order_id'],str(signed.signatures[0]),bytes(signed))
        return self.recover()

    def apply_receipt(self,intent,signature,tx):
        from runtime import record_outcome
        if tx is None:return False
        meta=tx['meta']
        if meta['err'] is not None:raise ValueError('Failed atomic transaction cannot become a completed cycle')
        keys=tx['transaction']['message']['accountKeys']
        payer=keys[0] if isinstance(keys[0],str) else keys[0]['pubkey']
        if payer!=str(self.user) or tx['transaction']['signatures'][0]!=signature:
            raise ValueError('Atomic receipt signer/signature mismatch')
        def total(field,mint):
            return sum(int(b['uiTokenAmount']['amount']) for b in meta.get(field,[]) if b.get('owner')==str(self.user) and b['mint']==mint)
        sol=total('postTokenBalances',SOL_MINT)-total('preTokenBalances',SOL_MINT)
        usdc=total('postTokenBalances',USDC_MINT)-total('preTokenBalances',USDC_MINT)
        native=meta['preBalances'][0]-meta['postBalances'][0]
        if sol!=0 or usdc<intent['min_usdc_units']-intent['max_usdc_units'] or not 0<=native<=intent['fee_budget_lamports']:
            raise ValueError('Atomic confirmed balances violate reserved bounds')
        if tx.get('blockTime') is None:return False
        receipt={'order_id':intent['order_id'],'signature':signature,'kind':'atomic_arbitrage','mint':SOL_MINT,
                 'usdc_change_units':usdc,'native_fee_lamports':native,'fee_usd':native/1e9*intent['sol_price'],'side':'cycle','timestamp':tx['blockTime']}
        with self.coordinator.db:
            self.coordinator.db.execute('BEGIN IMMEDIATE');state=self.coordinator.read()
            previous=next((f for f in state['fills'] if f['order_id']==intent['order_id']),None)
            if previous:
                if previous!=receipt:raise ValueError('Duplicate atomic receipt differs')
                return True
            if state['pending']!=intent:raise ValueError('Atomic receipt does not match pending cycle')
            net=usdc/1e6-native/1e9*intent['sol_price']
            won=record_outcome(state,STRATEGY_ID,net)
            state['closed_trades'].append({'position_id':intent['order_id'],'exit_order_id':intent['order_id'],
                'mint':SOL_MINT,'strategy_id':STRATEGY_ID,'closed_at':tx['blockTime'],'net_pnl_usd':net,'successful':won})
            state.setdefault('asset_history',{}).setdefault(SOL_MINT,{}).setdefault(STRATEGY_ID,[]).append(usdc/intent['max_usdc_units'])
            state['fills'].append(receipt);state['pending']=None;state['atomic_failed_streak']=0
            self.coordinator._write(state)
        return True

    def recover(self):
        intent=self.coordinator.read()['pending']
        if not intent or intent.get('kind')!='atomic_arbitrage':return 'idle'
        row=self.orders.db.execute('SELECT signature FROM orders WHERE order_id=?',(intent['order_id'],)).fetchone()
        if row is None:return 'atomic reservation has no journal row; manual reconciliation required'
        result=self.orders.reconcile(intent['order_id'])
        if result=='failed':
            with self.coordinator.db:
                self.coordinator.db.execute('BEGIN IMMEDIATE')
                state=self.coordinator.read()
                if state['pending']!=intent:raise ValueError('Atomic failure does not match pending cycle')
                stats=state['performance'].get(STRATEGY_ID)
                if stats:stats['consecutive_net_wins']=0
                state['pending']=None
                state['atomic_failed_streak']=state.get('atomic_failed_streak',0)+1
                if state['atomic_failed_streak']>=2:state['arbitrage_halted']=True
                self.coordinator._write(state)
            return 'atomic cycle failed on chain; both swap legs reverted (fee may apply)'
        if result!='confirmed':return 'atomic cycle awaiting confirmation; no replacement submission'
        tx=self.rpc.call('getTransaction',[row[0],{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':1}])
        return 'confirmed atomic cycle' if self.apply_receipt(intent,row[0],tx) else 'awaiting atomic transaction details'
