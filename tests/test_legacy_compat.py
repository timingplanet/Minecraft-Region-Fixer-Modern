import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from nbt import nbt
from nbt import region
from regionfixer_core import constants as c
from regionfixer_core.scan import scan_data, scan_region_file
from regionfixer_core.world import ScannedDataFile, ScannedRegionFile, World


def nbt_bytes(tags):
    root = nbt.NBTFile()
    root.name = ""
    root.tags.extend(tags)
    buffer = io.BytesIO()
    root.write_file(buffer=buffer)
    return buffer.getvalue()


def legacy_level_chunk(x=0, z=0):
    level = nbt.TAG_Compound(name="Level")
    level.tags.append(nbt.TAG_Int(name="xPos", value=x))
    level.tags.append(nbt.TAG_Int(name="zPos", value=z))
    level.tags.append(nbt.TAG_List(name="Entities", type=nbt.TAG_Compound))
    return nbt_bytes([
        nbt.TAG_Int(name="DataVersion", value=1343),  # Java 1.12.2-era layout
        level,
    ])


def modern_level_chunk(x=0, z=0):
    return nbt_bytes([
        nbt.TAG_Int(name="DataVersion", value=5000),
        nbt.TAG_Int(name="xPos", value=x),
        nbt.TAG_Int(name="zPos", value=z),
        nbt.TAG_List(name="sections", type=nbt.TAG_Compound),
    ])


def write_region(path, raw_chunk, compression=region.COMPRESSION_ZLIB):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    rf = region.RegionFile(path)
    try:
        rf.write_blockdata(0, 0, raw_chunk, compression=compression)
    finally:
        rf.close()


def write_level_dat(world_path, name="Compatibility Test World"):
    root = nbt.NBTFile()
    root.name = ""
    data = nbt.TAG_Compound(name="Data")
    data.tags.append(nbt.TAG_String(name="LevelName", value=name))
    root.tags.append(data)
    os.makedirs(world_path, exist_ok=True)
    root.write_file(filename=os.path.join(world_path, "level.dat"))


class LegacyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="regionfixer-legacy-")

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_legacy_root_region_still_scans_cleanly(self):
        path = os.path.join(self.tempdir, "region", "r.0.0.mca")
        write_region(path, legacy_level_chunk())
        scanned = scan_region_file(ScannedRegionFile(path, folder="region"), 300, False)
        self.assertEqual(scanned.status, c.REGION_OK)
        self.assertEqual(scanned[(0, 0)][c.TUPLE_STATUS], c.CHUNK_OK)

    def test_modern_chunk_format_still_scans_cleanly(self):
        path = os.path.join(self.tempdir, "region", "r.0.0.mca")
        write_region(path, modern_level_chunk())
        scanned = scan_region_file(ScannedRegionFile(path, folder="region"), 300, False)
        self.assertEqual(scanned.status, c.REGION_OK)
        self.assertEqual(scanned[(0, 0)][c.TUPLE_STATUS], c.CHUNK_OK)

    def test_legacy_gzip_and_zlib_compression_remain_supported(self):
        raw = legacy_level_chunk()
        for compression in (region.COMPRESSION_GZIP, region.COMPRESSION_ZLIB):
            path = os.path.join(self.tempdir, str(compression), "r.0.0.mca")
            write_region(path, raw, compression=compression)
            rf = region.RegionFile(path)
            try:
                self.assertEqual(rf.get_blockdata(0, 0), raw)
            finally:
                rf.close()

    def test_legacy_idcounts_uncompressed_nbt_still_scans(self):
        path = os.path.join(self.tempdir, "data", "idcounts.dat")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(nbt_bytes([nbt.TAG_Int(name="map", value=7)]))
        result = scan_data(ScannedDataFile(path))
        self.assertEqual(result.status, c.DATAFILE_OK)

    def test_old_dimension_layout_is_discovered(self):
        for relative in (
            "region/r.0.0.mca",
            "DIM-1/region/r.0.0.mca",
            "DIM1/region/r.0.0.mca",
        ):
            write_region(os.path.join(self.tempdir, relative), legacy_level_chunk())
        discovered = {r._get_dimension_directory() for r in World(self.tempdir).regionsets}
        self.assertEqual(discovered, {
            "minecraft/overworld",
            "minecraft/the_nether",
            "minecraft/the_end",
        })

    def test_cross_version_chunk_replacement_does_not_mix_dimensions_or_status(self):
        target_path = os.path.join(self.tempdir, "target-modern")
        backup_path = os.path.join(self.tempdir, "backup-legacy")

        # Both target chunks are wrong-located. The legacy Overworld backup is
        # healthy, while the legacy Nether backup is intentionally also wrong.
        write_region(
            os.path.join(target_path, "dimensions", "minecraft", "overworld", "region", "r.0.0.mca"),
            modern_level_chunk(x=1, z=0),
        )
        write_region(
            os.path.join(target_path, "dimensions", "minecraft", "the_nether", "region", "r.0.0.mca"),
            modern_level_chunk(x=1, z=0),
        )
        write_region(os.path.join(backup_path, "region", "r.0.0.mca"), legacy_level_chunk(x=0, z=0))
        write_region(os.path.join(backup_path, "DIM-1", "region", "r.0.0.mca"), legacy_level_chunk(x=1, z=0))

        target = World(target_path)
        backup = World(backup_path)
        for regionset in target.regionsets:
            for coords, scanned_region in list(regionset._set.items()):
                regionset._set[coords] = scan_region_file(scanned_region, 300, False)

        fixed = target.replace_problematic_chunks(
            [backup], c.CHUNK_WRONG_LOCATED, 300, False
        )
        self.assertEqual(fixed, 1)

        statuses = {}
        for regionset in World(target_path).regionsets:
            scanned = scan_region_file(next(iter(regionset._set.values())), 300, False)
            statuses[regionset._get_dimension_directory()] = scanned[(0, 0)][c.TUPLE_STATUS]

        self.assertEqual(statuses["minecraft/overworld"], c.CHUNK_OK)
        self.assertEqual(statuses["minecraft/the_nether"], c.CHUNK_WRONG_LOCATED)

    def test_cli_entry_point_and_existing_verbose_option_still_work(self):
        world_path = os.path.join(self.tempdir, "Legacy Test World")
        write_region(os.path.join(world_path, "region", "r.0.0.mca"), legacy_level_chunk())
        write_level_dat(world_path, "Legacy Test World")
        project_root = os.path.dirname(os.path.dirname(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(project_root, "regionfixer.py"), "--verbose", world_path],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, c.RV_OK, msg=result.stdout)
        self.assertIn("Scanning world:", result.stdout)
        self.assertIn("No problems found", result.stdout)


class ModernDataLocationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="regionfixer-modern-data-")

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_modern_last_id_uncompressed_nbt_scans(self):
        path = os.path.join(self.tempdir, "data", "minecraft", "maps", "last_id.dat")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(nbt_bytes([nbt.TAG_Int(name="map", value=12)]))
        result = scan_data(ScannedDataFile(path))
        self.assertEqual(result.status, c.DATAFILE_OK)

    def test_dimension_specific_data_files_are_included(self):
        root_data = os.path.join(self.tempdir, "data", "minecraft", "weather.dat")
        end_data = os.path.join(
            self.tempdir,
            "dimensions", "minecraft", "the_end", "data", "minecraft", "ender_dragon_fight.dat",
        )
        for path in (root_data, end_data):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # The discovery test only needs a .dat file; parsing is tested elsewhere.
            open(path, "wb").close()
        world = World(self.tempdir)
        self.assertIn(root_data, world.data_files._set)
        self.assertIn(end_data, world.data_files._set)


if __name__ == "__main__":
    unittest.main()
