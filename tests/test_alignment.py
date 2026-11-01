"""Validate alignment matching, input contracts and output publication."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from fixtures import ROOT, Hit, REPO, VARIANTS, checked_run, write_channel_hits


def timed_hits(channel, timestamps):
    return [Hit(1, channel, 100, timestamp) for timestamp in timestamps]


class AlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="labr3-alignment-tests-")
        cls.addClassCleanup(cls.storage.cleanup)
        cls.work = Path(cls.storage.name)
        cls.executables = {}
        for variant in VARIANTS:
            source = cls.work / variant
            source.mkdir()
            for name in ("main.cpp", "Alignment.cpp", "Alignment.h", "set.h", "Makefile"):
                shutil.copyfile(REPO / variant / "alignment" / name, source / name)
            checked_run(["make", "-B", "-C", str(source)])
            cls.executables[variant] = source / "alignment"

    def prepare_case(self, hits, **options):
        case = tempfile.TemporaryDirectory(prefix="case-", dir=self.work)
        self.addCleanup(case.cleanup)
        directory = Path(case.name)
        alignment = directory / "alignment"
        alignment.mkdir()
        if hits is not None:
            write_channel_hits(directory / "raw2root/run0001.root", hits, **options)
        return alignment, alignment / "run0001_aligiment.root"

    def execute(self, variant, directory, reference=None, arguments=None):
        if arguments is None:
            arguments = ["1"]
            if reference is not None:
                arguments += ["--reference", str(reference)]
        return subprocess.run([str(self.executables[variant]), *arguments], cwd=directory,
                              capture_output=True, text=True, timeout=30)

    def assert_alignment(self, hits, expected, reference=0, requested_reference=None,
                         replace_existing=False, **options):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case(hits, **options)
                if replace_existing:
                    output.write_bytes(b"previous output")
                result = self.execute(variant, directory, requested_reference)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                source = ROOT.TFile.Open(str(output), "READ")
                self.assertTrue(source and not source.IsZombie())
                try:
                    histogram_names = {key.GetName() for key in source.GetListOfKeys()
                                       if key.GetName().startswith("h_ch")}
                    names = {f"h_ch{reference:02d}_ch{channel:02d}" for channel in expected}
                    self.assertEqual(histogram_names, names)
                    for channel, values in expected.items():
                        histogram = source.Get(f"h_ch{reference:02d}_ch{channel:02d}")
                        self.assertEqual(histogram.GetEntries(), len(values))
                        self.assertEqual(histogram.GetNbinsX(), 20000)
                        expected_bins = {}
                        for value in values:
                            index = histogram.FindBin(value)
                            expected_bins[index] = expected_bins.get(index, 0) + 1
                        for index, count in expected_bins.items():
                            self.assertEqual(histogram.GetBinContent(index), count)
                        self.assertEqual(histogram.Integral(), len(values))
                        if values:
                            self.assertAlmostEqual(histogram.GetMean(), sum(values) / len(values), places=9)
                    self.assertEqual(source.Get("alignment_reference_channel").GetVal(), reference)
                    self.assertEqual(source.Get("alignment_window_ps").GetVal(), 1_000_000)
                    self.assertEqual(source.Get("alignment_time_difference").GetTitle(),
                                     "t_reference - t_channel (ns)")
                    self.assertIn("target used at most once", source.Get("alignment_matching_rule").GetTitle())
                    channels = {hit.channel for hit in hits} | set(options.get("empty_channels", ()))
                    self.assertEqual(source.Get("alignment_present_channels").GetVal(),
                                     sum(1 << channel for channel in channels))
                    counts = source.Get("h_input_entries")
                    for channel in range(8):
                        self.assertEqual(counts.GetBinContent(channel + 1),
                                         sum(hit.channel == channel for hit in hits))
                finally:
                    source.Close()
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def assert_rejected(self, hits, diagnostic, requested_reference=None,
                        preserve_existing=False, **options):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case(hits, **options)
                previous = b"previous complete output"
                if preserve_existing:
                    output.write_bytes(previous)
                result = self.execute(variant, directory, requested_reference)
                self.assertGreater(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(diagnostic.lower(), result.stderr.lower())
                if preserve_existing:
                    self.assertEqual(output.read_bytes(), previous)
                else:
                    self.assertFalse(output.exists())
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def test_positive_window_boundary_keeps_reference(self):
        hits = timed_hits(0, [1_000_000]) + timed_hits(1, [0, 1_000_100])
        self.assert_alignment(hits, {1: [-.1]})

    def test_reference_input_order_is_checked(self):
        hits = timed_hits(0, [100, 0]) + timed_hits(1, [50])
        self.assert_rejected(hits, "not sorted")

    def test_wrong_timestamp_type_is_rejected(self):
        self.assert_rejected(timed_hits(0, [100]) + timed_hits(1, [150]),
                             "timestamp", wrong_timestamp_type=True)

    def test_signed_differences_and_counts(self):
        hits = timed_hits(0, [0, 5_000_000]) + timed_hits(1, [-1000, 2000, 5_003_000])
        self.assert_alignment(hits, {1: [1., -2., -3.]})

    def test_negative_window_boundary(self):
        hits = timed_hits(0, [0, 2_000_000]) + timed_hits(1, [1_000_000, 2_000_100])
        self.assert_alignment(hits, {1: [-.1]})

    def test_strict_window_edges(self):
        hits = timed_hits(0, [0]) + timed_hits(1, [-1_000_000, -999999, 999999, 1_000_000])
        self.assert_alignment(hits, {1: [999.999, -999.999]})

    def test_earliest_reference_is_reused(self):
        self.assert_alignment(timed_hits(0, [0, 100]) + timed_hits(1, [50, 100, 150]),
                              {1: [-.05, -.1, -.15]})

    def test_duplicate_timestamps_do_not_duplicate_target(self):
        self.assert_alignment(timed_hits(0, [0, 0]) + timed_hits(1, [0, 1]), {1: [0., -.001]})

    def test_large_timestamps_preserve_picoseconds(self):
        base = 2**60
        self.assert_alignment(timed_hits(0, [base]) + timed_hits(1, [base - 1, base + 1]),
                              {1: [.001, -.001]})

    def test_extreme_timestamp_spans_do_not_overflow(self):
        for ref, target in ((-(2**63), 2**63-1), (2**63-1, -(2**63)),
                            (-(2**63), 0), (0, -(2**63))):
            with self.subTest(reference=ref, target=target):
                self.assert_alignment(timed_hits(0, [ref]) + timed_hits(1, [target]), {1: []})

    def test_empty_input(self):
        self.assert_alignment([], {}, reference=-1)

    def test_all_empty_channel_trees(self):
        self.assert_alignment([], {}, reference=-1, empty_channels=(0, 2))

    def test_single_channel(self):
        self.assert_alignment(timed_hits(3, [0, 100]), {}, reference=3)

    def test_empty_lower_channels_do_not_become_reference(self):
        self.assert_alignment(timed_hits(2, [1000]) + timed_hits(3, [1050]),
                              {0: [], 1: [], 3: [-.05]}, reference=2, empty_channels=(0, 1))

    def test_explicit_reference_and_lower_target(self):
        self.assert_alignment(timed_hits(2, [1000]) + timed_hits(3, [1200]),
                              {2: [.2]}, reference=3, requested_reference=3)

    def test_missing_or_empty_explicit_reference(self):
        self.assert_rejected(timed_hits(3, [100]), "reference channel is missing or empty",
                             requested_reference=2)
        self.assert_rejected(timed_hits(3, [100]), "reference channel is missing or empty",
                             requested_reference=2, empty_channels=(2,))

    def test_target_input_order_is_checked(self):
        self.assert_rejected(timed_hits(0, [0]) + timed_hits(1, [200, 100]), "not sorted")

    def test_missing_input(self):
        self.assert_rejected(None, "cannot open input")

    def test_corrupt_input(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case([])
                (directory.parent / "raw2root/run0001.root").write_bytes(b"not a ROOT file")
                result = self.execute(variant, directory)
                self.assertGreater(result.returncode, 0)
                self.assertIn("cannot open input", result.stderr)
                self.assertFalse(output.exists())

    def test_missing_required_branch(self):
        for name in ("channel", "timestamp"):
            with self.subTest(branch=name):
                self.assert_rejected(timed_hits(0, [0]), "invalid scalar branch", omit=name)

    def test_timestamp_array_is_rejected(self):
        self.assert_rejected(timed_hits(0, [0]), "invalid scalar branch timestamp", array_timestamp=True)

    def test_multileaf_scalar_branch_is_rejected(self):
        for hits in ([], timed_hits(0, [0])):
            with self.subTest(hits=hits):
                self.assert_rejected(hits, "invalid scalar branch timestamp",
                                     empty_channels=(0,), multileaf_timestamp=True)

    def test_channel_mismatch(self):
        for channel in (-1, 8):
            with self.subTest(channel=channel):
                self.assert_rejected(timed_hits(channel, [0]), "channel mismatch", tree_channel=0)

    def test_unexpected_channel_and_wrong_stage_tree(self):
        self.assert_rejected(timed_hits(0, [0]), "unexpected channel tree", tree_channel=8)
        self.assert_rejected(timed_hits(0, [0]), "unexpected channel tree", tree_name="tr")

    def test_non_tree_and_metadata_only_input(self):
        for variant in VARIANTS:
            for name, diagnostic in (("tr_ch00", "invalid input tree"),
                                     ("metadata", "missing channel trees")):
                with self.subTest(variant=variant, object=name):
                    directory, output = self.prepare_case([])
                    source = ROOT.TFile(str(directory.parent / "raw2root/run0001.root"), "RECREATE")
                    ROOT.TNamed(name, "wrong input").Write()
                    source.Close()
                    result = self.execute(variant, directory)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn(diagnostic, result.stderr)
                    self.assertFalse(output.exists())

    def test_optional_energy_and_waveforms_are_not_read(self):
        self.assert_alignment(timed_hits(0, [0]) + timed_hits(1, [100]), {1: [-.1]},
                              omit="energy_ch", waveform=True)

    def test_failure_preserves_existing_output(self):
        self.assert_rejected(timed_hits(0, [0]) + timed_hits(1, [200, 100]), "not sorted",
                             preserve_existing=True)

    def test_success_replaces_existing_output(self):
        self.assert_alignment(timed_hits(0, [0]) + timed_hits(1, [100]), {1: [-.1]},
                              replace_existing=True)

    @unittest.skipIf(os.geteuid() == 0, "requires normal filesystem permission checks")
    def test_unwritable_output_directory(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case(timed_hits(0, [0]))
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

    def test_output_publish_failure(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case(timed_hits(0, [0]))
                output.mkdir()
                result = self.execute(variant, directory)
                self.assertGreater(result.returncode, 0)
                self.assertIn("cannot publish output", result.stderr)
                self.assertTrue(output.is_dir())
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def test_invalid_arguments(self):
        for variant in VARIANTS:
            for arguments in ([], ["abc"], ["-1"], ["1", "--reference", "8"],
                              ["1", "--reference", "-1"], ["1", "--ref", "0"],
                              ["1", "--reference"]):
                with self.subTest(variant=variant, arguments=arguments):
                    directory, output = self.prepare_case([])
                    result = self.execute(variant, directory, arguments=arguments)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn("alignment:", result.stderr)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
