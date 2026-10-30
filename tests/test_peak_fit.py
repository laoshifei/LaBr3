"""Validate statistical results and failed-fit behavior independently of real data."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

import numpy as np

from fixtures import ROOT, REPO

sys.path.insert(0, str(REPO))
from analysis import notebook_analysis as analysis


class PeakFitTests(unittest.TestCase):
    def histogram(self, bins=120, low=-6, high=6):
        histogram = ROOT.TH1D(f"test_{uuid.uuid4().hex}", "", bins, low, high)
        histogram.SetDirectory(0)
        return histogram

    def test_known_gaussian_width_with_background(self):
        histogram = self.histogram()
        random = np.random.default_rng(317)
        for value in np.concatenate((random.normal(.4, .65, 60_000), random.uniform(-6, 6, 10_000))):
            histogram.Fill(float(value))
        fit = analysis.fit_peak(histogram, (-3, 4), "ns")
        self.assertTrue(fit["usable"], fit)
        self.assertLess(abs(fit["mean"] - .4), 5 * fit["mean_error"])
        self.assertLess(abs(fit["fwhm"] - analysis.FWHM_FACTOR * .65), 5 * fit["fwhm_error"])
        self.assertGreater(fit["fwhm_error"], 0)
        self.assertLess(fit["deviance_per_ndf"], 2.)
        json.dumps(fit, allow_nan=False)

    def test_empty_and_sparse_histograms_do_not_report_resolution(self):
        for count in (0, 20):
            with self.subTest(count=count):
                histogram = self.histogram()
                for _ in range(count):
                    histogram.Fill(.4)
                fit = analysis.fit_peak(histogram, (-3, 4), "ns")
                self.assertFalse(fit["usable"])
                self.assertEqual(fit["status"], "skipped")
                self.assertNotIn("fwhm", fit)

    def test_invalid_fit_ranges_are_explicit_errors(self):
        for bounds in [(2, -2), (float("nan"), 2), (-7, 2), (-2.03, 2)]:
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                analysis.fit_peak(self.histogram(), bounds, "ns")

    def test_invalid_analysis_settings_are_rejected(self):
        for settings in [{"channel_a": 2, "channel_b": 2}, {"gates_a": [[200, 100]]},
                         {"time_bins": 0}, {"gate_a": []}]:
            with self.subTest(settings=settings), self.assertRaises(Exception):
                analysis.make_config(settings)

    def test_model_bin_integrals_conserve_gaussian_area(self):
        edges = np.linspace(-10, 10, 201)
        fit = {"parameters": {"amplitude": 100., "mean": 0., "sigma": 1., "background": 2.}}
        average = analysis.model_counts(edges, fit)
        area = float(np.sum((average - 2.) * np.diff(edges)))
        self.assertAlmostEqual(area, 100 * np.sqrt(2 * np.pi), places=9)

    def test_export_round_trip_includes_counts_and_config(self):
        from fixtures import Hit, write_built
        with tempfile.TemporaryDirectory(prefix="labr3-export-test-") as temporary:
            output = Path(temporary)
            path = output / "events.root"
            write_built(path, [[Hit(0, 2, 150., 0), Hit(0, 3, 350., 1000)]])
            settings = {"gates_a": [[100, 200]], "gates_b": [[300, 400]]}
            result = analysis.analyze_events(path, settings)
            fit = analysis.fit_peak(result.selected_times, (-2, 2), "ns")
            analysis.export_results(output, {"example": True}, settings, result, {"timing": fit})
            saved = json.loads((output / "results.json").read_text())
            self.assertEqual(saved["counts"]["selected_pairs"], 1)
            self.assertEqual(saved["analysis_settings"], settings)
            source = ROOT.TFile.Open(str(output / "histograms.root"))
            self.assertEqual(source.Get("selected_times").GetEntries(), 1)
            self.assertEqual(source.Get("board0_ch2").GetEntries(), 1)
            source.Close()


if __name__ == "__main__":
    unittest.main()
