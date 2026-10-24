import contextlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools.create_benchmark_project import main, make_plan


class BenchmarkProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in ("sample.prt.1", "sample.prt.2", "sample.asm.1", "sample.drw.1"):
            (self.source / name).write_bytes(name.encode())

    def test_complete_bounded_graph_with_reused_components(self):
        items, files, edges, stats = make_plan(self.source, 10000, 1000)
        self.assertEqual(len(items), 11000)
        self.assertEqual(len(files), 35750)
        self.assertEqual(stats["drawings"], 2750)
        self.assertEqual(stats["roots"], 10)
        self.assertEqual(stats["max_levels"], 4)
        self.assertEqual(len({name for _, name in files}), len(files))
        self.assertTrue(all(0 <= p < 1000 and p < c < 11000 for p, c in edges))
        children = {c for _, c in edges}
        self.assertEqual(set(range(11000)) - children, set(range(10)))
        self.assertGreater(stats["expanded_bom_rows"], len(items))
        counts = {}
        for _, child in edges:
            counts[child] = counts.get(child, 0) + 1
        self.assertTrue(any(n > 1 for child, n in counts.items() if child >= 1000))
        self.assertEqual(make_plan(self.source, 10000, 1000)[2:], (edges, stats))

    def test_one_assembly_and_one_part_have_no_self_link(self):
        items, files, edges, stats = make_plan(self.source, 1, 1)
        self.assertEqual(set(edges), {(0, 1)})
        self.assertEqual(stats["expanded_bom_rows"], 2)

    def test_dry_run_and_existing_destination_never_mutate_database(self):
        db = self.root / "fixture.db"
        with sqlite3.connect(db) as conn:
            conn.executescript("CREATE TABLE schema_migrations(version INTEGER);"
                               "INSERT INTO schema_migrations VALUES(49);"
                               "CREATE TABLE projects(id INTEGER,name TEXT,working_directory TEXT);")
            conn.execute("INSERT INTO projects VALUES(1,'source',?)", (str(self.source),))
        original = db.read_bytes()
        dest = self.root / "benchmark"
        argv = ["--database", str(db), "--source", str(self.source),
                "--destination", str(dest), "--parts", "10", "--assemblies", "2"]
        with contextlib.redirect_stdout(io.StringIO()):
            result = main(argv)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(dest.exists())
        self.assertEqual(db.read_bytes(), original)
        with patch("tools.create_benchmark_project.shutil.disk_usage") as usage:
            usage.return_value.free = 6 * 1024 ** 3
            with self.assertRaisesRegex(ValueError, "less than.*6 GiB"):
                main(argv + ["--apply"])
        self.assertFalse(dest.exists())
        self.assertEqual(db.read_bytes(), original)
        dest.mkdir()
        marker = dest / "existing.prt.1"
        marker.write_bytes(b"must be preserved")
        with self.assertRaises(ValueError):
            main(argv + ["--apply"])
        self.assertEqual(marker.read_bytes(), b"must be preserved")
        self.assertEqual(db.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
