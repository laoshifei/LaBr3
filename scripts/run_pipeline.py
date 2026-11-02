"""Run selected LaBr3 stages on one explicit input, with isolated outputs and logs."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[1]
VARIANTS = ("DT5720_DPP_PSD", "DT5730_DPP_PSD", "DT5730_DPP_PHA")
STAGES = ("raw2root", "alignment", "sort", "build", "analysis")
INPUT_KIND = {"raw2root": "bin", "alignment": "raw", "sort": "raw", "build": "sorted", "analysis": "events"}
OUTPUT_KIND = {"raw2root": "raw", "sort": "sorted", "build": "events"}
DEFAULT_STAGES = {"bin": "raw2root,sort,build", "raw": "sort,build", "sorted": "build", "events": "analysis"}
OUTPUT_NAME = {"raw2root": "raw.root", "alignment": "alignment.root", "sort": "sorted.root", "build": "events.root"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def file_identity(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def write_manifest(directory, manifest):
    temporary = directory / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(directory / "manifest.json")


def parse_stages(text, input_kind):
    stages = text.split(",")
    if not stages or any(stage not in STAGES for stage in stages) or len(stages) != len(set(stages)):
        raise ValueError("Stages must be distinct names from: " + ",".join(STAGES))
    available = {input_kind}
    previous = -1
    for stage in stages:
        if STAGES.index(stage) <= previous or INPUT_KIND[stage] not in available:
            raise ValueError(f"Stage {stage} is out of order or lacks {INPUT_KIND[stage]} input")
        previous = STAGES.index(stage)
        if stage in OUTPUT_KIND:
            available.add(OUTPUT_KIND[stage])
    return stages


def reject_split_input(path):
    """Reject recognizable CAEN continuations and numeric sibling volumes.

    This does not certify completeness: a renamed or isolated first volume cannot
    be distinguished from a complete file without the future input manifest.
    """
    match = re.fullmatch(r"(.*?run_\d+)(?:_(\d+))?", path.stem, flags=re.IGNORECASE)
    if match and match.group(2) is not None:
        raise ValueError("Split BIN continuation is unsupported (P1-07): " + path.name)
    base = match.group(1) if match else path.stem
    siblings = [item for item in path.parent.iterdir() if item.suffix.lower() == ".bin"]
    if not match:
        numbered = re.fullmatch(r"(.+)_(\d+)", base)
        if numbered and any(item.stem.lower() == numbered.group(1).lower() for item in siblings):
            base = numbered.group(1)
    related = [item.name for item in siblings if item != path and
               re.fullmatch(re.escape(base) + r"(?:_\d+)?", item.stem, flags=re.IGNORECASE)]
    if related:
        raise ValueError("Related BIN volumes found; split input is unsupported (P1-07): " + ", ".join(sorted(related)))


def parser():
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--variant", required=True, choices=VARIANTS)
    result.add_argument("--run", required=True, type=int, help="Run label (0..2147483647), not used to infer paths")
    result.add_argument("--input", required=True, type=Path)
    result.add_argument("--input-kind", choices=DEFAULT_STAGES, default="bin",
                        help="bin, raw channel trees, sorted hits, or built events (default: bin)")
    result.add_argument("--stages", help="Comma-separated stages in processing order; alignment is optional")
    result.add_argument("--output-dir", required=True, type=Path, help="New result directory; existing paths are refused")
    result.add_argument("--calibration", type=Path, help="Explicit energy calibration, required for sort")
    result.add_argument("--offsets", type=Path, help="Explicit timestamp offsets, required for sort")
    result.add_argument("--reference", type=int, help="Reference channel (0..7), only with alignment")
    result.add_argument("--analysis-config", type=Path, help="JSON settings, required for analysis")
    result.add_argument("--bin-dir", type=Path, default=REPO / ".build",
                        help="Directory containing <variant>/<stage> executables (default: project .build)")
    result.add_argument("--timeout", type=float, help="Optional time limit in seconds per stage")
    return result


def prepare(args):
    if not 0 <= args.run <= 2147483647:
        raise ValueError("Run number must be in [0, 2147483647]")
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        raise ValueError("Timeout must be finite and positive")
    stages = parse_stages(args.stages if args.stages is not None else DEFAULT_STAGES[args.input_kind], args.input_kind)
    for option, needed in (("calibration", "sort"), ("offsets", "sort"), ("analysis_config", "analysis")):
        if (getattr(args, option) is not None) != (needed in stages):
            raise ValueError(f"--{option.replace('_', '-')} is required exactly when {needed} is selected")
    if args.reference is not None and ("alignment" not in stages or not 0 <= args.reference <= 7):
        raise ValueError("--reference requires alignment and a channel in [0, 7]")
    source = file_identity(args.input)
    if args.input_kind == "bin":
        reject_split_input(Path(source["path"]))
    if os.path.lexists(args.output_dir):
        raise ValueError(f"Output path already exists: {args.output_dir}")
    parameters = {name: file_identity(getattr(args, name)) for name in
                  ("calibration", "offsets", "analysis_config") if getattr(args, name) is not None}
    if "analysis_config" in parameters:
        sys.path.insert(0, str(REPO))
        from analysis.run_analysis import load_settings
        load_settings(parameters["analysis_config"]["path"])
    binaries = {}
    for stage in stages:
        if stage == "analysis":
            continue
        binary = args.bin_dir / args.variant / stage
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError(f"Missing executable: {binary}. Build it with make {args.variant}/{stage}")
        binaries[stage] = file_identity(binary)
    return stages, source, parameters, binaries


def git_metadata():
    result = {}
    for name, arguments in (("head", ["rev-parse", "HEAD"]), ("status", ["status", "--short"])):
        try:
            command = subprocess.run(["git", "-C", str(REPO), *arguments], text=True,
                                     capture_output=True, timeout=10)
            result[name] = command.stdout.strip() if command.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            result[name] = None
    return result


def stop_process(process):
    # A separate session lets timeout/cancellation stop descendants as well.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # A child may exit on SIGTERM while its descendants ignore it.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def execute(command, directory, log, timeout, record):
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=directory, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            record["returncode"] = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stop_process(process)
            record["returncode"] = process.returncode
            raise
    if record["returncode"] != 0:
        raise RuntimeError(f"{record['name']} exited with status {record['returncode']}; see {log}")


def run(args, prepared):
    stages, source, parameters, binaries = prepared
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "status": "running", "created_utc": utc_now(),
                "variant": args.variant, "run": args.run, "input_kind": args.input_kind,
                "input": source, "input_unchanged": None,
                "scope": {"input_files": 1, "acquisition_complete": None,
                          "description": "Selected stages for one file; acquisition completeness is not established"},
                "requested_stages": stages, "parameters": parameters,
                "software": {"python": platform.python_version(), "git": git_metadata(), "binaries": binaries,
                             "driver": file_identity(__file__),
                             "build_origin": "Prebuilt binaries; checkout metadata does not establish their build provenance"},
                "stages": [{"name": stage, "status": "not_run"} for stage in stages]}
    active = None
    started = time.monotonic()
    try:
        write_manifest(directory, manifest)
        (directory / "logs").mkdir()
        snapshot_dir = directory / "parameters"
        snapshot_dir.mkdir()
        snapshots = {}
        for name, identity in parameters.items():
            destination = snapshot_dir / (name + (".json" if name == "analysis_config" else ".dat"))
            shutil.copyfile(identity["path"], destination)
            if file_identity(destination)["sha256"] != identity["sha256"]:
                raise RuntimeError(f"Parameter changed while copying: {identity['path']}")
            identity["snapshot"] = str(destination.relative_to(directory))
            snapshots[name] = destination
        available = {args.input_kind: Path(source["path"])}
        for index, active in enumerate(manifest["stages"], 1):
            stage = active["name"]
            source_path = available[INPUT_KIND[stage]]
            if stage == "analysis":
                destination = directory / "analysis"
                command = [sys.executable, "-B", str(REPO / "analysis/run_analysis.py"),
                           "--input", str(source_path), "--config", str(snapshots["analysis_config"]),
                           "--output-dir", str(destination)]
                expected = [destination / name for name in ("results.json", "fits.csv", "histograms.root")]
            else:
                destination = directory / OUTPUT_NAME[stage]
                command = [binaries[stage]["path"], "--input", str(source_path), "--output", str(destination)]
                if stage == "sort":
                    command += ["--calibration", str(snapshots["calibration"]), "--offsets", str(snapshots["offsets"])]
                elif stage == "alignment" and args.reference is not None:
                    command += ["--reference", str(args.reference)]
                expected = [destination]
            log = directory / "logs" / f"{index:02d}-{stage}.log"
            active.update(status="running", started_utc=utc_now(), command=command,
                          input=str(source_path), log=str(log.relative_to(directory)))
            write_manifest(directory, manifest)
            print(f"[{index}/{len(stages)}] {stage}: {log}", flush=True)
            stage_started = time.monotonic()
            try:
                execute(command, directory, log, args.timeout, active)
                for path in expected:
                    if not path.is_file() or path.stat().st_size == 0:
                        raise RuntimeError(f"{stage} did not produce its expected output: {path}")
                active["outputs"] = [file_identity(path) for path in expected]
                active["status"] = "succeeded"
                if stage in OUTPUT_KIND:
                    available[OUTPUT_KIND[stage]] = destination
            finally:
                active["finished_utc"] = utc_now()
                active["elapsed_seconds"] = time.monotonic() - stage_started
            write_manifest(directory, manifest)
        active = None
        manifest["input_unchanged"] = file_identity(source["path"]) == source
        if not manifest["input_unchanged"]:
            raise RuntimeError("Input changed during processing")
        for identity in binaries.values():
            if file_identity(identity["path"]) != identity:
                raise RuntimeError(f"Executable changed during processing: {identity['path']}")
        manifest["status"] = "succeeded"
    except (Exception, KeyboardInterrupt) as error:
        manifest["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        manifest["error"] = str(error) or "Interrupted"
        if active is not None and active["status"] == "running":
            active["status"] = manifest["status"]
            active["error"] = manifest["error"]
        raise
    finally:
        manifest["finished_utc"] = utc_now()
        manifest["elapsed_seconds"] = time.monotonic() - started
        write_manifest(directory, manifest)
        print(f"Pipeline {manifest['status']}: {directory / 'manifest.json'}", flush=True)
    return directory


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        prepared = prepare(args)
        run(args, prepared)
    except KeyboardInterrupt:
        print("Pipeline interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        return 1
    return 0


def terminate(signum, frame):
    raise KeyboardInterrupt(f"Received {signal.Signals(signum).name}")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
