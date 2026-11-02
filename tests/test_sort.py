"""Exercise sorting and calibration against explicit scalar-field expectations."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from fixtures import (ROOT, Hit, REPO, VARIANTS, checked_run, copy_program, read_events, read_sorted_hits,
                      write_channel_hits)


def calibration_text(values=None):
    values = values or {}
    return "".join(f"{channel} {' '.join(map(str, values.get(channel, (0, 1, 0))))} 0\n"
                   for channel in range(8))


def offsets_text(values=None):
    values = values or {}
    return "".join(f"{channel} {values.get(channel, 0)}\n" for channel in range(8))


class SortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="labr3-sort-tests-")
        cls.addClassCleanup(cls.storage.cleanup)
        cls.work = Path(cls.storage.name)
        cls.executables = {}
        for variant in VARIANTS:
            source = copy_program(cls.work, variant, "sort")
            binary = source / "sort"
            checked_run(["make", "-B", "-C", str(source)])
            cls.executables[variant] = binary

    def prepare_case(self, hits, calibration=None, offsets=None, **fixture_options):
        case = tempfile.TemporaryDirectory(prefix="case-", dir=self.work)
        self.addCleanup(case.cleanup)
        directory = Path(case.name)
        sort = directory / "sort"
        sort.mkdir()
        if hits is not None:
            write_channel_hits(directory / "raw2root/run0001.root", hits, **fixture_options)
        (sort / "LaBr3_cali.dat").write_text(calibration_text() if calibration is None else calibration)
        (sort / "LaBr3_ts_offset.dat").write_text(offsets_text() if offsets is None else offsets)
        return sort, sort / "run0001_sort.root"

    def execute(self, variant, directory, arguments=("1",)):
        return subprocess.run([str(self.executables[variant]), *arguments], cwd=directory,
                              capture_output=True, text=True, timeout=30)

    def assert_sorted(self, hits, expected, replace_existing=False, **options):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case(hits, **options)
                if replace_existing:
                    output.write_bytes(b"previous complete output")
                result = self.execute(variant, directory)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                title, types, actual = read_sorted_hits(output)
                self.assertEqual(title, variant)
                self.assertEqual(types, {"board": "Short_t", "channel": "Short_t",
                                         "energy": "Double_t", "timestamp": "Long64_t"})
                self.assertEqual(actual, expected)
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def assert_rejected(self, hits, diagnostic, missing_parameter=None,
                        preserve_existing=False, **options):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case(hits, **options)
                if missing_parameter:
                    (directory / missing_parameter).unlink()
                previous = b"previous complete output"
                if preserve_existing:
                    output.write_bytes(previous)
                result = self.execute(variant, directory)
                self.assertGreater(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(diagnostic.lower(), result.stderr.lower())
                if preserve_existing:
                    self.assertEqual(output.read_bytes(), previous)
                else:
                    self.assertFalse(output.exists())
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def test_preserves_all_scalar_fields(self):
        hits = [Hit(9, 7, 123, 400), Hit(2, 0, 65535, 200),
                Hit(3, 2, 100, 300), Hit(5, 2, 101, 100)]
        self.assert_sorted(hits, [hits[3], hits[1], hits[2], hits[0]])

    def test_full_range_quadratic_energy(self):
        hits = [Hit(0, 2, energy, index) for index, energy in enumerate((0, 1, 32768, 46341, 65535))]
        energies = (.5, 2.75, 268500992.5, 536964752.75, 1073840126.75)
        expected = [Hit(0, 2, energy, index) for index, energy in enumerate(energies)]
        self.assert_sorted(hits, expected, calibration=calibration_text({2: (.5, 2, .25)}))

    def test_large_timestamps_keep_picosecond_precision(self):
        base = 2**60
        hits = [Hit(0, 0, 10, base + 3), Hit(0, 1, 11, base + 1), Hit(0, 2, 12, base + 2)]
        self.assert_sorted(hits, [hits[1], hits[2], hits[0]])

    def test_signed_offsets_use_integer_addition(self):
        base = 2**60
        hits = [Hit(0, 0, 10, base), Hit(0, 1, 11, base + 1)]
        expected = [Hit(0, 1, 11, base - 2), Hit(0, 0, 10, base + 7)]
        self.assert_sorted(hits, expected, offsets=offsets_text({0: 7, 1: -3}))

    def test_empty_input(self):
        self.assert_sorted([], [])

    def test_equal_timestamps_have_stable_order(self):
        hits = [Hit(4, 3, 12, 8), Hit(2, 2, 21, 12), Hit(3, 2, 22, 12), Hit(1, 0, 1, 10)]
        expected = [Hit(1, 0, 1, 10), Hit(2, 2, 21, 10), Hit(3, 2, 22, 10), Hit(4, 3, 12, 10)]
        self.assert_sorted(hits, expected, offsets=offsets_text({2: -2, 3: 2}))

    def test_unordered_channel_input(self):
        hits = [Hit(1, 2, 10, 20), Hit(1, 2, 11, 10), Hit(2, 2, 12, 20), Hit(3, 2, 13, 0)]
        self.assert_sorted(hits, [hits[3], hits[1], hits[0], hits[2]])

    def test_fractional_offsets_round_once(self):
        hits = [Hit(0, channel, 100, 0) for channel in range(4)]
        expected = [Hit(0, 3, 100, -1), hits[0], hits[2], Hit(0, 1, 100, 1)]
        self.assert_sorted(hits, expected, offsets=offsets_text({0: .49, 1: .5, 2: -.49, 3: -.5}))

    def test_large_integer_offsets_are_exact(self):
        offset = 2**53 + 1
        hits = [Hit(0, 0, 100, 0), Hit(0, 1, 100, 0)]
        expected = [Hit(0, 1, 100, -offset), Hit(0, 0, 100, offset)]
        self.assert_sorted(hits, expected, offsets=offsets_text({0: offset, 1: -offset}))

    def test_signed_timestamp_boundaries(self):
        minimum, maximum = -(2**63), 2**63 - 1
        hits = [Hit(0, 0, 10, minimum), Hit(0, 1, 11, maximum),
                Hit(0, 2, 12, minimum + 1), Hit(0, 3, 13, maximum - 1)]
        expected = [hits[0], Hit(0, 2, 12, minimum), hits[1], Hit(0, 3, 13, maximum)]
        self.assert_sorted(hits, expected, offsets=offsets_text({2: -1, 3: 1}))

    def test_signed_offset_boundaries(self):
        minimum, maximum = -(2**63), 2**63 - 1
        hits = [Hit(0, 0, 10, 0), Hit(0, 1, 11, 0)]
        expected = [Hit(0, 0, 10, minimum), Hit(0, 1, 11, maximum)]
        self.assert_sorted(hits, expected, offsets=offsets_text({0: minimum, 1: maximum}))

    def test_timestamp_overflow_is_rejected(self):
        for timestamp, offset in ((2**63-1, 1), (-(2**63), -1),
                                  (1, 2**63-1), (-1, -(2**63))):
            with self.subTest(timestamp=timestamp, offset=offset):
                self.assert_rejected([Hit(0, 0, 100, timestamp)], "timestamp overflow",
                                     offsets=offsets_text({0: offset}))

    def test_invalid_offsets_are_rejected(self):
        for offset in (2**63, -(2**63)-1, "1e1000", "nan", "inf"):
            with self.subTest(offset=offset):
                self.assert_rejected([Hit(0, 0, 100, 0)], "offset",
                                     offsets=offsets_text({0: offset}))

    def test_parameters_without_final_newline(self):
        self.assert_sorted([Hit(2, 7, 100, 200)], [Hit(2, 7, 201, 207)],
                           calibration=calibration_text({7: (1, 2, 0)}).rstrip("\n"),
                           offsets=offsets_text({7: 7}).rstrip("\n"))

    def test_parameter_order_and_blank_lines(self):
        calibration = "\r\n\r\n".join(reversed(calibration_text({2: (1, 2, 0)}).splitlines()))
        offsets = "\r\n\r\n".join(reversed(offsets_text({2: -3}).splitlines()))
        self.assert_sorted([Hit(2, 2, 100, 200)], [Hit(2, 2, 201, 197)],
                           calibration="\n" + calibration, offsets="  \n" + offsets)

    def test_missing_parameter_files(self):
        for filename in ("LaBr3_cali.dat", "LaBr3_ts_offset.dat"):
            with self.subTest(filename=filename):
                self.assert_rejected([], "cannot open parameters", missing_parameter=filename)

    def test_missing_parameter_channels(self):
        for key, text in (("calibration", calibration_text()), ("offsets", offsets_text())):
            with self.subTest(parameter=key):
                self.assert_rejected([Hit(0, 0, 100, 0)], "missing parameter channel 7",
                                     **{key: "\n".join(text.splitlines()[:-1])})

    def test_duplicate_parameter_channels(self):
        for key, text in (("calibration", calibration_text()), ("offsets", offsets_text())):
            with self.subTest(parameter=key):
                self.assert_rejected([], "duplicate parameter channel",
                                     **{key: text + text.splitlines()[0]})

    def test_invalid_parameter_channels(self):
        for key, text in (("calibration", calibration_text()), ("offsets", offsets_text())):
            for channel in (-1, 8):
                with self.subTest(parameter=key, channel=channel):
                    self.assert_rejected([], "invalid parameter channel",
                                         **{key: str(channel) + text[1:]})

    def test_malformed_parameter_rows(self):
        cases = (("calibration", "0 0 1 0", "invalid calibration"),
                 ("calibration", "0 0 1 0 0 99", "extra parameter"),
                 ("calibration", "0 0 1 0 0 # comment", "extra parameter"),
                 ("offsets", "0", "invalid time offset"),
                 ("offsets", "0 0 99", "extra parameter"),
                 ("offsets", "x 0", "invalid parameter channel"))
        for key, first_line, diagnostic in cases:
            with self.subTest(parameter=key, row=first_line):
                text = calibration_text() if key == "calibration" else offsets_text()
                text = first_line + "\n" + "\n".join(text.splitlines()[1:])
                self.assert_rejected([], diagnostic, **{key: text})

    def test_nonfinite_calibration_is_rejected(self):
        for coefficients in (("nan", 1, 0), (0, "inf", 0), (0, 1, "1e309")):
            with self.subTest(coefficients=coefficients):
                self.assert_rejected([], "invalid calibration",
                                     calibration=calibration_text({0: coefficients}))

    def test_nonfinite_calibrated_energy_is_rejected(self):
        self.assert_rejected([Hit(0, 0, 65535, 0)], "non-finite calibrated energy",
                             calibration=calibration_text({0: (0, 1, "1e308")}))

    def test_missing_input(self):
        self.assert_rejected(None, "cannot open input")

    def test_missing_scalar_branch(self):
        self.assert_rejected([Hit(1, 2, 100, 0)], "invalid scalar branch board", omit="board")

    def test_wrong_scalar_type(self):
        self.assert_rejected([Hit(1, 2, 100, 0)], "invalid scalar branch energy_ch",
                             wrong_energy_type=True)

    def test_record_channel_must_match_tree(self):
        for channel in (-1, 3, 8):
            with self.subTest(channel=channel):
                self.assert_rejected([Hit(1, channel, 100, 0)], "channel mismatch", tree_channel=2)

    def test_waveforms_are_not_read(self):
        hits = [Hit(1, 2, 100, 0)]
        self.assert_sorted(hits, hits, waveform=True)

    def test_failure_preserves_existing_output(self):
        self.assert_rejected([Hit(1, 0, 100, 0), Hit(1, 0, 100, 2**63-1)],
                             "timestamp overflow", offsets=offsets_text({0: 1}),
                             preserve_existing=True)

    def test_success_replaces_existing_output(self):
        hits = [Hit(1, 2, 100, 0)]
        self.assert_sorted(hits, hits, replace_existing=True)

    def test_output_publish_failure(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case([Hit(1, 2, 100, 0)])
                output.mkdir()
                result = self.execute(variant, directory)
                self.assertGreater(result.returncode, 0)
                self.assertIn("cannot publish output", result.stderr)
                self.assertTrue(output.is_dir())
                self.assertEqual(list(directory.glob("*.partial.*")), [])

    def test_invalid_run_number(self):
        for variant in VARIANTS:
            for arguments in ((), ("abc",), ("-1",), ("2147483648",)):
                with self.subTest(variant=variant, arguments=arguments):
                    directory, output = self.prepare_case([])
                    result = self.execute(variant, directory, arguments)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn("usage:" if not arguments else "invalid run number", result.stderr)
                    self.assertFalse(output.exists())

    def test_sort_output_builds_expected_events(self):
        hits = [Hit(1, 2, 100, 0), Hit(2, 3, 200, 50000),
                Hit(3, 2, 101, 49999), Hit(4, 3, 201, 100000)]
        expected = [[hits[0], hits[2], Hit(2, 3, 200, 49999)], [Hit(4, 3, 201, 99999)]]
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case(hits, offsets=offsets_text({3: -1}))
                result = self.execute(variant, directory)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                build = directory.parent / "build"
                build.mkdir()
                binary = build / "build"
                checked_run(["make", "-B", "-C", str(REPO / variant / "build"), f"out={binary}"])
                checked_run([str(binary), "1"], cwd=build)
                title, _, events = read_events(build / "run0001_build.root")
                self.assertEqual(title, variant)
                self.assertEqual(events, expected)

    def test_unexpected_channel_and_wrong_stage_trees(self):
        self.assert_rejected([Hit(1, 0, 100, 0)], "unexpected channel tree", tree_channel=8)
        self.assert_rejected([Hit(1, 0, 100, 0)], "unexpected channel tree", tree_name="tr")
        self.assert_rejected([Hit(1, 0, 100, 0), Hit(1, 8, 100, 0)], "unexpected channel tree")

    def test_timestamp_array_is_rejected(self):
        self.assert_rejected([Hit(1, 0, 100, 0)], "invalid scalar branch timestamp", array_timestamp=True)

    def test_multileaf_scalar_branch_is_rejected(self):
        for hits in ([], [Hit(1, 0, 100, 0)]):
            with self.subTest(hits=hits):
                self.assert_rejected(hits, "invalid scalar branch timestamp",
                                     empty_channels=(0,), multileaf_timestamp=True)

    def test_corrupt_input_and_non_tree_objects(self):
        for variant in VARIANTS:
            for kind in ("corrupt", "tr_ch02", "metadata"):
                with self.subTest(variant=variant, kind=kind):
                    directory, output = self.prepare_case([])
                    source = directory.parent / "raw2root/run0001.root"
                    if kind == "corrupt":
                        source.write_bytes(b"not a ROOT file")
                        diagnostic = "cannot open input"
                    else:
                        root = ROOT.TFile(str(source), "RECREATE")
                        ROOT.TNamed(kind, "wrong input").Write()
                        root.Close()
                        diagnostic = "invalid input tree" if kind == "tr_ch02" else "missing channel trees"
                    result = self.execute(variant, directory)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn(diagnostic, result.stderr)
                    self.assertFalse(output.exists())

    @unittest.skipIf(os.geteuid() == 0, "requires normal filesystem permission checks")
    def test_unwritable_output_directory(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, output = self.prepare_case([Hit(1, 0, 100, 0)])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
