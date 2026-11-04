"""MSF (Multi-Stream File) container reader -- the PDB 7.0 "big" format.

All Windows PDBs published on the Microsoft symbol server are MSF 7.0. This
module implements just enough of the container to read streams by index:

    block 0    : super block (magic, block size, block count, directory size,
                 index of the block map)
    block map  : array of block indices that hold the stream directory
    directory  : num_streams, stream sizes, then the block list of each stream

Pure standard library. Nothing is executed; the file is only parsed.
"""
import math
import struct

__all__ = ["MsfError", "MsfFile"]

MAGIC_700 = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0"
MAGIC_200 = b"Microsoft C/C++ program database 2.00\r\n\x1aJG\0\0"

# Stream indices are fixed by convention in PDB 7.0 (see LLVM's PDBFile.cpp).
STREAM_OLD_DIRECTORY = 0
STREAM_PDB_INFO = 1
STREAM_TPI = 2
STREAM_DBI = 3
STREAM_IPI = 4


class MsfError(ValueError):
    """Raised when a file is not a supported MSF container."""


def _u32(buf, off, what):
    if off + 4 > len(buf):
        raise MsfError("truncated MSF: cannot read %s at 0x%X" % (what, off))
    return struct.unpack_from("<I", buf, off)[0]


class MsfFile:
    def __init__(self, data: bytes, source: str = "<memory>"):
        self.data = data
        self.source = source
        self._streams = None
        self._parse_super_block()

    def _parse_super_block(self):
        data = self.data
        if data[:len(MAGIC_200)] == MAGIC_200:
            raise MsfError("%s: PDB 2.0 (Jet) containers are not supported; only "
                           "MSF 7.0, which is what modern Windows ships" % self.source)
        if data[:len(MAGIC_700)] != MAGIC_700:
            raise MsfError("%s: not an MSF 7.0 file (bad magic %r)"
                           % (self.source, data[:16]))
        self.block_size = _u32(data, 32, "block size")
        self.free_block_map_block = _u32(data, 36, "free block map")
        self.num_blocks = _u32(data, 40, "num blocks")
        self.num_directory_bytes = _u32(data, 44, "directory size")
        self.unknown = _u32(data, 48, "unknown field")
        self.block_map_addr = _u32(data, 52, "block map address")
        if self.block_size < 512 or self.block_size > (1 << 22):
            raise MsfError("%s: implausible block size %d" % (self.source, self.block_size))
        if self.num_blocks and self.num_blocks * self.block_size > len(data) + self.block_size:
            # Tolerate a slightly truncated tail but never a wild header.
            raise MsfError("%s: header claims %d blocks of %d bytes but the file is %d bytes"
                           % (self.source, self.num_blocks, self.block_size, len(data)))

    def block(self, index: int) -> bytes:
        start = index * self.block_size
        end = start + self.block_size
        if start < 0 or end > len(self.data):
            raise MsfError("%s: block %d out of range (file is %d bytes)"
                           % (self.source, index, len(self.data)))
        return self.data[start:end]

    @property
    def streams(self) -> dict:
        """index -> (size, [block indices]); parsed once and cached."""
        if self._streams is None:
            self._streams = self._read_directory()
        return self._streams

    def _read_directory(self) -> dict:
        blocks_in_dir = max(1, math.ceil(self.num_directory_bytes / self.block_size))
        map_blocks = max(1, math.ceil(blocks_in_dir * 4 / self.block_size))
        raw = b"".join(self.block(self.block_map_addr + i) for i in range(map_blocks))
        indices = list(struct.unpack_from("<%dI" % blocks_in_dir, raw, 0))
        payload = b"".join(self.block(i) for i in indices)[:self.num_directory_bytes]
        if len(payload) < 4:
            raise MsfError("%s: stream directory is empty" % self.source)
        num_streams = struct.unpack_from("<I", payload, 0)[0]
        if num_streams > 4096 or 4 + num_streams * 4 > len(payload):
            raise MsfError("%s: implausible stream count %d" % (self.source, num_streams))
        sizes = struct.unpack_from("<%dI" % num_streams, payload, 4)
        out, off = {}, 4 + num_streams * 4
        for index, size in enumerate(sizes):
            if size == 0xFFFFFFFF or size == 0:
                out[index] = (0, [])
                continue
            count = math.ceil(size / self.block_size)
            if off + count * 4 > len(payload):
                raise MsfError("%s: directory truncated while reading block list of "
                               "stream %d" % (self.source, index))
            out[index] = (size, list(struct.unpack_from("<%dI" % count, payload, off)))
            off += count * 4
        return out

    def num_streams(self) -> int:
        return len(self.streams)

    def has_stream(self, index: int) -> bool:
        return self.streams.get(index, (0, []))[0] > 0

    def read_stream(self, index: int) -> bytes:
        size, blocks = self.streams.get(index, (0, []))
        if not size:
            return b""
        data = b"".join(self.block(b) for b in blocks)
        if len(data) < size:
            raise MsfError("%s: stream %d is truncated (%d of %d bytes)"
                           % (self.source, index, len(data), size))
        return data[:size]

    def info(self) -> dict:
        return {
            "source": self.source,
            "format": "MSF 7.0",
            "block_size": self.block_size,
            "num_blocks": self.num_blocks,
            "num_streams": self.num_streams(),
            "directory_bytes": self.num_directory_bytes,
            "size_bytes": len(self.data),
        }
