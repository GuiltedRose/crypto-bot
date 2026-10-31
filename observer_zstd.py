"""UNTESTED bounded CPython/libzstd streaming compression; no network or startup."""
import hashlib
import time

CHUNK = 65536
MAX_RAW = 64_000_000
MAX_FRAME = 65_000_000


def compress_stream(chunks, output, level=3, timeout=30):
    """Consume bounded chunks with synchronous backpressure; never buffer a block.

    Caller owns the output and must discard partial output on failure. Publication
    and pruning still require the archive layer's independent verification/fsync.
    The deadline is cooperative between bounded native calls, not preemptive.
    """
    if type(level) is not int or not 1 <= level <= 6 or not 0 < timeout <= 120:
        raise ValueError('Invalid bounded codec settings')
    import zstandard
    raw_hash = hashlib.sha256()
    frame_hash = hashlib.sha256()
    raw_count = 0
    frame_count = 0
    deadline = time.monotonic() + timeout

    class Sink:
        def write(self, data):
            nonlocal frame_count
            if time.monotonic() > deadline:
                raise TimeoutError('Compression deadline exceeded')
            if frame_count + len(data) > MAX_FRAME:
                raise ValueError('Compressed output exceeds budget')
            view = memoryview(data)
            while view:
                written = output.write(view)
                if type(written) is not int or not 0 < written <= len(view):
                    raise OSError('Archive output made no progress')
                frame_hash.update(view[:written])
                frame_count += written
                view = view[written:]
            return len(data)

    params = zstandard.ZstdCompressionParameters.from_level(
        level, window_log=23, threads=0, write_checksum=1)
    compressor = zstandard.ZstdCompressor(compression_params=params)
    with compressor.stream_writer(Sink(), closefd=False, write_size=CHUNK) as writer:
        for chunk in chunks:
            if not isinstance(chunk, (bytes, bytearray, memoryview)) or len(chunk) > CHUNK:
                raise ValueError('Input chunk must be bytes and at most 64KiB')
            if time.monotonic() > deadline:
                raise TimeoutError('Compression deadline exceeded')
            raw_count += len(chunk)
            if raw_count > MAX_RAW:
                raise ValueError('Raw evidence exceeds budget')
            raw_hash.update(chunk)
            writer.write(chunk)
    return {'format': 'solana-observer-block-zstd-v1', 'raw_bytes': raw_count,
            'raw_sha256': raw_hash.hexdigest(), 'frame_bytes': frame_count,
            'frame_sha256': frame_hash.hexdigest(), 'level': level}
