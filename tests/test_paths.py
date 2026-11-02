"""Exercise runtime paths through the real programs and the notebook snapshot."""

from pathlib import Path
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest

from fixtures import (ROOT, Hit, REPO, VARIANTS, checked_run, copy_program,
                      read_events, read_raw_hits, read_sorted_hits,
                      write_bin, write_channel_hits, write_sorted)
from test_sort import calibration_text, offsets_text

STAGES = ("raw2root", "alignment", "sort", "build")
FILENAMES = {"raw2root": "run0001.root", "alignment": "run0001_aligiment.root",
             "sort": "run0001_sort.root", "build": "run0001_build.root"}
HITS = [Hit(9, 2, 100, 0), Hit(9, 3, 200, 100)]
CALIBRATION = {2: (1, 2, 0), 3: (-1, 3, 0)}
OFFSETS = {3: -100}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RuntimePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="labr3-path-tests-")
        cls.addClassCleanup(cls.storage.cleanup)
        cls.work = Path(cls.storage.name)
        cls.legacy_raw = cls.work / "legacy input/run_1/raw_1.BIN"
        cls.executables = {}
        for variant in VARIANTS:
            for stage in STAGES:
                source = copy_program(cls.work, variant, stage)
                if stage == "raw2root":
                    config = (source / "set.h").read_text()
                    config = re.sub(r'^#define FILE_PATH "[^"]+"',
                                    f'#define FILE_PATH "{cls.work / "legacy input"}"', config, flags=re.M)
                    config = re.sub(r'^#define FILE_HEAD "[^"]+"',
                                    '#define FILE_HEAD "raw_"', config, flags=re.M)
                    (source / "set.h").write_text(config)
                checked_run(["make", "-B", "-C", str(source)])
                cls.executables[variant, stage] = source / stage

    def directory(self):
        storage = tempfile.TemporaryDirectory(prefix="case-", dir=self.work)
        self.addCleanup(storage.cleanup)
        return Path(storage.name)

    def prepare_case(self, stage, legacy_input=False):
        root = self.directory()
        cwd = root / stage
        cwd.mkdir()
        if legacy_input:
            source = (self.legacy_raw if stage == "raw2root" else
                      root / ("sort/run0001_sort.root" if stage == "build" else "raw2root/run0001.root"))
        else:
            source = root / "输入 files" / ("hits.BIN" if stage == "raw2root" else "hits.root")
        if stage == "raw2root":
            write_bin(source, HITS)
        elif stage == "build":
            write_sorted(source, HITS)
        else:
            write_channel_hits(source, HITS)
        parameters = root / "参数 files"
        parameters.mkdir()
        calibration = parameters / "energy.dat"
        offsets = parameters / "offsets.dat"
        calibration.write_text(calibration_text(CALIBRATION))
        offsets.write_text(offsets_text(OFFSETS))
        (cwd / "LaBr3_cali.dat").write_text(calibration_text())
        (cwd / "LaBr3_ts_offset.dat").write_text(offsets_text())
        output = root / "输出 files/result.root"
        output.parent.mkdir()
        return cwd, source, output, calibration, offsets

    @staticmethod
    def stage_options(stage, calibration, offsets):
        if stage == "sort":
            return ["--calibration", str(calibration), "--offsets", str(offsets)]
        if stage == "alignment":
            return ["--reference", "3"]
        return []

    def execute(self, variant, stage, cwd, arguments):
        return subprocess.run([str(self.executables[variant, stage]), *map(str, arguments)],
                              cwd=cwd, text=True, capture_output=True, timeout=30)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_stage_output(self, stage, path):
        if stage == "raw2root":
            self.assertEqual(read_raw_hits(path), HITS)
        elif stage == "sort":
            self.assertEqual(read_sorted_hits(path)[2], [Hit(9, 2, 201., 0), Hit(9, 3, 599., 0)])
        elif stage == "build":
            self.assertEqual(read_events(path)[2], [HITS])
        else:
            source = ROOT.TFile.Open(str(path), "READ")
            try:
                self.assertEqual(source.Get("alignment_reference_channel").GetVal(), 3)
                histogram = source.Get("h_ch03_ch02")
                self.assertEqual(histogram.GetEntries(), 1)
                self.assertAlmostEqual(histogram.GetMean(), .1)
            finally:
                source.Close()

    def test_help_does_not_require_files(self):
        cwd = self.directory()
        for variant in VARIANTS:
            for stage in STAGES:
                for option in ("--help", "-h"):
                    with self.subTest(variant=variant, stage=stage, option=option):
                        result = self.execute(variant, stage, cwd, [option])
                        self.assert_success(result)
                        self.assertIn("usage:", result.stdout)
                        self.assertIn("--input", result.stdout)
                        self.assertIn("--output", result.stdout)
        self.assertEqual(list(cwd.iterdir()), [])

    def test_invalid_path_arguments_do_not_publish(self):
        for variant in VARIANTS:
            for stage in STAGES:
                cwd, source, output, _, _ = self.prepare_case(stage)
                output.write_bytes(b"previous output")
                cases = [(["--input", source], "usage:"), (["--output", output], "usage:"),
                         (["--input"], "missing value"), (["--output"], "missing value"),
                         (["--input", "", "--output", output], "missing value"),
                         (["--input", source, "--output", ""], "missing value"),
                         (["--input", source, "--output", output, "--input", source], "duplicate"),
                         (["--input", source, "--output", output, "--output", output], "duplicate"),
                         (["--input", "--output", output], "missing value"),
                         (["--input", source, "--output", output, "--unknown"], "unknown option"),
                         (["1", "2", "--input", source, "--output", output], "multiple run numbers")]
                for arguments, diagnostic in cases:
                    with self.subTest(variant=variant, stage=stage, arguments=arguments):
                        result = self.execute(variant, stage, cwd, arguments)
                        self.assertGreater(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn(diagnostic, result.stderr)
                        self.assertEqual(output.read_bytes(), b"previous output")
                self.assertEqual(list(cwd.parent.rglob("*.partial.*")), [])

    def test_unsupported_stage_options(self):
        unsupported = {"raw2root": "--reference", "build": "--calibration",
                       "alignment": "--offsets", "sort": "--reference"}
        for variant in VARIANTS:
            for stage in STAGES:
                with self.subTest(variant=variant, stage=stage):
                    cwd, source, output, _, _ = self.prepare_case(stage)
                    result = self.execute(variant, stage, cwd,
                                          ["--input", source, "--output", output, unsupported[stage], "0"])
                    self.assertGreater(result.returncode, 0)
                    self.assertIn("unknown option", result.stderr)
                    self.assertFalse(output.exists())

    def check_pipeline(self, variant, cwd, relative=False, run=None):
        root = self.directory()
        source = root / "输入 data.BIN"
        calibration = root / "能量 calibration.dat"
        offsets = root / "时间 offsets.dat"
        base = 2**53 + 123
        hits = [Hit(9, 2, 100, base), Hit(9, 3, 200, base + 100),
                Hit(11, 2, 300, base + 50000), Hit(11, 3, 400, base + 50100)]
        expected = [[Hit(9, 2, 201., base), Hit(9, 3, 599., base)],
                    [Hit(11, 2, 601., base + 50000), Hit(11, 3, 1199., base + 50000)]]
        write_bin(source, hits)
        calibration.write_text(calibration_text(CALIBRATION))
        offsets.write_text(offsets_text(OFFSETS))
        identities = {path: digest(path) for path in (source, calibration, offsets)}
        outputs = {stage: root / f"输出 {stage}.root" for stage in STAGES}
        inputs = {"raw2root": source, "alignment": outputs["raw2root"],
                  "sort": outputs["raw2root"], "build": outputs["sort"]}
        def path_argument(path):
            return os.path.relpath(path, cwd) if relative else str(path)
        for stage in STAGES:
            arguments = ["--output", path_argument(outputs[stage])]
            if run is not None:
                arguments.append(str(run))
            arguments += ["--input", path_argument(inputs[stage])]
            if stage == "sort":
                arguments += ["--calibration", path_argument(calibration), "--offsets", path_argument(offsets)]
            elif stage == "alignment":
                arguments += ["--reference", "3"]
            self.assert_success(self.execute(variant, stage, cwd, arguments))
        self.assertEqual(read_raw_hits(outputs["raw2root"]), sorted(hits, key=lambda hit: hit.channel))
        self.assertEqual(read_sorted_hits(outputs["sort"])[2], expected[0] + expected[1])
        self.assertEqual(read_events(outputs["build"])[2], expected)
        aligned = ROOT.TFile.Open(str(outputs["alignment"]), "READ")
        try:
            self.assertEqual(aligned.Get("alignment_reference_channel").GetVal(), 3)
            histogram = aligned.Get("h_ch03_ch02")
            self.assertEqual(histogram.GetEntries(), 2)
            self.assertAlmostEqual(histogram.GetMean(), -24.9)
        finally:
            aligned.Close()
        sys.path.insert(0, str(REPO))
        from analysis import notebook_analysis as analysis
        result = analysis.analyze_events(outputs["build"], {})
        self.assertEqual((result.events, result.hits, result.selected_pairs), (2, 4, 2))
        self.assertEqual(result.selected_times.GetMean(), 0.)
        for path, identity in identities.items():
            self.assertEqual(digest(path), identity)
        self.assertEqual(list(root.glob("*.partial.*")), [])

    def test_full_pipeline_from_project_and_external_directories(self):
        for variant in VARIANTS:
            for cwd in (REPO, self.directory()):
                with self.subTest(variant=variant, cwd=cwd):
                    self.check_pipeline(variant, cwd)

    def test_full_pipeline_with_relative_paths(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                self.check_pipeline(variant, self.directory(), relative=True)

    def test_run_number_with_both_path_overrides(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                self.check_pipeline(variant, self.directory(), run=742)

    def test_run_number_with_input_override(self):
        for variant in VARIANTS:
            for stage in STAGES:
                with self.subTest(variant=variant, stage=stage):
                    cwd, source, _, calibration, offsets = self.prepare_case(stage)
                    arguments = ["--input", source, "1"] + self.stage_options(stage, calibration, offsets)
                    self.assert_success(self.execute(variant, stage, cwd, arguments))
                    self.assert_stage_output(stage, cwd / FILENAMES[stage])

    def test_run_number_with_output_override(self):
        for variant in VARIANTS:
            for stage in STAGES:
                with self.subTest(variant=variant, stage=stage):
                    cwd, _, output, calibration, offsets = self.prepare_case(stage, legacy_input=True)
                    arguments = ["1", "--output", output] + self.stage_options(stage, calibration, offsets)
                    self.assert_success(self.execute(variant, stage, cwd, arguments))
                    self.assert_stage_output(stage, output)
                    self.assertFalse((cwd / FILENAMES[stage]).exists())

    def test_sort_parameter_overrides_are_independent(self):
        for variant in VARIANTS:
            for option in ("--calibration", "--offsets"):
                with self.subTest(variant=variant, option=option):
                    cwd, source, output, calibration, offsets = self.prepare_case("sort")
                    arguments = ["--input", source, "--output", output, option,
                                 calibration if option == "--calibration" else offsets]
                    self.assert_success(self.execute(variant, "sort", cwd, arguments))
                    expected = ([Hit(9, 2, 201., 0), Hit(9, 3, 599., 100)] if option == "--calibration" else
                                [Hit(9, 2, 100., 0), Hit(9, 3, 200., 0)])
                    self.assertEqual(read_sorted_hits(output)[2], expected)

    def test_missing_explicit_input_preserves_output(self):
        for variant in VARIANTS:
            for stage in STAGES:
                with self.subTest(variant=variant, stage=stage):
                    cwd, source, output, calibration, offsets = self.prepare_case(stage)
                    missing = source.with_name("missing input")
                    output.write_bytes(b"previous output")
                    arguments = ["--input", missing, "--output", output]
                    arguments += self.stage_options(stage, calibration, offsets)
                    result = self.execute(variant, stage, cwd, arguments)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn(str(missing), result.stderr)
                    self.assertEqual(output.read_bytes(), b"previous output")
                    self.assertEqual(list(cwd.parent.rglob("*.partial.*")), [])

    def test_missing_explicit_parameters_preserve_output(self):
        for variant in VARIANTS:
            for option in ("--calibration", "--offsets"):
                with self.subTest(variant=variant, option=option):
                    cwd, source, output, calibration, offsets = self.prepare_case("sort")
                    missing = cwd / "missing parameter"
                    output.write_bytes(b"previous output")
                    arguments = ["--input", source, "--output", output,
                                 "--calibration", missing if option == "--calibration" else calibration,
                                 "--offsets", missing if option == "--offsets" else offsets]
                    result = self.execute(variant, "sort", cwd, arguments)
                    self.assertGreater(result.returncode, 0)
                    self.assertIn(str(missing), result.stderr)
                    self.assertEqual(output.read_bytes(), b"previous output")

    def test_output_must_not_alias_input(self):
        for variant in VARIANTS:
            for stage in STAGES:
                for kind in ("same", "symlink", "hardlink"):
                    with self.subTest(variant=variant, stage=stage, alias=kind):
                        cwd, source, output, calibration, offsets = self.prepare_case(stage)
                        before = digest(source)
                        if kind == "same":
                            output = cwd / os.path.relpath(source, cwd)
                        elif kind == "symlink":
                            output.symlink_to(source)
                        else:
                            os.link(source, output)
                        arguments = ["--input", source, "--output", output]
                        arguments += self.stage_options(stage, calibration, offsets)
                        result = self.execute(variant, stage, cwd, arguments)
                        self.assertGreater(result.returncode, 0)
                        self.assertIn("output aliases input or parameter file", result.stderr)
                        self.assertEqual(digest(source), before)
                        self.assertEqual(digest(output), before)
                        self.assertEqual(list(cwd.parent.rglob("*.partial.*")), [])

    def test_output_must_not_alias_parameters(self):
        for variant in VARIANTS:
            for parameter in ("calibration", "offsets"):
                for kind in ("same", "symlink", "hardlink"):
                    with self.subTest(variant=variant, parameter=parameter, alias=kind):
                        cwd, source, output, calibration, offsets = self.prepare_case("sort")
                        protected = calibration if parameter == "calibration" else offsets
                        before = digest(protected)
                        if kind == "same":
                            output = protected
                        elif kind == "symlink":
                            output.symlink_to(protected)
                        else:
                            os.link(protected, output)
                        result = self.execute(variant, "sort", cwd,
                                              ["--input", source, "--output", output,
                                               *self.stage_options("sort", calibration, offsets)])
                        self.assertGreater(result.returncode, 0)
                        self.assertIn("output aliases input or parameter file", result.stderr)
                        self.assertEqual(digest(protected), before)

    def test_missing_output_directory_is_an_error(self):
        for variant in VARIANTS:
            for stage in STAGES:
                with self.subTest(variant=variant, stage=stage):
                    cwd, source, output, calibration, offsets = self.prepare_case(stage)
                    output = output.parent / "missing directory" / output.name
                    before = digest(source)
                    result = self.execute(variant, stage, cwd,
                                          ["--input", source, "--output", output,
                                           *self.stage_options(stage, calibration, offsets)])
                    self.assertGreater(result.returncode, 0)
                    self.assertIn("cannot create output", result.stderr)
                    self.assertFalse(output.exists())
                    self.assertEqual(digest(source), before)

    def test_notebook_snapshot_build_is_self_contained(self):
        sys.path.insert(0, str(REPO))
        from analysis import notebook_analysis as analysis
        root = self.directory()
        source = root / "sorted input.root"
        write_sorted(source, HITS)
        before = digest(source)
        built, directory, provenance = analysis.rebuild_events(source, VARIANTS[0], 7, root / "results")
        self.assertEqual(read_events(built)[2], [HITS])
        self.assertEqual(digest(source), before)
        common = directory / "source_snapshot/common/CommandLine.h"
        self.assertEqual(digest(common), digest(REPO / "common/CommandLine.h"))
        self.assertEqual(provenance["source_snapshot"]["common/CommandLine.h"]["sha256"], digest(common))
        self.assertEqual(provenance["rebuilt"]["entries"], 1)
        self.assertFalse((directory / "FAILED.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
