"""Validate an explicit BIN manifest and stream its records into one headed BIN."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import signal
import struct
import sys
import tempfile

import pipeline_records as records


VARIANTS = ("DT5720_DPP_PSD", "DT5730_DPP_PSD", "DT5730_DPP_PHA")
EXPORTS = {"RAW", "FILTERED", "UNFILTERED", "CUSTOM"}
PREFIX = struct.Struct("<hhq")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate manifest key: {key}")
        result[key] = value
    return result


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required).issubset(value) or set(value) - set(required) - set(optional):
        raise ValueError(f"Expected manifest fields {sorted(required)}; optional {sorted(optional)}")


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def load_manifest(path, *, content=None, check_files=True):
    path = Path(path).resolve()
    document = json.loads(path.read_bytes() if content is None else content, object_pairs_hook=unique_object)
    fields(document, ("schema_version", "variant", "run", "export", "streams"))
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("BIN manifest schema_version must be 1")
    if document["variant"] not in VARIANTS or not isinstance(document["export"], str) or document["export"] not in EXPORTS:
        raise ValueError("Unsupported variant or export in BIN manifest")
    integer(document["run"], 0, 2147483647, "run")
    if not isinstance(document["streams"], list) or not document["streams"]:
        raise ValueError("BIN manifest must declare at least one stream")
    ids, channels, seen_paths, inodes = set(), set(), set(), set()
    families = {}
    for stream in document["streams"]:
        fields(stream, ("id", "channels", "header_mode", "part_count", "files"), ("board",))
        if not isinstance(stream["id"], str) or not stream["id"].strip() or stream["id"] in ids:
            raise ValueError("Stream IDs must be nonempty and unique")
        ids.add(stream["id"])
        allowed = stream["channels"]
        if not isinstance(allowed, list) or not allowed:
            raise ValueError("Each stream must declare its allowed channels")
        for channel in allowed:
            integer(channel, 0, 7, "channel")
        if len(set(allowed)) != len(allowed) or channels.intersection(allowed):
            raise ValueError("Channels must be distinct within and across streams")
        channels.update(allowed)
        stream["channels"] = sorted(allowed)
        if stream.get("board") is not None:
            integer(stream["board"], -32768, 32767, "board")
        stream.setdefault("board", None)
        if stream["header_mode"] not in ("first", "each"):
            raise ValueError("header_mode must be first or each")
        integer(stream["part_count"], 1, 2147483647, "part_count")
        parts = stream["files"]
        if not isinstance(parts, list) or len(parts) != stream["part_count"]:
            raise ValueError("Missing volume: files must match part_count")
        for entry in parts:
            fields(entry, ("part", "path"), ("sha256",))
            integer(entry["part"], 0, stream["part_count"] - 1, "part")
            if not isinstance(entry["path"], str) or not entry["path"] or "\0" in entry["path"]:
                raise ValueError("Each part requires a nonempty file path")
            source = (path.parent / entry["path"]).resolve(strict=check_files)
            if source in seen_paths:
                raise ValueError(f"Duplicate input path: {source}")
            seen_paths.add(source)
            entry["path"] = str(source)
            pin = entry.get("sha256")
            if pin is not None:
                if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", pin):
                    raise ValueError("sha256 must contain 64 hexadecimal characters")
                entry["sha256"] = pin.lower()
            labels = {part.upper() for part in source.parts[:-1]} & (EXPORTS - {"CUSTOM"})
            if labels and labels != {document["export"]}:
                raise ValueError(f"Mixed or mismatched export category: {source}")
            if check_files:
                if not source.is_file():
                    raise ValueError(f"Not a regular input file: {source}")
                stat = source.stat()
                identity = (stat.st_dev, stat.st_ino)
                if identity in inodes:
                    raise ValueError(f"Duplicate input file (hard link or alias): {source}")
                inodes.add(identity)
            named = re.search(r"(?:^|_)run_(\d+)(?:_(\d+))?$", source.stem, flags=re.IGNORECASE)
            if named:
                if int(named.group(1)) != document["run"] or int(named.group(2) or 0) != entry["part"]:
                    raise ValueError(f"Filename run/part disagrees with manifest: {source.name}")
                base = source.stem.rsplit("_", 1)[0] if named.group(2) is not None else source.stem
                families.setdefault((source.parent, base.casefold()), set()).add(source)
        parts.sort(key=lambda entry: entry["part"])
        if [entry["part"] for entry in parts] != list(range(stream["part_count"])):
            raise ValueError("Missing or duplicate part number; expected 0 through part_count - 1")
    if check_files:
        for (parent, base), listed in families.items():
            for sibling in parent.iterdir():
                if (sibling.is_file() and sibling.suffix.lower() == ".bin" and
                        re.fullmatch(re.escape(base) + r"(?:_\d+)?", sibling.stem, flags=re.IGNORECASE) and
                        sibling.resolve() not in listed):
                    raise ValueError(f"Related volume is not listed in the manifest: {sibling}")
    return document


def read_exact(source, size, field):
    value = source.read(size)
    if len(value) != size:
        raise ValueError(f"Truncated {field}: read {len(value)} of {size} bytes")
    return value


def read_record(source, header):
    prefix = source.read(PREFIX.size)
    if not prefix:
        return None
    if len(prefix) != PREFIX.size:
        raise ValueError("Truncated record prefix")
    board, channel, timestamp = PREFIX.unpack(prefix)
    if not 0 <= channel <= 7:
        raise ValueError(f"Invalid channel: {channel}")
    body = bytearray(prefix)
    if header & 1:
        body.extend(read_exact(source, 2, "energy_ch"))
    if header & 2:
        energy = read_exact(source, 8, "energy_keV")
        if not math.isfinite(struct.unpack("<d", energy)[0]):
            raise ValueError("Non-finite energy_keV")
        body.extend(energy)
    if header & 4:
        body.extend(read_exact(source, 2, "energy_short"))
    body.extend(read_exact(source, 4, "flags"))
    if header & 8:
        wave = read_exact(source, 5, "waveform prefix")
        size = struct.unpack("<BI", wave)[1]
        if size > 10000:
            raise ValueError(f"Waveform size exceeds capacity: {size}")
        body.extend(wave)
        body.extend(read_exact(source, 2 * size, "waveform data"))
    return bytes(body), {"board": board, "channel": channel, "timestamp": timestamp}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def assemble(manifest_path, output_dir):
    manifest_path = Path(manifest_path).resolve(strict=True)
    content = manifest_path.read_bytes()
    source_identity = {"path": str(manifest_path), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    document = load_manifest(manifest_path, content=content)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "source_manifest.json").write_bytes(content)
    report = {"schema_version": 1, "variant": document["variant"], "run": document["run"],
              "export": document["export"], "manifest": {**source_identity, "snapshot": "source_manifest.json"},
              "files": [], "total_records": 0, "declared_parts_complete": True,
              "acquisition_complete": None}
    header, seen_payloads = None, {}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".assembled.partial.", dir=output_dir, delete=False) as output:
            temporary = Path(output.name)
            for stream in document["streams"]:
                last_times, last_record, last_record_info = {}, None, None
                for entry in stream["files"]:
                    path = Path(entry["path"])
                    current = {"stream": stream["id"], "part": entry["part"], "path": str(path),
                               "header_present": stream["header_mode"] == "each" or entry["part"] == 0,
                               "record_start": report["total_records"], "records": 0, "channels": {},
                               "first_record": None, "last_record": None, "boundary_equal_record": False,
                               "previous_stream_record": last_record_info}
                    file_digest, payload_digest = hashlib.sha256(), hashlib.sha256()
                    try:
                        with path.open("rb") as source:
                            if current["header_present"]:
                                encoded_header = read_exact(source, 2, "file header")
                                file_digest.update(encoded_header)
                                value = struct.unpack("<H", encoded_header)[0]
                                if header is None:
                                    header = value
                                    output.write(encoded_header)
                                elif value != header:
                                    raise ValueError(f"Header mismatch: {value:#06x} != {header:#06x}")
                            while True:
                                record = read_record(source, header)
                                if record is None:
                                    break
                                payload, info = record
                                channel, timestamp = info["channel"], info["timestamp"]
                                if channel not in stream["channels"] or (stream["board"] is not None and info["board"] != stream["board"]):
                                    raise ValueError("Record board/channel disagrees with stream declaration")
                                if channel in last_times and timestamp < last_times[channel]:
                                    raise ValueError(f"Timestamp decreases on channel {channel}")
                                last_times[channel] = timestamp
                                if current["records"] == 0:
                                    current["first_record"] = info
                                    current["boundary_equal_record"] = last_record is not None and payload == last_record
                                last_record, last_record_info = payload, info
                                current["last_record"] = info
                                state = current["channels"].setdefault(str(channel), {"records": 0, "first_timestamp": timestamp})
                                state["records"] += 1
                                state["last_timestamp"] = timestamp
                                current["records"] += 1
                                file_digest.update(payload)
                                payload_digest.update(payload)
                                output.write(payload)
                        identity = records.file_identity(path)
                        if identity["sha256"] != file_digest.hexdigest():
                            raise ValueError("Input changed during reading")
                        if entry.get("sha256") is not None and entry["sha256"] != identity["sha256"]:
                            raise ValueError("Input SHA-256 differs from manifest")
                        entry["sha256"] = identity["sha256"]
                        current.update(identity)
                        current["payload_sha256"] = payload_digest.hexdigest()
                        previous = seen_payloads.get(current["payload_sha256"])
                        if previous is not None:
                            if current["records"]:
                                raise ValueError(f"Duplicate nonempty file payload: already read {previous}")
                            current["duplicate_empty_payload_of"] = previous
                        else:
                            seen_payloads[current["payload_sha256"]] = str(path)
                    except (OSError, ValueError) as error:
                        raise ValueError(f"Stream {stream['id']}, part {entry['part']}, record {current['records']}, {path}: {error}") from error
                    report["total_records"] += current["records"]
                    current["record_stop"] = report["total_records"]
                    report["files"].append(current)
        records.verify_file(source_identity)
        for entry in report["files"]:
            records.verify_file(entry)
        destination = output_dir / "assembled.BIN"
        temporary.replace(destination)
        temporary = None
        report["header"] = header
        report["normalized"] = {**records.file_identity(destination), "snapshot": "assembled.BIN"}
        resolved = output_dir / "resolved_manifest.json"
        write_json(resolved, document)
        report["resolved_manifest"] = {**records.file_identity(resolved), "snapshot": "resolved_manifest.json"}
        write_json(output_dir / "input_report.json", report)
        return report
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_relocation(candidate_path, archived_path):
    """A replay may change paths, while retaining every archived declaration and hash."""
    original = load_manifest(archived_path, check_files=False)
    candidate = load_manifest(candidate_path)
    for document in (original, candidate):
        for stream in document["streams"]:
            for entry in stream["files"]:
                if "sha256" not in entry:
                    raise ValueError("Replay manifests must retain the archived SHA-256 for every part")
                if document is candidate and records.file_identity(entry["path"])["sha256"] != entry["sha256"]:
                    raise ValueError(f"Replay input changed: {entry['path']}")
                del entry["path"]
    if candidate != original:
        raise ValueError("Replay may change input paths only, not stream declarations or hashes")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = assemble(args.manifest, args.output_dir)
        print(f"Validated {len(report['files'])} files, {report['total_records']} records; header {report['header']:#06x}")
    except KeyboardInterrupt:
        print("BIN input preparation interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"BIN input preparation failed: {error}", file=sys.stderr)
        return 1
    return 0


def terminate(signum, frame):
    raise KeyboardInterrupt(f"Received {signal.Signals(signum).name}")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
