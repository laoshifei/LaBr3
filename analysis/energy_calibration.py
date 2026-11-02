"""Fit two reference peaks in raw channel trees and archive linear calibration candidates."""

import argparse
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import shutil
import sys
import uuid

import numpy as np
import ROOT

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from analysis import notebook_analysis as analysis


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate configuration key: {key}")
        result[key] = value
    return result


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required).issubset(value) or set(value) - set(required) - set(optional):
        raise ValueError(f"Expected fields {sorted(required)}; optional {sorted(optional)}")


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Expected a finite numeric value")
    return float(value)


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Expected integer in [{low}, {high}]")


def validate_ranges(ranges):
    if not isinstance(ranges, list) or len(ranges) != 2:
        raise ValueError("Exactly two disjoint peak ranges are required")
    for bounds in ranges:
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError("Each peak range must contain two edges")
        low, high = map(number, bounds)
        if not -.5 <= low < high <= 65535.5 or high - low < 8:
            raise ValueError("Fit ranges need at least eight ADC bins inside the UShort_t range")
        if any(abs(edge + .5 - round(edge + .5)) > 1e-9 for edge in (low, high)):
            raise ValueError("Fit edges must be half integers; integer ADC values are bin centers")
    if ranges[0][1] > ranges[1][0]:
        raise ValueError("Peak ranges must be ordered and disjoint")


def load_config(path):
    config = json.loads(Path(path).read_bytes(), object_pairs_hook=unique_object)
    fields(config, ("schema_version", "variant", "run", "source_identification", "reference", "channels"),
           ("input_sha256",))
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("Calibration configuration schema_version must be 1")
    if config["variant"] not in analysis.VARIANTS:
        raise ValueError("Unknown variant label")
    integer(config["run"], 0, 2147483647)
    if not isinstance(config["source_identification"], str) or not config["source_identification"].strip():
        raise ValueError("Source identification and its basis must be stated")
    if "input_sha256" in config and (not isinstance(config["input_sha256"], str) or
                                     not re.fullmatch(r"[0-9a-f]{64}", config["input_sha256"])):
        raise ValueError("input_sha256 must contain 64 lowercase hexadecimal characters")
    reference = config["reference"]
    fields(reference, ("nuclide", "source_url", "pdf_sha256", "pdf_page", "energy_keV", "standard_uncertainty_keV"))
    if (not isinstance(reference["nuclide"], str) or not reference["nuclide"] or
            not isinstance(reference["source_url"], str) or not reference["source_url"].startswith("https://") or
            not isinstance(reference["pdf_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", reference["pdf_sha256"])):
        raise ValueError("Reference requires a nuclide, HTTPS source URL and PDF SHA-256")
    integer(reference["pdf_page"], 1, 100000)
    for name in ("energy_keV", "standard_uncertainty_keV"):
        if not isinstance(reference[name], list) or len(reference[name]) != 2:
            raise ValueError("Reference requires exactly two energies and uncertainties")
        for value in reference[name]:
            if number(value) < 0:
                raise ValueError("Negative reference value")
    if not 0 < reference["energy_keV"][0] < reference["energy_keV"][1]:
        raise ValueError("Reference energies must be positive and increasing")
    if not isinstance(config["channels"], list) or not config["channels"]:
        raise ValueError("Select at least one board/channel")
    selected = set()
    for channel in config["channels"]:
        fields(channel, ("board", "channel", "ranges", "range_checks"))
        integer(channel["board"], -32768, 32767)
        integer(channel["channel"], 0, 7)
        key = (channel["board"], channel["channel"])
        if key in selected:
            raise ValueError("Duplicate board/channel selection")
        selected.add(key)
        validate_ranges(channel["ranges"])
        if not isinstance(channel["range_checks"], dict):
            raise ValueError("range_checks must be a named object")
        for label, ranges in channel["range_checks"].items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", label) or label == "nominal":
                raise ValueError("Invalid range-check name")
            validate_ranges(ranges)
    return config


def read_spectrum(source, board, channel):
    name = f"tr_ch{channel:02d}"
    cycles = sorted({int(key.GetCycle()) for key in source.GetListOfKeys() if key.GetName() == name})
    if not cycles:
        raise ValueError(f"Missing raw channel tree: {name}")
    cycle = cycles[-1]
    tree = source.Get(f"{name};{cycle}")
    if not tree or not tree.InheritsFrom("TTree"):
        raise ValueError(f"Expected a raw channel TTree: {name}")
    for field, expected in (("board", "Short_t"), ("channel", "Short_t"), ("energy_ch", "UShort_t")):
        branch = tree.GetBranch(field)
        leaf = tree.GetLeaf(field)
        if (not branch or not leaf or branch.GetListOfLeaves().GetEntries() != 1 or
                leaf.GetLeafCount() or leaf.GetLenStatic() != 1 or leaf.GetTypeName() != expected):
            raise ValueError(f"{name}.{field} must be a scalar {expected}")
    frame = ROOT.RDataFrame(tree)
    wrong_channel = frame.Filter(f"channel != {channel}").Count()
    selected = frame.Filter(f"board == {board}")
    count = selected.Count()
    action = selected.Histo1D((f"spectrum_{uuid.uuid4().hex}", "", 65536, -.5, 65535.5), "energy_ch")
    histogram = action.GetValue().Clone(f"board{board}_ch{channel}")
    histogram.SetDirectory(0)
    if int(wrong_channel.GetValue()):
        raise ValueError(f"Record channel contradicts tree name {name}")
    selected_count = int(count.GetValue())
    if int(histogram.Integral(0, 65537)) != selected_count:
        raise ValueError("Spectrum count differs from selected raw records")
    return histogram, {"board": board, "channel": channel, "tree": name,
                       "tree_title": str(tree.GetTitle()), "available_cycles": cycles, "selected_cycle": cycle,
                       "tree_entries": int(tree.GetEntries()), "selected_records": selected_count,
                       "other_board_records": int(tree.GetEntries()) - selected_count,
                       "adc_bins": 65536, "adc_edges": [-.5, 65535.5]}


def two_point_candidate(fits, reference):
    """Propagate both peak mean/width covariances and independent reference energies."""
    if len(fits) != 2 or not all(fit.get("usable") for fit in fits):
        return None
    covariance = np.zeros((6, 6))
    values = []
    for index, fit in enumerate(fits):
        matrix = np.asarray(fit["covariance"], dtype=float)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError("Invalid peak covariance")
        covariance[2 * index:2 * index + 2, 2 * index:2 * index + 2] = matrix[np.ix_([1, 2], [1, 2])]
        values += [number(fit["mean"]), number(fit["sigma"])]
    energies = list(map(number, reference["energy_keV"]))
    errors = np.asarray(reference["standard_uncertainty_keV"], dtype=float)
    covariance[4:, 4:] = np.diag(errors**2)
    if not np.allclose(covariance, covariance.T) or np.linalg.eigvalsh(covariance).min() < -1e-12:
        raise ValueError("Peak covariance is not positive semidefinite")
    x1, s1, x2, s2 = values
    e1, e2 = energies
    if not x1 < x2 or min(s1, s2) <= 0:
        raise ValueError("Peak centroids must be increasing and widths positive")
    distance = x2 - x1
    slope = (e2 - e1) / distance
    offset = e1 - slope * x1
    dslope = np.array([slope / distance, 0, -slope / distance, 0, -1 / distance, 1 / distance])
    doffset = -x1 * dslope
    doffset[0] -= slope
    doffset[4] += 1
    jacobian = np.stack([doffset, dslope])
    coefficient_covariance = jacobian @ covariance @ jacobian.T
    peaks = []
    for index, sigma in enumerate((s1, s2)):
        width = analysis.FWHM_FACTOR * sigma * slope
        dwidth = analysis.FWHM_FACTOR * sigma * dslope
        dwidth[2 * index + 1] += analysis.FWHM_FACTOR * slope
        resolution = 100 * width / energies[index]
        dresolution = 100 * dwidth / energies[index]
        dresolution[4 + index] -= 100 * width / energies[index]**2
        peaks.append({"reference_energy_keV": energies[index], "fwhm_keV": width,
                      "fwhm_keV_stat_error": math.sqrt(max(0., float(dwidth @ covariance @ dwidth))),
                      "fwhm_over_reference_percent": resolution,
                      "resolution_percent_stat_error": math.sqrt(max(0., float(dresolution @ covariance @ dresolution)))})
    return {"status": "candidate_requires_review", "mapping": "E_keV = p0 + p1 * energy_ch",
            "p0": offset, "p1": slope, "p2": 0., "coefficient_order": ["p0", "p1"],
            "coefficient_covariance": coefficient_covariance.tolist(),
            "p0_stat_error": math.sqrt(max(0., float(coefficient_covariance[0, 0]))),
            "p1_stat_error": math.sqrt(max(0., float(coefficient_covariance[1, 1]))),
            "reference_centroids_adc": [x1, x2], "reference_interval_keV": energies,
            "calibration_ndf": 0, "linearity_validated": False,
            "requires_model_review": any(fit.get("quality_flag") == "inspect_model" for fit in fits),
            "uncertainty_assumptions": "Independent disjoint-peak fits and independent reference energies; within-peak mean/sigma covariance retained; model/range systematics excluded",
            "peaks": peaks}


def write_json(path, value):
    content = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def process(args):
    config = load_config(args.config)
    source_identity = analysis.file_identity(args.input)
    config_identity = analysis.file_identity(args.config)
    if config.get("input_sha256", source_identity["sha256"]) != source_identity["sha256"]:
        raise ValueError("Input SHA-256 differs from calibration configuration")
    reference_identity = analysis.file_identity(args.reference_pdf) if args.reference_pdf else None
    if reference_identity and reference_identity["sha256"] != config["reference"]["pdf_sha256"]:
        raise ValueError("Reference PDF SHA-256 differs from configuration")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "status": "running", "created_utc": datetime.now(timezone.utc).isoformat(),
                "input": source_identity, "configuration": config_identity, "reference_pdf": reference_identity,
                "input_unchanged": None, "source_snapshot": {},
                "software": {"python": platform.python_version(), "ROOT": ROOT.gROOT.GetVersion(), "numpy": np.__version__},
                "scope": "Selected raw board/channel spectra; candidate only, no runtime parameter files applied or written"}
    input_file = None
    try:
        write_json(output / "manifest.json", manifest)
        for path in (Path(__file__), Path(analysis.__file__)):
            original = analysis.file_identity(path)
            relative = "source_snapshot/analysis/" + path.name
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            if analysis.file_identity(destination)["sha256"] != original["sha256"]:
                raise ValueError("Source changed during snapshot")
            manifest["source_snapshot"][path.name] = {**original, "snapshot": relative}
        for original, name in ((config_identity, "configuration.json"), (reference_identity, "reference.pdf")):
            if original:
                shutil.copyfile(original["path"], output / name)
                if analysis.file_identity(output / name)["sha256"] != original["sha256"]:
                    raise ValueError("Configuration/reference changed during snapshot")
                original["snapshot"] = name
        input_file = ROOT.TFile.Open(source_identity["path"], "READ")
        if not input_file or input_file.IsZombie():
            raise ValueError("Cannot open raw ROOT input")
        histograms, results, rows = [], [], []
        for selected in config["channels"]:
            histogram, selection = read_spectrum(input_file, selected["board"], selected["channel"])
            histograms.append(histogram)
            item = {"selection": selection, "fits": {}}
            for label, bounds in {"nominal": selected["ranges"], **selected["range_checks"]}.items():
                fits = [analysis.fit_peak(histogram, window, "ADC channel") for window in bounds]
                candidate = two_point_candidate(fits, config["reference"])
                item["fits"][label] = {"peaks": fits, "candidate": candidate}
                for index, fit in enumerate(fits):
                    rows.append({"board": selected["board"], "channel": selected["channel"], "range_case": label,
                                 "reference_energy_keV": config["reference"]["energy_keV"][index],
                                 **{name: fit.get(name) for name in ("usable", "mean", "mean_error", "fwhm", "fwhm_error", "deviance_per_ndf", "quality_flag")}})
            available = [entry["candidate"] for entry in item["fits"].values() if entry["candidate"]]
            item["range_sensitivity"] = {name: [min(c[name] for c in available), max(c[name] for c in available)]
                                         for name in ("p0", "p1")} if available else None
            item["range_sensitivity_note"] = "Range sensitivity only; not an independent linearity test or a systematic-error estimate"
            results.append(item)
        input_file.Close()
        input_file = None
        write_json(output / "results.json", {"configuration": config, "channels": results,
                                             "selected_records": sum(item["selection"]["selected_records"] for item in results)})
        with (output / "fits.csv").open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with ROOT.TFile(str(output / "spectra.root"), "CREATE") as archive:
            if archive.IsZombie():
                raise ValueError("Cannot create spectrum archive")
            for histogram in histograms:
                if histogram.Write() <= 0:
                    raise ValueError("Cannot write spectrum archive")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        manifest["software"]["matplotlib"] = matplotlib.__version__
        for item, histogram in zip(results, histograms):
            selection = item["selection"]
            stem = f"board{selection['board']}_ch{selection['channel']}"
            fits = item["fits"]["nominal"]["peaks"]
            figure = analysis.plot_fits([(f"{stem}: {energy:.3f} keV candidate", histogram, fit)
                                         for energy, fit in zip(config["reference"]["energy_keV"], fits)])
            figure.savefig(output / (stem + "_fits.png"), dpi=160, bbox_inches="tight")
            plt.close(figure)
        manifest["input_unchanged"] = analysis.file_identity(source_identity["path"]) == source_identity
        if not manifest["input_unchanged"]:
            raise ValueError("Input changed during calibration analysis")
        for original in [config_identity, reference_identity, *manifest["source_snapshot"].values()]:
            if original:
                for path in (original["path"], output / original["snapshot"]):
                    current = analysis.file_identity(path)
                    if any(current[key] != original[key] for key in ("bytes", "sha256")):
                        raise ValueError(f"Analysis source/configuration/reference changed: {path}")
        manifest["outputs"] = [analysis.file_identity(path) for path in sorted(output.iterdir())
                               if path.is_file() and path.name != "manifest.json"]
        manifest["status"] = "succeeded"
    except (Exception, KeyboardInterrupt) as error:
        manifest["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        manifest["error"] = str(error) or "Interrupted"
        raise
    finally:
        if input_file:
            input_file.Close()
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "manifest.json", manifest)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--reference-pdf", type=Path, help="Optional local copy matching the pinned reference hash")
    args = parser.parse_args()
    try:
        print(f"Calibration candidate analysis saved to {process(args)}")
    except KeyboardInterrupt:
        print("Calibration analysis interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Calibration analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
