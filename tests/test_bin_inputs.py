"""Independent byte-level checks for manifest validation and BIN assembly; no ROOT."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import bin_inputs


def packet(timestamp, channel=2, board=0, header=1, energy=100, kev=661.657, wave=(-32768, 0, 32767)):
    body = struct.pack("<hhq", board, channel, timestamp)
    if header & 1:
        body += struct.pack("<H", energy)
    if header & 2:
        body += struct.pack("<d", kev)
    if header & 4:
        body += struct.pack("<H", 1234)
    body += struct.pack("<I", 0x12345678)
    if header & 8:
        body += struct.pack("<BI", 1, len(wave)) + struct.pack(f"<{len(wave)}h", *wave)
    return body


class BinInputTests(unittest.TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory(prefix="labr3-bin-manifest-")
        self.addCleanup(storage.cleanup)
        self.work = Path(storage.name)
        self.manifest = self.work / "inputs.json"
        self.output = self.work / "prepared"

    def stream(self, bodies, identifier="ch2", channels=(2,), mode="first", header=1, board=None):
        directory = self.work / identifier
        directory.mkdir(parents=True, exist_ok=True)
        files = []
        for part, body in enumerate(bodies):
            path = directory / f"分卷 {part}.BIN"
            path.write_bytes((struct.pack("<H", header) if mode == "each" or part == 0 else b"") + body)
            files.append({"part": part, "path": str(path.relative_to(self.work))})
        return {"id": identifier, "channels": list(channels), "board": board, "header_mode": mode,
                "part_count": len(files), "files": files}

    def write(self, streams, export="CUSTOM"):
        document = {"schema_version": 1, "variant": "DT5730_DPP_PSD", "run": 9,
                    "export": export, "streams": streams}
        self.manifest.write_text(json.dumps(document), encoding="utf-8")
        return document

    def assert_failed(self, pattern):
        with self.assertRaisesRegex((ValueError, OSError), pattern):
            bin_inputs.assemble(self.manifest, self.output)
        self.assertFalse((self.output / "assembled.BIN").exists())
        self.assertEqual(list(self.work.rglob(".assembled.partial.*")), [])

    def test_all_field_layouts_and_both_header_modes_preserve_exact_bytes(self):
        for header in range(16):
            for mode in ("first", "each"):
                with self.subTest(header=header, mode=mode):
                    bodies = [packet(2**53 + 100, header=header), packet(2**53 + 101, header=header, energy=101)]
                    stream = self.stream(bodies, f"h{header}-{mode}", header=header, mode=mode)
                    self.write([stream])
                    output = self.work / f"output-{header}-{mode}"
                    before = {entry["path"]: (self.work / entry["path"]).read_bytes() for entry in stream["files"]}
                    report = bin_inputs.assemble(self.manifest, output)
                    self.assertEqual((output / "assembled.BIN").read_bytes(), struct.pack("<H", header) + b"".join(bodies))
                    self.assertEqual(report["total_records"], 2)
                    self.assertEqual([entry["records"] for entry in report["files"]], [1, 1])
                    self.assertEqual([entry["record_start"] for entry in report["files"]], [0, 1])
                    self.assertEqual(before, {name: (self.work / name).read_bytes() for name in before})

    def test_numeric_parts_are_sorted_including_part_ten(self):
        bodies = [packet(i) for i in range(12)]
        stream = self.stream(bodies)
        stream["files"].reverse()
        self.write([stream])
        report = bin_inputs.assemble(self.manifest, self.output)
        self.assertEqual([entry["part"] for entry in report["files"]], list(range(12)))
        self.assertEqual((self.output / "assembled.BIN").read_bytes(), b"\x01\x00" + b"".join(bodies))

    def test_independent_channel_streams_need_not_be_globally_time_sorted(self):
        first = [packet(100, channel=2), packet(300, channel=2)]
        second = [packet(200, channel=3), packet(400, channel=3)]
        self.write([self.stream([b"".join(first)], "ch2", mode="each"),
                    self.stream([b"".join(second)], "ch3", (3,), mode="each")])
        report = bin_inputs.assemble(self.manifest, self.output)
        self.assertEqual(report["total_records"], 4)
        self.assertEqual(report["files"][1]["record_start"], 2)
        self.assertEqual((self.output / "assembled.BIN").read_bytes(), b"\x01\x00" + b"".join(first + second))

    def test_equal_timestamps_and_identical_boundary_records_are_preserved(self):
        a, b, c = packet(100, energy=1), packet(200, energy=2), packet(200, energy=3)
        self.write([self.stream([a + b, b + c])])
        report = bin_inputs.assemble(self.manifest, self.output)
        self.assertEqual(report["total_records"], 4)
        self.assertTrue(report["files"][1]["boundary_equal_record"])
        self.assertEqual((self.output / "assembled.BIN").read_bytes(), b"\x01\x00" + a + b + b + c)

    def test_duplicate_nonempty_payload_is_rejected_even_without_repeated_header(self):
        self.write([self.stream([packet(100), packet(100)])])
        self.assert_failed("Duplicate nonempty file payload")

    def test_duplicate_paths_and_hard_links_are_rejected(self):
        stream = self.stream([packet(100), packet(200)], mode="each")
        original = copy.deepcopy(stream)
        stream["files"][1]["path"] = stream["files"][0]["path"]
        self.write([stream])
        with self.assertRaisesRegex(ValueError, "Duplicate input path"):
            bin_inputs.load_manifest(self.manifest)
        second = self.work / original["files"][1]["path"]
        second.unlink()
        os.link(self.work / original["files"][0]["path"], second)
        self.write([original])
        self.assert_failed("hard link or alias")

    def test_missing_duplicate_and_out_of_range_parts_are_rejected(self):
        original = self.stream([packet(100), packet(200)])
        for change in ("missing", "duplicate", "range"):
            with self.subTest(change=change):
                stream = copy.deepcopy(original)
                if change == "missing":
                    stream["files"].pop()
                else:
                    stream["files"][1]["part"] = 0 if change == "duplicate" else 2
                self.write([stream])
                with self.assertRaises(ValueError):
                    bin_inputs.load_manifest(self.manifest)
        self.assertFalse(self.output.exists())

    def test_missing_input_file_is_rejected(self):
        stream = self.stream([packet(100), packet(200)])
        (self.work / stream["files"][1]["path"]).unlink()
        self.write([stream])
        self.assert_failed("No such file")

    def test_filename_run_part_and_unlisted_known_volume_are_checked(self):
        first = self.work / "Data_run_9.BIN"
        second = self.work / "Data_run_9_1.BIN"
        first.write_bytes(b"\x01\x00" + packet(100))
        second.write_bytes(packet(200))
        stream = {"id": "ch2", "channels": [2], "header_mode": "first", "part_count": 1,
                  "files": [{"part": 0, "path": first.name}]}
        self.write([stream])
        self.assert_failed("not listed")
        stream["files"][0]["path"] = second.name
        self.write([stream])
        self.assert_failed("Filename run/part")

    def test_export_categories_cannot_be_mixed_or_mislabeled(self):
        first = self.stream([packet(100)], "RAW", channels=(2,))
        second = self.stream([packet(200, channel=3)], "UNFILTERED", channels=(3,))
        self.write([first, second], export="RAW")
        self.assert_failed("export category")

    def test_header_mismatch_is_rejected(self):
        stream = self.stream([packet(100), packet(200)], mode="each")
        second = self.work / stream["files"][1]["path"]
        second.write_bytes(b"\x03\x00" + packet(200, header=3))
        self.write([stream])
        self.assert_failed("Header mismatch")

    def test_truncated_records_cannot_cross_a_volume_boundary(self):
        body = packet(100, header=0xCAED)
        self.write([self.stream([body[:-1], body[-1:]], header=0xCAED)])
        self.assert_failed("Truncated waveform data")

    def test_nonfinite_energy_and_excessive_waveform_are_rejected(self):
        self.write([self.stream([packet(100, header=2, kev=float("nan"))], header=2)])
        self.assert_failed("Non-finite")
        other = self.work / "other"
        self.output = other
        prefix = struct.pack("<hhqIBI", 0, 2, 100, 0, 1, 10001)
        self.write([self.stream([prefix], "oversized", header=8)])
        self.assert_failed("capacity")

    def test_channel_and_board_declarations_are_enforced(self):
        for index, stream in enumerate((self.stream([packet(100, channel=3)], "wrong-channel"),
                                        self.stream([packet(100, board=1)], "wrong-board", board=0))):
            with self.subTest(index=index):
                self.output = self.work / f"bad-output-{index}"
                self.write([stream])
                self.assert_failed("board/channel")

    def test_per_channel_timestamp_order_is_checked_within_and_across_parts(self):
        for index, bodies in enumerate(([packet(200) + packet(100)], [packet(200), packet(100)])):
            with self.subTest(index=index):
                self.output = self.work / f"out-{index}"
                self.write([self.stream(bodies, f"times-{index}")])
                self.assert_failed("Timestamp decreases")

    def test_empty_channel_files_are_counted_without_false_duplicate_hits(self):
        self.write([self.stream([b""], "ch2", mode="each"), self.stream([b""], "ch3", (3,), mode="each")])
        report = bin_inputs.assemble(self.manifest, self.output)
        self.assertEqual(report["total_records"], 0)
        self.assertIn("duplicate_empty_payload_of", report["files"][1])
        self.assertEqual((self.output / "assembled.BIN").read_bytes(), b"\x01\x00")

    def test_pinned_hash_and_existing_output_are_protected(self):
        stream = self.stream([packet(100)])
        stream["files"][0]["sha256"] = "0" * 64
        self.write([stream])
        self.assert_failed("SHA-256 differs")
        original = (self.output / "source_manifest.json").read_bytes()
        self.assert_failed("File exists")
        self.assertEqual((self.output / "source_manifest.json").read_bytes(), original)

    def test_manifest_schema_types_keys_and_overlapping_channels_are_rejected(self):
        document = self.write([self.stream([packet(100)])])
        cases = []
        for name, value in (("schema_version", True), ("run", -1), ("export", "UNKNOWN"), ("streams", [])):
            changed = copy.deepcopy(document)
            changed[name] = value
            cases.append(changed)
        changed = copy.deepcopy(document)
        changed["extra"] = 1
        cases.append(changed)
        for name, value in (("header_mode", "guess"), ("channels", [True]), ("channels", [2, 2]),
                            ("board", 32768), ("part_count", True)):
            changed = copy.deepcopy(document)
            changed["streams"][0][name] = value
            cases.append(changed)
        changed = copy.deepcopy(document)
        changed["streams"].append(self.stream([packet(200)], "other-ch2"))
        cases.append(changed)
        for value in cases:
            with self.subTest(value=value):
                self.manifest.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    bin_inputs.load_manifest(self.manifest)
        self.manifest.write_text('{"run": 9, "run": 10}')
        with self.assertRaisesRegex(ValueError, "Duplicate manifest key"):
            bin_inputs.load_manifest(self.manifest)

    def test_resolved_manifest_pins_every_part_and_allows_path_only_relocation(self):
        self.write([self.stream([packet(100), packet(200)])])
        report = bin_inputs.assemble(self.manifest, self.output)
        resolved = self.output / "resolved_manifest.json"
        replacement = json.loads(resolved.read_text())
        for index, entry in enumerate(replacement["streams"][0]["files"]):
            source = Path(entry["path"])
            destination = self.work / f"moved-{index}.BIN"
            shutil.move(source, destination)
            entry["path"] = str(destination)
            self.assertEqual(entry["sha256"], hashlib.sha256(destination.read_bytes()).hexdigest())
        relocated = self.work / "relocated.json"
        relocated.write_text(json.dumps(replacement))
        bin_inputs.verify_relocation(relocated, resolved)
        replacement["streams"][0]["channels"] = [3]
        relocated.write_text(json.dumps(replacement))
        with self.assertRaisesRegex(ValueError, "paths only"):
            bin_inputs.verify_relocation(relocated, resolved)
        self.assertEqual(report["total_records"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
