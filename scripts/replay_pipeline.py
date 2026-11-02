"""Verify an archived successful run and repeat it in a new result directory."""

import argparse
import json
import os
from pathlib import Path
import sys

import pipeline_records as records
import bin_inputs


def replay_command(args):
    manifest_path = args.manifest.resolve(strict=True)
    directory = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") not in (2, 3) or manifest.get("status") != "succeeded":
        raise ValueError("Replay requires a successful schema-version-2/3 run")
    try:
        args.output_dir.resolve().relative_to(directory)
    except ValueError:
        pass
    else:
        raise ValueError("Replay output must be outside the original run directory")
    sources = manifest["source_snapshot"]
    required = {"scripts/run_pipeline.py", "scripts/pipeline_records.py", "scripts/replay_pipeline.py"}
    if manifest["schema_version"] == 3:
        required.add("scripts/bin_inputs.py")
    if not required.issubset(sources):
        raise ValueError("Archive is missing the recorded replay sources")
    if any(identity["snapshot"] != "source_snapshot/" + name for name, identity in sources.items()):
        raise ValueError("Source archive paths do not match the recorded layout")
    records.verify_archive(directory, sources)
    records.verify_archive(directory, manifest["parameters"])
    driver = records.archived_path(directory, "source_snapshot/scripts/run_pipeline.py")
    if "input_set" in manifest:
        if args.input is not None:
            raise ValueError("Use --input-manifest to relocate multi-file inputs")
        records.verify_archive(directory, {name: manifest["input_set"][name] for name in
                                           ("manifest", "resolved_manifest", "report")})
        archived = records.archived_path(directory, manifest["input_set"]["resolved_manifest"]["snapshot"])
        source = args.input_manifest or archived
        bin_inputs.verify_relocation(source, archived)
        input_option = "--input-manifest"
    else:
        if args.input_manifest is not None:
            raise ValueError("A single-file run cannot be replayed with --input-manifest")
        source = args.input or Path(manifest["input"]["path"])
        records.verify_file(manifest["input"], source)
        input_option = "--input"
    request = manifest["execution"]
    command = [sys.executable, "-B", str(driver), "--variant", manifest["variant"], "--run", str(manifest["run"]),
               input_option, str(source.resolve()), "--input-kind", manifest["input_kind"],
               "--stages", ",".join(manifest["requested_stages"]), "--output-dir", str(args.output_dir.resolve()),
               "--jobs", str(args.jobs if args.jobs is not None else request["jobs"]), "--replay-of", str(manifest_path)]
    for name, identity in manifest["parameters"].items():
        if name not in ("calibration", "offsets", "analysis_config"):
            raise ValueError(f"Unknown archived parameter: {name}")
        command += ["--" + name.replace("_", "-"), str(records.archived_path(directory, identity["snapshot"]))]
    if request["reference"] is not None:
        command += ["--reference", str(request["reference"])]
    timeout = args.timeout if args.timeout is not None else request["timeout"]
    if timeout is not None:
        command += ["--timeout", str(timeout)]
    if manifest["software"]["build_origin"] == "prebuilt":
        binaries = manifest["software"]["binaries"]
        expected = set(manifest["requested_stages"]) - {"analysis"}
        if set(binaries) != expected or any(identity["snapshot"] != f"executables/{manifest['variant']}/{stage}"
                                           for stage, identity in binaries.items()):
            raise ValueError("Prebuilt archive does not match the recorded programs")
        records.verify_archive(directory, binaries)
        command += ["--bin-dir", str(directory / "executables")]
        if args.root_config or args.cxx:
            raise ValueError("Compiler options do not apply to a prebuilt-binary replay")
    elif manifest["software"]["build_origin"] == "snapshot":
        tools = manifest["software"].get("build", {}).get("tools", {})
        for name, override in (("root_config", args.root_config), ("cxx", args.cxx)):
            value = override or tools.get(name, {}).get("path")
            if value:
                command += ["--" + name.replace("_", "-"), value]
    else:
        raise ValueError("Unknown archived build origin")
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--input", type=Path, help="Relocated single input; its SHA-256 must match the archive")
    inputs.add_argument("--input-manifest", type=Path, help="Relocated BIN paths, retaining all archived declarations and hashes")
    parser.add_argument("--root-config", help="Override the archived ROOT configuration executable")
    parser.add_argument("--cxx", help="Override the archived compiler executable")
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()
    try:
        command = replay_command(args)
        os.execv(sys.executable, command)
    except Exception as error:
        print(f"Replay failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
