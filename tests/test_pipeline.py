"""Fast pipeline orchestration tests using temporary executables, without ROOT."""

import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "scripts/run_pipeline.py"
VARIANT = "DT5730_DPP_PSD"
sys.path.insert(0, str(REPO / "scripts"))
import pipeline_records as records


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="labr3-pipeline-tests-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = self.work / "输入 sample.BIN"
        self.source.write_bytes(b"input bytes")
        self.calibration = self.work / "calibration.dat"
        self.calibration.write_text("calibration bytes\n")
        self.offsets = self.work / "offsets.dat"
        self.offsets.write_text("offset bytes\n")
        self.output = self.work / "结果 directory"
        self.bin_dir = self.work / "compiled programs"
        self.env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        for stage in ("raw2root", "alignment", "sort", "build"):
            tool = self.bin_dir / VARIANT / stage
            tool.parent.mkdir(parents=True, exist_ok=True)
            tool.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""
                import argparse, json, os, pathlib, sys, time
                p = argparse.ArgumentParser()
                p.add_argument('--input', required=True)
                p.add_argument('--output', required=True)
                for name in ('calibration', 'offsets', 'reference'):
                    p.add_argument('--' + name)
                args = p.parse_args()
                stage = pathlib.Path(sys.argv[0]).name
                print(json.dumps(vars(args)), flush=True)
                if os.environ.get('TEST_DELAY_STAGE') == stage:
                    pathlib.Path(os.environ['TEST_PID_FILE']).write_text(str(os.getpid()))
                    time.sleep(30)
                if os.environ.get('TEST_FAIL_STAGE') == stage:
                    sys.exit(7)
                if os.environ.get('TEST_MISSING_STAGE') == stage:
                    sys.exit(0)
                source = pathlib.Path(args.input)
                if os.environ.get('TEST_MUTATE_INPUT') == stage:
                    source.write_bytes(source.read_bytes() + b'changed')
                pathlib.Path(args.output).write_bytes(source.read_bytes() + stage.encode())
            """))
            tool.chmod(0o755)

    def command(self, *options, snapshot=False):
        return [sys.executable, "-B", str(DRIVER), "--variant", VARIANT, "--run", "9",
                "--input", str(self.source), "--output-dir", str(self.output),
                *([] if snapshot else ["--bin-dir", str(self.bin_dir)]), *map(str, options)]

    def invoke(self, *options, env=None, snapshot=False):
        return subprocess.run(self.command(*options, snapshot=snapshot), cwd=self.work, env=env or self.env,
                              text=True, capture_output=True, timeout=30)

    def sorting(self):
        return ["--calibration", self.calibration, "--offsets", self.offsets]

    def manifest(self):
        return json.loads((self.output / "manifest.json").read_text())

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_help_requires_no_root_or_input(self):
        result = subprocess.run([sys.executable, "-B", str(DRIVER), "--help"], capture_output=True, text=True)
        self.assert_success(result)
        self.assertIn("--input-kind", result.stdout)
        self.assertFalse(self.output.exists())

    def test_prebuilt_pipeline_records_commands_hashes_and_snapshots(self):
        self.assert_success(self.invoke(*self.sorting()))
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "succeeded")
        self.assertTrue(manifest["input_unchanged"])
        self.assertFalse(manifest["counts_complete"])
        self.assertEqual(manifest["software"]["build_origin"], "prebuilt")
        self.assertIsNone(manifest["scope"]["acquisition_complete"])
        self.assertEqual([item["name"] for item in manifest["stages"]], ["raw2root", "sort", "build"])
        for stage in manifest["stages"]:
            self.assertEqual(stage["status"], "succeeded")
            self.assertEqual(stage["returncode"], 0)
            self.assertGreaterEqual(stage["elapsed_seconds"], 0)
            self.assertTrue((self.output / stage["log"]).is_file())
            output = stage["outputs"][0]
            self.assertEqual(output["sha256"], hashlib.sha256(Path(output["path"]).read_bytes()).hexdigest())
        sort_command = manifest["stages"][1]["command"]
        for name, original in (("calibration", self.calibration), ("offsets", self.offsets)):
            snapshot = self.output / manifest["parameters"][name]["snapshot"]
            self.assertEqual(snapshot.read_bytes(), original.read_bytes())
            self.assertIn(str(snapshot), sort_command)
            self.assertNotIn(str(original), sort_command)

    def replay(self, destination, *options):
        return subprocess.run([sys.executable, "-B", str(REPO / "scripts/replay_pipeline.py"),
                               str(self.output / "manifest.json"), "--output-dir", str(destination),
                               *map(str, options)], cwd=self.work, env=self.env, text=True,
                              capture_output=True, timeout=30)

    def test_replay_uses_archived_parameters_and_binary_copies(self):
        self.assert_success(self.invoke(*self.sorting()))
        manifest = self.manifest()
        records.verify_archive(self.output, manifest["source_snapshot"])
        records.verify_archive(self.output, manifest["software"]["binaries"])
        self.calibration.write_text("changed original calibration")
        self.offsets.write_text("changed original offsets")
        for stage in ("raw2root", "sort", "build"):
            (self.bin_dir / VARIANT / stage).write_text("changed original executable")
        destination = self.work / "replayed"
        self.assert_success(self.replay(destination))
        replayed = json.loads((destination / "manifest.json").read_text())
        self.assertEqual(replayed["status"], "succeeded")
        self.assertEqual(replayed["replayed_from"]["path"], str(self.output / "manifest.json"))
        self.assertEqual((destination / "events.root").read_bytes(), (self.output / "events.root").read_bytes())
        for name in ("calibration", "offsets"):
            self.assertEqual(replayed["parameters"][name]["sha256"], manifest["parameters"][name]["sha256"])

    def test_replay_refuses_changed_source_parameter_or_binary(self):
        self.assert_success(self.invoke(*self.sorting()))
        for relative in ("source_snapshot/scripts/run_pipeline.py", "parameters/calibration.dat",
                         f"executables/{VARIANT}/build"):
            with self.subTest(path=relative):
                path = self.output / relative
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                try:
                    destination = self.work / "must-not-exist"
                    result = self.replay(destination)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("File changed", result.stderr)
                    self.assertFalse(destination.exists())
                finally:
                    path.write_bytes(original)

    def test_replay_input_relocation_requires_same_hash(self):
        self.assert_success(self.invoke(*self.sorting()))
        relocated = self.work / "relocated.BIN"
        self.source.rename(relocated)
        self.assert_success(self.replay(self.work / "relocated-result", "--input", relocated))
        relocated.write_bytes(b"different input")
        destination = self.work / "changed-input"
        self.assertNotEqual(self.replay(destination, "--input", relocated).returncode, 0)
        self.assertFalse(destination.exists())

    def test_replay_preserves_original_run_and_rejects_incomplete_runs(self):
        self.assert_success(self.invoke(*self.sorting()))
        self.assertNotEqual(self.replay(self.output / "nested").returncode, 0)
        self.assertNotEqual(self.replay(self.work / "zero-jobs", "--jobs", "0").returncode, 0)
        self.assertFalse((self.work / "zero-jobs").exists())
        manifest = self.manifest()
        manifest["status"] = "failed"
        (self.output / "manifest.json").write_text(json.dumps(manifest))
        destination = self.work / "incomplete-replay"
        self.assertNotEqual(self.replay(destination).returncode, 0)
        self.assertFalse(destination.exists())

    def test_build_tool_failure_retains_source_snapshot_and_stops_stages(self):
        result = self.invoke(*self.sorting(), "--cxx", "/bin/false", snapshot=True)
        self.assertNotEqual(result.returncode, 0)
        manifest = self.manifest()
        self.assertEqual(manifest["software"]["build_origin"], "snapshot")
        self.assertEqual(manifest["software"]["build"]["status"], "failed")
        self.assertEqual([stage["status"] for stage in manifest["stages"]], ["not_run"] * 3)
        records.verify_archive(self.output, manifest["source_snapshot"])
        self.assertFalse((self.output / "raw.root").exists())

    def test_stage_report_validation_and_handoff_conservation(self):
        log = self.work / "counts.log"
        def read(stage, counts):
            log.write_text("LABR3_STATS " + json.dumps({"version": 1, "stage": stage, "counts": counts}) + "\n")
            return records.read_stage_counts(stage, log, self.work, required=True)
        expected = {"input_hits": 3, "output_hits": 3, "output_events": 2,
                    **{f"events_m{i}": int(i in (1, 2)) for i in range(1, 9)}}
        self.assertEqual(read("build", expected), expected)
        for change in ({"output_hits": 2}, {"output_events": 1}, {"events_m1": 0}, {"input_hits": -1},
                       {"input_hits": 3.0}, {"events_m1": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                read("build", {**expected, **change})
        for stage, counts in (("raw2root", {"input_records": 3, "output_hits": 2}),
                              ("sort", {"input_hits": 3, "output_hits": 4}),
                              ("alignment", {"input_hits": 2, "matched_pairs": 3})):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                read(stage, counts)
        records.check_handoff("build", expected, {"output_hits": 3})
        with self.assertRaisesRegex(ValueError, "Hit count mismatch"):
            records.check_handoff("build", expected, {"output_hits": 4})
        with self.assertRaisesRegex(ValueError, "Event count mismatch"):
            records.check_handoff("analysis", {"input_hits": 3, "input_events": 3}, expected)

    def test_missing_duplicate_or_wrong_stage_count_reports_are_rejected(self):
        log = self.work / "counts.log"
        log.write_text("legacy output\n")
        self.assertIsNone(records.read_stage_counts("sort", log, self.work, required=False))
        with self.assertRaises(ValueError):
            records.read_stage_counts("sort", log, self.work, required=True)
        report = 'LABR3_STATS {"version":1,"stage":"sort","counts":{"input_hits":0,"output_hits":0}}\n'
        for text in (report * 2, report.replace('"sort"', '"build"'), report.replace('"version":1', '"version":9')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                log.write_text(text)
                records.read_stage_counts("sort", log, self.work, required=True)

    def test_archive_paths_cannot_escape_the_run_directory(self):
        self.assert_success(self.invoke(*self.sorting()))
        for relative in ("../calibration.dat", str(self.calibration)):
            with self.subTest(path=relative), self.assertRaises(ValueError):
                records.archived_path(self.output, relative)
        manifest = self.manifest()
        del manifest["source_snapshot"]["scripts/run_pipeline.py"]
        (self.output / "manifest.json").write_text(json.dumps(manifest))
        destination = self.work / "missing-driver-record"
        self.assertNotEqual(self.replay(destination).returncode, 0)
        self.assertFalse(destination.exists())

    def test_alignment_is_a_side_stage_and_does_not_feed_sort(self):
        self.assert_success(self.invoke("--stages", "raw2root,alignment,sort,build", "--reference", "3", *self.sorting()))
        stages = self.manifest()["stages"]
        self.assertEqual(stages[1]["input"], str(self.output / "raw.root"))
        self.assertEqual(stages[2]["input"], str(self.output / "raw.root"))
        self.assertEqual(stages[1]["command"][-2:], ["--reference", "3"])

    def test_stage_selection_uses_declared_input_kind(self):
        self.assert_success(self.invoke("--input-kind", "sorted", "--stages", "build"))
        self.assertEqual(self.manifest()["requested_stages"], ["build"])
        self.assertEqual((self.output / "events.root").read_bytes(), b"input bytesbuild")
        self.assertFalse((self.output / "raw.root").exists())

    def test_invalid_stage_graph_and_options_leave_no_results(self):
        cases = [("--stages", ""), ("--stages", "raw2root,build"),
                 ("--stages", "sort,raw2root"), ("--stages", "raw2root,raw2root"),
                 ("--stages", "unknown"), ("--stages", "build"),
                 ("--run", "-1"), ("--run", "2147483648"),
                 ("--timeout", "nan"), ("--timeout", "0"),
                 ("--reference", "8"), ("--reference", "2")]
        for options in cases:
            with self.subTest(options=options):
                result = self.invoke(*self.sorting(), *options)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def test_required_and_unused_parameter_options_are_rejected(self):
        for options in [[], ["--calibration", self.calibration],
                        ["--stages", "raw2root", *self.sorting()],
                        ["--input-kind", "events", "--stages", "analysis"]]:
            with self.subTest(options=options):
                self.assertNotEqual(self.invoke(*options).returncode, 0)
                self.assertFalse(self.output.exists())

    def test_missing_input_parameter_or_binary_fails_before_creating_output(self):
        cases = [["--input", self.work / "missing.BIN"],
                 ["--offsets", self.work / "missing.dat"],
                 ["--bin-dir", self.work / "missing binaries"]]
        for options in cases:
            with self.subTest(options=options):
                self.assertNotEqual(self.invoke(*self.sorting(), *options).returncode, 0)
                self.assertFalse(self.output.exists())

    def test_existing_output_directory_and_input_alias_are_preserved(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("keep")
        self.assertNotEqual(self.invoke(*self.sorting()).returncode, 0)
        self.assertEqual(list(self.output.iterdir()), [marker])
        original = self.source.read_bytes()
        for destination in (self.source, self.work / "alias"):
            if destination != self.source:
                destination.symlink_to(self.source)
            self.assertNotEqual(self.invoke(*self.sorting(), "--output-dir", destination).returncode, 0)
        self.assertEqual(self.source.read_bytes(), original)

    def test_known_split_first_volume_and_isolated_continuation_are_refused(self):
        first = self.work / "Data_run_4.BIN"
        second = self.work / "Data_run_4_1.BIN"
        first.write_bytes(b"first")
        second.write_bytes(b"continuation")
        for source in (first, second):
            result = self.invoke(*self.sorting(), "--input", source)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--input-manifest", result.stderr)
        first.unlink()
        result = self.invoke(*self.sorting(), "--input", second)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("continuation", result.stderr)
        self.assertFalse(self.output.exists())

    def test_generic_numeric_sibling_is_refused(self):
        (self.work / "输入 sample_1.bin").write_bytes(b"next")
        result = self.invoke(*self.sorting())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Related BIN volumes", result.stderr)
        self.assertFalse(self.output.exists())

    def test_failed_stage_stops_pipeline_and_preserves_diagnostics(self):
        result = self.invoke(*self.sorting(), env={**self.env, "TEST_FAIL_STAGE": "sort"})
        self.assertNotEqual(result.returncode, 0)
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual([s["status"] for s in manifest["stages"]], ["succeeded", "failed", "not_run"])
        self.assertEqual(manifest["stages"][1]["returncode"], 7)
        self.assertTrue((self.output / "raw.root").exists())
        self.assertFalse((self.output / "events.root").exists())
        self.assertIn("sort exited", result.stderr)

    def test_success_exit_without_output_is_failure(self):
        result = self.invoke(*self.sorting(), env={**self.env, "TEST_MISSING_STAGE": "raw2root"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.manifest()["stages"][0]["status"], "failed")
        self.assertFalse((self.output / "events.root").exists())

    def test_input_change_prevents_overall_success(self):
        result = self.invoke("--stages", "raw2root", env={**self.env, "TEST_MUTATE_INPUT": "raw2root"})
        self.assertNotEqual(result.returncode, 0)
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "failed")
        self.assertFalse(manifest["input_unchanged"])
        self.assertIn("Input changed", result.stderr)

    def bin_manifest_command(self):
        self.source.write_bytes(struct.pack("<HhhqHI", 1, 0, 2, 100, 99, 0))
        source = self.work / "inputs.json"
        source.write_text(json.dumps({"schema_version": 1, "variant": VARIANT, "run": 9, "export": "CUSTOM",
                                     "streams": [{"id": "ch2", "channels": [2], "header_mode": "each",
                                                  "part_count": 1, "files": [{"part": 0, "path": self.source.name}]}]}))
        command = self.command("--stages", "raw2root")
        index = command.index("--input")
        command[index:index + 2] = ["--input-manifest", str(source)]
        return command

    def test_manifest_requires_decoder_count_report_from_prebuilt_program(self):
        result = subprocess.run(self.bin_manifest_command(), cwd=self.work, env=self.env,
                                text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        saved = self.manifest()
        self.assertEqual(saved["input_preparation"]["status"], "succeeded")
        self.assertEqual(saved["stages"][0]["status"], "failed")
        self.assertIn("count report", result.stderr)

    def test_original_manifest_part_change_prevents_unchanged_flag_and_success(self):
        command = self.bin_manifest_command()
        tool = self.bin_dir / VARIANT / "raw2root"
        tool.write_text(f"#!{sys.executable}\nimport pathlib, sys\n"
                        f"pathlib.Path({str(self.source)!r}).write_bytes(b'changed original volume')\n"
                        "pathlib.Path(sys.argv[sys.argv.index('--output') + 1]).write_bytes(b'output')\n"
                        "print('LABR3_STATS {\"version\":1,\"stage\":\"raw2root\",\"counts\":{\"input_records\":1,\"output_hits\":1}}')\n")
        result = subprocess.run(command, cwd=self.work, env=self.env, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        saved = self.manifest()
        self.assertEqual(saved["input_preparation"]["status"], "succeeded")
        self.assertEqual(saved["stages"][0]["status"], "succeeded")
        self.assertEqual(saved["status"], "failed")
        self.assertFalse(saved["input_unchanged"])
        self.assertIn("File changed", result.stderr)

    def test_timeout_stops_child_and_records_failure(self):
        pid_file = self.work / "pid"
        result = self.invoke("--stages", "raw2root", "--timeout", "0.3",
                             env={**self.env, "TEST_DELAY_STAGE": "raw2root", "TEST_PID_FILE": str(pid_file)})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.manifest()["status"], "failed")
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_sigterm_stops_child_and_marks_interrupted(self):
        pid_file = self.work / "pid"
        with subprocess.Popen(self.command("--stages", "raw2root"), cwd=self.work,
                              env={**self.env, "TEST_DELAY_STAGE": "raw2root", "TEST_PID_FILE": str(pid_file)},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                deadline = time.monotonic() + 10
                while not pid_file.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(pid_file.exists())
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=10)
                self.assertEqual(process.returncode, 130)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertEqual(self.manifest()["status"], "interrupted")
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_invalid_analysis_json_is_rejected_without_root(self):
        config = self.work / "analysis.json"
        for document in ('[]', '{"channel_a": true}', '{"channel_a": 2.5}',
                         '{"channel_a": 8}', '{"channel_a": 2, "channel_b": 2}',
                         '{"channel_a": 2, "channel_a": 3}', '{"time_bins": 0}',
                         '{"time_low": NaN}', '{"gates_a": [[200, 100]]}', '{"unknown": 1}'):
            with self.subTest(document=document):
                config.write_text(document)
                result = self.invoke("--input-kind", "events", "--analysis-config", config)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
