"""Batch export of event counts and energy/timing histograms (no peak fitting)."""

import argparse
import json
import math
from pathlib import Path
import sys


INTEGER_FIELDS = {"channel_a", "channel_b", "board_a", "board_b", "energy_bins", "time_bins"}
FLOAT_FIELDS = {"energy_low", "energy_high", "time_low", "time_high"}
GATE_FIELDS = {"gates_a", "gates_b"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate analysis setting: {key}")
        result[key] = value
    return result


def load_settings(path):
    """Validate JSON types before PyROOT can silently coerce an invalid value."""
    settings = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(settings, dict):
        raise ValueError("Analysis configuration must be a JSON object")
    unknown = set(settings) - INTEGER_FIELDS - FLOAT_FIELDS - GATE_FIELDS
    if unknown:
        raise ValueError(f"Unknown analysis settings: {sorted(unknown)}")
    for name, value in settings.items():
        if name in INTEGER_FIELDS:
            if type(value) is not int or not -(2**31) <= value < 2**31:
                raise ValueError(f"{name} must be a 32-bit integer")
            if name.startswith("channel_") and not 0 <= value <= 7:
                raise ValueError(f"{name} must be in [0, 7]")
            if name.startswith("board_") and value < -1:
                raise ValueError(f"{name} must be -1 or nonnegative")
            if name.endswith("_bins") and value <= 0:
                raise ValueError(f"{name} must be positive")
        elif name in FLOAT_FIELDS:
            finite_number(value, name)
        else:
            if not isinstance(value, list):
                raise ValueError(f"{name} must be a list of [low, high] gates")
            for gate in value:
                if not isinstance(gate, list) or len(gate) != 2:
                    raise ValueError(f"{name} must contain [low, high] gates")
                for endpoint in gate:
                    finite_number(endpoint, name)
                if gate[0] >= gate[1]:
                    raise ValueError(f"{name} gates must be increasing")
    # Check ranges that depend on defaults with the actual C++ configuration in
    # the worker; these checks catch all invalid explicitly supplied pairs early.
    for low, high in (("energy_low", "energy_high"), ("time_low", "time_high")):
        if low in settings and high in settings and settings[low] >= settings[high]:
            raise ValueError(f"{low} must be less than {high}")
    if "channel_a" in settings and settings.get("channel_b") == settings["channel_a"]:
        raise ValueError("Select two different channels")
    return settings


def finite_number(value, name):
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be finite and numeric")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite and numeric")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New directory for results.json, fits.csv and histograms.root")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from analysis import notebook_analysis as analysis
        analysis.ROOT.gROOT.SetBatch(True)
        config = analysis.make_config(settings)
        effective = {name: int(getattr(config, name)) for name in INTEGER_FIELDS}
        effective.update({name: float(getattr(config, name)) for name in FLOAT_FIELDS})
        effective.update({name: [[float(gate.low), float(gate.high)] for gate in getattr(config, name)]
                          for name in GATE_FIELDS})
        identity = analysis.file_identity(args.input)
        result = analysis.analyze_events(args.input, effective)
        if analysis.file_identity(args.input) != identity:
            raise RuntimeError("Analysis input changed during reading")
        args.output_dir.mkdir(parents=True, exist_ok=False)
        provenance = {"input_events": identity, "root_version": str(analysis.ROOT.gROOT.GetVersion()),
                      "task": "event_counts_and_histograms", "requested_settings": settings}
        report = analysis.export_results(args.output_dir, provenance, effective, result, {},
                                         {"scope": "No peak fits or detector-resolution inference"})
        print(json.dumps(report["counts"], ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(f"Analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
