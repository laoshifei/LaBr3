"""Read real ROOT schemas and exercise the shared analysis plus all legacy macros."""

import gc
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from fixtures import ROOT, REPO, VARIANTS, Hit, checked_run, write_built

if not ROOT.gInterpreter.Declare(f'#include "{REPO / "analysis/EventAnalysis.h"}"'):
    raise RuntimeError("Cannot compile event analysis")


def pair(a=150., b=350., dt=1000, reverse=False, base=0):
    hits = [Hit(0, 2, a, base), Hit(0, 3, b, base + dt)]
    return list(reversed(hits)) if reverse else hits


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(prefix="labr3-analysis-tests-")
        self.addCleanup(self.storage.cleanup)
        self.path = Path(self.storage.name) / "events.root"

    def analyze(self, events, config=None, **options):
        write_built(self.path, events, **options)
        reader = ROOT.labr3.EventReader(str(self.path))
        return ROOT.labr3.Analyze(reader, config or ROOT.labr3.AnalysisConfig())

    def test_read_all_fields_for_multiplicities_one_through_eight(self):
        events = [[Hit(i % 2, i, i + 0.25, 2**60 + i) for i in range(m)]
                  for m in range(1, 9)]
        write_built(self.path, events)
        reader = ROOT.labr3.EventReader(str(self.path))
        for entry, expected in enumerate(events):
            self.assertEqual(reader.GetEntry(entry), 1)
            actual = [Hit(int(reader.board[i]), int(reader.channel[i]),
                          float(reader.energy[i]), int(reader.timestamp[i]))
                      for i in range(reader.m)]
            self.assertEqual(actual, expected)
        result = ROOT.labr3.Analyze(reader, ROOT.labr3.AnalysisConfig())
        self.assertEqual(result.events, 8)
        self.assertEqual(result.hits, 36)
        self.assertEqual(list(result.multiplicities), [0] + [1] * 8)

    def test_energy_gates_do_not_reuse_previous_time_difference(self):
        config = ROOT.labr3.AnalysisConfig()
        config.gates_a.push_back(ROOT.labr3.EnergyWindow(100, 200))
        config.gates_b.push_back(ROOT.labr3.EnergyWindow(300, 400))
        events = [pair(), pair(reverse=True), pair(a=50), pair(b=450),
                  pair(a=100), pair(a=200), pair(b=300), pair(b=400)]
        result = self.analyze(events, config)
        self.assertEqual(result.channel_pairs, 8)
        self.assertEqual(result.selected_pairs, 2)
        self.assertEqual(result.selected_times.GetEntries(), 2)
        self.assertAlmostEqual(result.selected_times.GetMean(), 1.)

    def test_wrong_channel_and_board_are_excluded(self):
        config = ROOT.labr3.AnalysisConfig()
        config.board_a = config.board_b = 0
        events = [pair(), [Hit(0, 1, 150., 0), Hit(0, 3, 350., 1000)],
                  [Hit(1, 2, 150., 0), Hit(0, 3, 350., 1000)]]
        result = self.analyze(events, config)
        self.assertEqual(result.selected_pairs, 1)
        self.assertEqual(result.hits, 6)

    def test_time_direction_and_large_integer_timestamps(self):
        result = self.analyze([pair(dt=-1000, reverse=True, base=2**60)])
        self.assertAlmostEqual(result.selected_times.GetMean(), -1.)
        self.assertAlmostEqual(ROOT.labr3.DeltaTimeNs(-(2**63), 2**63-1),
                               (2**64-1) / 1000.)

    def test_time_overflow_is_counted_without_hiding_selected_events(self):
        result = self.analyze([pair(dt=100_000)])
        self.assertEqual(result.selected_pairs, 1)
        self.assertEqual(result.selected_times.Integral(), 0)
        self.assertEqual(result.selected_times.GetBinContent(401), 1)

    def test_empty_tree(self):
        result = self.analyze([])
        self.assertEqual(result.events, 0)
        self.assertEqual(result.selected_pairs, 0)

    def test_invalid_multiplicities_are_rejected(self):
        for m in [0, 9]:
            with self.subTest(m=m), self.assertRaisesRegex(Exception, "multiplicity"):
                self.analyze([[Hit(0, i % 8, 1., i) for i in range(m)]])

    def test_invalid_schema_is_rejected(self):
        for options in [{"omit": "timestamp"}, {"wrong_energy_type": True}]:
            with self.subTest(options=options), self.assertRaisesRegex(Exception, "branch"):
                self.analyze([pair()], **options)

    def test_nonfinite_energy_is_rejected(self):
        with self.assertRaisesRegex(Exception, "energy"):
            self.analyze([pair(a=float("nan"))])

    def test_reader_does_not_close_borrowed_file(self):
        write_built(self.path, [pair()])
        source = ROOT.TFile.Open(str(self.path))
        reader = ROOT.labr3.EventReader(source.Get("tr"))
        reader.GetEntry(0)
        del reader
        gc.collect()
        self.assertTrue(source.IsOpen())
        self.assertEqual(source.Get("tr").GetEntries(), 1)
        source.Close()

    def test_chain_crosses_files(self):
        second = self.path.with_name("second.root")
        write_built(self.path, [pair()])
        write_built(second, [pair(reverse=True)])
        chain = ROOT.TChain("tr")
        chain.Add(str(self.path)); chain.Add(str(second))
        reader = ROOT.labr3.EventReader(chain)
        result = ROOT.labr3.Analyze(reader, ROOT.labr3.AnalysisConfig())
        self.assertEqual(result.selected_pairs, 2)


class LegacyMacroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="labr3-legacy-analysis-")
        cls.addClassCleanup(cls.storage.cleanup)
        cls.work = Path(cls.storage.name)
        cls.binaries = {}
        flags = shlex.split(checked_run(["root-config", "--cflags", "--libs"]).stdout)
        for variant in VARIANTS:
            driver = cls.work / f"{variant}.cpp"
            driver.write_text('#include <TROOT.h>\n' + f'#include "{REPO / variant / "analysis/ana.C"}"\n' + '''
int main(int argc, char **argv) {
    gROOT->SetBatch(true);
    try {
        TFile input(argv[1], "READ");
        TTree *tree = nullptr;
        input.GetObject("tr", tree);
        double entries;
        { ana analysis(tree); analysis.Loop(); entries = analysis.TimeResolution()->GetEntries(); }
        if (!input.IsOpen()) return 4;
        std::cout << "SELECTED " << entries << std::endl;
    } catch (const std::exception &error) {
        std::cerr << error.what() << std::endl;
        return 3;
    }
    return 0;
}
''')
            binary = cls.work / variant
            checked_run(["g++", "-O2", str(driver), "-o", str(binary)] + flags)
            cls.binaries[variant] = binary

    def test_all_legacy_macros_use_safe_reader_and_correct_selection(self):
        events = [pair(a=600,b=1120), pair(a=600,b=1120,reverse=True), pair(a=0,b=0),
                  [Hit(0,1,600.,0), Hit(0,3,1120.,1000)]]
        events += [[Hit(0,i,600.,i) for i in range(m)] for m in range(1,9) if m != 2]
        path = self.work / "valid.root"
        write_built(path, events)
        for variant, binary in self.binaries.items():
            with self.subTest(variant=variant):
                result = checked_run([str(binary),str(path)])
                count = 2 if variant == "DT5730_DPP_PSD" else 3
                self.assertIn(f"SELECTED {count}", result.stdout)

    def test_all_legacy_macros_reject_over_capacity(self):
        path = self.work / "invalid.root"
        write_built(path, [[Hit(0,i%8,600.,i) for i in range(9)]])
        for variant, binary in self.binaries.items():
            with self.subTest(variant=variant):
                result = subprocess.run([str(binary),str(path)],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,3)
                self.assertIn("multiplicity",result.stderr)


if __name__ == "__main__":
    unittest.main()
