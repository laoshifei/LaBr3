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

from fixtures import (ROOT, Hit, REPO, VARIANTS, checked_run, copy_program,
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

    def execute(self, variant, source, output, *options, cwd=None):
        cwd = cwd or self.case
        # Relative paths exercise resolution before the driver changes the child cwd.
        command = [sys.executable, "-B", str(REPO / "scripts/run_pipeline.py"),
                   "--variant", variant, "--run", "9", "--input", os.path.relpath(source, cwd),
                   "--output-dir", os.path.relpath(output, cwd), "--bin-dir", str(self.bin_dir)]
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
                                      *self.sorting(), cwd=cwd)
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
                self.assertEqual([stage["status"] for stage in manifest["stages"]], ["succeeded"] * 5)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
