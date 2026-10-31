"""UNEXECUTED offline wire-format tests; synthetic fixtures, never signed/traded."""
import unittest
from unittest.mock import patch
import solana_binary as decoder


def fixture(version=None, program=bytes([7])*32, loaded=False):
    # One synthetic signature, writable signer and static program account.
    message = bytes([1,0,1,2])+bytes([1])*32+program+bytes([2])*32
    message += bytes([1,1,1,2 if loaded else 0,3])+b'abc'
    if version is not None:
        message = bytes([128|version])+message
        message += bytes([1])+bytes([3])*32+bytes([1,0,0]) if loaded else bytes([0])
    return bytes([1])+bytes(64)+message


class BinaryTests(unittest.TestCase):
    def test_valid_legacy_v0_and_loaded_indices(self):
        for raw in (fixture(),fixture(0),fixture(0,loaded=True)):
            result=decoder.parse_transaction(raw,'python')
            self.assertFalse(result['execution_verified'])
            self.assertFalse(result['provenance_verified'])
            self.assertFalse(result['loaded_addresses_resolved'])
            self.assertEqual(result['instructions'][0][2],list(b'abc'))

    def test_fallback_and_strict_native_selection(self):
        with patch.object(decoder,'_native',None):
            self.assertEqual(decoder.parse_transaction(fixture()),decoder.parse_transaction(fixture(),'python'))
            with self.assertRaises(RuntimeError):decoder.parse_transaction(fixture(),'rust')
        with patch.object(decoder,'_native',side_effect=ValueError('native quarantine')):
            with self.assertRaises(ValueError):decoder.parse_transaction(fixture())

    def test_invalid_lengths_versions_truncation_indices_and_json(self):
        bad=[b'',bytes(1233),b'\x80\x00',b'\xff\xff\x04',b'\x14',fixture(1),
             fixture()+b'junk',fixture()[:-1],fixture(loaded=True), b'{"transaction":{}}']
        raw=bytearray(fixture()); raw[65]=0; bad.append(bytes(raw))
        for raw in bad:
            with self.subTest(raw=raw[:8]),self.assertRaises(ValueError):decoder.parse_transaction(raw,'python')
        for end in range(len(fixture())):
            with self.assertRaises(ValueError):decoder.parse_transaction(fixture()[:end],'python')
        with self.assertRaises(TypeError):decoder.parse_transaction({'transaction':{}})

    def test_program_identity_is_preserved_without_program_decoding(self):
        from solders.pubkey import Pubkey
        for program in ('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',
                        'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',
                        '11111111111111111111111111111111'):
            key=bytes(Pubkey.from_string(program))
            result=decoder.parse_transaction(fixture(program=key),'python')
            self.assertEqual(result['account_keys'][1],list(key))
            self.assertFalse(result['execution_verified'])

    def test_solders_wire_oracle(self):
        from solders.transaction import VersionedTransaction
        for raw in (fixture(),fixture(0),fixture(0,loaded=True)):
            tx=VersionedTransaction.from_bytes(raw)
            self.assertEqual(bytes(tx),raw)
            self.assertEqual([list(bytes(key)) for key in tx.message.account_keys],decoder.parse_transaction(raw,'python')['account_keys'])

    @unittest.skipUnless(decoder._native is not None,'Build optional native extension first')
    def test_native_differential_valid_and_malformed(self):
        inputs=[fixture(),fixture(0),fixture(0,loaded=True),fixture(1),fixture()+b'junk',bytes(1233),b'\xff\xff\x04']
        inputs += [fixture()[:end] for end in range(len(fixture()))]
        for raw in inputs:
            with self.subTest(size=len(raw)):
                try: expected=decoder.parse_transaction(raw,'python')
                except ValueError:
                    with self.assertRaises(ValueError):decoder.parse_transaction(raw,'rust')
                else:self.assertEqual(decoder.parse_transaction(raw,'rust'),expected)
