"""Direct, locally constructed PumpSwap V2 adapter for standard SOL/USDC pools.

No remote transaction builders or private-key transmission. Supports only
non-canonical pools without creator fees, cashback or mayhem features.
"""
import base64
import json
import math
import struct
import time
import hashlib
from pathlib import Path
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0, to_bytes_versioned
from solders.transaction import VersionedTransaction
from solders.signature import Signature
from solders.hash import Hash
from bot import SOL_MINT
from market import USDC_MINT
from wallet import MAINNET_GENESIS

PROGRAM = Pubkey.from_string('pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA')
TOKEN = Pubkey.from_string('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')
ATA = Pubkey.from_string('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL')
SYSTEM = Pubkey.default()
FEE_PROGRAM = Pubkey.from_string('pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ')
DEFAULT_POOL = 'Gf7sXMoP8iRw4iiXmJ1nq4vxcRycbGXy5RL8a8LnTd3v'
_idl_path=Path(__file__).parent/'vendor/pump_amm.json'
_idl_raw=_idl_path.read_bytes()
_idl_manifest=json.loads(_idl_path.with_name('pump_amm.manifest.json').read_text())
if hashlib.sha256(_idl_raw).hexdigest()!=_idl_manifest['sha256']:
    raise ValueError('Pinned PumpSwap specification hash mismatch')
IDL=json.loads(_idl_raw)
if IDL['address']!=str(PROGRAM):
    raise ValueError('Pinned PumpSwap program identity mismatch')
TYPES = {t['name']: t['type'] for t in IDL['types']}


class SnapshotChanged(ValueError):
    """Pool identity/config changed between reads; discard and refresh."""


def coherent_pool(initial,latest):
    # Batch two includes current vault balances and current pool accounting.
    # Changing accrued fees/virtual reserves is normal trading activity, not
    # evidence that the pool identity is invalid. Never mix old accounting
    # with the fresh vault balances.
    identity=('index','creator','base_mint','quote_mint','lp_mint',
              'pool_base_token_account','pool_quote_token_account','coin_creator',
              'is_mayhem_mode','is_cashback_coin','is_holder_reward','creator_fee_bps')
    if any(initial.get(key)!=latest.get(key) for key in identity):
        raise SnapshotChanged('Pool identity/config changed while reading; refresh')
    return latest


def pda(seeds, program=PROGRAM):
    return Pubkey.find_program_address(seeds, program)[0]


def ata(owner, mint):
    return pda([bytes(owner), bytes(TOKEN), bytes(mint)], ATA)


def decode_type(data, kind, offset):
    if kind == 'pubkey':
        return str(Pubkey.from_bytes(data[offset:offset+32])), offset+32
    if kind == 'bool':
        value = data[offset]
        if value not in (0,1):
            raise ValueError('Invalid Borsh boolean')
        return bool(value), offset+1
    if isinstance(kind,str) and kind[0] in ('u','i'):
        width = int(kind[1:])//8
        if len(data) < offset+width:
            raise ValueError('Truncated account')
        return int.from_bytes(data[offset:offset+width], 'little', signed=kind[0]=='i'), offset+width
    if isinstance(kind,dict) and 'defined' in kind:
        return decode_struct(data, TYPES[kind['defined']['name']]['fields'], offset)
    if isinstance(kind,dict) and ('array' in kind or 'vec' in kind):
        if 'array' in kind:
            element,count = kind['array']
        else:
            element = kind['vec']
            count,offset = decode_type(data,'u32',offset)
        if count > 10000:
            raise ValueError('Excessive vector length')
        result = []
        for _ in range(count):
            value,offset = decode_type(data,element,offset)
            result.append(value)
        return result,offset
    raise ValueError('Unsupported account type')


def decode_struct(data, fields, offset):
    result = {}
    for field in fields:
        result[field['name']],offset = decode_type(data,field['type'],offset)
    return result,offset


def decode_account(account, name, owner):
    if not account or account['owner'] != str(owner):
        raise ValueError('Account owner mismatch: '+name)
    data = base64.b64decode(account['data'][0], validate=True)
    discriminator = next(a['discriminator'] for a in IDL['accounts'] if a['name']==name)
    if data[:8] != bytes(discriminator):
        raise ValueError('Account discriminator mismatch: '+name)
    # Official layout appends zero-valued fields to older accounts. No dynamic
    # padding for fee vectors or config: these must match the pinned schema.
    if name == 'Pool':
        if len(data) < 243:
            raise ValueError('Truncated pool')
        data += bytes(max(0, 512-len(data)))
    return decode_struct(data,TYPES[name]['fields'],8)[0]


def token_account(account, mint, authority):
    if not account:
        return 0
    if account['owner'] != str(TOKEN):
        raise ValueError('Only standard SPL Token accounts supported')
    data = base64.b64decode(account['data'][0], validate=True)
    if (len(data) != 165 or data[:32] != bytes(mint) or data[32:64] != bytes(authority)
            or data[108] != 1 or int.from_bytes(data[72:76],'little') != 0
            or int.from_bytes(data[129:133],'little') != 0):
        raise ValueError('Token account identity/state/delegate mismatch')
    return int.from_bytes(data[64:72],'little')


def expected_output(amount, base_reserves, quote_reserves, input_base, fees):
    if min(amount,base_reserves,quote_reserves) <= 0 or not 0 <= fees < 10000:
        raise ValueError('Invalid quote inputs')
    if input_base:
        gross = quote_reserves*amount//(base_reserves+amount)
        return gross*(10000-fees)//10000
    effective = amount*10000//(10000+fees)
    return base_reserves*effective//(quote_reserves+effective)


class PumpSwap:
    def __init__(self, rpc, wallet, pool=DEFAULT_POOL):
        self.rpc, self.wallet, self.pool = rpc, wallet, Pubkey.from_string(pool)
        if rpc.call('getGenesisHash',[]) != MAINNET_GENESIS:
            raise ValueError('Mainnet RPC required')
        self.user = wallet.pubkey()

    def accounts(self, addresses):
        response = self.rpc.call('getMultipleAccounts',[[str(a) for a in addresses],
                                 {'encoding':'base64','commitment':'confirmed'}])
        return response['value'], response['context']['slot']

    def snapshot(self):
        global_key = pda([b'global_config'])
        fee_key = pda([b'fee_config',bytes(PROGRAM)],FEE_PROGRAM)
        records,_ = self.accounts([self.pool])
        pool = decode_account(records[0],'Pool',PROGRAM)
        base,quote = Pubkey.from_string(pool['base_mint']),Pubkey.from_string(pool['quote_mint'])
        if {str(base),str(quote)} != {SOL_MINT,USDC_MINT}:
            raise ValueError('Only SOL/USDC pool approved')
        if (pool['is_mayhem_mode'] or pool['is_cashback_coin'] or pool['is_holder_reward'] or
                pool['coin_creator'] != str(SYSTEM) or pool['creator_fee_bps']):
            raise ValueError('Unsupported special/creator-fee pool')
        derived = pda([b'pool',struct.pack('<H',pool['index']),bytes(Pubkey.from_string(pool['creator'])),bytes(base),bytes(quote)])
        if derived != self.pool:
            raise ValueError('Pool PDA mismatch')
        vault_base = Pubkey.from_string(pool['pool_base_token_account'])
        vault_quote = Pubkey.from_string(pool['pool_quote_token_account'])
        user_base,user_quote = ata(self.user,base),ata(self.user,quote)
        addresses = [self.pool,global_key,fee_key,base,quote,vault_base,vault_quote,user_base,user_quote,self.user]
        records,slot = self.accounts(addresses)
        latest = decode_account(records[0],'Pool',PROGRAM)
        pool=coherent_pool(pool,latest)
        config = decode_account(records[1],'GlobalConfig',PROGRAM)
        fee_config = decode_account(records[2],'FeeConfig',FEE_PROGRAM)
        for account,decimals in zip(records[3:5],[9 if str(base)==SOL_MINT else 6,9 if str(quote)==SOL_MINT else 6]):
            if not account or account['owner'] != str(TOKEN):
                raise ValueError('Unsupported mint program')
            data = base64.b64decode(account['data'][0])
            if len(data)!=82 or data[44]!=decimals or data[45]!=1:
                raise ValueError('Invalid mint layout/decimals')
        base_balance = token_account(records[5],base,self.pool)
        quote_balance = token_account(records[6],quote,self.pool)+pool['virtual_quote_reserves']
        user_balances = [token_account(records[7],base,self.user),token_account(records[8],quote,self.user)]
        fees = fee_config['flat_fees']['lp_fee_bps']+fee_config['flat_fees']['protocol_fee_bps']
        if min(base_balance,quote_balance)<=0 or not 0<=fees<=100:
            raise ValueError('Invalid reserves or unexpectedly high pool fees')
        price = (base_balance/1e6)/(quote_balance/1e9) if str(base)==USDC_MINT else (quote_balance/1e6)/(base_balance/1e9)
        return dict(pool=pool,config=config,base=base,quote=quote,global_key=global_key,fee_key=fee_key,
                    vault_base=vault_base,vault_quote=vault_quote,user_base=user_base,user_quote=user_quote,
                    base_reserves=base_balance,quote_reserves=quote_balance,user_balances=user_balances,
                    native_lamports=records[9]['lamports'],fee_bps=fees,price=price,slot=slot,read_at=time.time())

    def build(self, side, input_units, slippage_bps=50, snapshot=None):
        """Construct unsigned message with exact-input and hard minimum output.

        Token accounts must already exist/funded. No implicit inventory
        conversion, rent spending, SOL wrapping or third-party transfers.
        """
        s = snapshot or self.snapshot()
        if time.time()-s['read_at'] > 10:
            raise ValueError('Stale pool snapshot')
        if side not in ('buy','sell') or not isinstance(input_units,int) or not 0<input_units<2**64:
            raise ValueError('Invalid swap request')
        if not 0<=slippage_bps<=50:
            raise ValueError('Slippage exceeds 0.5% maximum')
        buying_base = (side=='buy') == (str(s['base'])==SOL_MINT)
        if s['config']['disable_flags'] & (8 if buying_base else 16):
            raise ValueError('Pool trading disabled')
        input_index = 1 if buying_base else 0
        if s['user_balances'][input_index] < input_units:
            raise ValueError('Insufficient prepared token balance; inventory setup needed')
        # The initial reserve pays fees; an immutable floor would eventually
        # prevent closing an existing position.
        if s['native_lamports'] < (40000 if side=='buy' else 20000):
            raise ValueError('Insufficient native SOL for bounded execution fees')
        expected = expected_output(input_units,s['base_reserves'],s['quote_reserves'],not buying_base,s['fee_bps'])
        minimum = expected*(10000-slippage_bps)//10000
        if minimum<=0:
            raise ValueError('Swap too small')
        # Small reserve-relative trades only; on-chain bounds enforce the output.
        reserve = s['quote_reserves'] if buying_base else s['base_reserves']
        if input_units*1000 > reserve:
            raise ValueError('Input exceeds 0.1% of pool reserves')
        name = 'buy_exact_quote_in_v2' if buying_base else 'sell_v2'
        definition = next(i for i in IDL['instructions'] if i['name']==name)
        buyback = Pubkey.from_string(s['config']['buyback_fee_recipients'][0])
        mapping = dict(pool=self.pool,user=self.user,global_config=s['global_key'],base_mint=s['base'],quote_mint=s['quote'],
                       user_base_token_account=s['user_base'],user_quote_token_account=s['user_quote'],
                       pool_base_token_account=s['vault_base'],pool_quote_token_account=s['vault_quote'],
                       base_token_program=TOKEN,quote_token_program=TOKEN,system_program=SYSTEM,
                       user_volume_accumulator=pda([b'user_volume_accumulator',bytes(self.user)]),fee_config=s['fee_key'],
                       buyback_fee_recipient=ata(buyback,s['quote']),event_authority=pda([b'__event_authority']),program=PROGRAM)
        metas = [AccountMeta(mapping[a['name']],a.get('signer',False),a.get('writable',False)) for a in definition['accounts']]
        instruction = Instruction(PROGRAM,bytes(definition['discriminator'])+struct.pack('<QQ',input_units,minimum),metas)
        blockhash = self.rpc.call('getLatestBlockhash',[{'commitment':'confirmed'}])['value']
        message = MessageV0.try_compile(self.user,[instruction],[],Hash.from_string(blockhash['blockhash']))
        unsigned = VersionedTransaction.populate(message,[Signature.default()])
        return unsigned,dict(side=side,input_units=input_units,min_output=minimum,expected_output=expected,
                             input_index=input_index,snapshot=s,last_valid_block_height=blockhash['lastValidBlockHeight'],
                             message_hash=hashlib.sha256(to_bytes_versioned(message)).hexdigest())

    def simulate_and_sign(self, unsigned, context, max_network_fee_lamports=20000):
        """Simulate exact locally constructed message; sign only bounded swaps."""
        # Verify instruction identity/arguments/accounts by rebuilding from the
        # same snapshot. A fresh blockhash may differ, but instruction bytes and
        # keys must be identical; only the locally constructed message is used.
        s = context['snapshot']
        if hashlib.sha256(to_bytes_versioned(unsigned.message)).hexdigest()!=context['message_hash']:
            raise ValueError('Constructed transaction was modified')
        if time.time()-s['read_at'] > 10:
            raise ValueError('Quote expired')
        if unsigned.message.header.num_required_signatures!=1 or unsigned.message.account_keys[0]!=self.user:
            raise ValueError('Unexpected signing authority')
        if len(unsigned.message.instructions)!=1 or unsigned.message.address_table_lookups:
            raise ValueError('Unexpected instructions or lookup tables')
        ix = unsigned.message.instructions[0]
        if unsigned.message.account_keys[ix.program_id_index] != PROGRAM:
            raise ValueError('Unexpected program')
        name='buy_exact_quote_in_v2' if context['input_index']==1 else 'sell_v2'
        definition=next(i for i in IDL['instructions'] if i['name']==name)
        if bytes(ix.data)!=bytes(definition['discriminator'])+struct.pack('<QQ',context['input_units'],context['min_output']):
            raise ValueError('Swap bounds mismatch')
        fee = self.rpc.call('getFeeForMessage',[base64.b64encode(to_bytes_versioned(unsigned.message)).decode(),{'commitment':'confirmed'}])['value']
        if fee is None or fee>max_network_fee_lamports:
            raise ValueError('Network fee exceeds limit')
        response=self.rpc.call('simulateTransaction',[base64.b64encode(bytes(unsigned)).decode(),{
            'encoding':'base64','sigVerify':False,'commitment':'confirmed',
            'accounts':{'encoding':'base64','addresses':[str(s['user_base']),str(s['user_quote']),str(self.user)]}}])['value']
        if response['err'] is not None:
            raise ValueError('Swap simulation failed; no transaction signed')
        accounts=response.get('accounts')
        if not accounts or len(accounts)!=3:
            raise ValueError('Simulation omitted account results')
        after=[token_account(accounts[0],s['base'],self.user),token_account(accounts[1],s['quote'],self.user)]
        index=context['input_index']
        if s['user_balances'][index]-after[index] != context['input_units'] or after[1-index]-s['user_balances'][1-index]<context['min_output']:
            raise ValueError('Simulated token changes violate limits')
        native_spent=s['native_lamports']-accounts[2]['lamports']
        remaining_for_exit = max_network_fee_lamports if context['side']=='buy' else 0
        if native_spent>max_network_fee_lamports or accounts[2]['lamports']<remaining_for_exit:
            raise ValueError('Simulation spends rent/additional SOL beyond fee budget')
        if time.time()-s['read_at'] > 10:
            raise ValueError('Quote expired during simulation')
        signed=VersionedTransaction(unsigned.message,[self.wallet])
        return signed,fee
