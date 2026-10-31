"""Optional binary structure parser. Never execution/consensus or trade evidence.

Legacy/v0 wire transactions only, max 1232 bytes. JSON RPC stays on its existing
path. Native malformed-input errors propagate, never silently fall back.
"""
try:
    from _solana_binary import parse_transaction as _native
except ImportError:
    _native = None


def parse_reference(raw):
    if not isinstance(raw, bytes): raise TypeError('Binary transaction bytes required')
    if not 1 <= len(raw) <= 1232: raise ValueError('transaction size outside supported packet limit')
    offset = 0
    def take(count):
        nonlocal offset
        if offset+count > len(raw): raise ValueError('truncated transaction')
        value = list(raw[offset:offset+count]); offset += count
        return value
    def byte(): return take(1)[0]
    def short(limit):
        value = 0
        for index in range(3):
            part = byte()
            if index == 2 and part > 3: raise ValueError('invalid compact-u16')
            value |= (part & 127) << (7*index)
            if not part & 128:
                if index and part == 0: raise ValueError('noncanonical compact-u16')
                if value > limit: raise ValueError('length exceeds limit')
                return value
        raise ValueError('invalid compact-u16')
    signatures = [take(64) for _ in range(short(19))]
    first = byte()
    if first & 128:
        if first != 128: raise ValueError('unsupported message version')
        version, required = 0, byte()
    else: version, required = -1, first
    header = (required, byte(), byte())
    count = short(256)
    if not required or required != len(signatures) or required > count or header[1] >= required or header[2] > count-required:
        raise ValueError('invalid message header')
    keys = [take(32) for _ in range(count)]
    blockhash = take(32)
    instructions = []
    for _ in range(short(1232)):
        program = byte()
        if program >= len(keys): raise ValueError('program index outside static keys')
        accounts = take(short(256))
        instructions.append((program, accounts, take(short(1232))))
    lookups, loaded = [], 0
    if version == 0:
        for _ in range(short(256)):
            key = take(32); writable = take(short(256)); readonly = take(short(256))
            loaded += len(writable)+len(readonly)
            if len(keys)+loaded > 256: raise ValueError('too many account indices')
            lookups.append((key,writable,readonly))
    if offset != len(raw): raise ValueError('trailing transaction bytes')
    if any(index >= len(keys)+loaded for _,accounts,_ in instructions for index in accounts):
        raise ValueError('account index out of bounds')
    return (version,signatures,header,keys,blockhash,instructions,lookups)


def parse_transaction(raw, backend='auto'):
    if backend not in ('auto','python','rust'): raise ValueError('Unknown backend')
    if not isinstance(raw, bytes): raise TypeError('Binary transaction bytes required')
    if not 1 <= len(raw) <= 1232: raise ValueError('transaction size outside supported packet limit')
    if backend == 'rust' and _native is None: raise RuntimeError('Rust extension unavailable')
    parsed = _native(raw) if backend != 'python' and _native is not None else parse_reference(raw)
    # PyO3 versions may map Rust byte vectors to bytes rather than integer lists.
    def normalize(value):
        if isinstance(value, bytes): return list(value)
        if isinstance(value, tuple): return tuple(normalize(item) for item in value)
        if isinstance(value, list): return [normalize(item) for item in value]
        return value
    parsed = normalize(parsed)
    return dict(zip(('version','signatures','header','account_keys','recent_blockhash','instructions','address_table_lookups'), parsed),
                execution_verified=False, provenance_verified=False, loaded_addresses_resolved=False)
