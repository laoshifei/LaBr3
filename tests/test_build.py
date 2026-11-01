"""Run the real build executables against explicit event-grouping examples."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from fixtures import ROOT, Hit, REPO, VARIANTS, checked_run, hits_at, read_events, write_sorted


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="labr3-build-tests-")
        cls.addClassCleanup(cls.storage.cleanup)
        cls.work = Path(cls.storage.name)
        cls.executables = {}
        for variant in VARIANTS:
            binary = cls.work / variant
            checked_run(["make", "-B", "-C", str(REPO / variant / "build"),
                         f"out={binary}"])
            cls.executables[variant] = binary

    def run_case(self, variant, hits, **fixture_options):
        case = tempfile.TemporaryDirectory(prefix="case-", dir=self.work)
        self.addCleanup(case.cleanup)
        directory = Path(case.name)
        build_dir = directory / "build"
        build_dir.mkdir()
        if hits is not None:
            write_sorted(directory / "sort/run0001_sort.root", hits, **fixture_options)
        return build_dir, build_dir / "run0001_build.root"

    def execute(self, variant, build_dir, arguments=("1",)):
        return subprocess.run([str(self.executables[variant]), *arguments], cwd=build_dir,
                              capture_output=True, text=True, timeout=30)

    def assert_grouping(self, timestamps, groups, replace_existing=False, **fixture_options):
        hits = hits_at(timestamps)
        expected = [[hits[i] for i in group] for group in groups]
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.run_case(variant, hits, **fixture_options)
                if replace_existing:
                    output.write_bytes(b"previous output")
                result = self.execute(variant, directory)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                title, types, events = read_events(output)
                self.assertEqual(title, variant)
                self.assertEqual(types, {"m": "UShort_t", "board": "Short_t",
                                        "channel": "Short_t", "energy": "Double_t",
                                        "timestamp": "Long64_t"})
                self.assertEqual(events, expected)
                self.assertEqual(sum(map(len, events)), len(hits))
                self.assertTrue(all(1 <= len(event) <= 8 for event in events))
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def assert_failure(self, timestamps, diagnostic, preserve_existing=False,
                       output_is_directory=False, input_hits=None, **fixture_options):
        hits = input_hits if input_hits is not None else (None if timestamps is None else hits_at(timestamps))
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.run_case(variant, hits, **fixture_options)
                if preserve_existing:
                    output.write_bytes(b"previous output")
                if output_is_directory:
                    output.mkdir()
                result = self.execute(variant, directory)
                self.assertGreater(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(diagnostic, result.stderr.lower())
                if preserve_existing:
                    self.assertEqual(output.read_bytes(), b"previous output")
                elif output_is_directory:
                    self.assertTrue(output.is_dir())
                else:
                    self.assertFalse(output.exists(), "Failure left a final output file")
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def test_empty_tree(self):
        self.assert_grouping([], [])

    def test_single_record(self):
        self.assert_grouping([1000], [[0]])

    def test_final_coincidence(self):
        self.assert_grouping([0, 1000, 80000, 90000], [[0, 1], [2, 3]])

    def test_final_single(self):
        self.assert_grouping([0, 60000], [[0], [1]])

    def test_strict_window_boundary(self):
        self.assert_grouping([0, 49999, 50000, 50001], [[0, 1], [2, 3]])

    def test_window_uses_first_hit(self):
        self.assert_grouping([0, 40000, 80000], [[0, 1], [2]])

    def test_equal_timestamps(self):
        self.assert_grouping([0, 0, 0], [[0, 1, 2]])

    def test_eight_hits(self):
        self.assert_grouping(list(range(8)), [list(range(8))])

    def test_eight_hits_followed_by_new_window(self):
        self.assert_grouping(list(range(8)) + [50000], [list(range(8)), [8]])

    def test_nine_hits_fail(self):
        self.assert_failure([0] * 9, "multiplicity")

    def test_overflow_after_completed_event(self):
        self.assert_failure([0] + [60000] * 9, "multiplicity")

    def test_out_of_order_within_window(self):
        self.assert_failure([0, 40000, 30000], "not sorted")

    def test_out_of_order_across_windows(self):
        self.assert_failure([0, 60000, 55000], "not sorted")

    def test_signed_timestamp_span(self):
        self.assert_grouping([-(2**63), 2**63 - 1], [[0], [1]])

    def test_large_timestamps_preserve_picoseconds(self):
        base = 2**53 + 123
        self.assert_grouping([base, base + 49999, base + 50000], [[0, 1], [2]])

    def test_failure_preserves_existing_output(self):
        self.assert_failure([0] + [60000] * 9, "multiplicity", preserve_existing=True)

    def test_success_replaces_existing_output(self):
        self.assert_grouping([1000], [[0]], replace_existing=True)

    def test_missing_input(self):
        self.assert_failure(None, "cannot open input")

    def test_missing_tree(self):
        self.assert_failure([0], "missing tree", tree_name="other")

    def test_missing_branch(self):
        self.assert_failure([0], "energy", omit="energy")

    def test_wrong_branch_type(self):
        self.assert_failure([0], "energy", wrong_energy_type=True)

    def test_multileaf_scalar_branch_is_rejected(self):
        for timestamps in ([], [0]):
            with self.subTest(timestamps=timestamps):
                self.assert_failure(timestamps, "invalid scalar branch timestamp", multileaf_timestamp=True)

    def test_output_publish_failure(self):
        self.assert_failure([0], "cannot publish output", output_is_directory=True)

    def test_invalid_channels(self):
        for channel in (-1, 8):
            with self.subTest(channel=channel):
                self.assert_failure([], "invalid channel", input_hits=[Hit(1, channel, 100., 0)],
                                    preserve_existing=True)

    def test_nonfinite_energy(self):
        for energy in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(energy=energy):
                self.assert_failure([], "non-finite energy", input_hits=[Hit(1, 2, energy, 0)],
                                    preserve_existing=True)

    def test_unneeded_waveforms_are_ignored(self):
        self.assert_grouping([0, 1000, 50000], [[0, 1], [2]], waveform=True)

    def test_corrupt_input_and_non_tree_object(self):
        for variant in VARIANTS:
            for kind in ("corrupt", "object"):
                with self.subTest(variant=variant, kind=kind):
                    directory, output = self.run_case(variant, [])
                    source = directory.parent / "sort/run0001_sort.root"
                    if kind == "corrupt":
                        source.write_bytes(b"not a ROOT file")
                    else:
                        root = ROOT.TFile(str(source), "RECREATE")
                        ROOT.TNamed("tr", "not a tree").Write()
                        root.Close()
                    result = self.execute(variant, directory)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn("cannot open input" if kind == "corrupt" else "missing tree", result.stderr)
                    self.assertFalse(output.exists())

    @unittest.skipIf(os.geteuid() == 0, "requires normal filesystem permission checks")
    def test_unwritable_output_directory(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.run_case(variant, hits_at([0]))
                output.write_bytes(b"previous output")
                directory.chmod(0o555)
                try:
                    result = self.execute(variant, directory)
                finally:
                    directory.chmod(0o755)
                self.assertGreater(result.returncode, 0)
                self.assertIn("cannot create output", result.stderr)
                self.assertEqual(output.read_bytes(), b"previous output")
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def test_invalid_run_number(self):
        for variant in VARIANTS:
            for arguments in ((), ("abc",), ("-1",), ("2147483648",)):
                with self.subTest(variant=variant, arguments=arguments):
                    directory, output = self.run_case(variant, [])
                    result = self.execute(variant, directory, arguments)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn("usage:" if not arguments else "invalid run number", result.stderr)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
