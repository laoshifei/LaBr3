"""Build orchestration checks with fake tools; no ROOT installation is required."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]
VARIANTS = ("DT5720_DPP_PSD", "DT5730_DPP_PSD", "DT5730_DPP_PHA")
STAGES = ("raw2root", "alignment", "sort", "build")
PROGRAMS = {f"{variant}/{stage}" for variant in VARIANTS for stage in STAGES}


class MakeTests(unittest.TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory(prefix="labr3-make-tests-")
        self.addCleanup(storage.cleanup)
        self.work = Path(storage.name)
        self.repo = self.work / "repository with spaces"
        self.repo.mkdir()
        shutil.copyfile(REPO / "Makefile", self.repo / "Makefile")
        (self.repo / "common").mkdir()
        shutil.copyfile(REPO / "common/CommandLine.h", self.repo / "common/CommandLine.h")
        for program in PROGRAMS:
            destination = self.repo / program
            destination.mkdir(parents=True)
            for source in (REPO / program).iterdir():
                if source.name == "Makefile" or source.suffix in (".cpp", ".h"):
                    shutil.copyfile(source, destination / source.name)
        tool_dir = self.work / "fake tools"
        tool_dir.mkdir()
        self.compiler = tool_dir / "cxx"
        self.root_config = tool_dir / "root-config"
        self.log = self.work / "compile.jsonl"
        self.write_tool(self.compiler, """
            import json, os, pathlib, sys
            if sys.argv[1:] == ['--version']:
                print('test compiler')
                sys.exit(0)
            if pathlib.Path.cwd().name == os.environ.get('LABR3_TEST_FAIL_STAGE'):
                sys.exit('deliberate compile failure')
            args = sys.argv[1:]
            assert '-DLABR3_FAKE_ROOT' in args
            assert all(pathlib.Path(arg).is_file() for arg in args if arg.endswith('.cpp'))
            output = pathlib.Path(args[args.index('-o') + 1])
            output.write_text('#!/bin/sh\\nexit 0\\n')
            output.chmod(0o755)
            fd = os.open(os.environ['LABR3_TEST_COMPILE_LOG'], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.write(fd, (json.dumps(str(pathlib.Path.cwd())) + '\\n').encode())
            os.close(fd)
        """)
        self.write_tool(self.root_config, """
            import os, sys
            option = sys.argv[1]
            if option == os.environ.get('LABR3_TEST_FAIL_ROOT_OPTION'):
                sys.exit(1)
            print({'--version': 'test ROOT', '--cflags': '-DLABR3_FAKE_ROOT', '--libs': '-lTestRoot'}[option])
        """)
        self.env = os.environ.copy()
        # Do not inherit a parent make's overrides or jobserver in these subprocesses.
        for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL"):
            self.env.pop(name, None)
        self.env["LABR3_TEST_COMPILE_LOG"] = str(self.log)

    @staticmethod
    def write_tool(path, source):
        path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(source))
        path.chmod(0o755)

    def make(self, *arguments, compiler=None, root_config=None, env=None, directory=None):
        command = ["make", "--no-print-directory", "-C", str(directory or self.repo),
                   f"CXX={compiler or self.compiler}", f"ROOT_CONFIG={root_config or self.root_config}",
                   *arguments]
        return subprocess.run(command, cwd=self.work, env=env or self.env,
                              text=True, capture_output=True, timeout=60)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def compiled(self):
        if not self.log.exists():
            return []
        return [Path(json.loads(line)).relative_to(self.repo).as_posix()
                for line in self.log.read_text().splitlines()]

    def test_help_without_dependencies(self):
        result = self.make("help", compiler="missing-test-compiler", root_config="missing-test-root")
        self.assert_success(result)
        self.assertIn(".build/<variant>/<stage>", result.stdout)
        self.assertFalse((self.repo / ".build").exists())

    def test_missing_or_broken_compiler_fails_before_building(self):
        for compiler, message in (("missing-test-compiler", "compiler not found"),
                                  ("/bin/false", "CXX cannot run")):
            with self.subTest(compiler=compiler):
                result = self.make("-j4", compiler=compiler)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.repo / ".build").exists())

    def test_missing_or_broken_root_fails_before_building(self):
        result = self.make("-j4", root_config="missing-test-root")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root-config not found", result.stderr)
        for option in ("--version", "--cflags", "--libs"):
            with self.subTest(option=option):
                result = self.make("-j4", env={**self.env, "LABR3_TEST_FAIL_ROOT_OPTION": option})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"root-config failed for {option}", result.stderr)
        self.assertFalse((self.repo / ".build").exists())

    def test_default_parallel_build_and_incremental_noop(self):
        self.assert_success(self.make("-j4"))
        self.assertCountEqual(self.compiled(), PROGRAMS)
        outputs = {name: (self.repo / ".build" / name).stat().st_mtime_ns for name in PROGRAMS}
        for name in PROGRAMS:
            self.assertFalse((self.repo / name / Path(name).name).exists())
        self.assert_success(self.make("-j4"))
        self.assertEqual(len(self.compiled()), 12)
        self.assertEqual(outputs, {name: (self.repo / ".build" / name).stat().st_mtime_ns
                                   for name in PROGRAMS})

    def test_variant_and_single_stage_selection(self):
        self.assert_success(self.make("-j4", "DT5720_DPP_PSD"))
        self.assertCountEqual(self.compiled(), [f"DT5720_DPP_PSD/{stage}" for stage in STAGES])
        self.assert_success(self.make("DT5730_DPP_PHA/sort"))
        self.assertEqual(self.compiled()[-1], "DT5730_DPP_PHA/sort")
        self.assertEqual(len(self.compiled()), 5)

    def test_source_makefile_and_common_header_trigger_rebuilds(self):
        self.assert_success(self.make("-j4"))
        for relative in ("DT5730_DPP_PSD/raw2root/main.cpp", "DT5730_DPP_PSD/raw2root/Makefile"):
            path = self.repo / relative
            path.write_text(path.read_text() + "\n")
            before = len(self.compiled())
            self.assert_success(self.make("-j4"))
            self.assertEqual(self.compiled()[before:], ["DT5730_DPP_PSD/raw2root"])
        common = self.repo / "common/CommandLine.h"
        common.write_text(common.read_text() + "\n")
        before = len(self.compiled())
        self.assert_success(self.make("-j4"))
        self.assertCountEqual(self.compiled()[before:], PROGRAMS)

    def test_compile_failure_propagates(self):
        result = self.make("DT5730_DPP_PSD/sort", env={**self.env, "LABR3_TEST_FAIL_STAGE": "sort"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deliberate compile failure", result.stderr)
        self.assertFalse((self.repo / ".build/DT5730_DPP_PSD/sort").exists())

    def test_standalone_builds_and_output_overrides(self):
        for name in sorted(PROGRAMS):
            with self.subTest(program=name):
                source = self.repo / name
                self.assert_success(self.make(f"GXX={self.compiler}", directory=source))
                self.assertTrue((source / source.name).is_file())
                output = self.work / f"override-{name.replace('/', '-')}"
                option = "OBJ" if source.name == "raw2root" else "out"
                self.assert_success(self.make(f"GXX={self.compiler}", f"{option}={output}", directory=source))
                self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
