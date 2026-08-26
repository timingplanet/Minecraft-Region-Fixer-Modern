import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from nbt import nbt
from nbt import region
from regionfixer_core import constants as c
from regionfixer_core.world import RegionSet, World


def write_level_dat(world_path, name="Summary Test World", data_version=5000):
    root = nbt.NBTFile()
    root.name = ""
    data = nbt.TAG_Compound(name="Data")
    data.tags.append(nbt.TAG_String(name="LevelName", value=name))
    data.tags.append(nbt.TAG_Int(name="DataVersion", value=data_version))
    root.tags.append(data)
    os.makedirs(world_path, exist_ok=True)
    root.write_file(filename=os.path.join(world_path, "level.dat"))


def chunk_bytes(x=0, z=0):
    root = nbt.NBTFile()
    root.name = ""
    root.tags.append(nbt.TAG_Int(name="DataVersion", value=5000))
    root.tags.append(nbt.TAG_Int(name="xPos", value=x))
    root.tags.append(nbt.TAG_Int(name="zPos", value=z))
    root.tags.append(nbt.TAG_List(name="sections", type=nbt.TAG_Compound))
    import io
    buffer = io.BytesIO()
    root.write_file(buffer=buffer)
    return buffer.getvalue()


def write_region(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    rf = region.RegionFile(path)
    try:
        rf.write_blockdata(0, 0, chunk_bytes(), compression=region.COMPRESSION_ZLIB)
    finally:
        rf.close()


class ScanSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="regionfixer-summary-")

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_cli_clean_world_has_detailed_readable_summary(self):
        write_level_dat(self.tempdir)
        write_region(os.path.join(
            self.tempdir, "dimensions", "minecraft", "overworld", "region", "r.0.0.mca"
        ))
        project_root = os.path.dirname(os.path.dirname(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(project_root, "regionfixer.py"), "--verbose", self.tempdir],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, c.RV_OK, msg=result.stdout)
        for expected in (
            "SCAN SUMMARY",
            "Overall result  CLEAN",
            "Storage layout  Namespaced-dimension layout",
            "DataVersion     5000",
            "FILES SCANNED",
            "CHUNK HEALTH",
            "REGION FILE HEALTH",
            "PLAYER / DATA HEALTH",
            "DIMENSION / REGION-TYPE BREAKDOWN",
            "Overworld",
            "Level/Region",
            "RESULT",
            "No problems found",
        ):
            self.assertIn(expected, result.stdout)


    def test_cli_problem_world_summarizes_problem_categories(self):
        write_level_dat(self.tempdir, name="Problem Summary World")
        # Write a chunk into r.0.0.mca whose NBT claims it belongs at x=5.
        # Region Fixer should classify it as wrong-located.
        region_path = os.path.join(
            self.tempdir, "dimensions", "minecraft", "overworld", "region", "r.0.0.mca"
        )
        os.makedirs(os.path.dirname(region_path), exist_ok=True)
        open(region_path, "wb").close()
        root = nbt.NBTFile()
        root.name = ""
        root.tags.append(nbt.TAG_Int(name="DataVersion", value=5000))
        root.tags.append(nbt.TAG_Int(name="xPos", value=5))
        root.tags.append(nbt.TAG_Int(name="zPos", value=0))
        root.tags.append(nbt.TAG_List(name="sections", type=nbt.TAG_Compound))
        import io
        buffer = io.BytesIO()
        root.write_file(buffer=buffer)
        rf = region.RegionFile(region_path)
        try:
            rf.write_blockdata(0, 0, buffer.getvalue(), compression=region.COMPRESSION_ZLIB)
        finally:
            rf.close()

        bad_data = os.path.join(self.tempdir, "data", "minecraft", "broken.dat")
        os.makedirs(os.path.dirname(bad_data), exist_ok=True)
        with open(bad_data, "wb") as handle:
            handle.write(b"not valid nbt")

        project_root = os.path.dirname(os.path.dirname(__file__))
        result = subprocess.run(
            [sys.executable, os.path.join(project_root, "regionfixer.py"), "--verbose", self.tempdir],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, c.RV_BAD_WORLD, msg=result.stdout)
        self.assertIn("Overall result  ISSUES FOUND", result.stdout)
        self.assertIn("Wrong located         1", result.stdout)
        self.assertIn("World/dimension data files  1 total, 1 unreadable", result.stdout)
        self.assertIn("ISSUES FOUND - 1 chunk problem, 0 region problems, and 1 data-file problem.", result.stdout)
        self.assertIn("Use --log <file> for exact problematic chunk/file locations.", result.stdout)

    def test_generate_report_false_keeps_legacy_key_shape(self):
        region_set = RegionSet(region_list=[])
        chunk_counts, region_counts = region_set.generate_report(False)
        self.assertEqual(set(chunk_counts), set(c.CHUNK_PROBLEMS) | {"TOTAL"})
        self.assertEqual(set(region_counts), set(c.REGION_PROBLEMS) | {"TOTAL"})

        write_level_dat(self.tempdir)
        world = World(self.tempdir)
        chunk_counts, region_counts = world.generate_report(False)
        self.assertEqual(set(chunk_counts), set(c.CHUNK_PROBLEMS) | {"TOTAL"})
        self.assertEqual(set(region_counts), set(c.REGION_PROBLEMS) | {"TOTAL"})


if __name__ == "__main__":
    unittest.main()
