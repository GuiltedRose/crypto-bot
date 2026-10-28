"""Keyless Jupiter exact-input spot routing; validate locally before signing.

Only standard SPL tokens, wallet ATAs and bounded Jupiter route instructions.
No remote transactions, SOL wrapping, tips, approvals, or external signers.
"""
import base64
import hashlib
import json
import math
import struct
import time
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from solders.pubkey import Pubkey
from solders.instruction import Instruction,AccountMeta
from solders.message import MessageV0,to_bytes_versioned
from solders.transaction import VersionedTransaction
from solders.signature import Signature
from solders.hash import Hash
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.compute_budget import set_compute_unit_limit,set_compute_unit_price
from pumpswap import TOKEN,ATA,SYSTEM
from tokens import ata,token_account,mint_decimals,program_for,TOKEN_2022
from market import USDC_MINT
from bot import SOL_MINT
from wallet import MAINNET_GENESIS

PROGRAM=Pubkey.from_string('JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4')
LOOKUP='AddressLookupTab1e1111111111111111111111111'
ENDPOINT='https://api.jup.ag/swap/v2/build'
_last_request=0.0
INTERMEDIATES=frozenset({SOL_MINT,'USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA',
                        'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
                        'USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB'})


def fetch_build(user,input_mint,output_mint,amount):
    global _last_request
    delay=2.1-(time.monotonic()-_last_request)
    if delay>0:
        time.sleep(delay)
    _last_request=time.monotonic()
    query=urlencode(dict(inputMint=input_mint,outputMint=output_mint,amount=amount,
                         taker=str(user),slippageBps=50,maxAccounts=40,wrapAndUnwrapSol='false',
                         useSharedAccounts='false',transactionVersion='0'))
    with urlopen(Request(ENDPOINT+'?'+query,headers={'User-Agent':'solana-spot-bot/1'}),timeout=25) as response:
        return json.load(response)


def instruction(raw):
    return Instruction(Pubkey.from_string(raw['programId']),base64.b64decode(raw['data'],validate=True),
                       [AccountMeta(Pubkey.from_string(a['pubkey']),a['isSigner'],a['isWritable']) for a in raw['accounts']])


def validate_build(document,user,input_mint,output_mint,amount,max_price_impact=.005):
    """Bind API data AND encoded on-chain bounds to our exact-input intent."""
    if (document['inputMint']!=input_mint or document['outputMint']!=output_mint or
        document['swapMode']!='ExactIn' or int(document['inAmount'])!=amount or amount<=0 or
        document['slippageBps']!=50 or document.get('otherInstructions') or
        document.get('cleanupInstruction') or document.get('tipInstruction')):
        raise ValueError('Unexpected Jupiter route envelope')
    output=int(document['outAmount'])
    minimum=(output*9950+9999)//10000
    impact=float(document['priceImpactPct'])
    if output<=0 or minimum<=0 or int(document['otherAmountThreshold'])!=minimum or not math.isfinite(impact):
        raise ValueError('Unsafe Jupiter output/impact bounds')
    if max_price_impact is not None and abs(impact)>max_price_impact:
        raise ValueError('Jupiter price impact exceeds entry limit')
    source,destination=ata(user,Pubkey.from_string(input_mint)),ata(user,Pubkey.from_string(output_mint))
    swap=instruction(document['swapInstruction'])
    data=bytes(swap.data)
    if swap.program_id!=PROGRAM:
        raise ValueError('Unexpected Jupiter program')
    # V2 fixed fields precede the route vector. Legacy routes put them last.
    route_v2=hashlib.sha256(b'global:route_v2').digest()[:8]
    shared_v2=hashlib.sha256(b'global:shared_accounts_route_v2').digest()[:8]
    route=hashlib.sha256(b'global:route').digest()[:8]
    shared=hashlib.sha256(b'global:shared_accounts_route').digest()[:8]
    if data[:8]==route_v2 and len(data)>=35:
        incoming,quoted,slip,platform,positive=struct.unpack_from('<QQHHH',data,8)
        if positive!=0:
            raise ValueError('Positive slippage fee unsupported')
        expected=[user,source,destination,Pubkey.from_string(input_mint),Pubkey.from_string(output_mint),program_for(input_mint),program_for(output_mint),PROGRAM]
        indexes=range(8)
        if struct.unpack_from('<I',data,30)[0] not in range(1,9):
            raise ValueError('Invalid route length')
    elif data[:8]==shared_v2 and len(data)>=36:
        incoming,quoted,slip,platform,positive=struct.unpack_from('<QQHHH',data,9)
        if positive!=0 or struct.unpack_from('<I',data,31)[0] not in range(1,9):
            raise ValueError('Invalid shared route bounds')
        authority=Pubkey.find_program_address([b'authority',bytes([data[8]])],PROGRAM)[0]
        event=Pubkey.find_program_address([b'__event_authority'],PROGRAM)[0]
        expected=[authority,user,source,destination,Pubkey.from_string(input_mint),Pubkey.from_string(output_mint),program_for(input_mint),program_for(output_mint),event,PROGRAM]
        indexes=[0,1,2,5,6,7,8,9,10,11]
    elif data[:8]==route and len(data)>=31:
        incoming,quoted,slip,platform=struct.unpack_from('<QQHB',data,len(data)-19)
        expected=[TOKEN,user,source,destination,PROGRAM,Pubkey.from_string(output_mint)]
        indexes=range(6)
    elif data[:8]==shared and len(data)>=32:
        incoming,quoted,slip,platform=struct.unpack_from('<QQHB',data,len(data)-19)
        expected=[TOKEN,user,source,destination,Pubkey.from_string(input_mint),Pubkey.from_string(output_mint),PROGRAM,PROGRAM]
        indexes=[0,2,3,6,7,8,9,10]
    else:
        raise ValueError('Unsupported Jupiter instruction layout')
    if (incoming,quoted,slip,platform)!=(amount,output,50,0):
        raise ValueError('Encoded Jupiter bounds mismatch')
    if len(swap.accounts)<max(indexes)+1 or any(swap.accounts[i].pubkey!=key for i,key in zip(indexes,expected)):
        raise ValueError('Jupiter token/signer account mismatch')
    if not any(a.pubkey==user and a.is_signer for a in swap.accounts):
        raise ValueError('Missing wallet signing authority')
    if any(a.is_signer and a.pubkey!=user for a in swap.accounts):
        raise ValueError('External signer prohibited')
    setups=[]
    for raw in document.get('setupInstructions',[]):
        ix=instruction(raw)
        accounts=ix.accounts
        if (ix.program_id!=ATA or bytes(ix.data)!=b'\x01' or len(accounts)!=6 or
            accounts[0]!=AccountMeta(user,True,True) or accounts[2].pubkey!=user or
            accounts[4].pubkey!=SYSTEM or accounts[5].pubkey!=program_for(accounts[3].pubkey)):
            raise ValueError('Only wallet idempotent ATA creation permitted')
        mint=accounts[3].pubkey
        if str(mint) not in {input_mint,output_mint,*INTERMEDIATES} or accounts[1].pubkey!=ata(user,mint):
            raise ValueError('Unexpected ATA destination')
        if any(a.is_signer and a.pubkey!=user for a in accounts):
            raise ValueError('External ATA signer prohibited')
        if accounts[1].pubkey not in {a.pubkey for a in swap.accounts}:
            raise ValueError('ATA absent from swap route')
        setups.append(ix)
    # Replace provider compute-price instructions with locally capped values.
    # Provider cannot add transfers through this field.
    for raw in document.get('computeBudgetInstructions',[]):
        ix=instruction(raw)
        if str(ix.program_id)!='ComputeBudget111111111111111111111111111111' or ix.accounts or not bytes(ix.data) or bytes(ix.data)[0] not in (2,3):
            raise ValueError('Unexpected compute instruction')
    return setups,swap,minimum,output,source,destination


class Jupiter:
    def __init__(self,rpc,wallet,mint,fetch=fetch_build):
        self.rpc,self.wallet,self.user,self.mint,self.fetch=rpc,wallet,wallet.pubkey(),mint,fetch
        if mint==USDC_MINT:
            raise ValueError('Quote token cannot be a traded asset')
        self.decimals=None

    def accounts(self,addresses):
        return self.rpc.call('getMultipleAccounts',[[str(a) for a in addresses],{'encoding':'base64','commitment':'confirmed'}])['value']

    def metadata(self):
        record=self.accounts([self.mint])[0]
        self.decimals=mint_decimals(record,self.mint)
        return self.decimals

    def build(self,side,units):
        from venue_policy import entry_allowed
        if side=='buy' and not entry_allowed('jupiter'):
            raise ValueError('Venue eligibility blocks new entry')
        if side not in ('buy','sell'):
            raise ValueError('Spot side required')
        if self.rpc.call('getGenesisHash',[])!=MAINNET_GENESIS:
            raise ValueError('Mainnet RPC required')
        decimals=self.metadata()
        incoming,outgoing=(USDC_MINT,self.mint) if side=='buy' else (self.mint,USDC_MINT)
        document=self.fetch(self.user,incoming,outgoing,units)
        read_at=time.time()
        setups,swap,minimum,output,source,destination=validate_build(document,self.user,incoming,outgoing,units,max_price_impact=.005 if side=='buy' else None)
        # Resolve and verify actual on-chain lookup contents, never trust API arrays.
        tables=[]
        for address,claimed in (document.get('addressesByLookupTableAddress') or {}).items():
            account=self.accounts([address])[0]
            if not account or account['owner']!=LOOKUP:
                raise ValueError('Invalid address lookup owner')
            raw=base64.b64decode(account['data'][0],validate=True)
            if len(raw)<56 or (len(raw)-56)%32 or int.from_bytes(raw[:4],'little')!=1 or int.from_bytes(raw[4:12],'little')!=2**64-1:
                raise ValueError('Invalid/deactivating address lookup')
            keys=[Pubkey.from_bytes(raw[i:i+32]) for i in range(56,len(raw),32)]
            if [str(k) for k in keys]!=claimed:
                raise ValueError('Lookup addresses changed')
            tables.append(AddressLookupTableAccount(Pubkey.from_string(address),keys))
        before=self.accounts([source,destination,self.user])
        balances=[token_account(before[0],Pubkey.from_string(incoming),self.user),token_account(before[1],Pubkey.from_string(outgoing),self.user)]
        if balances[0]<units:
            raise ValueError('Insufficient token balance')
        # Forbid any other wallet-owned writable token account in the route.
        writable=list(dict.fromkeys(a.pubkey for a in swap.accounts if a.is_writable and a.pubkey not in (source,destination,self.user)))
        extras={}
        for offset in range(0,len(writable),100):
            for address,record in zip(writable[offset:offset+100],self.accounts(writable[offset:offset+100])):
                if record and record['owner']==str(TOKEN):
                    raw=base64.b64decode(record['data'][0],validate=True)
                    if len(raw)==165 and raw[32:64]==bytes(self.user):
                        mint=Pubkey.from_bytes(raw[:32])
                        if str(mint) not in INTERMEDIATES or address!=ata(self.user,mint):
                            raise ValueError('Route touches unrelated wallet token account')
                        extras[str(address)]={'mint':str(mint),'balance':token_account(record,mint,self.user)}
        rents={str(TOKEN):self.rpc.call('getMinimumBalanceForRentExemption',[165])}
        if any(ix.accounts[5].pubkey==TOKEN_2022 for ix in setups):
            rents[str(TOKEN_2022)]=self.rpc.call('getMinimumBalanceForRentExemption',[170])
        rent_cost=0
        if setups:
            setup_records=self.accounts([ix.accounts[1].pubkey for ix in setups])
            for ix,record in zip(setups,setup_records):
                mint=ix.accounts[3].pubkey
                if not record:
                    rent_cost+=rents[str(ix.accounts[5].pubkey)]
                else:
                    token_account(record,mint,self.user)
                if str(mint) in INTERMEDIATES and str(mint) not in (incoming,outgoing):
                    mint_record=self.accounts([mint])[0]
                    if not mint_record or mint_record['owner']!=str(TOKEN):
                        raise ValueError('Unsupported intermediate token mint')
                    extras[str(ix.accounts[1].pubkey)]={'mint':str(mint),'balance':token_account(record,mint,self.user)}
        if len(set(str(ix.accounts[1].pubkey) for ix in setups))!=len(setups):
            raise ValueError('Duplicate account setup')
        native=before[2]['lamports']
        if native<rent_cost+40000:
            raise ValueError('Insufficient native fee reserve')
        blockhash=self.rpc.call('getLatestBlockhash',[{'commitment':'confirmed'}])['value']
        message=MessageV0.try_compile(self.user,[set_compute_unit_limit(1400000),set_compute_unit_price(0),*setups,swap],tables,Hash.from_string(blockhash['blockhash']))
        unsigned=VersionedTransaction.populate(message,[Signature.default()])
        if len(bytes(unsigned))>1232 or message.header.num_required_signatures!=1:
            raise ValueError('Unsupported transaction size/signers')
        return unsigned,dict(side=side,input_units=units,min_output=minimum,expected_output=output,
            decimals=decimals,source=source,destination=destination,input_mint=incoming,output_mint=outgoing,
            balances=balances,native=native,rent=rent_cost,extras=extras,
            setup_addresses={str(ix.accounts[1].pubkey) for ix in setups},read_at=read_at,
            message_hash=hashlib.sha256(to_bytes_versioned(message)).hexdigest())

    def simulate(self,unsigned,context):
        if hashlib.sha256(to_bytes_versioned(unsigned.message)).hexdigest()!=context['message_hash']:
            raise ValueError('Constructed transaction was modified')
        if time.time()-context['read_at']>25:
            raise ValueError('Quote expired')
        fee=self.rpc.call('getFeeForMessage',[base64.b64encode(to_bytes_versioned(unsigned.message)).decode(),{'commitment':'confirmed'}])['value']
        if fee is None or fee>20000:
            raise ValueError('Network fee exceeds limit')
        result=self.rpc.call('simulateTransaction',[base64.b64encode(bytes(unsigned)).decode(),{
            'encoding':'base64','sigVerify':False,'commitment':'confirmed',
            'accounts':{'encoding':'base64','addresses':[str(context['source']),str(context['destination']),str(self.user),*context['extras']]}}])['value']
        if result['err'] is not None:
            raise ValueError('Swap simulation failed; no transaction signed')
        accounts=result.get('accounts')
        if not accounts or len(accounts)!=3+len(context['extras']):
            raise ValueError('Missing simulated accounts')
        after=[token_account(accounts[i],Pubkey.from_string(context['input_mint'] if i==0 else context['output_mint']),self.user) for i in range(2)]
        if (context['balances'][0]-after[0]!=context['input_units'] or after[1]-context['balances'][1]<context['min_output'] or
            accounts[2]['owner']!=str(SYSTEM) or context['native']-accounts[2]['lamports']>context['rent']+fee or accounts[2]['lamports']<20000):
            raise ValueError('Simulated balances violate intent')
        for (address,extra),account in zip(context['extras'].items(),accounts[3:]):
            if token_account(account,Pubkey.from_string(extra['mint']),self.user)!=extra['balance']:
                raise ValueError('Intermediate wallet balance changed')
        if time.time()-context['read_at']>25:
            raise ValueError('Quote expired during simulation')
        context['simulation_hash']=context['message_hash']
        return fee

    def sign(self,unsigned,context):
        if context.get('simulation_hash')!=context['message_hash']:
            raise ValueError('Exact transaction simulation required before signing')
        if time.time()-context['read_at']>25 or hashlib.sha256(to_bytes_versioned(unsigned.message)).hexdigest()!=context['message_hash']:
            raise ValueError('Quote expired or modified')
        return VersionedTransaction(unsigned.message,[self.wallet])
