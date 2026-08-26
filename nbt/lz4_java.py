"""Decode the LZ4Block stream used by Minecraft region compression type 4.

Minecraft uses the block stream format from lz4-java's LZ4BlockInputStream,
not the standard LZ4 frame format.  Keeping the decoder here avoids adding a
runtime dependency just to scan a world that happens to use LZ4 regions.
"""

from struct import unpack

MAGIC = b"LZ4Block"
HEADER_LENGTH = 21
METHOD_RAW = 0x10
METHOD_LZ4 = 0x20
XXHASH_SEED = 0x9747B28C
CHECKSUM_MASK = 0xFFFFFFFF

_P1 = 0x9E3779B1
_P2 = 0x85EBCA77
_P3 = 0xC2B2AE3D
_P4 = 0x27D4EB2F
_P5 = 0x165667B1
_MASK32 = 0xFFFFFFFF


def _rotl32(value, count):
    value &= _MASK32
    return ((value << count) | (value >> (32 - count))) & _MASK32


def _round(acc, lane):
    acc = (acc + (lane * _P2)) & _MASK32
    acc = _rotl32(acc, 13)
    return (acc * _P1) & _MASK32


def xxhash32(data, seed=0):
    """Small pure-Python XXH32 implementation for lz4-java checksums."""
    data = bytes(data)
    length = len(data)
    index = 0
    seed &= _MASK32

    if length >= 16:
        v1 = (seed + _P1 + _P2) & _MASK32
        v2 = (seed + _P2) & _MASK32
        v3 = seed
        v4 = (seed - _P1) & _MASK32
        limit = length - 16
        while index <= limit:
            v1 = _round(v1, unpack("<I", data[index:index + 4])[0]); index += 4
            v2 = _round(v2, unpack("<I", data[index:index + 4])[0]); index += 4
            v3 = _round(v3, unpack("<I", data[index:index + 4])[0]); index += 4
            v4 = _round(v4, unpack("<I", data[index:index + 4])[0]); index += 4
        result = (_rotl32(v1, 1) + _rotl32(v2, 7) +
                  _rotl32(v3, 12) + _rotl32(v4, 18)) & _MASK32
    else:
        result = (seed + _P5) & _MASK32

    result = (result + length) & _MASK32

    while index + 4 <= length:
        lane = unpack("<I", data[index:index + 4])[0]
        result = (result + lane * _P3) & _MASK32
        result = (_rotl32(result, 17) * _P4) & _MASK32
        index += 4

    while index < length:
        result = (result + data[index] * _P5) & _MASK32
        result = (_rotl32(result, 11) * _P1) & _MASK32
        index += 1

    result ^= result >> 15
    result = (result * _P2) & _MASK32
    result ^= result >> 13
    result = (result * _P3) & _MASK32
    result ^= result >> 16
    return result & _MASK32


def _decompress_raw_lz4(block, expected_size):
    """Decompress one raw LZ4 block (no frame header)."""
    source = memoryview(block)
    source_index = 0
    output = bytearray()

    while source_index < len(source):
        token = source[source_index]
        source_index += 1

        literal_length = token >> 4
        if literal_length == 15:
            while True:
                if source_index >= len(source):
                    raise ValueError("Truncated LZ4 literal length")
                value = source[source_index]
                source_index += 1
                literal_length += value
                if value != 255:
                    break

        literal_end = source_index + literal_length
        if literal_end > len(source):
            raise ValueError("Truncated LZ4 literals")
        output.extend(source[source_index:literal_end])
        source_index = literal_end

        # A final literal-only sequence has no match offset.
        if source_index == len(source):
            break
        if source_index + 2 > len(source):
            raise ValueError("Truncated LZ4 match offset")

        offset = source[source_index] | (source[source_index + 1] << 8)
        source_index += 2
        if offset == 0 or offset > len(output):
            raise ValueError("Invalid LZ4 match offset %d" % offset)

        match_length = token & 0x0F
        if match_length == 15:
            while True:
                if source_index >= len(source):
                    raise ValueError("Truncated LZ4 match length")
                value = source[source_index]
                source_index += 1
                match_length += value
                if value != 255:
                    break
        match_length += 4

        # Copy in chunks so overlapping matches work without a byte-at-a-time
        # loop in the common case.
        while match_length:
            copy_length = min(match_length, offset)
            start = len(output) - offset
            output.extend(output[start:start + copy_length])
            match_length -= copy_length

        if len(output) > expected_size:
            raise ValueError("LZ4 block expanded beyond declared size")

    if len(output) != expected_size:
        raise ValueError("LZ4 block size mismatch: expected %d, got %d" %
                         (expected_size, len(output)))
    return bytes(output)


def decompress(data):
    """Decode a complete lz4-java LZ4Block stream."""
    data = bytes(data)
    index = 0
    result = bytearray()
    saw_terminal = False

    while index < len(data):
        if index + HEADER_LENGTH > len(data):
            raise ValueError("Truncated LZ4Block header")
        header = data[index:index + HEADER_LENGTH]
        index += HEADER_LENGTH

        if header[:8] != MAGIC:
            raise ValueError("Invalid LZ4Block magic")

        token = header[8]
        method = token & 0xF0
        compression_level = token & 0x0F
        compressed_length, decompressed_length, checksum = unpack("<III", header[9:21])

        if compressed_length == 0 and decompressed_length == 0:
            if checksum != 0:
                raise ValueError("Invalid LZ4Block terminal checksum")
            saw_terminal = True
            break

        max_block_size = 1 << (10 + compression_level)
        if decompressed_length <= 0 or decompressed_length > max_block_size:
            raise ValueError("Invalid LZ4Block decompressed size %d" % decompressed_length)
        if index + compressed_length > len(data):
            raise ValueError("Truncated LZ4Block payload")

        payload = data[index:index + compressed_length]
        index += compressed_length

        if method == METHOD_RAW:
            if compressed_length != decompressed_length:
                raise ValueError("Invalid raw LZ4Block lengths")
            block = payload
        elif method == METHOD_LZ4:
            block = _decompress_raw_lz4(payload, decompressed_length)
        else:
            raise ValueError("Unknown LZ4Block method 0x%02x" % method)

        calculated = xxhash32(block, XXHASH_SEED) & CHECKSUM_MASK
        if calculated != checksum:
            raise ValueError("LZ4Block checksum mismatch")
        result.extend(block)

    if not saw_terminal:
        raise ValueError("LZ4Block stream has no terminal block")
    if index != len(data):
        # lz4-java streams should finish exactly at the terminal marker. Treat
        # non-empty trailing bytes as corruption rather than silently ignoring.
        trailing = data[index:]
        if any(trailing):
            raise ValueError("Unexpected data after LZ4Block terminal block")

    return bytes(result)
