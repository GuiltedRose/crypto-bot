"""Explicit one-time SOL-to-USDC inventory setup, separate from strategy trades.

Default is read-only planning. --execute spends real funds and requires an
explicit SOL conversion amount. This is NOT a 1% strategy allocation.
"""
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import struct
from solders.instruction import Instruction,AccountMeta
from solders.message import MessageV0,to_bytes_versioned
from solders.transaction import VersionedTransaction
from solders.signature import Signature
from solders.system_program import transfer,TransferParams
from config import LiveConfig
from wallet import load_wallet
from live import Rpc,LiveOrders
from pumpswap import PumpSwap,PROGRAM,TOKEN,ATA,SYSTEM,IDL,pda,token_account
from execution import raw_units
from inventory_plan import plan


def prepare(adapter,amount_sol):
    s=adapter.snapshot()
    # Setup is intentionally narrow: selected USDC-base/WSOL-quote pool.
    from market import USDC_MINT
    if str(s['base'])!=USDC_MINT:
        raise ValueError('Unsupported setup orientation')
    amount=raw_units(amount_sol,9)
    volume=pda([b'user_volume_accumulator',bytes(adapter.user)])
    existing,_=adapter.accounts([s['user_base'],s['user_quote'],volume])
    if any(s['user_balances']):
        raise ValueError('Setup requires zero existing WSOL/USDC inventory; reconcile existing balances first')
    instructions=[]
    rent=0
    for account,mint,record in zip([s['user_base'],s['user_quote']],[s['base'],s['quote']],existing[:2]):
        if record is None:
            rent+=adapter.rpc.call('getMinimumBalanceForRentExemption',[165,{'commitment':'confirmed'}])
            instructions.append(Instruction(ATA,b'\x01',[
                AccountMeta(adapter.user,True,True),AccountMeta(account,False,True),
                AccountMeta(adapter.user,False,False),AccountMeta(mint,False,False),
                AccountMeta(SYSTEM,False,False),AccountMeta(TOKEN,False,False)]))
    if existing[2] is None:
        rent+=adapter.rpc.call('getMinimumBalanceForRentExemption',[90,{'commitment':'confirmed'}])
        definition=next(i for i in IDL['instructions'] if i['name']=='init_user_volume_accumulator')
        keys={'payer':adapter.user,'user':adapter.user,'user_volume_accumulator':volume,
              'system_program':SYSTEM,'event_authority':pda([b'__event_authority']),'program':PROGRAM}
        instructions.append(Instruction(PROGRAM,bytes(definition['discriminator']),[
            AccountMeta(keys[a['name']],a.get('signer',False),a.get('writable',False)) for a in definition['accounts']]))
    if amount<=0 or amount+rent+20000+5000000>s['native_lamports']:
        raise ValueError('Conversion/rent would consume native fee reserve')
    instructions.append(transfer(TransferParams(from_pubkey=adapter.user,to_pubkey=s['user_quote'],lamports=amount)))
    instructions.append(Instruction(TOKEN,b'\x11',[AccountMeta(s['user_quote'],False,True)]))
    # Synthetic balance is only for constructing the swap following funding
    # in this same atomic transaction. Simulation below validates actual effects.
    funded={**s,'user_balances':[0,amount]}
    swap,context=adapter.build('sell',amount,snapshot=funded)
    compiled=swap.message.instructions[0]
    keys=swap.message.account_keys
    # Recreate original account privileges from the official local definition.
    definition=next(i for i in IDL['instructions'] if i['name']=='buy_exact_quote_in_v2')
    metas=[AccountMeta(keys[index],spec.get('signer',False),spec.get('writable',False))
           for index,spec in zip(compiled.accounts,definition['accounts'])]
    instructions.append(Instruction(PROGRAM,bytes(compiled.data),metas))
    message=MessageV0.try_compile(adapter.user,instructions,[],swap.message.recent_blockhash)
    unsigned=VersionedTransaction.populate(message,[Signature.default()])
    fee=adapter.rpc.call('getFeeForMessage',[base64.b64encode(to_bytes_versioned(message)).decode(),{'commitment':'confirmed'}])['value']
    if fee is None or fee>20000:
        raise ValueError('Unexpected setup network fee')
    simulation=adapter.rpc.call('simulateTransaction',[base64.b64encode(bytes(unsigned)).decode(),{
        'encoding':'base64','sigVerify':False,'commitment':'confirmed',
        'accounts':{'encoding':'base64','addresses':[str(s['user_base']),str(s['user_quote']),str(adapter.user)]}}])['value']
    if simulation['err'] is not None or not simulation.get('accounts'):
        raise ValueError('Inventory setup simulation failed')
    after=simulation['accounts']
    usdc=token_account(after[0],s['base'],adapter.user)
    wsol=token_account(after[1],s['quote'],adapter.user)
    spent=s['native_lamports']-after[2]['lamports']
    if usdc<context['min_output'] or wsol!=0 or after[2]['lamports']<5000000 or spent>amount+rent+20000:
        raise ValueError('Simulated inventory setup violates funding bounds')
    return message,{'convert_sol':amount/1e9,'minimum_usdc':context['min_output']/1e6,
                    'simulated_usdc':usdc/1e6,'simulated_native_sol_remaining':after[2]['lamports']/1e9,
                    'estimated_rent_sol':rent/1e9,'network_fee_sol':fee/1e9}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--convert-sol',type=float)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    config=LiveConfig.from_env()
    adapter=PumpSwap(Rpc(config.rpc_url),load_wallet(config))
    if args.convert_sol is None:
        if args.execute:
            parser.error('--execute requires an explicit --convert-sol amount')
        print(json.dumps(plan(adapter),indent=2))
        return
    os.umask(0o077)
    Path('state').mkdir(exist_ok=True)
    lock=open('state/runner.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    message,report=prepare(adapter,args.convert_sol)
    print(json.dumps(report,indent=2))
    if not args.execute:
        print('Simulation only. No signature or transaction submitted.')
        return
    orders=LiveOrders(adapter.rpc,'state/setup.sqlite')
    if orders.db.execute('SELECT 1 FROM orders').fetchone():
        raise ValueError('Previous setup journal exists; reconcile it before another setup')
    signed=VersionedTransaction(message,[adapter.wallet])
    print(json.dumps({'setup_status':orders.submit('initial-inventory',str(signed.signatures[0]),bytes(signed)),
                      'signature':str(signed.signatures[0])}))


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print('Inventory setup stopped ('+type(error).__name__+'). Check the setup journal if submission began; do not retry blindly.')
        raise SystemExit(1)
