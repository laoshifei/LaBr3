"""Run the batch CLI against real ROOT programs and deterministic small inputs."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fixtures import (ROOT, Hit, RawRecord, REPO, VARIANTS, checked_run, copy_program, encode_bin_record,
                      read_events, read_raw_hits, read_sorted_hits, write_bin,
                      write_built, write_channel_hits, write_sorted)
from test_sort import calibration_text, offsets_text


STAGES = ("raw2root", "alignment", "sort", "build")
BASE = 2**53 + 123
HITS = [Hit(9, 2, 100, BASE), Hit(9, 3, 200, BASE + 100),
        Hit(11, 2, 300, BASE + 50000), Hit(11, 3, 400, BASE + 50100)]
EVENTS = [[Hit(9, 2, 201., BASE), Hit(9, 3, 599., BASE)],
          [Hit(11, 2, 601., BASE + 50000), Hit(11, 3, 1199., BASE + 50000)]]


class PipelineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage = tempfile.TemporaryDirectory(prefix="labr3-batch-integration-")
        cls.addClassCleanup(storage.cleanup)
        cls.work = Path(storage.name)
        source = cls.work / "source with spaces"
        for variant in VARIANTS:
            for stage in STAGES:
                copy_program(source, variant, stage)
        shutil.copyfile(REPO / "Makefile", source / "Makefile")
        checked_run(["make", "-j4", "-C", str(source)])
        cls.bin_dir = source / ".build"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="case-", dir=self.work)
        self.addCleanup(temporary.cleanup)
        self.case = Path(temporary.name)
        self.source = self.case / "输入 sample.BIN"
        write_bin(self.source, HITS)
        self.calibration = self.case / "calibration.dat"
        self.calibration.write_text(calibration_text({2: (1, 2, 0), 3: (-1, 3, 0)}))
        self.offsets = self.case / "offsets.dat"
        self.offsets.write_text(offsets_text({3: -100}))
        self.config = self.case / "analysis settings.json"
        self.settings = {"channel_a": 2, "channel_b": 3, "gates_a": [[200, 700]],
                         "gates_b": [[500, 700]], "time_bins": 20, "time_low": -1, "time_high": 1}
        self.config.write_text(json.dumps(self.settings))

    def execute(self, variant, source, output, *options, cwd=None, snapshot=False, input_manifest=False):
        cwd = cwd or self.case
        # Relative paths exercise resolution before the driver changes the child cwd.
        command = [sys.executable, "-B", str(REPO / "scripts/run_pipeline.py"),
                   "--variant", variant, "--run", "9", "--input-manifest" if input_manifest else "--input", os.path.relpath(source, cwd),
                   "--output-dir", os.path.relpath(output, cwd)]
        if not snapshot:
            command += ["--bin-dir", str(self.bin_dir)]
        command += [os.path.relpath(item, cwd) if isinstance(item, Path) else str(item) for item in options]
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=120,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def sorting(self):
        return ["--calibration", self.calibration, "--offsets", self.offsets]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_analysis(self, output):
        report = json.loads((output / "analysis/results.json").read_text())
        self.assertEqual((report["counts"]["events"], report["counts"]["hits"],
                          report["counts"]["channel_pairs"], report["counts"]["selected_pairs"]), (2, 4, 2, 1))
        self.assertEqual(report["counts"]["multiplicities"]["2"], 2)
        self.assertEqual(report["analysis_settings"]["gates_b"], [[500., 700.]])
        self.assertEqual(report["analysis_settings"]["board_a"], -1)
        self.assertEqual(report["fits"], {})
        root_file = ROOT.TFile.Open(str(output / "analysis/histograms.root"), "READ")
        try:
            self.assertEqual(root_file.Get("selected_times").GetEntries(), 1)
            self.assertEqual(root_file.Get("selected_times").GetMean(), 0.)
            self.assertEqual(root_file.Get("board9_ch2").GetEntries(), 1)
        finally:
            root_file.Close()

    def test_all_variants_through_alignment_and_analysis_with_relative_paths(self):
        originals = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (self.source, self.calibration, self.offsets, self.config)}
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                output = self.case / f"结果 {variant}"
                cwd = REPO if variant == VARIANTS[0] else self.case
                result = self.execute(variant, self.source, output,
                                      "--stages", "raw2root,alignment,sort,build,analysis",
                                      "--reference", "3", "--analysis-config", self.config,
                                      *self.sorting(), cwd=cwd, snapshot=True)
                self.assert_success(result)
                self.assertEqual(read_raw_hits(output / "raw.root"), sorted(HITS, key=lambda hit: hit.channel))
                self.assertEqual(read_sorted_hits(output / "sorted.root")[2], EVENTS[0] + EVENTS[1])
                self.assertEqual(read_events(output / "events.root")[2], EVENTS)
                aligned = ROOT.TFile.Open(str(output / "alignment.root"), "READ")
                try:
                    self.assertEqual(aligned.Get("alignment_reference_channel").GetVal(), 3)
                    self.assertEqual(aligned.Get("h_ch03_ch02").GetEntries(), 2)
                    self.assertAlmostEqual(aligned.Get("h_ch03_ch02").GetMean(), -24.9)
                finally:
                    aligned.Close()
                self.assert_analysis(output)
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(manifest["status"], "succeeded")
                self.assertTrue(manifest["input_unchanged"])
                self.assertTrue(manifest["counts_complete"])
                self.assertEqual(manifest["software"]["build_origin"], "snapshot")
                self.assertEqual(manifest["software"]["build"]["status"], "succeeded")
                self.assertEqual([stage["status"] for stage in manifest["stages"]], ["succeeded"] * 5)
                counters = {stage["name"]: stage["counts"] for stage in manifest["stages"]}
                self.assertEqual(counters["raw2root"], {"input_records": 4, "output_hits": 4})
                self.assertEqual(counters["alignment"], {"input_hits": 4, "matched_pairs": 2})
                self.assertEqual(counters["sort"], {"input_hits": 4, "output_hits": 4})
                self.assertEqual(counters["build"]["output_events"], 2)
                self.assertEqual(counters["build"]["events_m2"], 2)
                self.assertEqual(counters["analysis"]["selected_pairs"], 1)
                for stage in manifest["stages"]:
                    self.assertTrue(stage["command"][0].startswith(str(output / "source_snapshot"))
                                    if stage["name"] != "analysis" else
                                    stage["command"][2].startswith(str(output / "source_snapshot")))
                self.assertEqual(list(output.rglob("*.partial.*")), [])
        self.assertEqual(originals, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in originals})

    def test_start_from_each_root_stage(self):
        raw = self.case / "raw input.root"
        sorted_file = self.case / "sorted input.root"
        events = self.case / "events input.root"
        write_channel_hits(raw, HITS)
        write_sorted(sorted_file, EVENTS[0] + EVENTS[1])
        write_built(events, EVENTS)
        for kind, source, options in [("raw", raw, self.sorting()), ("sorted", sorted_file, []),
                                      ("events", events, ["--analysis-config", self.config])]:
            with self.subTest(kind=kind):
                output = self.case / kind
                self.assert_success(self.execute(VARIANTS[0], source, output, "--input-kind", kind, *options))
                if kind == "events":
                    self.assert_analysis(output)
                else:
                    self.assertEqual(read_events(output / "events.root")[2], EVENTS)

    def test_truncated_bin_stops_before_sort(self):
        write_bin(self.source, HITS, trailing=b"\x01")
        output = self.case / "truncated"
        result = self.execute(VARIANTS[0], self.source, output, *self.sorting())
        self.assertNotEqual(result.returncode, 0)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual([stage["status"] for stage in manifest["stages"]], ["failed", "not_run", "not_run"])
        self.assertFalse((output / "raw.root").exists())
        self.assertEqual(list(output.rglob("*.partial.*")), [])

    def test_bad_offsets_stop_after_conversion(self):
        self.offsets.write_text("invalid offset\n")
        output = self.case / "bad-offsets"
        result = self.execute(VARIANTS[0], self.source, output, *self.sorting())
        self.assertNotEqual(result.returncode, 0)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual([stage["status"] for stage in manifest["stages"]], ["succeeded", "failed", "not_run"])
        self.assertEqual(read_raw_hits(output / "raw.root"), sorted(HITS, key=lambda hit: hit.channel))
        self.assertFalse((output / "events.root").exists())

    def test_analysis_rejects_wrong_root_stage(self):
        source = self.case / "sorted.root"
        write_sorted(source, EVENTS[0] + EVENTS[1])
        output = self.case / "wrong-stage"
        result = self.execute(VARIANTS[0], source, output, "--input-kind", "events", "--analysis-config", self.config)
        self.assertNotEqual(result.returncode, 0)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["stages"][0]["status"], "failed")
        self.assertFalse((output / "analysis/results.json").exists())

    def test_snapshot_replay_rebuilds_events_and_reproduces_analysis(self):
        source = self.case / "sorted.root"
        write_sorted(source, EVENTS[0] + EVENTS[1])
        output = self.case / "original run"
        self.assert_success(self.execute(VARIANTS[0], source, output, "--input-kind", "sorted",
                                         "--stages", "build,analysis", "--analysis-config", self.config,
                                         snapshot=True))
        self.config.write_text('{"channel_a": 0, "channel_b": 1}')
        replayed = self.case / "replayed run"
        command = [sys.executable, "-B", str(REPO / "scripts/replay_pipeline.py"),
                   str(output / "manifest.json"), "--output-dir", str(replayed)]
        result = subprocess.run(command, cwd=self.case, text=True, capture_output=True, timeout=120,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assert_success(result)
        self.assertEqual(read_events(replayed / "events.root")[2], EVENTS)
        self.assert_analysis(replayed)
        before = json.loads((output / "manifest.json").read_text())
        after = json.loads((replayed / "manifest.json").read_text())
        self.assertEqual([stage["counts"] for stage in before["stages"]],
                         [stage["counts"] for stage in after["stages"]])
        self.assertEqual(after["replayed_from"]["sha256"], hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest())
        for name, identity in before["source_snapshot"].items():
            self.assertEqual(identity["sha256"], after["source_snapshot"][name]["sha256"])

    def test_empty_pipeline_records_zero_counts(self):
        write_bin(self.source, [])
        output = self.case / "empty"
        self.assert_success(self.execute(VARIANTS[0], self.source, output, *self.sorting()))
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertTrue(manifest["counts_complete"])
        for stage in manifest["stages"]:
            self.assertTrue(all(value == 0 for value in stage["counts"].values()))
        self.assertEqual(read_events(output / "events.root")[2], [])

    def split_manifest(self, variant, truncated=False):
        folder = self.case / "UNFILTERED"
        folder.mkdir(exist_ok=True)
        first, second = folder / "Data_run_9.BIN", folder / "Data_run_9_1.BIN"
        write_bin(first, HITS[:2])
        temporary = self.case / "last.BIN"
        write_bin(temporary, HITS[2:])
        payload = temporary.read_bytes()[2:]
        second.write_bytes(payload[:-1] if truncated else payload)
        document = {"schema_version": 1, "variant": variant, "run": 9, "export": "UNFILTERED",
                    "streams": [{"id": "hits", "channels": [2, 3], "header_mode": "first", "part_count": 2,
                                 "files": [{"part": 1, "path": str(second.relative_to(self.case))},
                                           {"part": 0, "path": str(first.relative_to(self.case))}]}]}
        manifest = self.case / "split inputs.json"
        manifest.write_text(json.dumps(document))
        return manifest

    def test_split_manifest_all_variants_conserve_records_through_analysis(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                manifest = self.split_manifest(variant)
                output = self.case / f"split-{variant}"
                result = self.execute(variant, manifest, output, "--stages", "raw2root,alignment,sort,build,analysis",
                                      "--reference", "3", "--analysis-config", self.config,
                                      *self.sorting(), cwd=REPO, input_manifest=True)
                self.assert_success(result)
                self.assertEqual(read_events(output / "events.root")[2], EVENTS)
                self.assert_analysis(output)
                saved = json.loads((output / "manifest.json").read_text())
                self.assertEqual(saved["schema_version"], 3)
                self.assertEqual(saved["input_preparation"]["status"], "succeeded")
                self.assertEqual(saved["scope"]["input_files"], 2)
                self.assertIsNone(saved["scope"]["acquisition_complete"])
                self.assertTrue(saved["scope"]["declared_parts_complete"])
                self.assertEqual(saved["input_set"]["total_records"], 4)
                self.assertEqual([entry["records"] for entry in saved["input_set"]["files"]], [2, 2])
                self.assertEqual([entry["record_start"] for entry in saved["input_set"]["files"]], [0, 2])
                self.assertEqual(saved["stages"][0]["counts"]["output_hits"], 4)
                self.assertEqual((output / "inputs/assembled.BIN").read_bytes(), self.source.read_bytes())

    def test_independent_headed_channel_files_replay_after_input_relocation(self):
        streams = []
        for channel in (2, 3):
            path = self.case / f"channel-{channel}.BIN"
            write_bin(path, [hit for hit in HITS if hit.channel == channel])
            streams.append({"id": f"ch{channel}", "channels": [channel], "header_mode": "each", "part_count": 1,
                            "files": [{"part": 0, "path": path.name}]})
        manifest = self.case / "channels.json"
        manifest.write_text(json.dumps({"schema_version": 1, "variant": VARIANTS[0], "run": 9,
                                        "export": "CUSTOM", "streams": streams}))
        output = self.case / "channel run"
        self.assert_success(self.execute(VARIANTS[0], manifest, output, "--stages", "raw2root,sort,build,analysis",
                                         "--analysis-config", self.config, *self.sorting(), input_manifest=True, snapshot=True))
        self.assertEqual(read_events(output / "events.root")[2], EVENTS)
        self.assert_analysis(output)
        original = json.loads((output / "manifest.json").read_text())
        replacement = json.loads((output / "inputs/resolved_manifest.json").read_text())
        moved = self.case / "moved inputs"
        moved.mkdir()
        for stream in replacement["streams"]:
            source = Path(stream["files"][0]["path"])
            destination = moved / source.name
            source.rename(destination)
            stream["files"][0]["path"] = str(destination)
        manifest.unlink()
        relocated = self.case / "relocated.json"
        relocated.write_text(json.dumps(replacement))
        replayed = self.case / "channel replay"
        command = [sys.executable, "-B", str(REPO / "scripts/replay_pipeline.py"), str(output / "manifest.json"),
                   "--input-manifest", str(relocated), "--output-dir", str(replayed)]
        result = subprocess.run(command, cwd=self.case, text=True, capture_output=True, timeout=120,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assert_success(result)
        self.assertEqual(read_events(replayed / "events.root")[2], EVENTS)
        self.assert_analysis(replayed)
        saved = json.loads((replayed / "manifest.json").read_text())
        self.assertEqual(saved["input_set"]["normalized"]["sha256"], original["input_set"]["normalized"]["sha256"])
        self.assertEqual([entry["sha256"] for entry in saved["input_set"]["files"]],
                         [entry["sha256"] for entry in original["input_set"]["files"]])

    def test_invalid_split_record_stops_before_compilation(self):
        manifest = self.split_manifest(VARIANTS[0], truncated=True)
        output = self.case / "invalid split"
        result = self.execute(VARIANTS[0], manifest, output, "--stages", "raw2root", input_manifest=True, snapshot=True)
        self.assertNotEqual(result.returncode, 0)
        saved = json.loads((output / "manifest.json").read_text())
        self.assertEqual(saved["input_preparation"]["status"], "failed")
        self.assertNotIn("build", saved["software"])
        self.assertEqual(saved["stages"][0]["status"], "not_run")
        self.assertFalse((output / "raw.root").exists())
        self.assertFalse((output / "inputs/assembled.BIN").exists())
        self.assertEqual(list(output.rglob(".assembled.partial.*")), [])

    def test_manifest_label_and_stage_constraints_fail_without_results(self):
        manifest = self.split_manifest(VARIANTS[0])
        for index, (variant, options) in enumerate(((VARIANTS[1], ["--stages", "raw2root"]),
                                                   (VARIANTS[0], ["--input-kind", "sorted", "--stages", "build"]))):
            with self.subTest(index=index):
                output = self.case / f"invalid-manifest-{index}"
                result = self.execute(variant, manifest, output, *options, input_manifest=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_decoder_count_must_match_validated_manifest(self):
        manifest = self.split_manifest(VARIANTS[0])
        tool = self.case / "fake" / VARIANTS[0] / "raw2root"
        tool.parent.mkdir(parents=True)
        tool.write_text(f"#!{sys.executable}\nimport pathlib, sys\n"
                        "pathlib.Path(sys.argv[sys.argv.index('--output') + 1]).write_bytes(b'wrong output')\n"
                        "print('LABR3_STATS {\"version\":1,\"stage\":\"raw2root\",\"counts\":{\"input_records\":3,\"output_hits\":3}}')\n")
        tool.chmod(0o755)
        output = self.case / "mismatched decoder"
        result = self.execute(VARIANTS[0], manifest, output, "--stages", "raw2root",
                              "--bin-dir", self.case / "fake", input_manifest=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Decoder record count differs", result.stderr)
        saved = json.loads((output / "manifest.json").read_text())
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["input_set"]["total_records"], 4)

    def test_multi_file_conversion_preserves_optional_fields_waveforms_and_record_numbers(self):
        records = [RawRecord(timestamp=100), RawRecord(timestamp=200, energy_keV=123.25, waveform=(10, -20))]
        files = []
        for part, record in enumerate(records):
            path = self.case / f"optional-{part}.BIN"
            path.write_bytes(b"\x0f\x00" + encode_bin_record(record, 15))
            files.append({"part": part, "path": path.name})
        manifest = self.case / "optional.json"
        manifest.write_text(json.dumps({"schema_version": 1, "variant": VARIANTS[0], "run": 9, "export": "CUSTOM",
                                        "streams": [{"id": "ch2", "channels": [2], "header_mode": "each",
                                                     "part_count": 2, "files": files}]}))
        output = self.case / "optional output"
        self.assert_success(self.execute(VARIANTS[0], manifest, output, "--stages", "raw2root", input_manifest=True))
        root_file = ROOT.TFile.Open(str(output / "raw.root"), "READ")
        try:
            tree = root_file.Get("tr_ch02")
            self.assertEqual(tree.GetLeaf("energy_keV").GetTypeName(), "Double_t")
            self.assertEqual(tree.GetEntries(), 2)
            for index, row in enumerate(tree):
                expected = records[index]
                self.assertEqual((int(row.n), int(row.board), int(row.channel), int(row.timestamp)),
                                 (index, expected.board, expected.channel, expected.timestamp))
                self.assertEqual((int(row.energy_ch), int(row.energy_short)), (expected.energy_ch, expected.energy_short))
                self.assertEqual(float(row.energy_keV), expected.energy_keV)
                self.assertEqual(list(row.data), list(expected.waveform))
                self.assertEqual(list(row.dt), list(range(len(expected.waveform))))
        finally:
            root_file.Close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
