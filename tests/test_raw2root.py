"""Check decoder fields, optional payloads, corruption, and output publication."""

from dataclasses import replace
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

from fixtures import (ROOT, Hit, RawRecord, REPO, VARIANTS, checked_run,
                      encode_bin_record, read_raw_hits, write_bin)


class RawConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="labr3-raw-tests-")
        cls.addClassCleanup(cls.storage.cleanup)
        cls.work = Path(cls.storage.name)
        cls.executables = {}
        cls.input_path = cls.work / "input/run_1/UNFILTERED/Data_run_1.BIN"
        cls.input_path.parent.mkdir(parents=True)
        for variant in VARIANTS:
            source = cls.work / variant
            source.mkdir()
            for name in ("main.cpp", "Makefile"):
                shutil.copyfile(REPO / variant / "raw2root" / name, source / name)
            config = (REPO / variant / "raw2root/set.h").read_text()
            config = re.sub(r'^#define FILE_PATH "[^"]+"',
                            f'#define FILE_PATH "{cls.work / "input"}"', config, flags=re.M)
            config = re.sub(r'^#define FILE_HEAD "[^"]+"',
                            '#define FILE_HEAD "UNFILTERED/Data_run_"', config, flags=re.M)
            (source / "set.h").write_text(config)
            binary = source / "raw2root"
            checked_run(["make", "-B", "-C", str(source)])
            cls.executables[variant] = binary

    def run_conversion(self, variant, directory, arguments=("1",)):
        return subprocess.run([str(self.executables[variant]), *arguments],
                              cwd=directory, capture_output=True, text=True, timeout=30)

    def check_records(self, hits, replace_existing=False):
        write_bin(self.input_path, hits)
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory(prefix="output-", dir=self.work) as directory:
                    output = Path(directory) / "run0001.root"
                    if replace_existing:
                        output.write_bytes(b"previous output")
                    result = self.run_conversion(variant, directory)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(read_raw_hits(output), sorted(hits, key=lambda h: h.channel))
                    self.assertEqual(list(Path(directory).glob("*.partial.*")), [])

    def check_failure(self, payload, diagnostic, preserve_existing=False):
        self.input_path.write_bytes(payload)
        self.check_input_failure(diagnostic, preserve_existing)

    def check_input_failure(self, diagnostic, preserve_existing=False):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory(prefix="output-", dir=self.work) as directory:
                    output = Path(directory) / "run0001.root"
                    previous = b"previous complete output"
                    if preserve_existing:
                        output.write_bytes(previous)
                    result = self.run_conversion(variant, directory)
                    self.assertGreater(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(diagnostic, result.stderr.lower())
                    if preserve_existing:
                        self.assertEqual(output.read_bytes(), previous)
                    else:
                        self.assertFalse(output.exists(), "Failure published a final ROOT file")
                    self.assertEqual(list(Path(directory).glob("*.partial.*")), [])

    def check_optional_records(self, header, records):
        self.input_path.write_bytes(struct.pack("<H", header) +
                                    b"".join(encode_bin_record(r, header) for r in records))
        expected_types = {"board": "Short_t", "channel": "Short_t",
                          "timestamp": "Long64_t", "n": "Long64_t"}
        for flag, fields in ((1, {"energy_ch": "UShort_t"}),
                             (2, {"energy_keV": "Double_t"}),
                             (4, {"energy_short": "UShort_t"}),
                             (8, {"size": "UInt_t", "data": "Short_t", "dt": "Short_t"})):
            if header & flag:
                expected_types.update(fields)
        for variant in VARIANTS:
            with self.subTest(variant=variant, header=hex(header)):
                with tempfile.TemporaryDirectory(prefix="output-", dir=self.work) as directory:
                    result = self.run_conversion(variant, directory)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    source = ROOT.TFile.Open(str(Path(directory) / "run0001.root"), "READ")
                    self.assertTrue(source and not source.IsZombie())
                    try:
                        tree = source.Get("tr_ch02")
                        self.assertTrue(tree)
                        self.assertEqual(tree.GetTitle(), variant)
                        actual_types = {branch.GetName(): branch.GetLeaf(branch.GetName()).GetTypeName()
                                        for branch in tree.GetListOfBranches()}
                        self.assertEqual(actual_types, expected_types)
                        self.assertEqual(tree.GetEntries(), len(records))
                        for index, record in enumerate(records):
                            self.assertGreater(tree.GetEntry(index), 0)
                            self.assertEqual(int(tree.board), record.board)
                            self.assertEqual(int(tree.channel), record.channel)
                            self.assertEqual(int(tree.timestamp), record.timestamp)
                            self.assertEqual(int(tree.n), index)
                            if header & 1:
                                self.assertEqual(int(tree.energy_ch), record.energy_ch)
                            if header & 2:
                                self.assertEqual(float(tree.energy_keV), record.energy_keV)
                            if header & 4:
                                self.assertEqual(int(tree.energy_short), record.energy_short)
                            if header & 8:
                                self.assertEqual(int(tree.size), len(record.waveform))
                                self.assertEqual(list(tree.data), list(record.waveform))
                                self.assertEqual(list(tree.dt), list(range(len(record.waveform))))
                    finally:
                        source.Close()

    def test_no_records(self):
        self.check_records([])

    def test_single_record(self):
        self.check_records([Hit(1, 2, 1234, 123456)])

    def test_multiple_records(self):
        self.check_records([Hit(1, 7, 65535, 223456), Hit(2, 0, 1234, 123456)])

    def test_truncated_header(self):
        for payload in (b"", b"\x01"):
            with self.subTest(size=len(payload)):
                self.check_failure(payload, "truncated header")

    def test_truncated_last_record(self):
        record = encode_bin_record(RawRecord(), 1)
        # Partial board/channel/timestamp/energy/flags, after a complete record.
        for size, field in ((1, "board"), (3, "channel"), (7, "timestamp"),
                            (13, "energy_ch"), (17, "flags")):
            with self.subTest(field=field):
                self.check_failure(b"\x01\x00" + record + record[:size],
                                   f"truncated record 1, {field}")

    def test_invalid_channels(self):
        for channel in (-1, 8, 16, 32767):
            with self.subTest(channel=channel):
                record = encode_bin_record(replace(RawRecord(), channel=channel), 1)
                self.check_failure(b"\x01\x00" + record, "invalid channel")

    def test_optional_fields_and_types(self):
        # Include no energy, keV alone, short energy, waveform alone, and all fields.
        for header in (0, 2, 5, 8, 15):
            self.check_optional_records(header, [RawRecord(),
                replace(RawRecord(), board=3, energy_ch=0, energy_keV=-0.125,
                        energy_short=65535, timestamp=123456789012345679)])

    def test_high_header_bits_preserve_field_flags(self):
        # Existing local files carry the 0xCAE0 marker in their upper bits.
        self.check_optional_records(0xCAED, [RawRecord()])

    def test_waveform_capacity_and_changing_lengths(self):
        waveform = (-32768, 32767) + tuple(range(9998))
        self.check_optional_records(15, [replace(RawRecord(), waveform=(42,)),
                                        replace(RawRecord(), waveform=waveform),
                                        replace(RawRecord(), waveform=())])

    def test_oversized_waveform_rejected_before_payload_read(self):
        prefix = encode_bin_record(replace(RawRecord(), waveform=()), 8)
        for size in (10001, 0xFFFFFFFF):
            with self.subTest(size=size):
                record = prefix[:-4] + struct.pack("<I", size)
                self.check_failure(b"\x08\x00" + record, "exceeds capacity")

    def test_truncated_optional_fields(self):
        record = encode_bin_record(RawRecord(), 15)
        for size, field in ((17, "energy_kev"), (23, "energy_short"), (28, "waveform_code"),
                            (30, "waveform size"), (35, "waveform data")):
            with self.subTest(field=field):
                self.check_failure(b"\x0f\x00" + record[:size],
                                   f"truncated record 0, {field}")

    def test_nonfinite_energy_keV(self):
        for energy in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(energy=energy):
                record = encode_bin_record(replace(RawRecord(), energy_keV=energy), 2)
                self.check_failure(b"\x02\x00" + record, "non-finite energy_kev")

    def test_missing_input(self):
        if self.input_path.exists():
            self.input_path.unlink()
        self.check_input_failure("cannot open input")

    def test_read_error_is_distinct_from_eof(self):
        # On Linux fopen accepts directories, but fread fails with EISDIR.
        if self.input_path.exists():
            self.input_path.unlink()
        self.input_path.mkdir()
        try:
            self.check_input_failure("read error at header")
        finally:
            self.input_path.rmdir()

    def test_failure_preserves_existing_output(self):
        record = encode_bin_record(RawRecord(), 1)
        self.check_failure(b"\x01\x00" + record + record[:-1],
                           "truncated record 1, flags", preserve_existing=True)

    def test_success_replaces_existing_output(self):
        self.check_records([Hit(1, 2, 1234, 123456)], replace_existing=True)

    def test_output_publish_failure(self):
        write_bin(self.input_path, [Hit(1, 2, 1234, 123456)])
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory(prefix="output-", dir=self.work) as directory:
                    output = Path(directory) / "run0001.root"
                    output.mkdir()
                    result = self.run_conversion(variant, directory)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn("cannot publish output", result.stderr)
                    self.assertTrue(output.is_dir())
                    self.assertEqual(list(Path(directory).glob("*.partial.*")), [])

    def test_invalid_run_number(self):
        for variant in VARIANTS:
            for arguments in ((), ("abc",), ("-1",), ("2147483648",), ("1junk",)):
                with self.subTest(variant=variant, arguments=arguments):
                    with tempfile.TemporaryDirectory(prefix="output-", dir=self.work) as directory:
                        result = self.run_conversion(variant, directory, arguments)
                        self.assertGreater(result.returncode, 0)
                        self.assertIn("raw2root:", result.stderr)
                        self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
