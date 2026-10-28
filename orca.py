"""Direct keyless Orca SOL/USDC exact-output instruction adapter.

The pinned official SDK helper never receives a wallet secret. Python checks
its program, amounts, account identities and RPC ownership independently.
"""
import base64
import hashlib
import json
import os
import struct
import subprocess
import time
from pathlib import Path
from solders.pubkey import Pubkey
from solders.instruction import Instruction,AccountMeta
from pumpswap import TOKEN,ata,token_account
from bot import SOL_MINT
from market import USDC_MINT

PROGRAM=Pubkey.from_string('whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc')
POOL=Pubkey.from_string('Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE')
MEMO=Pubkey.from_string('MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr')
HELPER=Path(__file__).parent/'bin/orca-quote'


def pool_identity(record,pool=POOL,expected_pair=(SOL_MINT,USDC_MINT)):
    if not record or record['owner']!=str(PROGRAM):raise ValueError('Orca pool owner mismatch')
    data=base64.b64decode(record['data'][0],validate=True)
    if len(data)!=653 or data[:8]!=hashlib.sha256(b'account:Whirlpool').digest()[:8]:
        raise ValueError('Orca pool layout mismatch')
    a,b=Pubkey.from_bytes(data[101:133]),Pubkey.from_bytes(data[181:213])
    if (str(a),str(b))!=tuple(expected_pair):raise ValueError('Orca pair mismatch')
    derived=Pubkey.find_program_address([b'whirlpool',data[8:40],bytes(a),bytes(b),data[43:45]],PROGRAM)[0]
    if derived!=pool:raise ValueError('Orca pool PDA mismatch')
    return a,b,Pubkey.from_bytes(data[133:165]),Pubkey.from_bytes(data[213:245])


def validate_instruction(raw,user,identity,amount,maximum,pool=POOL,flags=b'\x00\x00'):
    data=base64.b64decode(raw['data'],validate=True)
    accounts=[AccountMeta(Pubkey.from_string(a['pubkey']),a['isSigner'],a['isWritable']) for a in raw['accounts']]
    if (raw['programId']!=str(PROGRAM) or len(data)<43 or
        data[:8]!=hashlib.sha256(b'global:swap_v2').digest()[:8] or
        struct.unpack_from('<QQ',data,8)!=(amount,maximum) or data[40:42]!=flags):
        raise ValueError('Orca exact-output bounds/layout mismatch')
    remaining=data[42:]
    if remaining==b'\x00':count=15
    elif remaining==b'\x01\x01\x00\x00\x00\x06\x02':count=17
    else:raise ValueError('Unsupported Orca remaining-account layout')
    if len(accounts)!=count:raise ValueError('Unexpected Orca account count')
    a,b,vault_a,vault_b=identity
    expected=[TOKEN,TOKEN,MEMO,user,pool,a,b,ata(user,a),vault_a,ata(user,b),vault_b]
    if any(accounts[i].pubkey!=key for i,key in enumerate(expected)):
        raise ValueError('Orca wallet/mint/vault account mismatch')
    if accounts[14].pubkey!=Pubkey.find_program_address([b'oracle',bytes(pool)],PROGRAM)[0]:
        raise ValueError('Orca oracle PDA mismatch')
    if not accounts[3].is_signer or any(a.is_signer for i,a in enumerate(accounts) if i!=3):
        raise ValueError('Unexpected Orca signer')
    writable={4,7,8,9,10,11,12,13,14,*range(15,count)}
    if any(a.is_writable!=(i in writable) for i,a in enumerate(accounts)):
        raise ValueError('Orca account privileges mismatch')
    return Instruction(PROGRAM,data,accounts)


class Orca:
    def __init__(self,rpc,user):self.rpc,self.user=rpc,user
    def accounts(self,keys):
        return self.rpc.call('getMultipleAccounts',[[str(k) for k in keys],{'encoding':'base64','commitment':'confirmed'}])['value']
    def exact_output_buy(self,sol_units):
        started=time.time()
        identity=pool_identity(self.accounts([POOL])[0])
        request={'rpc_url':self.rpc.endpoint,'wallet':str(self.user),'pool':str(POOL),
                 'mint':SOL_MINT,'amount':sol_units,'mode':'ExactOut'}
        try:
            result=subprocess.run([str(HELPER)],input=json.dumps(request),text=True,capture_output=True,timeout=20,
                env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8'})
        except (subprocess.TimeoutExpired,OSError):
            raise ValueError('Orca public quote unavailable') from None
        if result.returncode:raise ValueError('Orca public quote unavailable')
        response=json.loads(result.stdout)
        quote=response['quote']
        if quote['mode']!='ExactOut' or quote['token_out']!=sol_units or len(response['instructions'])!=1:
            raise ValueError('Unexpected Orca quote/setup instructions')
        maximum=quote['max_input']
        if type(maximum) is not int or maximum<=0:raise ValueError('Invalid Orca quote amount')
        ix=validate_instruction(response['instructions'][0],self.user,identity,sol_units,maximum)
        validate_pool_accounts(self.accounts,ix,identity,POOL)
        return ix,{'sol_units':sol_units,'max_usdc_units':maximum,'quoted_at':started}


def validate_pool_accounts(accounts,ix,identity,pool):
    # Validate vault mint/authority and every supplied tick-array PDA against RPC.
    a,b,va,vb=identity
    vaults=accounts([va,vb]);token_account(vaults[0],a,pool);token_account(vaults[1],b,pool)
    ticks=[ix.accounts[i].pubkey for i in [11,12,13,*range(15,len(ix.accounts))]]
    records=accounts(ticks)
    for address,record in zip(ticks,records):
        if not record or record['owner']!=str(PROGRAM):raise ValueError('Orca tick array unavailable')
        data=base64.b64decode(record['data'][0],validate=True)
        index=struct.unpack_from('<i',data,8)[0]
        expected=Pubkey.find_program_address([b'tick_array',bytes(pool),str(index).encode()],PROGRAM)[0]
        if address!=expected:raise ValueError('Orca tick-array PDA mismatch')


BAT_MINT='EPeUFDgHRxs9xxEPVaL6kfGQvCon7jmAWKVUHuux1Tpz'
BAT_POOL=Pubkey.from_string('BrVVTnrihfyXiHZMVwPsKeLN2vVxMGpgk7Wt6cdrkZLV')


class BatOrca(Orca):
    """Reward sales: BAT exact input to USDC, no wrapping, setup or custody."""
    def quote_sell(self,available_units):
        started=time.time()
        record=self.accounts([BAT_POOL])[0]
        identity=pool_identity(record,BAT_POOL,(BAT_MINT,USDC_MINT))
        a,b,va,vb=identity
        vaults=self.accounts([va,vb])
        balance=token_account(vaults[0],a,BAT_POOL)
        # Bound each sale to 0.1% of actual input-token vault inventory.
        units=min(available_units,balance//1000)
        if type(units) is not int or units<=0:raise ValueError('BAT reward sale too small')
        request={'rpc_url':self.rpc.endpoint,'wallet':str(self.user),'pool':str(BAT_POOL),
                 'mint':BAT_MINT,'amount':units,'mode':'ExactIn'}
        try:
            result=subprocess.run([str(HELPER)],input=json.dumps(request),text=True,capture_output=True,timeout=20,
                env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8'})
        except (subprocess.TimeoutExpired,OSError):raise ValueError('BAT direct quote unavailable') from None
        if result.returncode:raise ValueError('BAT direct quote unavailable')
        response=json.loads(result.stdout);quote=response['quote']
        if quote['mode']!='ExactIn' or quote['token_in']!=units or len(response['instructions'])!=1:
            raise ValueError('Unexpected BAT swap/setup instructions')
        minimum=quote['min_output']
        if type(minimum) is not int or minimum<=0:raise ValueError('BAT reward sale too small')
        ix=validate_instruction(response['instructions'][0],self.user,identity,units,minimum,BAT_POOL,flags=b'\x01\x01')
        validate_pool_accounts(self.accounts,ix,identity,BAT_POOL)
        raw=base64.b64decode(record['data'][0],validate=True)
        spot=(int.from_bytes(raw[65:81],'little')/(1<<64))**2
        if minimum/units<spot*.995:raise ValueError('BAT reward sale price impact exceeds limit')
        return ix,{'input_units':units,'min_output':minimum,'quoted_at':started}
