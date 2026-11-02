"""Read local acquisition metadata and parameter provenance; never alter source files."""

import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


REPO = Path(__file__).resolve().parents[1]
VARIANTS = ("DT5720_DPP_PSD", "DT5730_DPP_PSD", "DT5730_DPP_PHA")
BOARD_FIELDS = ("id", "modelName", "serialNumber", "active", "adcBitCount", "sampleTime",
                "channelCount", "dppType")
ACQUISITION_FIELDS = ("runId", "acquisitionMode", "timedRunEnabled", "timedRunDuration",
                      "saveRaw", "saveFiltered", "saveUnfiltered", "fileFormatList",
                      "energyFormatList", "savingOptionList", "limitedFileSize", "fileSize")
PARAMETERS = {"SRV_PARAM_CH_ENABLED", "SRV_PARAM_CH_THRESHOLD", "SRV_PARAM_CH_POLARITY",
              "SRV_PARAM_CH_BLINE_DCOFFSET", "SRV_PARAM_CH_GATE", "SRV_PARAM_CH_GATESHORT",
              "SRV_PARAM_CH_GATEPRE", "SRV_PARAM_RECLEN", "SRV_PARAM_CH_PRETRG",
              "SRV_PARAM_CH_TIME_OFFSET", "SRV_PARAM_CH_SELF_TRG_ENABLE", "SRV_PARAM_WAVEFORMS",
              "SW_PARAMETER_CH_ENERGY_CALIBRATION_P0", "SW_PARAMETER_CH_ENERGY_CALIBRATION_P1",
              "SW_PARAMETER_CH_ENERGY_CALIBRATION_P2", "SW_PARAMETER_CH_ENERGY_CALIBRATION_UDM"}
INFO_FIELDS = {"id", "time.start", "time.stop", "time.real"}
INFO_CALIBRATION = re.compile(r"board\.[\w-]+\.\d+\.calibration\.energy\.(?:c[012]|uom)")


def identity(path, repo, content):
    return {"path": path.relative_to(repo).as_posix(), "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest()}


def scalar(node, path):
    """Read only the selected element, never descriptor defaults or other descendants."""
    matches = node.findall(path)
    if len(matches) > 1:
        raise ValueError(f"Repeated XML field: {path}")
    return None if not matches else (matches[0].text or "").strip()


def parameter_values(entries, nested):
    result = {}
    for entry in entries:
        key = scalar(entry, "key")
        if key not in PARAMETERS:
            continue
        if key in result:
            raise ValueError(f"Repeated XML parameter: {key}")
        value = scalar(entry, "value/value" if nested else "value")
        if value is None:
            raise ValueError(f"Missing actual XML parameter value: {key}")
        result[key] = {"value": value, "unit": scalar(entry, "value/descriptor/udm") if nested else None}
    return dict(sorted(result.items()))


def settings_metadata(content):
    root = ET.fromstring(content)
    if root.tag != "configuration":
        raise ValueError("Expected a configuration XML root")
    result = {"acquisition": {name: scalar(root, "acquisitionMemento/" + name)
                              for name in ACQUISITION_FIELDS}, "boards": []}
    board_ids = set()
    for board in root.findall("board"):
        values = {name: scalar(board, name) for name in BOARD_FIELDS}
        if values["id"] is None or values["id"] in board_ids:
            raise ValueError("Missing or duplicate XML board id")
        board_ids.add(values["id"])
        values["parameters"] = parameter_values(board.findall("parameters/entry"), nested=True)
        values["channel_overrides"] = {}
        for channel in board.findall("channel"):
            index = scalar(channel, "index")
            if index is None or index in values["channel_overrides"]:
                raise ValueError("Missing or duplicate XML channel index")
            values["channel_overrides"][index] = parameter_values(channel.findall("values/entry"), nested=False)
        result["boards"].append(values)
    return result


def info_metadata(content):
    fields = {}
    for line in content.decode("utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if not separator or (key not in INFO_FIELDS and not INFO_CALIBRATION.fullmatch(key)):
            continue
        if key in fields:
            raise ValueError(f"Repeated run.info field: {key}")
        fields[key] = value.strip()
    elapsed = None
    if fields.get("time.start") and fields.get("time.stop"):
        pattern = "%Y/%m/%d %H:%M:%S.%f%z"
        start, stop = (datetime.strptime(fields[key], pattern) for key in ("time.start", "time.stop"))
        elapsed = (stop - start).total_seconds()
        if elapsed < 0:
            raise ValueError("run.info stop time precedes start time")
    return {"fields": fields, "wall_elapsed_seconds_from_endpoints": elapsed,
            "live_time_seconds": None, "acquisition_complete": None}


def parameter_table(content, calibration):
    names = ("p0", "p1", "p2", "chi2") if calibration else ("offset_ps",)
    rows = {}
    for line_number, line in enumerate(content.decode("utf-8").splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != len(names) + 1:
            raise ValueError(f"Parameter column count at line {line_number}")
        channel = int(fields[0])
        if channel not in range(8) or channel in rows:
            raise ValueError(f"Invalid or duplicate parameter channel at line {line_number}")
        if not all(Decimal(value).is_finite() for value in fields[1:]):
            raise ValueError(f"Non-finite parameter at line {line_number}")
        # Keep the literal values: a numeric table does not establish physical calibration.
        rows[channel] = dict(zip(names, fields[1:]))
    if set(rows) != set(range(8)):
        raise ValueError("Parameter table must contain channels 0 through 7")
    return {str(channel): rows[channel] for channel in sorted(rows)}


def git_output(repo, *arguments):
    try:
        command = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *arguments],
                                 capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return command.stdout if command.returncode == 0 else None


def history(repo, path, content):
    relative = path.relative_to(repo).as_posix()
    log = git_output(repo, "log", "--format=%H", "--", relative)
    commits = [] if log is None else log.decode("ascii").splitlines()
    head_content = git_output(repo, "show", "HEAD:" + relative)
    return {"path_history_available": log is not None,
            "oldest_change_at_current_path": commits[-1] if commits else None,
            "latest_change_at_current_path": commits[0] if commits else None,
            "matches_head": None if head_content is None else head_content == content}


def audit(repo):
    repo = repo.resolve(strict=True)
    head = git_output(repo, "rev-parse", "HEAD")
    report = {"schema_version": 1, "repository_head": None if head is None else head.decode("ascii").strip(),
              "audit_tool": {"path": "scripts/audit_metadata.py",
                             "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              "scope": "Selected metadata and parameter files only; no BIN/ROOT records decoded",
              "parameters": [], "runs": []}
    observed = []

    def read(path):
        content = path.read_bytes()
        source = identity(path, repo, content)
        observed.append(source)
        return source, content

    for variant in VARIANTS:
        for calibration, name in ((True, "LaBr3_cali.dat"), (False, "LaBr3_ts_offset.dat")):
            path = repo / variant / "sort" / name
            entry = {"variant": variant, "kind": "energy" if calibration else "time_offset",
                     "path": path.relative_to(repo).as_posix(), "present": path.is_file(),
                     "source_run": None, "derivation": None, "validated_for_runs": None}
            if entry["present"]:
                source, content = read(path)
                entry.update(file=source, rows=parameter_table(content, calibration), git=history(repo, path, content))
            report["parameters"].append(entry)
        daq = repo / "data" / variant / "DAQ"
        directories = {path.parent for name in ("settings.xml", "run.info") for path in daq.glob("*/" + name)}
        for directory in sorted(directories):
            run = {"directory": directory.relative_to(repo).as_posix(), "variant_directory": variant,
                   "settings": None, "run_info": None}
            for name, field, parse in (("settings.xml", "settings", settings_metadata), ("run.info", "run_info", info_metadata)):
                path = directory / name
                if path.is_file():
                    source, content = read(path)
                    run[field] = {"file": source, **parse(content)}
            report["runs"].append(run)
    for source in observed:
        path = repo / source["path"]
        if identity(path, repo, path.read_bytes()) != source:
            raise ValueError(f"Metadata input changed during audit: {source['path']}")
    report["source_files_unchanged"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output", type=Path, required=True, help="New JSON report; existing files are refused")
    args = parser.parse_args()
    try:
        result = audit(args.repo)
        content = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(content)
        print(f"Recorded {len(result['parameters'])} parameter paths and {len(result['runs'])} metadata directories: {args.output}")
    except Exception as error:
        print(f"Metadata audit failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
