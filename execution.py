"""Bridge durable strategy intents to locally constructed PumpSwap trades."""
import math
from decimal import Decimal, ROUND_DOWN
from runtime import Fill
from bot import SOL_MINT,Risk
from market import USDC_MINT


def raw_units(amount,decimals):
    return int((Decimal(str(amount))*10**decimals).to_integral_value(rounding=ROUND_DOWN))


def confirmed_fill(rpc, signature, intent, user, sol_price):
    tx=rpc.call('getTransaction',[signature,{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':1}])
    if tx is None:
        return None
    meta=tx['meta']
    if meta['err'] is not None:
        raise ValueError('Failed transaction cannot become a fill')
    message=tx['transaction']['message']
    keys=[k if isinstance(k,str) else k['pubkey'] for k in message['accountKeys']]
    if not keys or keys[0]!=str(user) or signature!=tx['transaction']['signatures'][0]:
        raise ValueError('Transaction signer/signature mismatch')
    mint=intent.get('mint',SOL_MINT)
    decimals=intent.get('decimals',9)
    def totals(field):
        result={mint:0,USDC_MINT:0}
        for balance in meta.get(field,[]):
            if balance.get('owner')==str(user) and balance['mint'] in result:
                result[balance['mint']]+=int(balance['uiTokenAmount']['amount'])
        return result
    before,after=totals('preTokenBalances'),totals('postTokenBalances')
    sol=(after[mint]-before[mint])/10**decimals
    usdc=(after[USDC_MINT]-before[USDC_MINT])/1e6
    if intent['side']=='buy':
        if sol<=0 or usdc>=0 or -usdc>intent['budget_usd']+1e-6:
            raise ValueError('Confirmed buy violates intent')
        quantity,price=sol,-usdc/sol
    else:
        if sol>=0 or usdc<=0 or not math.isclose(-sol,intent['quantity'],rel_tol=1e-6,abs_tol=1e-9):
            raise ValueError('Confirmed sell violates intent')
        quantity,price=-sol,usdc/-sol
    # For this adapter native SOL cannot move except fees/rent. Charge actual
    # native balance reduction; pool trading fees are already in fill price.
    native_cost=max(0,meta['preBalances'][0]-meta['postBalances'][0])/1e9*sol_price
    if tx.get('blockTime') is None:
        return None
    return Fill(intent['order_id'],intent['side'],quantity,price,tx['blockTime'],native_cost,mint)


class Execution:
    def __init__(self, coordinator, orders, adapter):
        self.coordinator,self.orders,self.adapter=coordinator,orders,adapter

    def recover(self,sol_price):
        intent=self.coordinator.read()['pending']
        if not intent:
            if self.orders.unresolved():
                raise ValueError('Order journal and strategy state disagree')
            return 'idle'
        row=self.orders.db.execute('SELECT signature FROM orders WHERE order_id=?',(intent['order_id'],)).fetchone()
        if row is None:
            # Crash before submission cannot be distinguished automatically
            # from a manually altered journal. Preserve the reservation.
            return 'reserved intent has no journal entry; manual reconciliation required'
        status=self.orders.reconcile(intent['order_id'])
        if status=='failed':
            self.coordinator.record_failed(intent['order_id'],executed=True)
            return 'failed on chain'
        if status!='confirmed':
            return 'awaiting confirmation; no replacement order'
        fill=confirmed_fill(self.adapter.rpc,row[0],intent,self.adapter.user,sol_price)
        if fill is None:
            return 'awaiting confirmed transaction details'
        self.coordinator.apply_fill(fill)
        return 'confirmed '+fill.side

    def submit_reserved(self,intent):
        # Preparation failures happen before any send and may safely release
        # the reservation. Once submit starts, ambiguity must preserve it.
        try:
            if intent.get('side') not in ('buy','sell') or intent.get('direction','long')!='long':
                raise ValueError('Short/cover execution unsupported by PumpSwap spot adapter')
            s=self.adapter.snapshot()
            units=raw_units(intent['budget_usd'],6) if intent['side']=='buy' else raw_units(intent['quantity'],9)
            unsigned,context=self.adapter.build(intent['side'],units,snapshot=s)
            signed,fee=self.adapter.simulate_and_sign(unsigned,context)
            if intent['side']=='buy':
                expected_sol=context['expected_output']/1e9
                risk=Risk()
                costs=fee*2/1e9*s['price']+intent['budget_usd']*2*(s['fee_bps']/10000+risk.slippage_per_side)
                if expected_sol*intent['target_distance'] < risk.min_target_cost_multiple*costs:
                    raise ValueError('Trade target cannot cover fresh execution costs')
        except Exception:
            self.coordinator.record_failed(intent['order_id'])
            raise
        self.orders.submit(intent['order_id'],str(signed.signatures[0]),bytes(signed))
        return self.recover(s['price'])
