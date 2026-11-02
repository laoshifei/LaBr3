"""Small deterministic inputs for the existing ROOT command-line programs."""

from array import array
from dataclasses import dataclass
from pathlib import Path
import struct
import shutil
import subprocess

import ROOT

ROOT.gROOT.SetBatch(True)

REPO = Path(__file__).resolve().parents[1]
VARIANTS = ("DT5720_DPP_PSD", "DT5730_DPP_PHA", "DT5730_DPP_PSD")


@dataclass(frozen=True)
class Hit:
    board: int
    channel: int
    energy: float
    timestamp: int


def hits_at(timestamps):
    return [Hit(1 + i % 4, i % 8, i + 0.125, ts)
            for i, ts in enumerate(timestamps)]


def checked_run(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            timeout=120)
    if result.returncode:
        raise RuntimeError(f"{command!r}\n{result.stdout}\n{result.stderr}")
    return result


def copy_program(root, variant, stage):
    """Keep the repository layout so shared headers and Make dependencies resolve."""
    source = root / variant / stage
    source.mkdir(parents=True)
    for path in (REPO / variant / stage).iterdir():
        if path.name == "Makefile" or path.suffix in (".cpp", ".h"):
            shutil.copyfile(path, source / path.name)
    common = root / "common"
    common.mkdir(exist_ok=True)
    for name in ("CommandLine.h", "StageReport.h"):
        shutil.copyfile(REPO / "common" / name, common / name)
    return source


def write_sorted(path, hits, tree_name="tr", omit=None, wrong_energy_type=False, waveform=False,
                 multileaf_timestamp=False):
    """Write scalar hits with the schema consumed by build."""
    path.parent.mkdir(parents=True, exist_ok=True)
    output = ROOT.TFile(str(path), "RECREATE")
    tree = ROOT.TTree(tree_name, "synthetic sorted hits")
    buffers = {"board": array("h", [0]), "channel": array("h", [0]),
               "energy": array("i" if wrong_energy_type else "d", [0]),
               "timestamp": array("q", [0] * (2 if multileaf_timestamp else 1))}
    types = {"board": "S", "channel": "S", "energy": "I" if wrong_energy_type else "D",
             "timestamp": "L"}
    try:
        for name, buffer in buffers.items():
            if name != omit:
                extra = ":extra/L" if name == "timestamp" and multileaf_timestamp else ""
                tree.Branch(name, buffer, f"{name}/{types[name]}{extra}")
        if waveform:
            size = array("I", [10001])
            samples = array("h", [7] * 10001)
            tree.Branch("size", size, "size/i")
            tree.Branch("data", samples, "data[size]/S")
        for hit in hits:
            for name, buffer in buffers.items():
                value = getattr(hit, name)
                buffer[0] = int(value) if name == "energy" and wrong_energy_type else value
            tree.Fill()
        tree.Write()
    finally:
        output.Close()


def read_events(path):
    source = ROOT.TFile.Open(str(path), "READ")
    if not source or source.IsZombie():
        raise AssertionError(f"Cannot read output: {path}")
    try:
        tree = source.Get("tr")
        if not tree:
            raise AssertionError(f"Missing output tree: {path}")
        types = {name: tree.GetLeaf(name).GetTypeName()
                 for name in ("m", "board", "channel", "energy", "timestamp")}
        events = []
        for entry in tree:
            m = int(entry.m)
            fields = {name: list(getattr(entry, name))
                      for name in ("board", "channel", "energy", "timestamp")}
            if any(len(values) != m for values in fields.values()):
                raise AssertionError("Array length does not match multiplicity")
            events.append([Hit(int(fields["board"][i]), int(fields["channel"][i]),
                               float(fields["energy"][i]), int(fields["timestamp"][i]))
                           for i in range(m)])
        return str(tree.GetTitle()), types, events
    finally:
        source.Close()


def write_bin(path, hits, trailing=b""):
    """Header bit 0: raw energy; each complete record also contains a flags word."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        output.write(struct.pack("<H", 1))
        for hit in hits:
            output.write(struct.pack("<hhQHI", hit.board, hit.channel,
                                     hit.timestamp, int(hit.energy), 0))
        output.write(trailing)


@dataclass(frozen=True)
class RawRecord:
    board: int = 1
    channel: int = 2
    timestamp: int = 123456789012345678
    energy_ch: int = 65535
    energy_keV: float = 661.657125
    energy_short: int = 1234
    flags: int = 0x12345678
    waveform_code: int = 1
    waveform: tuple = (-32768, 0, 32767)


def encode_bin_record(record, header):
    """Encode the existing little-endian layout, including optional fields."""
    encoded = struct.pack("<hhq", record.board, record.channel, record.timestamp)
    if header & 1:
        encoded += struct.pack("<H", record.energy_ch)
    if header & 2:
        encoded += struct.pack("<d", record.energy_keV)
    if header & 4:
        encoded += struct.pack("<H", record.energy_short)
    encoded += struct.pack("<I", record.flags)
    if header & 8:
        encoded += struct.pack("<BI", record.waveform_code, len(record.waveform))
        encoded += struct.pack(f"<{len(record.waveform)}h", *record.waveform)
    return encoded


def write_built(path, events, omit=None, wrong_energy_type=False):
    """Write variable-length event arrays, including intentionally invalid multiplicities."""
    path.parent.mkdir(parents=True, exist_ok=True)
    output = ROOT.TFile(str(path), "RECREATE")
    tree = ROOT.TTree("tr", "synthetic events")
    capacity = max([1] + [len(event) for event in events])
    multiplicity = array("H", [0])
    buffers = {"board": array("h", [0] * capacity),
               "channel": array("h", [0] * capacity),
               "energy": array("i" if wrong_energy_type else "d", [0] * capacity),
               "timestamp": array("q", [0] * capacity)}
    types = {"board": "S", "channel": "S", "energy": "I" if wrong_energy_type else "D",
             "timestamp": "L"}
    try:
        tree.Branch("m", multiplicity, "m/s")
        for name, buffer in buffers.items():
            if name != omit:
                tree.Branch(name, buffer, f"{name}[m]/{types[name]}")
        for event in events:
            multiplicity[0] = len(event)
            for i, hit in enumerate(event):
                for name, buffer in buffers.items():
                    value = getattr(hit, name)
                    buffer[i] = int(value) if name == "energy" and wrong_energy_type else value
            tree.Fill()
        tree.Write()
    finally:
        output.Close()


def read_raw_hits(path):
    source = ROOT.TFile.Open(str(path), "READ")
    if not source or source.IsZombie():
        raise AssertionError(f"Cannot read output: {path}")
    try:
        result = []
        for channel in range(8):
            tree = source.Get(f"tr_ch{channel:02d}")
            if not tree:
                continue
            for entry in tree:
                result.append(Hit(int(entry.board), int(entry.channel),
                                  int(entry.energy_ch), int(entry.timestamp)))
        return result
    finally:
        source.Close()


def write_channel_hits(path, hits, omit=None, wrong_energy_type=False,
                       tree_channel=None, waveform=False, empty_channels=(),
                       wrong_timestamp_type=False, array_timestamp=False, tree_name=None,
                       multileaf_timestamp=False):
    """Write raw per-channel trees for sorting, including deliberate bad schemas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    output = ROOT.TFile(str(path), "RECREATE")
    groups = {}
    for hit in hits:
        groups.setdefault(hit.channel if tree_channel is None else tree_channel, []).append(hit)
    for channel in empty_channels:
        groups.setdefault(channel, [])
    buffers = {"board": array("h", [0]), "channel": array("h", [0]),
               "energy_ch": array("d" if wrong_energy_type else "H", [0]),
               "timestamp": array("d" if wrong_timestamp_type else "q",
                                  [0] * (2 if array_timestamp or multileaf_timestamp else 1))}
    types = {"board": "S", "channel": "S", "energy_ch": "D" if wrong_energy_type else "s",
             "timestamp": "D" if wrong_timestamp_type else "L"}
    size = array("I", [10001])
    samples = array("h", [7] * 10001)
    try:
        for channel, records in groups.items():
            tree = ROOT.TTree(tree_name or f"tr_ch{channel:02d}", "synthetic channel hits")
            for name, buffer in buffers.items():
                if name != omit:
                    shape = "[2]" if name == "timestamp" and array_timestamp else ""
                    extra = ":extra/L" if name == "timestamp" and multileaf_timestamp else ""
                    tree.Branch(name, buffer, f"{name}{shape}/{types[name]}{extra}")
            if waveform:
                tree.Branch("size", size, "size/i")
                tree.Branch("data", samples, "data[size]/S")
                tree.Branch("dt", samples, "dt[size]/S")
            for hit in records:
                buffers["board"][0] = hit.board
                buffers["channel"][0] = hit.channel
                buffers["energy_ch"][0] = hit.energy if wrong_energy_type else int(hit.energy)
                buffers["timestamp"][0] = hit.timestamp
                tree.Fill()
            tree.Write()
    finally:
        output.Close()


def read_sorted_hits(path):
    source = ROOT.TFile.Open(str(path), "READ")
    if not source or source.IsZombie():
        raise AssertionError(f"Cannot read output: {path}")
    try:
        tree = source.Get("tr")
        if not tree:
            raise AssertionError(f"Missing sorted tree: {path}")
        types = {name: tree.GetLeaf(name).GetTypeName()
                 for name in ("board", "channel", "energy", "timestamp")}
        hits = [Hit(int(row.board), int(row.channel), float(row.energy), int(row.timestamp))
                for row in tree]
        return str(tree.GetTitle()), types, hits
    finally:
        source.Close()
