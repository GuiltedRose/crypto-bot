"""Locally constructed Pump curve V3 / PumpSwap V2 transactions.

No remote transaction builder. Accepts a public key, not a signing secret.
Simulation is unsigned and cannot submit a transaction. Unsupported extensions,
cashback, mayhem and holder-reward features fail closed.
"""
import base64
import hashlib
import json
import struct
import time
from pathlib import Path
from solders.pubkey import Pubkey
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0, to_bytes_versioned
from solders.transaction import VersionedTransaction
from solders.signature import Signature
from solders.hash import Hash
from solders.system_program import transfer, TransferParams
from meme_dip import SOL_MINT
from wallet import MAINNET_GENESIS

TOKEN = Pubkey.from_string('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')
TOKEN2022 = Pubkey.from_string('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')
ATA = Pubkey.from_string('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL')
SYSTEM = Pubkey.default()
WSOL = Pubkey.from_string(SOL_MINT)
CURVE = Pubkey.from_string('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P')
SWAP = Pubkey.from_string('pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA')
FEE = Pubkey.from_string('pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ')


def specification(stem):
    path=Path(__file__).parent/'vendor'/f'{stem}.json'
    raw=path.read_bytes(); manifest=json.loads(path.with_name(f'{stem}.manifest.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=manifest['sha256']:
        raise ValueError('Pinned instruction specification changed')
    value=json.loads(raw)
    if value['address']!=manifest['program']:
        raise ValueError('Specification program mismatch')
    return value


SPECS={str(CURVE):specification('pump'),str(SWAP):specification('pump_amm')}


def pda(program,*seeds):
    return Pubkey.find_program_address(list(seeds),program)[0]


def associated(owner,mint,program=TOKEN):
    return pda(ATA,bytes(owner),bytes(program),bytes(mint))


def raw_account(account,owner):
    if not account or account['owner']!=str(owner) or account.get('executable',False):
        raise ValueError('Account owner/state mismatch')
    return base64.b64decode(account['data'][0],validate=True)


def decode(account,name,program):
    spec=SPECS[str(program)]; raw=raw_account(account,FEE if name=='FeeConfig' else program)
    expected=bytes(next(a['discriminator'] for a in spec['accounts'] if a['name']==name))
    if raw[:8]!=expected:
        raise ValueError('Account discriminator mismatch')
    types={t['name']:t['type'] for t in spec['types']}
    # Old curve/pool accounts have zero-valued trailing fields. Only the
    # documented fixed-size trade accounts may be expanded this way.
    minimum={'BondingCurve':81,'Pool':243}.get(name)
    if minimum:
        if len(raw)<minimum: raise ValueError('Truncated trade account')
        raw+=bytes(max(0,512-len(raw)))
    offset=8
    def read(kind):
        nonlocal offset
        if kind=='pubkey':
            size=32
        elif kind=='bool': size=1
        elif isinstance(kind,str) and kind[0] in ('u','i'): size=int(kind[1:])//8
        elif isinstance(kind,dict):
            if 'defined' in kind:
                return {f['name']:read(f['type']) for f in types[kind['defined']['name']]['fields']}
            element,count=kind['array'] if 'array' in kind else (kind['vec'],read('u32'))
            if count>1000: raise ValueError('Excessive account vector')
            return [read(element) for _ in range(count)]
        else: raise ValueError('Unsupported field')
        data=raw[offset:offset+size];offset+=size
        if len(data)!=size: raise ValueError('Truncated account field')
        if kind=='pubkey': return str(Pubkey.from_bytes(data))
        value=int.from_bytes(data,'little',signed=kind.startswith('i'))
        if kind=='bool' and value not in (0,1): raise ValueError('Invalid account boolean')
        return bool(value) if kind=='bool' else value
    return {f['name']:read(f['type']) for f in types[name]['fields']}


def extensions(raw,kind,allowed):
    if len(raw)==(82 if kind==1 else 165): return
    if len(raw)<166 or raw[165]!=kind:
        raise ValueError('Invalid Token-2022 account type')
    offset=166; seen=set()
    while offset<len(raw):
        if not any(raw[offset:]): break
        if offset+4>len(raw): raise ValueError('Truncated token extension')
        tag,size=struct.unpack_from('<HH',raw,offset);offset+=4
        if tag not in allowed or tag in seen or offset+size>len(raw):
            raise ValueError('Unsupported or invalid token extension')
        seen.add(tag);offset+=size


def mint_info(account,require_revoked=True):
    owner=Pubkey.from_string(account['owner'])
    if owner not in (TOKEN,TOKEN2022): raise ValueError('Unsupported token program')
    raw=raw_account(account,owner)
    if len(raw)<82 or raw[45]!=1: raise ValueError('Invalid mint')
    if require_revoked and (int.from_bytes(raw[:4],'little')!=0 or int.from_bytes(raw[46:50],'little')!=0):
        raise ValueError('Mint/freeze authority remains enabled')
    if owner==TOKEN and len(raw)!=82: raise ValueError('Unexpected SPL mint size')
    if owner==TOKEN2022: extensions(raw,1,{18,19})  # metadata pointer and metadata only
    decimals=raw[44]
    if decimals>12: raise ValueError('Unsupported mint decimals')
    return owner,decimals


def token_balance(account,mint,owner,program):
    if account is None: return 0
    raw=raw_account(account,program)
    if (len(raw)<165 or raw[:32]!=bytes(mint) or raw[32:64]!=bytes(owner) or raw[108]!=1
            or int.from_bytes(raw[72:76],'little') or int.from_bytes(raw[129:133],'little')):
        raise ValueError('Token account mint/owner/state/delegate mismatch')
    if program==TOKEN and len(raw)!=165: raise ValueError('Unexpected SPL account size')
    if program==TOKEN2022: extensions(raw,2,{7})  # immutable owner only
    return int.from_bytes(raw[64:72],'little')


def create_ata(user,mint,program):
    return Instruction(ATA,b'\x01',[AccountMeta(user,True,True),AccountMeta(associated(user,mint,program),False,True),
        AccountMeta(user,False,False),AccountMeta(mint,False,False),AccountMeta(SYSTEM,False,False),AccountMeta(program,False,False)])


def fee_ceiling(config,creator_bps):
    schedules=[config['flat_fees']]+[row['fees'] for row in config['fee_tiers']]
    # SOL quotes only. Stable/exotic schedules do not apply.
    rate=max(sum(f.values()) for f in schedules)+creator_bps
    if type(rate) is not int or not 0<=rate<=500: raise ValueError('Unsupported fee ceiling')
    return rate


def quote_output(side,amount,base,quote,fee_bps):
    if type(amount) is not int or min(amount,base,quote)<=0 or not 0<=fee_bps<=500:
        raise ValueError('Invalid integer quote inputs')
    if side=='buy':
        net=max(0,amount*10000//(10000+fee_bps)-4)
        return net*base//(quote+net)
    if side!='sell': raise ValueError('Unknown side')
    gross=amount*quote//(base+amount)
    return max(0,gross-(gross*fee_bps+9999)//10000-4)


class MemeAdapter:
    def __init__(self,rpc,user,mint,pool,kind):
        if kind not in ('bonding_curve','pumpswap'): raise ValueError('Unsupported market kind')
        self.rpc,self.user,self.mint,self.pool=rpc,Pubkey.from_string(str(user)),Pubkey.from_string(mint),Pubkey.from_string(pool)
        self.kind=kind;self.program=CURVE if kind=='bonding_curve' else SWAP
        if self.mint==WSOL: raise ValueError('A meme mint is required')
        if rpc.call('getGenesisHash',[])!=MAINNET_GENESIS: raise ValueError('Mainnet RPC required')

    def accounts(self,keys,min_slot=None):
        config={'encoding':'base64','commitment':'confirmed'}
        if min_slot is not None: config['minContextSlot']=min_slot
        row=self.rpc.call('getMultipleAccounts',[[str(key) for key in keys],config])
        if len(row['value'])!=len(keys): raise ValueError('Incomplete RPC account batch')
        return row['value'],row['context']['slot']

    def snapshot(self,for_entry=False):
        global_key=pda(self.program,b'global' if self.kind=='bonding_curve' else b'global_config')
        fee_key=pda(FEE,b'fee_config',bytes(self.program))
        rows,slot=self.accounts([self.pool,global_key,fee_key,self.mint])
        trade=decode(rows[0],'BondingCurve' if self.kind=='bonding_curve' else 'Pool',self.program)
        config=decode(rows[1],'Global' if self.kind=='bonding_curve' else 'GlobalConfig',self.program)
        fees=decode(rows[2],'FeeConfig',self.program)
        token_program,decimals=mint_info(rows[3],require_revoked=for_entry)
        if any(trade.get(key) for key in ('is_mayhem_mode','is_cashback_coin','is_holder_reward')):
            raise ValueError('Unsupported special token mode')
        if self.kind=='bonding_curve':
            if self.pool!=pda(CURVE,b'bonding-curve',bytes(self.mint)): raise ValueError('Curve PDA mismatch')
            if trade['quote_mint']!=str(SYSTEM): raise ValueError('Only native SOL quote supported')
            if trade['complete']: raise ValueError('Curve migrated; resolve its PumpSwap pool')
            base_vault=associated(self.pool,self.mint,token_program);quote_vault=associated(self.pool,WSOL)
        else:
            if trade['base_mint']!=str(self.mint) or trade['quote_mint']!=SOL_MINT:
                raise ValueError('Pool mint/quote mismatch')
            expected=pda(SWAP,b'pool',struct.pack('<H',trade['index']),bytes(Pubkey.from_string(trade['creator'])),bytes(self.mint),bytes(WSOL))
            if expected!=self.pool: raise ValueError('Pool PDA mismatch')
            base_vault=Pubkey.from_string(trade['pool_base_token_account']);quote_vault=Pubkey.from_string(trade['pool_quote_token_account'])
        base_user=associated(self.user,self.mint,token_program);quote_user=associated(self.user,WSOL)
        volume=pda(self.program,b'user_volume_accumulator',bytes(self.user))
        buyback=Pubkey.from_string(config['buyback_fee_recipients'][0])
        recipient=buyback if self.kind=='bonding_curve' else associated(buyback,WSOL)
        keys=[self.pool,global_key,fee_key,self.mint,base_vault,quote_vault,base_user,quote_user,self.user,volume,recipient]
        fresh,slot=self.accounts(keys,slot)
        # Use one coherent batch for reserves, config, balances and identities.
        current=decode(fresh[0],'BondingCurve' if self.kind=='bonding_curve' else 'Pool',self.program)
        identity=('creator','complete','quote_mint','base_mint','pool_base_token_account','pool_quote_token_account',
                  'is_mayhem_mode','is_cashback_coin','is_holder_reward','creator_fee_bps','index','depth')
        if any(current.get(k)!=trade.get(k) for k in identity) or fresh[1]!=rows[1] or fresh[2]!=rows[2] or fresh[3]!=rows[3]:
            raise ValueError('Market identity/config changed during snapshot; retry')
        trade=current
        base_balance=token_balance(fresh[4],self.mint,self.pool,token_program)
        if self.kind=='bonding_curve':
            base,quote=trade['virtual_token_reserves'],trade['virtual_quote_reserves']
        else:
            base=base_balance;quote=token_balance(fresh[5],WSOL,self.pool,TOKEN)+trade['virtual_quote_reserves']
        if min(base,quote)<=0: raise ValueError('Empty reserves')
        if not fresh[10]: raise ValueError('Buyback account missing')
        if self.kind=='pumpswap': token_balance(fresh[10],WSOL,buyback,TOKEN)
        if fresh[8] is None or fresh[8]['owner']!=str(SYSTEM): raise ValueError('Invalid wallet system account')
        return dict(read_at=time.time(),slot=slot,trade=trade,config=config,token_program=token_program,decimals=decimals,
            global_key=global_key,fee_key=fee_key,base_vault=base_vault,quote_vault=quote_vault,base_user=base_user,
            quote_user=quote_user,volume=volume,recipient=recipient,base=base,quote=quote,records=fresh,keys=keys,
            fee_bps=fee_ceiling(fees,trade.get('creator_fee_bps',0)),
            base_balance=token_balance(fresh[6],self.mint,self.user,token_program),
            quote_balance=token_balance(fresh[7],WSOL,self.user,TOKEN),native_lamports=fresh[8]['lamports'])

    def output(self,side,amount,snapshot):
        result=quote_output(side,amount,snapshot['base'],snapshot['quote'],snapshot['fee_bps'])
        if self.kind=='bonding_curve':
            if side=='buy' and result>=snapshot['trade']['real_token_reserves']:
                raise ValueError('Entry crosses migration boundary')
            if side=='sell' and result>snapshot['trade']['real_quote_reserves']:
                raise ValueError('Insufficient real curve quote reserves')
        return result

    def build(self,side,amount,slippage_bps=50,snapshot=None):
        s=snapshot or self.snapshot(for_entry=side=='buy')
        if time.time()-s['read_at']>10: raise ValueError('Quote expired')
        if type(slippage_bps) is not int or not 0<=slippage_bps<=50: raise ValueError('Slippage exceeds 0.5%')
        if type(amount) is not int or not 0<amount<2**64: raise ValueError('Invalid exact input')
        if side=='sell' and amount>s['base_balance']: raise ValueError('Insufficient owned tokens')
        if side=='buy' and amount*1000>s['quote']: raise ValueError('Entry exceeds 0.1% reserve impact limit')
        if self.kind=='pumpswap' and s['config']['disable_flags'] & (8 if side=='buy' else 16):
            raise ValueError('Pool side disabled')
        output=self.output(side,amount,s);minimum=output*(10000-slippage_bps)//10000
        if minimum<=0: raise ValueError('Zero minimum output')
        program=s['token_program']; instructions=[]
        if s['records'][6] is None:
            if side!='buy': raise ValueError('No token account for exit')
            instructions.append(create_ata(self.user,self.mint,program))
        wrap=0
        if self.kind=='pumpswap':
            if s['records'][7] is None: instructions.append(create_ata(self.user,WSOL,TOKEN))
            if side=='buy':
                wrap=max(0,amount-s['quote_balance'])
                if wrap:
                    instructions.append(transfer(TransferParams(from_pubkey=self.user,to_pubkey=s['quote_user'],lamports=wrap)))
                    instructions.append(Instruction(TOKEN,b'\x11',[AccountMeta(s['quote_user'],False,True)]))
        mapping=dict(user=self.user,global_config=s['global_key'],global_=s['global_key'],base_mint=self.mint,
            quote_mint=WSOL,base_token_program=program,quote_token_program=TOKEN,system_program=SYSTEM,
            pool=self.pool,bonding_curve=self.pool,user_base_token_account=s['base_user'],associated_base_user=s['base_user'],
            user_quote_token_account=s['quote_user'],associated_quote_user=s['quote_user'],pool_base_token_account=s['base_vault'],
            associated_base_bonding_curve=s['base_vault'],pool_quote_token_account=s['quote_vault'],associated_quote_bonding_curve=s['quote_vault'],
            user_volume_accumulator=s['volume'],fee_config=s['fee_key'],buyback_fee_recipient=s['recipient'],
            event_authority=pda(self.program,b'__event_authority'),program=self.program)
        mapping['global']=s['global_key']
        name=('buy_exact_quote_in_v3' if side=='buy' else 'sell_v3') if self.kind=='bonding_curve' else ('buy_exact_quote_in_v2' if side=='buy' else 'sell_v2')
        definition=next(i for i in SPECS[str(self.program)]['instructions'] if i['name']==name)
        data=bytes(definition['discriminator'])+struct.pack('<QQ',amount,minimum)+(b'\x00' if self.kind=='bonding_curve' and side=='buy' else b'')
        instructions.append(Instruction(self.program,data,[AccountMeta(mapping[a['name']],a.get('signer',False),a.get('writable',False)) for a in definition['accounts']]))
        block=self.rpc.call('getLatestBlockhash',[{'commitment':'confirmed'}])['value']
        message=MessageV0.try_compile(self.user,instructions,[],Hash.from_string(block['blockhash']))
        unsigned=VersionedTransaction.populate(message,[Signature.default()])
        return unsigned,dict(side=side,input_units=amount,min_output=minimum,expected_output=output,wrap_lamports=wrap,
            instructions=instructions,message_hash=hashlib.sha256(to_bytes_versioned(message)).hexdigest(),last_valid_block_height=block['lastValidBlockHeight'],snapshot=s)

    def simulate(self,unsigned,context,max_fee=20000,max_rent=5000000,reserve=5000000):
        s=context['snapshot']
        if time.time()-s['read_at']>10: raise ValueError('Quote expired before simulation')
        if hashlib.sha256(to_bytes_versioned(unsigned.message)).hexdigest()!=context['message_hash']:
            raise ValueError('Transaction message changed')
        if unsigned.message.header.num_required_signatures!=1 or unsigned.message.account_keys[0]!=self.user:
            raise ValueError('Unexpected signer')
        fee=self.rpc.call('getFeeForMessage',[base64.b64encode(to_bytes_versioned(unsigned.message)).decode(),{'commitment':'confirmed'}])['value']
        if type(fee) is not int or not 0<=fee<=max_fee: raise ValueError('Network fee exceeds budget')
        # Return every writable account, including setup/volume accounts. Their
        # rent increases must explain all extra SOL spent beyond the trade/fee.
        keys=list(unsigned.message.account_keys)
        addresses=[str(k) for k in keys]
        before,slot=self.accounts(keys,s['slot'])
        observed={str(k):v for k,v in zip(keys,before)}
        for key,record in zip(s['keys'],s['records']):
            if key in (self.user,self.mint,s['global_key'],s['fee_key'],s['base_user'],s['quote_user']) and str(key) in observed and observed[str(key)]!=record:
                raise ValueError('Wallet/config changed before simulation')
        result=self.rpc.call('simulateTransaction',[base64.b64encode(bytes(unsigned)).decode(),{
            'encoding':'base64','sigVerify':False,'commitment':'confirmed','minContextSlot':slot,
            'accounts':{'encoding':'base64','addresses':addresses}}])['value']
        if result['err'] is not None: raise ValueError('Swap simulation rejected')
        after=result.get('accounts')
        if not after or len(after)!=len(keys): raise ValueError('Incomplete simulation results')
        values=dict(zip(addresses,after)); user_after=values[str(self.user)]
        base_after=token_balance(values[str(s['base_user'])],self.mint,self.user,s['token_program'])
        quote_after=token_balance(values.get(str(s['quote_user'])),WSOL,self.user,TOKEN) if self.kind=='pumpswap' else 0
        side=context['side'];amount=context['input_units'];minimum=context['min_output']
        native_spent=s['native_lamports']-user_after['lamports']
        setup_keys={str(s['base_user']),str(s['quote_user']),str(s['volume']),str(self.pool)}
        rent=0;recoverable_rent=0
        for key,old,new in zip(addresses,before,after):
            if key not in setup_keys or new is None: continue
            old_len=len(base64.b64decode(old['data'][0])) if old else 0
            new_len=len(base64.b64decode(new['data'][0]))
            if old is None or new_len>old_len:
                # Only genuine rent-exemption deposits; tokens/SOL transfers
                # into an existing same-size account are not setup costs.
                required=self.rpc.call('getMinimumBalanceForRentExemption',[new_len])
                deposit=max(0,min(required,new['lamports'])-(old['lamports'] if old else 0))
                rent+=deposit
                if key in (str(s['base_user']),str(s['quote_user'])): recoverable_rent+=deposit
        if rent>max_rent: raise ValueError('Account setup exceeds rent budget')
        if side=='buy':
            if base_after-s['base_balance']<minimum: raise ValueError('Simulated buy output below minimum')
            if self.kind=='bonding_curve': spent=native_spent-fee-rent
            else:
                if s['quote_balance']+context['wrap_lamports']-quote_after!=amount: raise ValueError('Unexpected quote debit')
                spent=native_spent-fee-rent-context['wrap_lamports']+amount
            if spent!=amount: raise ValueError('Unexpected native debit during buy')
            if user_after['lamports']<reserve+max_fee: raise ValueError('Exit fee reserve would be depleted')
            output=base_after-s['base_balance']
        else:
            if s['base_balance']-base_after!=amount: raise ValueError('Unexpected token debit during sell')
            output=-native_spent+fee+rent if self.kind=='bonding_curve' else quote_after-s['quote_balance']
            if output<minimum: raise ValueError('Simulated exit output below minimum')
            if self.kind=='pumpswap' and native_spent>fee+rent: raise ValueError('Unexpected native debit during sell')
        if time.time()-s['read_at']>20: raise ValueError('Simulation quote expired')
        return dict(fee_lamports=fee,rent_lamports=rent,recoverable_rent_lamports=recoverable_rent,output_units=output,min_output=minimum,
                    native_delta_lamports=-native_spent,slot=slot,simulation_passed=True)

    def simulate_round_trip(self,input_units,max_fee=20000,max_rent=5000000):
        """Unsigned atomic buy/sell probe against real RPC state.

        Both swaps run in ONE simulation. No fake account override is sent.
        Sells the guaranteed minimum bought, leaving any better-fill surplus.
        This verifies current reversibility, not future sell availability.
        """
        s=self.snapshot(for_entry=True)
        _,buy=self.build('buy',input_units,snapshot=s)
        sell_snapshot=dict(s,base_balance=s['base_balance']+buy['min_output'],records=list(s['records']))
        # The preceding local ATA instruction creates this account atomically.
        # This only permits construction of the subsequent sell instruction.
        if sell_snapshot['records'][6] is None: sell_snapshot['records'][6]={}
        _,sell=self.build('sell',buy['min_output'],snapshot=sell_snapshot)
        instructions=buy['instructions']+[sell['instructions'][-1]]
        block=self.rpc.call('getLatestBlockhash',[{'commitment':'confirmed'}])['value']
        message=MessageV0.try_compile(self.user,instructions,[],Hash.from_string(block['blockhash']))
        unsigned=VersionedTransaction.populate(message,[Signature.default()])
        fee=self.rpc.call('getFeeForMessage',[base64.b64encode(to_bytes_versioned(message)).decode(),{'commitment':'confirmed'}])['value']
        if type(fee) is not int or not 0<=fee<=max_fee: raise ValueError('Round-trip fee exceeds budget')
        keys=[str(s['base_user']),str(s['quote_user']),str(self.user),str(s['volume']),str(self.pool)]
        before,slot=self.accounts([Pubkey.from_string(k) for k in keys],s['slot'])
        for index in (0,1,2):
            expected=s['records'][6+index] if index<2 else s['records'][8]
            if before[index]!=expected: raise ValueError('Wallet changed before round-trip simulation')
        result=self.rpc.call('simulateTransaction',[base64.b64encode(bytes(unsigned)).decode(),{
            'encoding':'base64','sigVerify':False,'commitment':'confirmed','minContextSlot':slot,
            'accounts':{'encoding':'base64','addresses':keys}}])['value']
        if result['err'] is not None: raise ValueError('Round-trip simulation rejected: '+json.dumps(result['err']))
        after=result.get('accounts')
        if not after or len(after)!=len(keys): raise ValueError('Incomplete round-trip simulation')
        base_after=token_balance(after[0],self.mint,self.user,s['token_program'])
        quote_after=token_balance(after[1],WSOL,self.user,TOKEN) if self.kind=='pumpswap' else 0
        if base_after<s['base_balance']: raise ValueError('Round trip spends inherited meme tokens')
        rent=0
        for old,new in zip(before,after):
            if new is None: continue
            old_size=len(base64.b64decode(old['data'][0])) if old else 0
            new_size=len(base64.b64decode(new['data'][0]))
            if old is None or new_size>old_size:
                rent+=max(0,min(self.rpc.call('getMinimumBalanceForRentExemption',[new_size]),new['lamports'])-(old['lamports'] if old else 0))
        if rent>max_rent: raise ValueError('Round-trip rent exceeds budget')
        delta=after[2]['lamports']-s['native_lamports']
        if self.kind=='pumpswap':
            output=quote_after-s['quote_balance']+input_units-buy['wrap_lamports']
            if delta != -buy['wrap_lamports']-rent-fee: raise ValueError('Round trip unexpected native debit')
        else: output=delta+input_units+rent+fee
        if output<sell['min_output']: raise ValueError('Round-trip sell violates minimum output')
        return {'simulation_passed':True,'buy_input_lamports':input_units,'sell_input_units':buy['min_output'],
                'sell_output_lamports':output,'surplus_token_units':base_after-s['base_balance'],
                'fee_lamports':fee,'rent_lamports':rent,'transactions_submitted':0}
