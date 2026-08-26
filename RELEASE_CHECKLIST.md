# Release checklist

Use this before publishing a tagged release.

1. Run `python -m unittest discover -s tests -v`.
2. Run `python -m compileall -q regionfixer.py regionfixer_core nbt mutf8 progressbar tests`.
3. Verify `python regionfixer.py --help` and `python regionfixer.py --version`.
4. Scan an untouched copy of at least one legacy-layout world.
5. Scan an untouched copy of at least one current namespaced-dimension world.
6. Confirm the detailed summary is readable for both a clean world and a world
   with known problems.
7. Test backup replacement only on disposable copies, including a cross-layout
   backup if available.
8. Confirm the release still contains `COPYING.txt`, `CONTRIBUTORS.txt`, and
   `FORK_NOTICE.md`.
9. Review the destructive-operation warning in the README before publishing.
10. Tag the release with the same version reported by `regionfixer.py --version`.

Do not claim a Minecraft version is supported solely because a directory can be
found. Real-world scan and repair testing should accompany format changes.
