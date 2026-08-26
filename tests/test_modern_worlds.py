import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

from regionfixer_core.world import World
from nbt import region
from nbt import lz4_java
from nbt import nbt




def modern_chunk_nbt_bytes(x=0, z=0, data_version=5000):
    root = nbt.NBTFile()
    root.name = ""
    root.tags.append(nbt.TAG_Int(name="DataVersion", value=data_version))
    root.tags.append(nbt.TAG_Int(name="xPos", value=x))
    root.tags.append(nbt.TAG_Int(name="zPos", value=z))
    root.tags.append(nbt.TAG_List(name="sections", type=nbt.TAG_Compound))
    buffer = io.BytesIO()
    root.write_file(buffer=buffer)
    return buffer.getvalue()


def write_level_dat(world_path, name="Modern Test World"):
    root = nbt.NBTFile()
    root.name = ""
    data = nbt.TAG_Compound(name="Data")
    data.tags.append(nbt.TAG_String(name="LevelName", value=name))
    root.tags.append(data)
    root.write_file(filename=os.path.join(world_path, "level.dat"))


def make_empty_region(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"\x00" * (2 * region.SECTOR_LENGTH))


def write_raw_region_chunk(path, payload, compression_byte):
    """Create r.0.0.mca with one chunk at local 0,0."""
    if len(payload) + 5 > region.SECTOR_LENGTH:
        raise ValueError("test payload too large")
    make_empty_region(path)
    with open(path, "r+b") as handle:
        handle.seek(0)
        handle.write(struct.pack(">IB", 2, 1)[1:])
        handle.seek(2 * region.SECTOR_LENGTH)
        handle.write(struct.pack(">I", len(payload) + 1))
        handle.write(struct.pack(">B", compression_byte))
        handle.write(payload)
        handle.write(b"\x00" * (region.SECTOR_LENGTH - len(payload) - 5))


def raw_lz4_java_stream(data):
    # 64 KiB block size => compression level token low nibble 6.
    token = 0x16
    checksum = lz4_java.xxhash32(data, lz4_java.XXHASH_SEED) & lz4_java.CHECKSUM_MASK
    block = (lz4_java.MAGIC + bytes([token]) +
             struct.pack("<III", len(data), len(data), checksum) + data)
    terminal = lz4_java.MAGIC + bytes([token]) + struct.pack("<III", 0, 0, 0)
    return block + terminal


class ModernWorldLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="regionfixer-modern-")

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_discovers_modern_default_and_custom_dimensions(self):
        paths = [
            "dimensions/minecraft/overworld/region/r.0.0.mca",
            "dimensions/minecraft/overworld/poi/r.0.0.mca",
            "dimensions/minecraft/overworld/entities/r.0.0.mca",
            "dimensions/minecraft/the_nether/region/r.0.0.mca",
            "dimensions/minecraft/the_end/region/r.0.0.mca",
            "dimensions/example/deep/caverns/region/r.0.0.mca",
        ]
        for relative in paths:
            make_empty_region(os.path.join(self.tempdir, relative))

        modern_player = os.path.join(self.tempdir, "players", "data", "player.dat")
        os.makedirs(os.path.dirname(modern_player), exist_ok=True)
        open(modern_player, "wb").close()
        namespaced_data = os.path.join(self.tempdir, "data", "minecraft", "weather.dat")
        os.makedirs(os.path.dirname(namespaced_data), exist_ok=True)
        open(namespaced_data, "wb").close()

        world = World(self.tempdir)
        discovered = {(r._get_dimension_directory(), r._get_region_type_directory())
                      for r in world.regionsets}

        self.assertIn(("minecraft/overworld", "region"), discovered)
        self.assertIn(("minecraft/overworld", "poi"), discovered)
        self.assertIn(("minecraft/overworld", "entities"), discovered)
        self.assertIn(("minecraft/the_nether", "region"), discovered)
        self.assertIn(("minecraft/the_end", "region"), discovered)
        self.assertIn(("example/deep/caverns", "region"), discovered)
        self.assertTrue(world.isworld)
        self.assertIn(modern_player, world.players._set)
        self.assertIn(namespaced_data, world.data_files._set)

    def test_legacy_and_modern_dimension_ids_match(self):
        old = os.path.join(self.tempdir, "old")
        new = os.path.join(self.tempdir, "new")
        make_empty_region(os.path.join(old, "region", "r.0.0.mca"))
        make_empty_region(os.path.join(old, "DIM-1", "region", "r.0.0.mca"))
        make_empty_region(os.path.join(old, "DIM1", "region", "r.0.0.mca"))
        make_empty_region(os.path.join(new, "dimensions", "minecraft", "overworld", "region", "r.0.0.mca"))
        make_empty_region(os.path.join(new, "dimensions", "minecraft", "the_nether", "region", "r.0.0.mca"))
        make_empty_region(os.path.join(new, "dimensions", "minecraft", "the_end", "region", "r.0.0.mca"))

        old_ids = {r._get_dimension_directory() for r in World(old).regionsets}
        new_ids = {r._get_dimension_directory() for r in World(new).regionsets}
        self.assertEqual(old_ids, new_ids)
        self.assertEqual(old_ids, {"minecraft/overworld", "minecraft/the_nether", "minecraft/the_end"})


class ModernRegionFormatTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="regionfixer-region-")
        self.region_path = os.path.join(self.tempdir, "r.0.0.mca")

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_compression_id_3_uncompressed(self):
        raw = b"modern-uncompressed-chunk-data"
        write_raw_region_chunk(self.region_path, raw, region.COMPRESSION_NONE)
        rf = region.RegionFile(self.region_path)
        try:
            self.assertEqual(rf.get_blockdata(0, 0), raw)
        finally:
            rf.close()

    def test_compression_id_4_lz4_java_block_stream(self):
        raw = (b"LZ4 modern region data " * 20)
        stream = raw_lz4_java_stream(raw)
        write_raw_region_chunk(self.region_path, stream, region.COMPRESSION_LZ4)
        rf = region.RegionFile(self.region_path)
        try:
            self.assertEqual(rf.get_blockdata(0, 0), raw)
        finally:
            rf.close()

    def test_compression_id_4_real_compressed_lz4_block_and_full_checksum(self):
        # Raw LZ4 block for b"abcd" * 4:
        # 4 literal bytes followed by a 12-byte match at offset 4.
        raw = b"abcd" * 4
        compressed = bytes([0x48]) + b"abcd" + b"\x04\x00"
        token = 0x26  # LZ4 method + 64 KiB block-size level.
        checksum = lz4_java.xxhash32(raw, lz4_java.XXHASH_SEED)
        self.assertGreater(checksum, 0x0FFFFFFF)  # catches truncated-checksum bugs
        block = (lz4_java.MAGIC + bytes([token]) +
                 struct.pack("<III", len(compressed), len(raw), checksum) + compressed)
        terminal = lz4_java.MAGIC + bytes([0x16]) + struct.pack("<III", 0, 0, 0)
        write_raw_region_chunk(self.region_path, block + terminal, region.COMPRESSION_LZ4)
        rf = region.RegionFile(self.region_path)
        try:
            self.assertEqual(rf.get_blockdata(0, 0), raw)
        finally:
            rf.close()

    def test_external_mcc_chunk_read_and_unlink(self):
        raw = b"external modern chunk"
        payload = zlib.compress(raw)
        write_raw_region_chunk(self.region_path, b"", region.EXTERNAL_CHUNK_FLAG | region.COMPRESSION_ZLIB)
        # External chunks have length=1, not 1+payload; adjust the generated header.
        with open(self.region_path, "r+b") as handle:
            handle.seek(2 * region.SECTOR_LENGTH)
            handle.write(struct.pack(">I", 1))
            handle.write(struct.pack(">B", region.EXTERNAL_CHUNK_FLAG | region.COMPRESSION_ZLIB))
        external_path = os.path.join(self.tempdir, "c.0.0.mcc")
        with open(external_path, "wb") as handle:
            handle.write(payload)

        rf = region.RegionFile(self.region_path)
        self.assertTrue(rf.metadata[0, 0].external)
        self.assertEqual(rf.get_blockdata(0, 0), raw)
        rf.unlink_chunk(0, 0)
        rf.close()
        self.assertFalse(os.path.exists(external_path))

    def test_oversized_write_uses_external_sidecar(self):
        # Type 3 avoids compression so the sector limit is deterministic.
        raw = bytes(range(256)) * 4200  # ~1.07 MiB > 255 sectors
        open(self.region_path, "wb").close()
        rf = region.RegionFile(self.region_path)
        rf.write_blockdata(0, 0, raw, compression=region.COMPRESSION_NONE)
        self.assertTrue(rf.metadata[0, 0].external)
        self.assertEqual(rf.metadata[0, 0].blocklength, 1)
        self.assertEqual(rf.get_blockdata(0, 0), raw)
        rf.close()
        self.assertTrue(os.path.exists(os.path.join(self.tempdir, "c.0.0.mcc")))


class ModernEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="regionfixer-modern-cli-")

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_cli_scans_modern_overworld_with_lz4_region(self):
        region_path = os.path.join(
            self.tempdir, "dimensions", "minecraft", "overworld", "region", "r.0.0.mca"
        )
        os.makedirs(os.path.dirname(region_path), exist_ok=True)
        raw_nbt = modern_chunk_nbt_bytes()
        write_raw_region_chunk(region_path, raw_lz4_java_stream(raw_nbt), region.COMPRESSION_LZ4)
        write_level_dat(self.tempdir)

        project_root = os.path.dirname(os.path.dirname(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(project_root, "regionfixer.py"), "--verbose", self.tempdir],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout)
        self.assertIn("1 region/level files", result.stdout)
        self.assertIn("No problems found", result.stdout)


if __name__ == "__main__":
    unittest.main()
