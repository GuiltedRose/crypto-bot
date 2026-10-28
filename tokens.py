"""Reviewed SPL layouts, including metadata-only Token-2022 mints.

Transfer fees/hooks, confidential balances and mutable balance extensions are
unsupported. Token-2022 accounts may only add immutable-owner metadata.
"""
import base64
import struct
from solders.pubkey import Pubkey
from pumpswap import TOKEN,ATA

TOKEN_2022=Pubkey.from_string('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')
AFKHERO='9kwT8gUEMKQhnmc4mVb2iUAybfjzvVMM5LwffKFcpump'


def program_for(mint):
    return TOKEN_2022 if str(mint)==AFKHERO else TOKEN


def ata(owner,mint):
    return Pubkey.find_program_address([bytes(owner),bytes(program_for(mint)),bytes(mint)],ATA)[0]


def extensions(data,account_type,allowed):
    if len(data)<166 or data[165]!=account_type:
        raise ValueError('Invalid Token-2022 account type')
    offset=166
    seen=set()
    while offset<len(data):
        if not any(data[offset:]):
            break
        if offset+4>len(data):
            raise ValueError('Truncated extension header')
        kind,length=struct.unpack_from('<HH',data,offset)
        offset+=4
        if kind not in allowed or kind in seen or offset+length>len(data):
            raise ValueError('Unsupported token extension')
        if kind==7 and length!=0:
            raise ValueError('Invalid immutable-owner extension')
        seen.add(kind)
        offset+=length


def mint_decimals(record,mint):
    program=program_for(mint)
    if not record or record['owner']!=str(program):
        raise ValueError('Unsupported mint token program')
    data=base64.b64decode(record['data'][0],validate=True)
    if len(data)<82 or data[45]!=1 or data[44]>12:
        raise ValueError('Invalid token mint')
    if program==TOKEN:
        if len(data)!=82:raise ValueError('Unexpected standard token extension')
    elif len(data)!=82:
        if any(data[82:165]):raise ValueError('Invalid mint padding')
        extensions(data,1,{18,19})  # metadata pointer and token metadata only
    return data[44]


def token_account(record,mint,authority):
    if not record:
        return 0
    program=program_for(mint)
    if record['owner']!=str(program):
        raise ValueError('Unexpected token account program')
    data=base64.b64decode(record['data'][0],validate=True)
    if (len(data)<165 or data[:32]!=bytes(mint) or data[32:64]!=bytes(authority) or data[108]!=1
        or int.from_bytes(data[72:76],'little') or int.from_bytes(data[129:133],'little')):
        raise ValueError('Token account identity/state/delegate mismatch')
    if program==TOKEN:
        if len(data)!=165:raise ValueError('Unexpected token account extension')
    elif len(data)!=165:
        extensions(data,2,{7})
    return int.from_bytes(data[64:72],'little')
