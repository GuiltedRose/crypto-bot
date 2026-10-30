import base64
import copy
from dataclasses import replace
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.signature import Signature
from solders.keypair import Keypair
from meme_dip import DipRules
from meme_execution import (MemeAdapter,CURVE,SWAP,TOKEN,TOKEN2022,SYSTEM,WSOL,SPECS,pda,associated,
                            quote_output,token_balance,mint_info,decode)
from meme_live import MemeLiveStore,MemeLiveEngine,receipt_fill
from run_meme_live import SimulationRpc
from test_trend_pullback import snapshot as trend_snapshot
from wallet import MAINNET_GENESIS


def account(raw,owner,lamports=2000000):
    return {'owner':str(owner),'data':[base64.b64encode(raw).decode(),'base64'],'lamports':lamports,'executable':False}


def token(mint,owner,units,program=TOKEN):
    data=bytearray(165);data[:32]=bytes(mint);data[32:64]=bytes(owner);data[64:72]=units.to_bytes(8,'little');data[108]=1
    return account(data,program)


def encoded(name,program,overrides):
    spec=SPECS[str(program)];types={t['name']:t['type'] for t in spec['types']}
    def pack(kind,value=None):
        if kind=='pubkey': return bytes(Pubkey.from_string(value)) if value else bytes(SYSTEM)
        if kind=='bool': return bytes([bool(value)])
        if isinstance(kind,str): return int(value or 0).to_bytes(int(kind[1:])//8,'little',signed=kind[0]=='i')
        if 'defined' in kind:
            return b''.join(pack(f['type'],(value or {}).get(f['name'])) for f in types[kind['defined']['name']]['fields'])
        if 'array' in kind:
            element,count=kind['array'];return b''.join(pack(element,v) for v in (value or [None]*count))
        values=value or [];return len(values).to_bytes(4,'little')+b''.join(pack(kind['vec'],v) for v in values)
    discriminator=next(a['discriminator'] for a in spec['accounts'] if a['name']==name)
    return account(bytes(discriminator)+pack({'defined':{'name':name}},overrides),__import__('meme_execution').FEE if name=='FeeConfig' else program)


class CurveRpc:
    def __init__(self):
        self.calls=[];self.user=Pubkey.new_unique();self.mint=Pubkey.new_unique();self.pool=pda(CURVE,b'bonding-curve',bytes(self.mint))
        self.base=associated(self.user,self.mint);self.quote=associated(self.user,WSOL);self.buyback=Pubkey.new_unique()
        mint=bytearray(82);mint[44]=6;mint[45]=1
        from meme_execution import FEE
        self.records={str(self.pool):encoded('BondingCurve',CURVE,{'virtual_token_reserves':10**15,'virtual_quote_reserves':100*10**9,
             'real_token_reserves':10**14,'real_quote_reserves':100*10**9,'token_total_supply':10**15}),
             str(self.mint):account(mint,TOKEN),str(self.user):account(b'',SYSTEM,65000000),
             str(pda(CURVE,b'global')):encoded('Global',CURVE,{'buyback_fee_recipients':[str(self.buyback)]*8}),
             str(pda(FEE,b'fee_config',bytes(CURVE))):encoded('FeeConfig',CURVE,{'flat_fees':{'protocol_fee_bps':100,'creator_fee_bps':25}}),
             str(associated(self.pool,self.mint)):token(self.mint,self.pool,10**14),str(self.buyback):account(b'',SYSTEM)}
        self.simulated=None
    def call(self,method,params):
        self.calls.append((method,params))
        if method=='getGenesisHash': return MAINNET_GENESIS
        if method=='getMultipleAccounts': return {'context':{'slot':100},'value':[copy.deepcopy(self.records.get(a)) for a in params[0]]}
        if method=='getLatestBlockhash': return {'value':{'blockhash':str(Hash.default()),'lastValidBlockHeight':120}}
        if method=='getFeeForMessage': return {'value':5000}
        if method=='getMinimumBalanceForRentExemption': return 1500000
        if method=='simulateTransaction':
            if self.simulated is None: raise AssertionError('Specify simulation fixture')
            return {'value':{'err':None,'accounts':[copy.deepcopy(self.simulated.get(a)) for a in params[1]['accounts']['addresses']]}}
        raise AssertionError('Unexpected RPC method '+method)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.rpc=CurveRpc();self.adapter=MemeAdapter(self.rpc,self.rpc.user,str(self.rpc.mint),str(self.rpc.pool),'bonding_curve')

    def test_constructs_unsigned_exact_input_bound_curve_and_creates_only_own_ata(self):
        market=self.adapter.snapshot(True);unsigned,ctx=self.adapter.build('buy',10000000,snapshot=market)
        self.assertTrue(all(s==Signature.default() for s in unsigned.signatures))
        self.assertEqual(len(unsigned.message.instructions),2)
        ix=unsigned.message.instructions[-1]
        definition=next(i for i in SPECS[str(CURVE)]['instructions'] if i['name']=='buy_exact_quote_in_v3')
        self.assertEqual(bytes(ix.data),bytes(definition['discriminator'])+struct.pack('<QQ',10000000,ctx['min_output'])+b'\0')
        self.assertEqual(len(ix.accounts),17)
        self.assertFalse(any(m=='sendTransaction' for m,p in self.rpc.calls))

    def test_simulation_validates_exact_debit_rent_output_and_fee_reserve(self):
        market=self.adapter.snapshot(True);unsigned,ctx=self.adapter.build('buy',10000000,snapshot=market)
        self.rpc.simulated=copy.deepcopy(self.rpc.records)
        self.rpc.simulated[str(self.rpc.base)]=token(self.rpc.mint,self.rpc.user,ctx['expected_output'])
        self.rpc.simulated[str(self.rpc.base)]['lamports']=1500000
        self.rpc.simulated[str(self.rpc.user)]['lamports']-=10000000+5000+1500000
        result=self.adapter.simulate(unsigned,ctx)
        self.assertEqual(result['rent_lamports'],1500000)
        self.assertEqual(result['output_units'],ctx['expected_output'])
        self.rpc.simulated[str(self.rpc.user)]['lamports']-=1
        with self.assertRaisesRegex(ValueError,'native debit'): self.adapter.simulate(unsigned,ctx)

    def test_token_authority_extension_delegate_and_unsupported_quote_are_rejected(self):
        a=copy.deepcopy(self.rpc.records[str(self.rpc.mint)]);raw=bytearray(base64.b64decode(a['data'][0]));raw[:4]=(1).to_bytes(4,'little');a['data'][0]=base64.b64encode(raw).decode()
        with self.assertRaisesRegex(ValueError,'authority'): mint_info(a)
        raw=bytearray(166);raw[45]=1;raw[165]=1;raw+=struct.pack('<HH',14,0)
        with self.assertRaisesRegex(ValueError,'extension'): mint_info(account(raw,TOKEN2022))
        a=token(self.rpc.mint,self.rpc.user,10);raw=bytearray(base64.b64decode(a['data'][0]));raw[72]=1;a['data'][0]=base64.b64encode(raw).decode()
        with self.assertRaisesRegex(ValueError,'delegate'): token_balance(a,self.rpc.mint,self.rpc.user,TOKEN)

    def test_curve_migration_boundary_and_stale_quotes_are_rejected(self):
        market=self.adapter.snapshot(True);market['trade']['real_token_reserves']=1
        with self.assertRaisesRegex(ValueError,'migration'): self.adapter.build('buy',10000000,snapshot=market)
        market['read_at']=0
        with self.assertRaisesRegex(ValueError,'expired'): self.adapter.build('buy',100,snapshot=market)

    def test_readonly_rpc_cannot_submit_or_query_secrets(self):
        rpc=SimulationRpc(self.rpc)
        for method in ('sendTransaction','requestAirdrop','signTransaction'):
            with self.assertRaisesRegex(ValueError,'forbidden'): rpc.call(method,[])


def receipt(user,mint,base,quote,before_units,after_units,native_delta,quote_delta=0,base_rent=0):
    keys=[user,base,quote]
    def rows(units,wrapped): return [{'accountIndex':1,'mint':mint,'owner':user,'uiTokenAmount':{'amount':str(units)}},
                                    {'accountIndex':2,'mint':str(WSOL),'owner':user,'uiTokenAmount':{'amount':str(wrapped)}}]
    return {'blockTime':1000,'transaction':{'signatures':['sig'],'message':{'accountKeys':keys}},
            'meta':{'err':None,'fee':5000,'preBalances':[65000000,0,1500000],
                    'postBalances':[65000000+native_delta,base_rent,1500000+quote_delta],
                    'preTokenBalances':rows(before_units,0),'postTokenBalances':rows(after_units,quote_delta)}}


class ReceiptTests(unittest.TestCase):
    def test_confirmed_curve_buy_excludes_own_rent_but_includes_network_fee(self):
        r=receipt('user','mint','base','quote',0,100,-11505000,base_rent=1500000)
        fill=receipt_fill(r,'user','mint','base','quote')
        self.assertEqual(fill['base_delta'],100)
        self.assertEqual(fill['net_cash_lamports'],-10005000)

    def test_wrapped_proceeds_are_cash_and_not_fake_rent_profit(self):
        r=receipt('user','mint','base','quote',100,0,-5000,quote_delta=12000000)
        fill=receipt_fill(r,'user','mint','base','quote')
        self.assertEqual(fill['net_cash_lamports'],11995000)
        self.assertEqual(fill['own_account_rent_delta'],0)


class FakeAdapter:
    def __init__(self,rpc,user,mint,pool,kind): self.rpc=rpc
    def snapshot(self,for_entry=False):
        return {'native_lamports':65000000,'quote_balance':0,'base_balance':1000,'base_user':'base','quote_user':'quote',
                'read_at':time.time(),'fee_bps':125}
    def simulate_round_trip(self,units): return {'simulation_passed':True}
    def output(self,side,units,market): return self.rpc.get('output',18000000)
    def build(self,side,units,**kwargs):
        return None,{'min_output':int(self.rpc.get('output',18000000)*.995) if side=='sell' else 100,'message_hash':'hash','last_valid_block_height':1000}
    def simulate(self,unsigned,context,**kwargs):
        return {'rent_lamports':0,'fee_lamports':5000,'output_units':100,'simulation_passed':True}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.rules=DipRules(entry_style='trend_pullback',evidence_mode='market_only',
            max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2)
        self.store=MemeLiveStore(self.temp.name,'user',self.rules)
        self.rpc={};self.engine=MemeLiveEngine(self.rpc,'user',self.store,self.rules,adapter_factory=FakeAdapter)
        self.row=trend_snapshot();self.row['market_kind']='bonding_curve';self.row['liquidity_usd']=10000
    def tearDown(self): self.store.close();self.temp.cleanup()
    def test_uses_live_dollar_size_without_creating_paper_fill_or_position(self):
        result=self.engine.prepare(self.row,100,6.5,1200)
        self.assertEqual(result['status'],'unsigned_simulation_passed',result)
        self.assertEqual(result['intent']['input_units'],17800000)
        self.assertIsNone(self.store.state['position'])
        self.assertIsNone(self.store.state['pending'])
    def test_percentage_input_and_frozen_intent_limits(self):
        self.engine.rules=replace(self.rules,position_fraction=.274,take_profit_fraction=.562,max_loss_fraction=.112)
        result=self.engine.prepare(self.row,100,6.5,1200)
        self.assertEqual(result['status'],'unsigned_simulation_passed',result)
        intent=result['intent']
        self.assertAlmostEqual(intent['input_units']/1e9*100,1.781)
        self.assertAlmostEqual(intent['take_profit_usd'],1.781*.562)
        self.assertAlmostEqual(intent['max_loss_usd'],1.781*.112)
        self.assertIsNone(self.store.state['position'])

    def test_halt_is_persistent_and_exits_take_priority_over_new_entries(self):
        self.engine.mark(6.5);self.store.state['realized_pnl_usd']=-.4;self.engine.mark(6.1)
        self.assertEqual(self.engine.prepare(self.row,100,6.1,1200)['status'],'drawdown_halted')
        self.store.state['position']={'mint':'Mint','pool':'Pool','kind':'bonding_curve','units':1000,'cost_usd':1.78,'opened_at':1200}
        result=self.engine.prepare(self.row,100,6.1,1200)
        self.assertEqual(result['status'],'unsigned_simulation_passed')
        self.assertEqual(result['intent']['reason'],'bot strategy drawdown')
        self.store.save('test',{})
        self.store.close();self.store=MemeLiveStore(self.temp.name,'user',self.rules)
        self.assertTrue(self.store.state['halted'])
    def test_net_profit_and_loss_not_gross_price_trigger_exits(self):
        self.store.state['position']={'mint':'Mint','pool':'Pool','kind':'bonding_curve','units':1000,'cost_usd':1.78,'opened_at':1200}
        self.rpc['output']=27800000
        self.assertEqual(self.engine.prepare(self.row,100,6.5,1200)['status'],'holding')
        self.rpc['output']=28200000
        self.assertEqual(self.engine.prepare(self.row,100,6.5,1200)['intent']['reason'],'profit target')
        self.rpc['output']=15000000
        self.assertEqual(self.engine.prepare(self.row,100,6.5,1200)['intent']['reason'],'bot strategy drawdown')
    def test_neural_early_profit_exit_requires_positive_simulated_minimum(self):
        self.store.state['position']={'mint':'Mint','pool':'Pool','kind':'bonding_curve',
            'units':1000,'cost_usd':1.78,'opened_at':1200}
        self.engine.neural_mode='active';self.rpc['output']=20500000
        self.assertEqual(self.engine.prepare(self.row,100,6.5,1200)['status'],'holding')
        with patch('neural_exits.decision',return_value={'reason':'neural early profit exit'}):
            result=self.engine.prepare(self.row,100,6.5,1200)
            self.assertEqual(result['intent']['reason'],'neural early profit exit')
            with patch.object(FakeAdapter,'simulate',return_value={'fee_lamports':10000000,'rent_lamports':0}):
                self.assertEqual(self.engine.prepare(self.row,100,6.5,1200)['status'],'holding')
        self.assertIsNotNone(self.store.state['position'])

    def test_live_neural_selection_receives_final_risk_and_execution_inputs(self):
        self.engine.neural_mode='learning'
        risk={'passed':True,'largest_sampled_owner_pct':5}
        self.engine.risk_assessor=type('Risk',(),{'assess':lambda *args:risk})()
        with patch('neural_autonomy.learning_decision',return_value={'allow':True,'status':'learning'}) as select:
            result=self.engine.prepare(self.row,100,6.5,1200)
        self.assertEqual(result['status'],'unsigned_simulation_passed')
        context=select.call_args.args[1]
        self.assertEqual(context['onchain_risk']['largest_sampled_owner_pct'],5)
        self.assertEqual(context['market_context']['fee_per_side'],.0125)
        self.assertGreater(context['market_context']['network_cost_fraction'],0)
        self.assertEqual(result['intent']['entry_context']['market_context'],context['market_context'])

    def test_promotion_history_gate_blocks_real_entries_but_not_position_management(self):
        self.engine.signer=object()
        self.engine.eligibility_provider=lambda:{'live_eligible':False,'consecutive_promotions':0}
        self.assertEqual(self.engine.prepare(self.row,100,6.5,1200)['status'],'live_eligibility_blocked')
        self.store.state['position']={'mint':'Mint','pool':'Pool','kind':'bonding_curve',
            'units':1000,'cost_usd':1.78,'opened_at':1200}
        self.assertEqual(self.engine.prepare(self.row,100,6.5,1200)['status'],'holding')

    def test_modeled_paper_execution_flags_cannot_enter_live(self):
        self.row['execution']={'model_only':True}
        with self.assertRaisesRegex(ValueError,'Paper'): self.engine.prepare(self.row,100,6.5,1200)
    def test_stale_data_and_changed_inventory_do_not_create_fills(self):
        self.assertEqual(self.engine.prepare(self.row,100,6.5,1300)['status'],'stale_market_observation')
        self.store.state['position']={'mint':'Mint','pool':'Pool','kind':'bonding_curve','units':2000,'cost_usd':1.78,'opened_at':1200}
        self.assertIn('inventory_changed',self.engine.prepare(self.row,100,6.5,1200)['status'])

    def test_two_recent_confirmed_losses_pause_entries_until_cooldown_or_a_win(self):
        now=time.time()
        for pnl in (-.3,-.2):
            self.store.save('confirmed_sell',{'mint':'Mint','pnl_usd':pnl})
        row=dict(self.row,observed_at=now)
        result=self.engine.prepare(row,100,6.5,now)
        self.assertEqual(result['status'],'loss_streak_cooldown')
        self.assertEqual(result['risk']['consecutive_losses'],2)
        self.store.save('confirmed_sell',{'mint':'Mint','pnl_usd':.1})
        self.assertIsNone(self.engine.loss_streak_cooldown(now))

class OrderJournal:
    def __init__(self):
        import sqlite3
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE orders(order_id TEXT,signature TEXT,state TEXT)')
        self.next_status='confirmed'
    def unresolved(self): return self.db.execute("SELECT * FROM orders WHERE state IN ('pending','uncertain')").fetchall()
    def reconcile(self,order): return self.next_status


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.rules=DipRules(entry_style='trend_pullback',evidence_mode='market_only',
            max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2)
        self.key=Keypair();self.user=str(self.key.pubkey());self.store=MemeLiveStore(self.temp.name,self.user,self.rules)
        self.orders=OrderJournal()
        self.intent={'id':'order','side':'buy','mint':'mint','pool':'pool','kind':'bonding_curve',
                     'input_units':10000000,'minimum':90,'sol_price_usd':100,'bar':960,'base_account':'base','quote_account':'quote'}
        self.tx=receipt(self.user,'mint','base','quote',0,100,-11505000,base_rent=1500000)
        parent=self
        class RPC:
            def call(self,method,params):
                if method!='getTransaction': raise AssertionError(method)
                return parent.tx
        self.engine=MemeLiveEngine(RPC(),self.user,self.store,self.rules,self.orders,self.key)
    def tearDown(self): self.store.close();self.orders.db.close();self.temp.cleanup()
    def reserve(self):
        self.store.state['pending']=copy.deepcopy(self.intent);self.store.save('reserved',self.intent)
    def test_confirmed_receipt_is_applied_once_across_restart(self):
        self.reserve();self.orders.db.execute("INSERT INTO orders VALUES('order','sig','confirmed')")
        result=self.engine.recover()
        self.assertEqual(result['status'],'confirmed_buy')
        self.assertEqual(self.store.state['position']['units'],100)
        self.assertAlmostEqual(self.store.state['position']['cost_usd'],1.0005)
        self.assertIsNone(self.store.state['pending'])
        self.assertEqual(self.engine.recover()['status'],'idle')
        self.store.close();self.store=MemeLiveStore(self.temp.name,self.user,self.rules)
        self.assertEqual(self.store.state['position']['units'],100)
    def test_confirmed_buy_preserves_intent_exit_limits(self):
        self.intent.update(take_profit_usd=.75,max_loss_usd=.15,
            entry_context={'recorded_before_order':True,'flow_metrics':{'buy_sell_ratio':1.2}})
        self.reserve();self.orders.db.execute("INSERT INTO orders VALUES('order','sig','confirmed')")
        self.engine.recover()
        self.assertEqual(self.store.state['position']['take_profit_usd'],.75)
        self.assertEqual(self.store.state['position']['max_loss_usd'],.15)
        self.assertEqual(self.store.state['position']['entry_context'],self.intent['entry_context'])

    def test_ambiguous_send_blocks_replacement_orders_and_preserves_position(self):
        self.reserve();self.orders.db.execute("INSERT INTO orders VALUES('order','sig','uncertain')");self.orders.next_status='uncertain'
        self.assertEqual(self.engine.recover()['status'],'uncertain_order_blocks_replacement')
        self.assertIsNotNone(self.store.state['pending']);self.assertIsNone(self.store.state['position'])
    def test_crash_before_signed_journal_can_release_only_unsent_intent(self):
        self.reserve()
        self.assertEqual(self.engine.recover()['status'],'unsent_intent_released')
        self.assertIsNone(self.store.state['pending'])
    def test_failed_transaction_charges_fee_without_inventing_position(self):
        self.reserve();self.orders.db.execute("INSERT INTO orders VALUES('order','sig','failed')");self.orders.next_status='failed'
        self.tx['meta']['err']={'InstructionError':[0,'InvalidArgument']}
        self.assertEqual(self.engine.recover()['status'],'transaction_failed')
        self.assertAlmostEqual(self.store.state['realized_pnl_usd'],-.0005)
        self.assertIsNone(self.store.state['position'])
        self.assertEqual(self.store.state['learning_model_live']['samples'],0)
    def test_missing_receipt_and_wrong_signature_do_not_apply_fills(self):
        self.reserve();self.orders.db.execute("INSERT INTO orders VALUES('order','sig','confirmed')");self.tx=None
        self.assertEqual(self.engine.recover()['status'],'waiting_for_confirmed_receipt')
        self.tx=receipt(self.user,'mint','base','quote',0,100,-11505000,base_rent=1500000)
        self.tx['transaction']['signatures'][0]='different'
        with self.assertRaisesRegex(ValueError,'signature'): self.engine.recover()
        self.assertIsNotNone(self.store.state['pending'])
    def test_sell_books_actual_proceeds_and_preserves_rent_principal(self):
        entry_context={'recorded_before_order':True,
            'flow_metrics':{'buy_sell_ratio':1.2,'hourly_turnover_to_liquidity':2,
                'max_completed_candle_range_fraction':.08}}
        self.store.state['position']={'mint':'mint','units':100,'cost_usd':1.0005,
            'entry_context':entry_context}
        self.store.save('reserved',{'id':'entry','side':'buy','mint':'mint','entry_context':entry_context})
        self.store.save('confirmed_buy',{'mint':'mint','signature':'entry-signature'})
        self.intent.update(side='sell',input_units=100,minimum=10000000)
        self.reserve();self.orders.db.execute("INSERT INTO orders VALUES('order','sig','confirmed')")
        self.tx=receipt(self.user,'mint','base','quote',100,0,-5000,quote_delta=12000000)
        result=self.engine.recover()
        self.assertEqual(result['status'],'confirmed_sell')
        self.assertAlmostEqual(result['pnl_usd'],.199)
        self.assertFalse(result['learning_update']['updated'])
        self.assertEqual(self.store.state['learning_model_live']['samples'],0)
        self.assertIsNone(self.store.state['position'])
        self.store.close();self.store=MemeLiveStore(self.temp.name,self.user,self.rules)
        self.assertEqual(self.store.state['learning_model_live']['samples'],1)

class PumpSwapTests(unittest.TestCase):
    def setUp(self):
        self.rpc=CurveRpc();rpc=self.rpc
        from meme_execution import FEE
        creator=Pubkey.new_unique();rpc.pool=pda(SWAP,b'pool',struct.pack('<H',0),bytes(creator),bytes(rpc.mint),bytes(WSOL))
        base=associated(rpc.pool,rpc.mint);quote=associated(rpc.pool,WSOL)
        rpc.records[str(rpc.pool)]=encoded('Pool',SWAP,{'creator':str(creator),'base_mint':str(rpc.mint),'quote_mint':str(WSOL),
            'pool_base_token_account':str(base),'pool_quote_token_account':str(quote)})
        rpc.records[str(pda(SWAP,b'global_config'))]=encoded('GlobalConfig',SWAP,{'buyback_fee_recipients':[str(rpc.buyback)]*8})
        rpc.records[str(pda(FEE,b'fee_config',bytes(SWAP)))]=encoded('FeeConfig',SWAP,{'flat_fees':{'lp_fee_bps':25,'protocol_fee_bps':100}})
        rpc.records[str(base)]=token(rpc.mint,rpc.pool,10**15)
        rpc.records[str(quote)]=token(WSOL,rpc.pool,100*10**9)
        rpc.records[str(associated(rpc.buyback,WSOL))]=token(WSOL,rpc.buyback,0)
        self.adapter=MemeAdapter(rpc,rpc.user,str(rpc.mint),str(rpc.pool),'pumpswap')
    def test_pumpswap_wraps_exact_shortfall_and_charges_own_rent_as_locked_principal(self):
        market=self.adapter.snapshot(True);unsigned,ctx=self.adapter.build('buy',10000000,snapshot=market)
        self.assertEqual(ctx['wrap_lamports'],10000000)
        self.assertEqual(len(unsigned.message.instructions),5)
        self.rpc.simulated=copy.deepcopy(self.rpc.records)
        self.rpc.simulated[str(self.rpc.base)]=token(self.rpc.mint,self.rpc.user,ctx['expected_output'])
        self.rpc.simulated[str(self.rpc.quote)]=token(WSOL,self.rpc.user,0)
        for key in (self.rpc.base,self.rpc.quote): self.rpc.simulated[str(key)]['lamports']=1500000
        self.rpc.simulated[str(self.rpc.user)]['lamports']-=10000000+5000+3000000
        result=self.adapter.simulate(unsigned,ctx)
        self.assertEqual(result['rent_lamports'],3000000)
        self.assertEqual(result['recoverable_rent_lamports'],3000000)
    def test_existing_wrapped_balance_reduces_native_funding(self):
        self.rpc.records[str(self.rpc.quote)]=token(WSOL,self.rpc.user,4000000)
        market=self.adapter.snapshot(True);unsigned,ctx=self.adapter.build('buy',10000000,snapshot=market)
        self.assertEqual(ctx['wrap_lamports'],6000000)
    def test_special_modes_are_rejected_without_signing(self):
        row=decode(self.rpc.records[str(self.rpc.pool)],'Pool',SWAP);row['is_cashback_coin']=True
        self.rpc.records[str(self.rpc.pool)]=encoded('Pool',SWAP,row)
        with self.assertRaisesRegex(ValueError,'special'): self.adapter.snapshot(True)

class AdditionalGuardTests(unittest.TestCase):
    setUp=EngineTests.setUp
    tearDown=EngineTests.tearDown
    def test_recoverable_rent_is_not_mistaken_for_trade_loss(self):
        with patch.object(FakeAdapter,'simulate',return_value={'rent_lamports':3000000,'recoverable_rent_lamports':3000000,
                                                            'fee_lamports':5000,'output_units':100,'simulation_passed':True}):
            result=self.engine.prepare(self.row,100,6.5,1200)
        self.assertEqual(result['status'],'unsigned_simulation_passed',result)
    def test_failed_reverse_probe_blocks_new_entry(self):
        with patch.object(FakeAdapter,'simulate_round_trip',return_value={'simulation_passed':False}):
            self.assertEqual(self.engine.prepare(self.row,100,6.5,1200)['status'],'reverse_route_simulation_failed')
        self.assertIsNone(self.store.state['pending'])
    def test_exit_failure_preserves_confirmed_position(self):
        self.store.state['position']={'mint':'Mint','pool':'Pool','kind':'bonding_curve','units':1000,'cost_usd':1.78,'opened_at':0}
        with patch.object(FakeAdapter,'simulate',side_effect=ValueError('no reverse quote')):
            with self.assertRaises(ValueError): self.engine.prepare(self.row,100,6.5,1200)
        self.assertEqual(self.store.state['position']['units'],1000)
    def test_wallet_policy_binding_and_single_writer_are_enforced(self):
        with self.assertRaisesRegex(ValueError,'holds this state'): MemeLiveStore(self.temp.name,'user',self.rules)
        self.store.close()
        with self.assertRaisesRegex(ValueError,'differ'): MemeLiveStore(self.temp.name,'different',self.rules)
        self.store=MemeLiveStore(self.temp.name,'user',self.rules)

class RunnerCycleTests(unittest.TestCase):
    def test_watchlist_to_signal_to_simulation_without_signing_or_paper_fill(self):
        from run_meme_live import tick
        row=trend_snapshot();row.update(market_kind='bonding_curve',liquidity_usd=10000)
        class Sources:
            def observation(self,mint): return copy.deepcopy(row)
            def discover(self): return []
        class Feed:
            def poll(self,wallets): return {'signals':[],'callouts':[]}
            def read_context(self,wallets): return row['research_context']
        rules=DipRules(entry_style='trend_pullback',evidence_mode='market_only',max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2)
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',rules)
            try:
                engine=MemeLiveEngine({},'user',store,rules,adapter_factory=FakeAdapter)
                with patch('run_meme_live.valuation',return_value=100),patch('run_meme_live.wallet_equity',return_value=6.5),patch('meme_live.time.time',return_value=1200):
                    result=tick(engine,Sources(),Feed(),['watched'],['Mint'],False)
                self.assertEqual(result['status'],'scan_complete')
                self.assertEqual(result['decisions'][0]['status'],'unsigned_simulation_passed',result)
                self.assertFalse(result['execution_enabled'])
                self.assertIsNone(store.state['position'])
            finally: store.close()

class ScanPacingTests(unittest.TestCase):
    def test_flat_scan_rests_but_position_and_pending_order_checks_use_shorter_rest(self):
        from run_meme_live import cycle_rest
        self.assertEqual(cycle_rest({'position':None,'pending':None},30),30)
        self.assertEqual(cycle_rest({'position':{'mint':'held'}},30),15)
        self.assertEqual(cycle_rest({'pending':{'id':'uncertain'}},30),15)
        self.assertEqual(cycle_rest({'position':{'mint':'held'}},10),10)

    def test_scan_caps_at_ten_unique_candidates_and_rotates_remaining_leads(self):
        from run_meme_live import tick
        class Sources:
            def observation(self,mint): return {'mint':mint}
            def discover(self): return []
        class Feed:
            def poll(self,wallets): return {}
            def read_context(self,wallets): return {}
        rules=DipRules(entry_style='trend_pullback',evidence_mode='market_only',max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2)
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',rules)
            try:
                engine=MemeLiveEngine({},'user',store,rules,adapter_factory=FakeAdapter)
                ranked=[f'mint{i}' for i in range(20)]
                with patch('run_meme_live.valuation',return_value=100),patch('run_meme_live.wallet_equity',return_value=6.5),patch('run_meme_live.rank_candidate_mints',return_value=ranked),patch.object(engine,'prepare',return_value={'status':'entry_rejected','reasons':['test rejection']}):
                    first=tick(engine,Sources(),Feed(),['watched'],[],False)
                    second=tick(engine,Sources(),Feed(),['watched'],[],False)
                first_mints=[row['mint'] for row in first['decisions']]
                second_mints=[row['mint'] for row in second['decisions']]
                self.assertEqual(len(set(first_mints)),10)
                self.assertEqual(first_mints[:2],second_mints[:2])
                self.assertNotEqual(first_mints[2:],second_mints[2:])
            finally: store.close()

class RulesAdoptionTests(unittest.TestCase):
    def test_explicit_flat_adoption_preserves_losses_and_halt(self):
        with tempfile.TemporaryDirectory() as root:
            old=DipRules(entry_style='trend_pullback',trend_min_bars=8)
            store=MemeLiveStore(root,'user',old)
            store.state.update(halted=True,realized_pnl_usd=-.25,peak_equity=6.5)
            store.save('test',{});store.close()
            new=replace(old,trend_min_bars=3)
            with self.assertRaises(ValueError): MemeLiveStore(root,'user',new)
            store=MemeLiveStore(root,'user',new,adopt_rules=True)
            self.assertTrue(store.state['halted'])
            self.assertEqual(store.state['realized_pnl_usd'],-.25)
            self.assertEqual(store.state['peak_equity'],6.5)
            self.assertEqual(store.state['binding']['rules']['trend_min_bars'],3)
            store.close()
    def test_positions_and_unresolved_orders_prevent_rule_changes(self):
        import sqlite3
        for position,unresolved in [(True,False),(False,True)]:
            with tempfile.TemporaryDirectory() as root:
                old=DipRules(entry_style='trend_pullback');store=MemeLiveStore(root,'user',old)
                if position: store.state['position']={'mint':'held'}
                store.save('test',{});store.close()
                if unresolved:
                    db=sqlite3.connect(str(Path(root)/'orders.sqlite'))
                    db.execute('CREATE TABLE orders(state TEXT)');db.execute("INSERT INTO orders VALUES('uncertain')");db.commit();db.close()
                with self.assertRaises(ValueError): MemeLiveStore(root,'user',replace(old,trend_min_bars=3),adopt_rules=True)
