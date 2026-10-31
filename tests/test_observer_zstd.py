"""UNEXECUTED CPython streaming compression tests; no node/network activity."""
import hashlib
import io
import unittest
import zstandard
from observer_zstd import compress_stream, CHUNK, MAX_WINDOW_BYTES


class StreamingTests(unittest.TestCase):
    def test_incremental_output_roundtrip_and_provenance(self):
        output = io.BytesIO()
        raw = bytes(range(256))*1024
        def chunks():
            for start in range(0,len(raw),CHUNK):
                yield raw[start:start+CHUNK]
        manifest = compress_stream(chunks(), output)
        self.assertEqual(MAX_WINDOW_BYTES, 8*1024*1024)
        self.assertLessEqual(zstandard.get_frame_parameters(output.getvalue()).window_size, MAX_WINDOW_BYTES)
        with zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW_BYTES).stream_reader(io.BytesIO(output.getvalue())) as reader:
            self.assertEqual(reader.read(), raw)
        self.assertEqual(manifest['raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(manifest['frame_sha256'], hashlib.sha256(output.getvalue()).hexdigest())

    def test_decoder_rejects_window_larger_than_eight_mib(self):
        output = io.BytesIO()
        params = zstandard.ZstdCompressionParameters.from_level(3, window_log=24)
        with zstandard.ZstdCompressor(compression_params=params).stream_writer(output, closefd=False) as writer:
            writer.write(b'x'*CHUNK)
            writer.flush(zstandard.FLUSH_BLOCK)
        frame = output.getvalue()
        self.assertGreater(zstandard.get_frame_parameters(frame).window_size, MAX_WINDOW_BYTES)
        with self.assertRaises(zstandard.ZstdError):
            with zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW_BYTES).stream_reader(io.BytesIO(frame)) as reader:
                reader.read()

    def test_oversized_chunk_stops_before_acceptance(self):
        with self.assertRaises(ValueError):
            compress_stream([b'x'*(CHUNK+1)], io.BytesIO())

    def test_short_writes_and_no_progress(self):
        class ShortSink(io.BytesIO):
            def write(self, data): return super().write(data[:3])
        output = ShortSink()
        result = compress_stream([b'bounded'], output)
        self.assertEqual(result['frame_bytes'], len(output.getvalue()))
        class Stalled:
            def write(self, data): return 0
        with self.assertRaises(OSError): compress_stream([b'x'], Stalled())

    def test_interruption_propagates_without_publication(self):
        def broken():
            yield b'first'
            raise OSError('injected source interruption')
        with self.assertRaises(OSError): compress_stream(broken(), io.BytesIO())
