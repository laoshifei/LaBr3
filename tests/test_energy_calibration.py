"""Numerical propagation and raw-input/archive checks for calibration candidates."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from fixtures import ROOT, REPO, Hit, write_channel_hits

sys.path.insert(0, str(REPO))
from analysis import energy_calibration as calibration


REFERENCE = {"energy_keV": [1173.228, 1332.492], "standard_uncertainty_keV": [.003, .004]}


def peak(mean, sigma, covariance):
    matrix = np.eye(4)
    matrix[np.ix_([1, 2], [1, 2])] = covariance
    return {"usable": True, "mean": mean, "sigma": sigma, "covariance": matrix.tolist(),
            "quality_flag": "inspect_residuals"}


class EnergyCalibrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="labr3-calibration-")
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.config_path = self.work / "settings.json"
        self.config = json.loads((REPO / "analysis/co60_run0009.json").read_text())
        self.config.pop("input_sha256")
        self.config["channels"] = self.config["channels"][:1]
        self.config["channels"][0]["range_checks"] = {}
        self.config_path.write_text(json.dumps(self.config))
        self.raw = self.work / "raw.root"
        self.output = self.work / "candidate"
        self.fits = [peak(600., 5., [[.04, .006], [.006, .01]]),
                     peak(680., 5.5, [[.09, -.01], [-.01, .0225]])]

    def invoke(self, *options, script=None, config=None, output=None):
        return subprocess.run([sys.executable, "-B", str(script or REPO / "analysis/energy_calibration.py"),
                               "--input", str(self.raw), "--config", str(config or self.config_path),
                               "--output-dir", str(output or self.output), *map(str, options)],
                              cwd=self.work, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "MPLCONFIGDIR": str(self.work / "mpl")})

    def test_two_point_mapping_has_zero_independent_linearity_degrees_of_freedom(self):
        result = calibration.two_point_candidate(self.fits, REFERENCE)
        self.assertAlmostEqual(result["p0"], -21.252)
        self.assertAlmostEqual(result["p1"], 1.9908)
        for mean, energy in zip((600., 680.), REFERENCE["energy_keV"]):
            self.assertAlmostEqual(result["p0"] + result["p1"] * mean, energy)
        self.assertEqual(result["calibration_ndf"], 0)
        self.assertFalse(result["linearity_validated"])
        self.fits[0]["quality_flag"] = "inspect_model"
        self.assertTrue(calibration.two_point_candidate(self.fits, REFERENCE)["requires_model_review"])

    def test_covariance_and_resolution_uncertainty_match_monte_carlo(self):
        result = calibration.two_point_candidate(self.fits, REFERENCE)
        mean = [600., 5., 680., 5.5, *REFERENCE["energy_keV"]]
        covariance = np.zeros((6, 6))
        covariance[:2, :2] = [[.04, .006], [.006, .01]]
        covariance[2:4, 2:4] = [[.09, -.01], [-.01, .0225]]
        covariance[4:, 4:] = np.diag(np.array(REFERENCE["standard_uncertainty_keV"])**2)
        draws = np.random.default_rng(8429).multivariate_normal(mean, covariance, 200000)
        slope = (draws[:, 5] - draws[:, 4]) / (draws[:, 2] - draws[:, 0])
        offset = draws[:, 4] - slope * draws[:, 0]
        np.testing.assert_allclose(np.cov([offset, slope]), result["coefficient_covariance"], rtol=.02)
        for index in (0, 1):
            width = calibration.analysis.FWHM_FACTOR * draws[:, 2 * index + 1] * slope
            resolution = 100 * width / draws[:, 4 + index]
            expected = result["peaks"][index]
            self.assertAlmostEqual(np.std(width) / expected["fwhm_keV_stat_error"], 1., delta=.02)
            self.assertAlmostEqual(np.std(resolution) / expected["resolution_percent_stat_error"], 1., delta=.02)

    def test_unusable_peak_does_not_produce_coefficients(self):
        self.fits[1] = {"usable": False, "reason": "insufficient counts"}
        self.assertIsNone(calibration.two_point_candidate(self.fits, REFERENCE))

    def test_invalid_peak_order_width_and_covariance_are_rejected(self):
        for mutation in (lambda f: f[1].update(mean=600.), lambda f: f[0].update(sigma=-1.),
                         lambda f: f[0]["covariance"][1].__setitem__(1, -1.),
                         lambda f: f[0]["covariance"][1].__setitem__(1, float("nan"))):
            fits = copy.deepcopy(self.fits)
            mutation(fits)
            with self.assertRaises(ValueError):
                calibration.two_point_candidate(fits, REFERENCE)

    def test_configuration_rejects_ambiguous_or_invalid_selections_and_ranges(self):
        for mutation in (lambda c: c.update(unknown=True), lambda c: c.update(run=True),
                         lambda c: c["channels"].append(copy.deepcopy(c["channels"][0])),
                         lambda c: c["channels"][0].update(board=32768),
                         lambda c: c["channels"][0].update(ranges=[[590, 614], [670, 696]]),
                         lambda c: c["channels"][0].update(ranges=[[589.5, 700.5], [669.5, 696.5]]),
                         lambda c: c["reference"].update(energy_keV=[1332., 1173.]),
                         lambda c: c["reference"].update(standard_uncertainty_keV=[float("nan"), .004])):
            config = copy.deepcopy(self.config)
            mutation(config)
            self.config_path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                calibration.load_config(self.config_path)
        self.config_path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            calibration.load_config(self.config_path)

    def test_reader_selects_one_cycle_and_board_and_preserves_adc_extremes(self):
        write_channel_hits(self.raw, [Hit(4, 2, 0, 0), Hit(4, 2, 65535, 1), Hit(5, 2, 100, 2)])
        with ROOT.TFile(str(self.raw), "UPDATE") as handle:
            handle.Get("tr_ch02").Write()
        with ROOT.TFile(str(self.raw), "READ") as handle:
            histogram, selected = calibration.read_spectrum(handle, 4, 2)
        self.assertGreater(len(selected["available_cycles"]), 1)
        self.assertEqual(selected["selected_cycle"], max(selected["available_cycles"]))
        self.assertEqual((selected["tree_entries"], selected["selected_records"], selected["other_board_records"]), (3, 2, 1))
        self.assertEqual(histogram.GetBinCenter(1), 0)
        self.assertEqual(histogram.GetBinCenter(65536), 65535)
        self.assertEqual((histogram.GetBinContent(1), histogram.GetBinContent(65536)), (1, 1))
        self.assertEqual(histogram.Integral(0, 65537), 2)

    def test_reader_rejects_wrong_branch_type_missing_field_and_misnamed_channel(self):
        for options, hits in [({"wrong_energy_type": True}, [Hit(0, 2, 50, 0)]),
                              ({"omit": "board"}, [Hit(0, 2, 50, 0)]),
                              ({"tree_channel": 2}, [Hit(0, 3, 50, 0)])]:
            write_channel_hits(self.raw, hits, **options)
            with ROOT.TFile(str(self.raw), "READ") as handle, self.assertRaises(ValueError):
                calibration.read_spectrum(handle, 0, 2)

    def test_empty_spectrum_reports_no_calibration(self):
        write_channel_hits(self.raw, [], empty_channels=(2,))
        with ROOT.TFile(str(self.raw), "READ") as handle:
            histogram, selected = calibration.read_spectrum(handle, 0, 2)
        self.assertEqual(selected["selected_records"], 0)
        fits = [calibration.analysis.fit_peak(histogram, bounds, "ADC channel")
                for bounds in self.config["channels"][0]["ranges"]]
        self.assertIsNone(calibration.two_point_candidate(fits, REFERENCE))

    def test_cli_archives_fits_without_dat_and_replays_from_saved_sources(self):
        random = np.random.default_rng(601332)
        energies = np.rint(np.concatenate((random.normal(600, 4, 6000), random.normal(680, 5, 6000),
                                          random.uniform(540, 740, 4000)))).astype(int)
        write_channel_hits(self.raw, [Hit(0, 2, int(value), index) for index, value in enumerate(energies)])
        original_hash = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        reference_pdf = self.work / "reference.pdf"
        reference_pdf.write_bytes(b"synthetic reference fixture")
        self.config["reference"]["pdf_sha256"] = hashlib.sha256(reference_pdf.read_bytes()).hexdigest()
        self.config_path.write_text(json.dumps(self.config))
        result = self.invoke("--reference-pdf", reference_pdf)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        saved = json.loads((self.output / "results.json").read_text())
        self.assertEqual(manifest["status"], "succeeded")
        self.assertTrue(manifest["input_unchanged"])
        self.assertEqual(saved["selected_records"], 16000)
        self.assertEqual(list(self.output.rglob("*.dat")), [])
        self.assertTrue((self.output / "board0_ch2_fits.png").is_file())
        candidate = saved["channels"][0]["fits"]["nominal"]["candidate"]
        self.assertIsNotNone(candidate)
        self.assertLess(abs(candidate["p1"] - 1.9908), 5 * candidate["p1_stat_error"])
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(manifest, json.loads((self.output / "manifest.json").read_text()))
        self.config_path.write_text("changed original settings")
        replay = self.work / "replay"
        result = self.invoke("--reference-pdf", self.output / "reference.pdf", output=replay,
                             script=self.output / "source_snapshot/analysis/energy_calibration.py",
                             config=self.output / "configuration.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        repeated = json.loads((replay / "results.json").read_text())
        self.assertEqual(saved["channels"], repeated["channels"])
        self.assertEqual(original_hash, hashlib.sha256(self.raw.read_bytes()).hexdigest())

    def test_input_hash_mismatch_creates_no_result(self):
        write_channel_hits(self.raw, [])
        self.config["input_sha256"] = "0" * 64
        self.config_path.write_text(json.dumps(self.config))
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Input SHA-256", result.stderr)
        self.assertFalse(self.output.exists())

    def test_wrong_reference_hash_creates_no_result(self):
        write_channel_hits(self.raw, [])
        reference = self.work / "wrong.pdf"
        reference.write_bytes(b"wrong reference")
        result = self.invoke("--reference-pdf", reference)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Reference PDF SHA-256", result.stderr)
        self.assertFalse(self.output.exists())

    def test_bad_raw_input_is_recorded_as_failed(self):
        self.raw.write_bytes(b"not a ROOT file")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
