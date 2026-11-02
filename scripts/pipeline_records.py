"""Source archives, build identities and count contracts for the batch pipeline."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def file_identity(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def verify_file(identity, path=None):
    current = file_identity(path or identity["path"])
    if any(current[key] != identity[key] for key in ("bytes", "sha256")):
        raise ValueError(f"File changed: {current['path']}")


def archived_path(directory, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("Archive paths must be relative")
    path = (directory / relative).resolve(strict=True)
    try:
        path.relative_to(directory.resolve(strict=True))
    except ValueError:
        raise ValueError(f"Archive path escapes its directory: {relative}") from None
    return path


def snapshot_sources(repo, directory, variant, stages):
    names = {"scripts/run_pipeline.py", "scripts/pipeline_records.py", "scripts/replay_pipeline.py", "scripts/bin_inputs.py"}
    if any(stage != "analysis" for stage in stages):
        names.update(("Makefile", "common/CommandLine.h", "common/StageReport.h"))
    for stage in stages:
        if stage == "analysis":
            names.update(("analysis/run_analysis.py", "analysis/notebook_analysis.py", "analysis/EventAnalysis.h"))
        else:
            for path in (repo / variant / stage).iterdir():
                if path.name == "Makefile" or path.suffix in (".cpp", ".h"):
                    names.add(path.relative_to(repo).as_posix())
    records = {}
    for name in sorted(names):
        source = repo / name
        identity = file_identity(source)
        relative = "source_snapshot/" + name
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        verify_file(identity, destination)
        records[name] = {**identity, "snapshot": relative}
    # A concurrent edit must not silently produce a mixture of source versions.
    for identity in records.values():
        verify_file(identity)
    return records


def verify_archive(directory, records):
    for identity in records.values():
        verify_file(identity, archived_path(directory, identity["snapshot"]))


def tool_info(command, root_config=False):
    found = shutil.which(command)
    if found is None:
        raise ValueError(f"Build tool not found: {command}")
    identity = file_identity(found)
    options = ("--version", "--cflags", "--libs") if root_config else ("--version",)
    for option in options:
        result = subprocess.run([identity["path"], option], text=True, capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError(f"Build tool failed: {command} {option}: {result.stderr.strip()}")
        identity[option[2:]] = result.stdout.strip()
    return identity


def build_environment():
    environment = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MAKEFILES"):
        environment.pop(name, None)
    relevant = ("PATH", "ROOTSYS", "LD_LIBRARY_PATH", "LIBRARY_PATH", "CPATH", "CPLUS_INCLUDE_PATH",
                "C_INCLUDE_PATH", "SOURCE_DATE_EPOCH")
    return environment, {name: environment.get(name) for name in relevant}


def read_stage_counts(stage, log, directory, required):
    if stage == "analysis":
        result = json.loads((directory / "analysis/results.json").read_text(encoding="utf-8"))["counts"]
        counts = {"input_events": result["events"], "input_hits": result["hits"],
                  "channel_pairs": result["channel_pairs"], "selected_pairs": result["selected_pairs"]}
    else:
        reports = []
        with log.open(encoding="utf-8", errors="replace") as source:
            for line in source:
                if line.startswith("LABR3_STATS "):
                    reports.append(json.loads(line[len("LABR3_STATS "):]))
        if not reports and not required:
            return None
        if len(reports) != 1 or reports[0].get("version") != 1 or reports[0].get("stage") != stage:
            raise ValueError(f"Missing or invalid count report from {stage}")
        counts = reports[0]["counts"]
    if not isinstance(counts, dict) or any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError(f"Invalid counters from {stage}")
    expected = {"raw2root": ("input_records", "output_hits"), "sort": ("input_hits", "output_hits"),
                "alignment": ("input_hits", "matched_pairs"),
                "build": ("input_hits", "output_hits", "output_events", *(f"events_m{i}" for i in range(1, 9))),
                "analysis": ("input_events", "input_hits", "channel_pairs", "selected_pairs")}[stage]
    if not set(expected).issubset(counts):
        raise ValueError(f"Incomplete counters from {stage}")
    if stage == "raw2root" and counts["input_records"] != counts["output_hits"]:
        raise ValueError("Conversion count mismatch")
    if stage in ("sort", "build") and counts["input_hits"] != counts["output_hits"]:
        raise ValueError(f"{stage} did not conserve hits")
    if stage == "build" and (sum(counts[f"events_m{i}"] for i in range(1, 9)) != counts["output_events"] or
                             sum(i * counts[f"events_m{i}"] for i in range(1, 9)) != counts["output_hits"]):
        raise ValueError("Event multiplicity counters do not conserve events/hits")
    if stage == "alignment" and counts["matched_pairs"] > counts["input_hits"]:
        raise ValueError("Alignment matches exceed input hits")
    if stage == "analysis" and not counts["selected_pairs"] <= counts["channel_pairs"] <= counts["input_events"]:
        raise ValueError("Invalid analysis selection counters")
    return counts


def check_handoff(stage, counts, upstream):
    if counts is None or upstream is None:
        return
    if "input_hits" in counts and counts["input_hits"] != upstream["output_hits"]:
        raise ValueError(f"Hit count mismatch before {stage}")
    if stage == "analysis" and counts["input_events"] != upstream["output_events"]:
        raise ValueError("Event count mismatch before analysis")
