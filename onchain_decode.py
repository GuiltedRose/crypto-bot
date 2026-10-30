"""Strict decoding of pinned Pump events carried by authenticated self-CPI.

No wallet-delta inference: routed transactions can contain several independent
swaps. Unknown layouts remain quarantined rather than becoming volume.
"""
import base64
import hashlib
import json
from pathlib import Path
from solders.pubkey import Pubkey
from wallet_watch import decode58, SOL

EVENT_IX = bytes.fromhex('e445a52e51cb9a1d')
ROOT = Path(__file__).parent / 'vendor'


def read_value(raw, kind, offset, types):
    def take(size):
        if size < 0 or offset + size > len(raw):
            raise ValueError('Truncated Borsh field')
        return raw[offset:offset + size], offset + size
    if kind == 'pubkey':
        value, end = take(32)
        return str(Pubkey.from_bytes(value)), end
    if kind == 'bool':
        value, end = take(1)
        if value[0] not in (0, 1):
            raise ValueError('Invalid Borsh bool')
        return bool(value[0]), end
    if kind == 'string':
        size, offset = read_value(raw, 'u32', offset, types)
        if size > 16384:
            raise ValueError('Oversized string')
        value, end = take(size)
        return value.decode('utf8'), end
    if isinstance(kind, str) and kind[:1] in ('u', 'i') and kind[1:].isdigit():
        value, end = take(int(kind[1:]) // 8)
        return int.from_bytes(value, 'little', signed=kind[0] == 'i'), end
    if isinstance(kind, dict) and 'defined' in kind:
        fields = types[kind['defined']['name']]['fields']
        if fields and isinstance(fields[0], str):
            values = []
            for field in fields:
                value, offset = read_value(raw, field, offset, types)
                values.append(value)
            return values, offset
        return read_struct(raw, fields, offset, types)
    if isinstance(kind, dict) and ('vec' in kind or 'array' in kind):
        if 'vec' in kind:
            element = kind['vec']
            size, offset = read_value(raw, 'u32', offset, types)
        else:
            element, size = kind['array']
        if size > 4096:
            raise ValueError('Oversized vector')
        values = []
        for _ in range(size):
            value, offset = read_value(raw, element, offset, types)
            values.append(value)
        return values, offset
    raise ValueError('Unsupported Borsh type')


def read_struct(raw, fields, offset, types):
    values = {}
    for field in fields:
        values[field['name']], offset = read_value(raw, field['type'], offset, types)
    return values, offset


class Decoder:
    def __init__(self):
        self.specs = {}
        for name in ('pump', 'pump_amm'):
            raw = (ROOT / (name + '.json')).read_bytes()
            manifest = json.loads((ROOT / (name + '.manifest.json')).read_text())
            if hashlib.sha256(raw).hexdigest() != manifest['sha256']:
                raise ValueError('Pinned IDL hash mismatch')
            idl = json.loads(raw)
            if idl['address'] != manifest['program']:
                raise ValueError('Pinned program identity mismatch')
            self.specs[idl['address']] = {
                'hash': manifest['sha256'], 'venue': name,
                'types': {t['name']: t['type'] for t in idl['types']},
                'events': {bytes(e['discriminator']): e['name'] for e in idl['events']},
                'instructions': {bytes(i['discriminator']): i for i in idl['instructions']}}

    def transaction(self, tx, slot, tx_index, block_time):
        meta, transaction = tx['meta'], tx['transaction']
        if meta is None:
            return [], ['missing transaction metadata']
        if meta['err'] is not None:
            return [], []
        keys = transaction['message']['accountKeys']
        keys = [key['pubkey'] if isinstance(key, dict) else key for key in keys]
        # json encoding needs loaded addresses; jsonParsed already expands keys.
        if not any(isinstance(k, dict) for k in transaction['message']['accountKeys']):
            loaded = meta.get('loadedAddresses') or {}
            keys += loaded.get('writable', []) + loaded.get('readonly', [])
        decimals = {}
        for balance in (meta.get('preTokenBalances') or []) + (meta.get('postTokenBalances') or []):
            mint, digits = balance['mint'], balance['uiTokenAmount']['decimals']
            if type(digits) is not int or not 0 <= digits <= 18 or mint in decimals and decimals[mint] != digits:
                return [], ['ambiguous mint decimals']
            decimals[mint] = digits
        signature = transaction['signatures'][0]
        inner = {group['index']: group['instructions'] for group in meta.get('innerInstructions') or []}
        events, issues = [], []
        for outer, root in enumerate(transaction['message']['instructions']):
            parents = {}
            swaps = []
            emitted = set()
            for ordinal, ix in enumerate([root] + inner.get(outer, [])):
                try:
                    program = ix.get('programId') or keys[ix['programIdIndex']]
                    height = 1 if ordinal == 0 else ix.get('stackHeight')
                    if height is None:
                        if program in self.specs:
                            issues.append('missing CPI stack height')
                        continue
                    for level in list(parents):
                        if level >= height:
                            del parents[level]
                    if program not in self.specs:
                        continue
                    spec = self.specs[program]
                    raw = decode58(ix.get('data', ''))
                    accounts = [keys[a] if isinstance(a, int) else a for a in ix.get('accounts', [])]
                    if raw[:8] != EVENT_IX:
                        instruction = spec['instructions'].get(raw[:8])
                        if instruction is None:
                            issues.append('unknown program instruction: ' + program)
                            continue
                        names = [a['name'] for a in instruction['accounts']]
                        binding = dict(zip(names, accounts))
                        parents[height] = (program, instruction['name'], binding, ordinal, None)
                        if instruction['name'].startswith(('buy', 'sell')):
                            swaps.append(ordinal)
                        arguments, end = read_struct(raw, instruction['args'], 8, spec['types'])
                        if end != len(raw):
                            raise ValueError('Unreviewed instruction layout/version')
                        parents[height] = (program, instruction['name'], binding, ordinal, arguments)
                        continue
                    authority = str(Pubkey.find_program_address([b'__event_authority'], Pubkey.from_string(program))[0])
                    if not accounts or accounts[0] != authority:
                        raise ValueError('Event authority mismatch')
                    parent = parents.get(height - 1)
                    if not parent or parent[0] != program:
                        raise ValueError('Unbound self-CPI event')
                    event_name = spec['events'].get(raw[8:16])
                    if event_name is None:
                        raise ValueError('Unknown event discriminator')
                    fields, end = read_struct(raw, spec['types'][event_name]['fields'], 16, spec['types'])
                    if end != len(raw):
                        issues.append(f'{event_name}: payload {len(raw)-16} bytes, pinned fields {end-16}, trailing {len(raw)-end}; program {program}')
                        raise ValueError('Unreviewed event layout/version')
                    event = self.normalize(spec, event_name, fields, parent, decimals)
                    event.update(instruction_name=parent[1], instruction_args=parent[4], signature=signature, slot=slot, tx_index=tx_index,
                                 outer_index=outer, event_index=ordinal, block_time=block_time,
                                 program=program, idl_hash=spec['hash'], event_name=event_name,
                                 transaction_fee_lamports=meta['fee'], raw_event=base64.b64encode(raw).decode())
                    events.append(event)
                    if event['kind'] == 'swap':
                        if parent[3] in emitted:
                            raise ValueError('Multiple trade events for one instruction')
                        emitted.add(parent[3])
                except (KeyError, ValueError, IndexError, TypeError, UnicodeError) as error:
                    issues.append(str(error))
            if set(swaps) != emitted:
                issues.append('trade instruction/event mismatch')
        if events or issues:
            transfers, transfer_issues = self.transfers(tx, keys)
            issues.extend(transfer_issues)
            for event in events:
                event['transaction_token_transfers'] = transfers
        # Transfers are transaction-scoped, never allocated by net wallet delta.
        return events, sorted(set(issues))

    def transfers(self, tx, keys):
        """Decode standard SPL transfers, retaining explicit unresolved extensions."""
        balances = {}
        for row in (tx['meta'].get('preTokenBalances') or []) + (tx['meta'].get('postTokenBalances') or []):
            balances[row['accountIndex']] = row
        result, issues = [], []
        groups = [(i, [ix]) for i, ix in enumerate(tx['transaction']['message']['instructions'])]
        groups += [(g['index'], g['instructions']) for g in tx['meta'].get('innerInstructions') or []]
        token_programs = {'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
                          'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'}
        for outer, instructions in groups:
            for index, ix in enumerate(instructions):
                program = ix.get('programId') or keys[ix['programIdIndex']]
                if program not in token_programs or 'data' not in ix:
                    continue
                try:
                    raw = decode58(ix['data'])
                    if raw[0] not in (3, 12):
                        if raw[0] == 26:
                            issues.append('Token-2022 transfer fee extension requires reviewed decoder')
                        continue
                    checked = raw[0] == 12
                    if len(raw) != (10 if checked else 9):
                        raise ValueError('Unreviewed SPL transfer layout')
                    accounts = [keys[a] if isinstance(a, int) else a for a in ix['accounts']]
                    source, destination = accounts[0], accounts[2 if checked else 1]
                    matching = [row for account_index, row in balances.items()
                                if keys[account_index] in (source, destination)]
                    mints = {row['mint'] for row in matching}
                    digits = {row['uiTokenAmount']['decimals'] for row in matching}
                    if len(mints) != 1 or len(digits) != 1:
                        raise ValueError('SPL transfer identity unavailable')
                    mint, decimals = next(iter(mints)), next(iter(digits))
                    if checked and (accounts[1] != mint or raw[9] != decimals):
                        raise ValueError('SPL checked transfer identity mismatch')
                    result.append({'outer_index': outer, 'instruction_index': index,
                                   'program': program, 'mint': mint, 'source': source,
                                   'destination': destination, 'decimals': decimals,
                                   'amount_units': str(int.from_bytes(raw[1:9], 'little'))})
                except (ValueError, KeyError, IndexError, TypeError) as error:
                    issues.append(str(error))
        return result, issues

    def normalize(self, spec, name, f, parent, decimals):
        _, instruction, binding, _, _ = parent
        result = {'kind': 'lifecycle', 'fields': f, 'venue': spec['venue']}
        if name not in ('TradeEvent', 'BuyEvent', 'SellEvent'):
            mint = f.get('mint', f.get('base_mint'))
            bound_mint = binding.get('mint', binding.get('base_mint'))
            if mint and bound_mint and mint != bound_mint:
                raise ValueError('Lifecycle mint identity mismatch')
            result.update(mint=mint, pool=f.get('pool', f.get('bonding_curve')),
                          wallet=f.get('user', f.get('creator')))
            return result
        buy = f['is_buy'] if name == 'TradeEvent' else name == 'BuyEvent'
        if not instruction.startswith('buy' if buy else 'sell'):
            raise ValueError('Trade direction/instruction mismatch')
        mint = binding.get('mint', binding.get('base_mint'))
        quote = binding.get('quote_mint', SOL)
        pool = binding.get('pool', binding.get('bonding_curve'))
        user = binding.get('user')
        if mint is None or pool is None or user is None or mint == quote:
            raise ValueError('Missing trade identity')
        if f.get('mint', mint) != mint or f.get('pool', pool) != pool or f['user'] != user:
            raise ValueError('Trade event identity mismatch')
        if name == 'TradeEvent':
            event_quote = f['quote_mint']
            # Legacy native-SOL events encode the default pubkey as quote mint.
            if event_quote == str(Pubkey.default()):
                event_quote = SOL
            if event_quote != quote:
                raise ValueError('Quote identity mismatch')
            amount = f['token_amount']
            quote_amount = f['sol_amount'] if quote == SOL else f['quote_amount']
        else:
            amount = f['base_amount_out' if buy else 'base_amount_in']
            quote_amount = f['quote_amount_in' if buy else 'quote_amount_out']
        if mint not in decimals or quote != SOL and quote not in decimals:
            raise ValueError('Mint decimals unavailable from transaction')
        if min(amount, quote_amount) <= 0:
            raise ValueError('Nonpositive swap amounts')
        fees = {k: f[k] for k in ('fee', 'creator_fee', 'lp_fee', 'protocol_fee',
                                 'coin_creator_fee', 'cashback', 'buyback_fee', 'holder_rewards') if k in f}
        result.update(kind='swap', mint=mint, quote_mint=quote, pool=pool, wallet=user,
                      direction='buy' if buy else 'sell', base_units=str(amount),
                      quote_units=str(quote_amount), base_decimals=decimals[mint],
                      quote_decimals=9 if quote == SOL else decimals[quote], fees=fees,
                      reserves={k: v for k, v in f.items() if 'reserves' in k},
                      amount_basis='program trade event; fees separate; not net wallet delta')
        return result
