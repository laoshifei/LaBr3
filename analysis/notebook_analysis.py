"""Reproducible ROOT event analysis for the editable real-run notebook."""

from datetime import datetime, timezone
from functools import lru_cache
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
import uuid

import numpy as np
import ROOT

REPO = Path(__file__).resolve().parents[1]
VARIANTS = ("DT5720_DPP_PSD", "DT5730_DPP_PSD", "DT5730_DPP_PHA")
FWHM_FACTOR = 2 * math.sqrt(2 * math.log(2))


@lru_cache(maxsize=1)
def load_core():
    if not ROOT.gInterpreter.Declare(f'#include "{REPO / "analysis/EventAnalysis.h"}"'):
        raise RuntimeError("Cannot compile EventAnalysis.h")


def file_identity(path):
    path = Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def tree_info(path):
    source = ROOT.TFile.Open(str(path), "READ")
    if not source or source.IsZombie():
        raise ValueError(f"Cannot open ROOT file: {path}")
    try:
        tree = source.Get("tr")
        if not tree or not tree.InheritsFrom("TTree"):
            raise ValueError(f"Missing event tree 'tr': {path}")
        return {"entries": int(tree.GetEntries()), "title": str(tree.GetTitle()),
                "branches": {str(leaf.GetName()): str(leaf.GetTypeName())
                             for leaf in tree.GetListOfLeaves()}}
    finally:
        source.Close()


def _command(args, cwd):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args!r}\n{result.stdout}\n{result.stderr}")
    return result.stdout + result.stderr


def rebuild_events(sorted_path, variant, run, output_root):
    """Compile the current builder in /tmp; publish a new, uniquely named run folder."""
    if variant not in VARIANTS or type(run) is not int or not 0 <= run <= 9999:
        raise ValueError("Use a supported variant and an integer run number in [0, 9999]")
    sorted_path = Path(sorted_path).resolve(strict=True)
    source_info = {**file_identity(sorted_path), **tree_info(sorted_path)}
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"{variant}_run{run:04d}_{stamp}_", dir=output_root))
    started = time.monotonic()
    files = [REPO / variant / "build" / name
             for name in ("Makefile", "Build.cpp", "Build.h", "main.cpp", "set.h")]
    files += [REPO / "analysis" / name for name in ("EventAnalysis.h", "notebook_analysis.py")]
    files += [REPO / variant / "sort" / name
              for name in ("LaBr3_cali.dat", "LaBr3_ts_offset.dat")]
    snapshots = {}
    for source in files:
        relative = source.relative_to(REPO)
        destination = output / "source_snapshot" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        snapshots[str(relative)] = file_identity(destination)
    log = ""
    try:
        with tempfile.TemporaryDirectory(prefix="labr3-real-build-") as temporary:
            work = Path(temporary)
            (work / "sort").mkdir()
            (work / "build").mkdir()
            (work / "sort" / f"run{run:04d}_sort.root").symlink_to(sorted_path)
            binary = work / "build_events"
            # Compile the recorded snapshot, so the manifest describes the executed source.
            build_source = output / "source_snapshot" / variant / "build"
            log += _command(["make", "-B", f"out={binary}"], build_source)
            log += _command([str(binary), str(run)], work / "build")
            built = output / f"run{run:04d}_build.root"
            shutil.copy2(work / "build" / built.name, built)
        if file_identity(sorted_path)["sha256"] != source_info["sha256"]:
            raise RuntimeError("Input changed during event building")
    except Exception as error:
        (output / "FAILED.txt").write_text(str(error), encoding="utf-8")
        raise
    finally:
        (output / "build.log").write_text(log, encoding="utf-8")
    git_head = _command(["git", "rev-parse", "HEAD"], REPO).strip()
    git_status = _command(["git", "status", "--short"], REPO).strip()
    provenance = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "variant_directory": variant, "run": run,
        "input_sorted": source_info, "rebuilt": {**file_identity(built), **tree_info(built)},
        "git_head": git_head, "git_status": git_status,
        "root_version": str(ROOT.gROOT.GetVersion()), "python_version": platform.python_version(),
        "source_snapshot": snapshots, "build_elapsed_seconds": time.monotonic() - started,
        "calibration_provenance": "Unknown for historical sorted input; current .dat files are reference snapshots only",
        "processing_scope": "Existing sorted ROOT -> rebuilt events -> spectra and local peak fits; BIN conversion and sorting were not rerun",
        "event_rule": "First-hit anchored, strict dt < 50000 ps; maximum multiplicity 8",
    }
    return built, output, provenance


def make_config(settings):
    load_core()
    config = ROOT.labr3.AnalysisConfig()
    allowed = {"channel_a", "channel_b", "board_a", "board_b", "energy_bins", "energy_low",
               "energy_high", "time_bins", "time_low", "time_high", "gates_a", "gates_b"}
    if set(settings) - allowed:
        raise ValueError(f"Unknown analysis settings: {sorted(set(settings) - allowed)}")
    for name, value in settings.items():
        if name.startswith("gates_"):
            for low, high in value:
                getattr(config, name).push_back(ROOT.labr3.EnergyWindow(low, high))
        else:
            setattr(config, name, value)
    config.Validate()
    return config


def analyze_events(path, settings):
    config = make_config(settings)
    reader = ROOT.labr3.EventReader(str(Path(path).resolve(strict=True)))
    return ROOT.labr3.Analyze(reader, config)


def histograms(result):
    return {str(item.first): item.second for item in result.spectra}


def histogram_arrays(histogram):
    count = histogram.GetNbinsX()
    edges = np.array([histogram.GetBinLowEdge(i) for i in range(1, count + 2)])
    values = np.array([histogram.GetBinContent(i) for i in range(1, count + 1)])
    return edges, values


def histogram_counts(histogram):
    return {"entries": int(histogram.GetEntries()),
            "in_range": int(histogram.Integral()),
            "underflow": int(histogram.GetBinContent(0)),
            "overflow": int(histogram.GetBinContent(histogram.GetNbinsX() + 1))}


def event_summary(result):
    return {"events": int(result.events), "hits": int(result.hits),
            "multiplicities": {str(i): int(result.multiplicities[i]) for i in range(1, 9)},
            "channel_pairs": int(result.channel_pairs), "selected_pairs": int(result.selected_pairs),
            "all_pair_times": histogram_counts(result.all_pair_times),
            "selected_times": histogram_counts(result.selected_times),
            "spectra": {name: histogram_counts(hist) for name, hist in histograms(result).items()}}


def old_event_counts(path):
    """Read only multiplicities for a historical comparison, without assuming correctness."""
    info = tree_info(path)
    frame = ROOT.RDataFrame("tr", str(path))
    distribution = frame.Histo1D(("old_m", "", 9, -.5, 8.5), "m")
    counts = {str(i): int(distribution.GetBinContent(i + 1)) for i in range(1, 9)}
    if sum(counts.values()) != info["entries"]:
        raise ValueError("Historical file contains unsupported multiplicities")
    return {"events": info["entries"], "hits": sum(int(m) * n for m, n in counts.items()),
            "multiplicities": counts, "title": info["title"]}


def fit_peak(histogram, fit_range, unit, minimum_counts=100):
    """Binned Poisson likelihood, Gaussian + nonnegative constant; no SciPy needed.

    The model predicts average bin content (ROOT option I), including empty bins.
    The requested endpoints must be histogram edges. Failed or boundary-limited
    fits retain diagnostics but do not receive a reported FWHM.
    """
    low, high = map(float, fit_range)
    edges, counts = histogram_arrays(histogram)
    if not (math.isfinite(low) and math.isfinite(high) and low < high):
        raise ValueError("Fit range must be finite and increasing")
    if not any(np.isclose(edges, low, rtol=0, atol=1e-9)) or not any(np.isclose(edges, high, rtol=0, atol=1e-9)):
        raise ValueError("Fit endpoints must match histogram bin edges")
    widths = np.diff(edges)
    if not np.allclose(widths, widths[0]):
        raise ValueError("Peak fits require uniform histogram bins")
    centers = (edges[:-1] + edges[1:]) / 2
    mask = (centers > low) & (centers < high)
    x, y = centers[mask], counts[mask]
    report = {"model": "Gaussian + constant background", "method": "Binned Poisson likelihood (ROOT L I R S Q N)",
              "requested_range": [low, high], "range": [low, high], "unit": unit,
              "counts_in_fit": int(y.sum()), "bins_in_fit": len(y),
              "usable": False, "status": "skipped", "reason": ""}
    if len(y) < 8 or y.sum() < minimum_counts or np.count_nonzero(y) < 4:
        report["reason"] = "Insufficient counts or occupied bins for a four-parameter peak fit"
        return report
    if np.any(y < 0):
        raise ValueError("Poisson fits require nonnegative counts")
    width = float(widths[0])
    background = float(np.quantile(y, .2))
    signal = np.maximum(y - background, 0.)
    if signal.sum() <= 0:
        report["reason"] = "No peak above the initial background estimate"
        return report
    mean = float(x[np.argmax(y)])
    sigma = float(np.sqrt(np.average((x - mean)**2, weights=signal)))
    sigma_min, sigma_max = width / 5, (high - low) / 2
    sigma = min(max(sigma, width), (high - low) / 4)
    function = ROOT.TF1(f"peak_{uuid.uuid4().hex}", "gaus(0)+[3]", low, high)
    maximum = max(float(y.max()) * 20, 100.)
    limits = [(0., maximum), (low, high), (sigma_min, sigma_max), (0., maximum)]
    function.SetParameters(max(float(y.max()) - background, 1.), mean, sigma, max(background, .1))
    for i, bounds in enumerate(limits):
        function.SetParLimits(i, *bounds)
    result = histogram.Fit(function, "LIRSQN")
    parameters = [float(function.GetParameter(i)) for i in range(4)]
    errors = [float(function.GetParError(i)) for i in range(4)]
    status, covariance_status = int(result), int(result.CovMatrixStatus())
    report.update(status=status, covariance_status=covariance_status,
                  parameters=dict(zip(("amplitude", "mean", "sigma", "background"),
                                      [v if math.isfinite(v) else None for v in parameters])),
                  parameter_errors=dict(zip(("amplitude", "mean", "sigma", "background"),
                                            [v if math.isfinite(v) else None for v in errors])))
    # A constant background consistent with zero is allowed; a constrained peak is not.
    at_boundary = any(min(p - a, b - p) <= 1e-4 * (b - a)
                      for p, (a, b) in zip(parameters[:3], limits[:3]))
    if status != 0 or not result.IsValid() or covariance_status < 2 or at_boundary or not all(
            math.isfinite(v) for v in parameters + errors) or errors[2] <= 0:
        report["reason"] = "Fit did not converge with a usable, unconstrained peak covariance"
        return report
    expected = np.array([function.Integral(float(left), float(right)) / width
                         for left, right in zip(edges[:-1][mask], edges[1:][mask])])
    if np.any(expected <= 0) or not np.all(np.isfinite(expected)):
        report["reason"] = "Invalid expected bin counts"
        return report
    terms = expected - y
    nonzero = y > 0
    terms[nonzero] += y[nonzero] * np.log(y[nonzero] / expected[nonzero])
    deviance = float(2 * terms.sum())
    ndf = len(y) - 4
    report.update(usable=True, reason="", mean=parameters[1], mean_error=errors[1],
                  sigma=parameters[2], sigma_error=errors[2],
                  fwhm=FWHM_FACTOR * parameters[2], fwhm_error=FWHM_FACTOR * errors[2],
                  covariance=[[float(result.CovMatrix(i, j)) for j in range(4)] for i in range(4)],
                  poisson_deviance=deviance, ndf=ndf, deviance_per_ndf=deviance / ndf,
                  quality_note=("Large residual deviance: this local model does not describe the peak adequately"
                                if deviance / ndf > 2 else
                                "Inspect residuals and fit-range dependence; convergence is not a physical validation"),
                  quality_flag="inspect_model" if deviance / ndf > 2 else "inspect_residuals")
    return report


def model_counts(edges, report):
    """Average the fitted model across each bin, matching ROOT option I."""
    p = report["parameters"]
    z = [(float(x) - p["mean"]) / (math.sqrt(2) * p["sigma"]) for x in edges]
    primitive = np.array([math.erf(value) for value in z])
    return p["background"] + (p["amplitude"] * p["sigma"] * math.sqrt(math.pi / 2)
                              * np.diff(primitive) / np.diff(edges))


def save_figure(figure, output, stem):
    for suffix in ("png", "pdf"):
        figure.savefig(Path(output) / f"{stem}.{suffix}", dpi=160, bbox_inches="tight")


def plot_energy(result, settings):
    import matplotlib.pyplot as plt
    spectra = histograms(result)
    figure, axes = plt.subplots(len(spectra), 1, figsize=(10, max(3, 3 * len(spectra))), squeeze=False)
    for axis, (name, histogram) in zip(axes[:, 0], sorted(spectra.items())):
        edges, values = histogram_arrays(histogram)
        axis.stairs(values, edges, linewidth=1, label=f"{name}: {int(histogram.GetEntries()):,} hits")
        for suffix in ("a", "b"):
            if name.endswith(f'_ch{settings[f"channel_{suffix}"]}'):
                for low, high in settings[f"gates_{suffix}"]:
                    axis.axvspan(low, high, color="tab:orange", alpha=.25)
        axis.set(yscale="log", ylim=(.7, max(float(values.max()), 1) * 2),
                 xlabel="Stored energy (calibration unverified)", ylabel="Counts / bin")
        axis.legend(); axis.grid(alpha=.2)
    figure.tight_layout()
    return figure


def plot_fits(items):
    """Show each local model together with Pearson residuals for visual checks."""
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(2, len(items), figsize=(max(7, 4 * len(items)), 6),
                               squeeze=False, height_ratios=[3, 1])
    for col, (name, histogram, report) in enumerate(items):
        top, bottom = axes[:, col]
        edges, counts = histogram_arrays(histogram)
        centers = (edges[:-1] + edges[1:]) / 2
        low, high = report["range"]
        top.stairs(counts, edges, label="Data")
        if report["usable"]:
            expected = model_counts(edges, report)
            mask = (centers > low) & (centers < high)
            top.plot(centers[mask], expected[mask], color="tab:red", label="Gaussian + background")
            bottom.plot(centers[mask], ((counts - expected) / np.sqrt(np.maximum(expected, 1e-12)))[mask], ".", ms=3)
            top.set_title(f'{name}\nFWHM = {report["fwhm"]:.3g} +/- {report["fwhm_error"]:.2g} {report["unit"]}\n'
                          f'Deviance / ndf = {report["deviance_per_ndf"]:.2f}'
                          + (' [check model]' if report['deviance_per_ndf'] > 2 else ''))
        else:
            top.set_title(f"{name}\nFit unavailable")
        top.set(xlim=(low, high), ylabel="Counts / bin")
        top.legend(fontsize=8)
        bottom.axhline(0, color="black", linewidth=.8)
        bottom.set(xlim=(low, high), xlabel=report["unit"], ylabel="Residual")
        top.grid(alpha=.2); bottom.grid(alpha=.2)
    figure.tight_layout()
    return figure


def plot_timing(result):
    import matplotlib.pyplot as plt
    figure, axis = plt.subplots(figsize=(10, 4))
    for hist, label in [(result.all_pair_times, "Channel pair, before energy gates"),
                        (result.selected_times, "After energy gates")]:
        edges, values = histogram_arrays(hist)
        axis.stairs(values, edges, label=f"{label}: {int(hist.GetEntries()):,}")
    axis.set(xlabel="t_B - t_A (ns)", ylabel="Counts / bin")
    if result.channel_pairs:
        axis.set_yscale("log")
        axis.set_ylim(bottom=.7)
    axis.legend(); axis.grid(alpha=.2); figure.tight_layout()
    return figure


def export_results(output, provenance, settings, result, fits, extra=None):
    output = Path(output)
    report = {"provenance": provenance, "analysis_settings": settings,
              "counts": event_summary(result), "fits": fits, "notes": extra or {}}
    (output / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                                   allow_nan=False) + "\n", encoding="utf-8")
    with (output / "fits.csv").open("w", newline="", encoding="utf-8") as destination:
        columns = ["name", "unit", "usable", "status", "counts_in_fit", "mean", "mean_error",
                   "fwhm", "fwhm_error", "poisson_deviance", "ndf", "deviance_per_ndf", "quality_flag", "reason"]
        writer = csv.DictWriter(destination, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for name, fit in fits.items():
            writer.writerow({"name": name, **fit})
    root_file = ROOT.TFile(str(output / "histograms.root"), "RECREATE")
    if root_file.IsZombie():
        raise OSError("Cannot create histogram output")
    try:
        for name, histogram in {**histograms(result), "all_pair_times": result.all_pair_times,
                                "selected_times": result.selected_times}.items():
            if histogram.Write(name) <= 0:
                raise OSError(f"Cannot write histogram: {name}")
        if root_file.TestBit(ROOT.TFile.kWriteError):
            raise OSError("ROOT histogram write failed")
    finally:
        root_file.Close()
    return report
