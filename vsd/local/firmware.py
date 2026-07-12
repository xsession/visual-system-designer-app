from __future__ import annotations

import hashlib
import math
import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:
    from elftools.elf.elffile import ELFFile
except ImportError:  # pragma: no cover
    ELFFile = None

try:
    from capstone import (
        Cs,
        CS_ARCH_ARM,
        CS_ARCH_ARM64,
        CS_ARCH_MIPS,
        CS_ARCH_PPC,
        CS_ARCH_RISCV,
        CS_ARCH_X86,
        CS_MODE_16,
        CS_MODE_32,
        CS_MODE_64,
        CS_MODE_ARM,
        CS_MODE_BIG_ENDIAN,
        CS_MODE_LITTLE_ENDIAN,
        CS_MODE_MCLASS,
        CS_MODE_RISCV32,
        CS_MODE_RISCV64,
        CS_MODE_THUMB,
    )
except ImportError:  # pragma: no cover
    Cs = None


ASCII_RE = re.compile(rb"[\x20-\x7e]{4,}")
UTF16LE_RE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


class FirmwareAnalyzer:
    def analyze(self, path: str | Path, architecture: str | None = None) -> dict[str, Any]:
        file_path = Path(path)
        data = file_path.read_bytes()
        report: dict[str, Any] = {
            "name": file_path.name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
            "format": detect_format(data, file_path.suffix),
            "entropy": shannon_entropy(data),
            "byte_histogram": list(Counter(data).get(index, 0) for index in range(256)),
            "strings": extract_strings(data, limit=500),
            "architecture_hint": architecture,
            "hexdump_preview": hexdump(data[:512]),
            "findings": [],
        }
        if report["format"] == "ELF":
            report["elf"] = self._analyze_elf(file_path, architecture)
        elif report["format"] == "UF2":
            report["uf2"] = analyze_uf2(data)
        elif report["format"] == "Intel HEX":
            report["intel_hex"] = analyze_intel_hex(data)
        else:
            report["raw"] = analyze_raw(data, architecture)
        report["findings"].extend(security_findings(data, report))
        return report

    def compare(self, left: str | Path, right: str | Path) -> dict[str, Any]:
        left_data = Path(left).read_bytes()
        right_data = Path(right).read_bytes()
        maximum = max(len(left_data), len(right_data))
        changed = []
        ranges = []
        range_start = None
        for offset in range(maximum):
            left_byte = left_data[offset] if offset < len(left_data) else None
            right_byte = right_data[offset] if offset < len(right_data) else None
            if left_byte != right_byte:
                changed.append(offset)
                if range_start is None:
                    range_start = offset
            elif range_start is not None:
                ranges.append([range_start, offset - 1])
                range_start = None
        if range_start is not None:
            ranges.append([range_start, maximum - 1])
        return {
            "left_size": len(left_data),
            "right_size": len(right_data),
            "changed_bytes": len(changed),
            "changed_ratio": len(changed) / maximum if maximum else 0.0,
            "changed_ranges": ranges[:1000],
            "left_entropy": shannon_entropy(left_data),
            "right_entropy": shannon_entropy(right_data),
        }

    def _analyze_elf(self, path: Path, architecture: str | None) -> dict[str, Any]:
        if ELFFile is None:
            return {
                "available": False,
                "reason": "Install the studio extra or pyelftools to decode ELF metadata.",
            }
        with path.open("rb") as handle:
            elf = ELFFile(handle)
            sections = []
            for section in elf.iter_sections():
                sections.append(
                    {
                        "name": section.name,
                        "type": str(section["sh_type"]),
                        "address": int(section["sh_addr"]),
                        "offset": int(section["sh_offset"]),
                        "size": int(section["sh_size"]),
                        "flags": int(section["sh_flags"]),
                        "entropy": shannon_entropy(section.data()) if section["sh_size"] else 0.0,
                    }
                )
            segments = [
                {
                    "type": str(segment["p_type"]),
                    "virtual_address": int(segment["p_vaddr"]),
                    "physical_address": int(segment["p_paddr"]),
                    "file_size": int(segment["p_filesz"]),
                    "memory_size": int(segment["p_memsz"]),
                    "flags": int(segment["p_flags"]),
                }
                for segment in elf.iter_segments()
            ]
            symbols = []
            for section in elf.iter_sections():
                if not hasattr(section, "iter_symbols"):
                    continue
                for symbol in section.iter_symbols():
                    if len(symbols) >= 5000:
                        break
                    symbols.append(
                        {
                            "name": symbol.name,
                            "address": int(symbol["st_value"]),
                            "size": int(symbol["st_size"]),
                            "bind": str(symbol["st_info"]["bind"]),
                            "type": str(symbol["st_info"]["type"]),
                            "section": str(symbol["st_shndx"]),
                        }
                    )
            executable_sections = []
            for section in elf.iter_sections():
                if int(section["sh_flags"]) & 0x4 and section["sh_size"]:
                    code = section.data()
                    executable_sections.append(
                        {
                            "name": section.name,
                            "address": int(section["sh_addr"]),
                            "disassembly": disassemble(
                                code[:65536],
                                int(section["sh_addr"]),
                                architecture or infer_architecture(str(elf["e_machine"])),
                                little_endian=elf.little_endian,
                                limit=2000,
                            ),
                        }
                    )
            return {
                "available": True,
                "entry": int(elf.header["e_entry"]),
                "machine": str(elf.header["e_machine"]),
                "class": elf.elfclass,
                "little_endian": elf.little_endian,
                "sections": sections,
                "segments": segments,
                "symbols": symbols,
                "executable_sections": executable_sections,
            }


def detect_format(data: bytes, suffix: str = "") -> str:
    if data.startswith(b"\x7fELF"):
        return "ELF"
    if len(data) >= 512 and data[:4] == b"UF2\n" and data[508:512] == b"0\nab":
        return "UF2"
    if data.lstrip().startswith(b":") and suffix.lower() in {".hex", ".ihex", ".ihx"}:
        return "Intel HEX"
    if data.startswith(b"S0") or data.startswith(b"S1") or suffix.lower() in {".srec", ".s19"}:
        return "Motorola S-record"
    if data.startswith(b"MZ"):
        return "PE/COFF"
    return "Raw binary"


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def extract_strings(data: bytes, limit: int = 500) -> list[dict[str, Any]]:
    found = []
    for match in ASCII_RE.finditer(data):
        found.append({"offset": match.start(), "encoding": "ascii", "value": match.group().decode("ascii")})
    for match in UTF16LE_RE.finditer(data):
        found.append({"offset": match.start(), "encoding": "utf-16le", "value": match.group().decode("utf-16le")})
    found.sort(key=lambda item: item["offset"])
    return found[:limit]


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset:offset + width]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk).ljust(width * 3 - 1)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


def analyze_uf2(data: bytes) -> dict[str, Any]:
    blocks = []
    for offset in range(0, len(data) - 511, 512):
        block = data[offset:offset + 512]
        magic0, magic1, flags, target, payload_size, block_no, block_count, family = struct.unpack_from("<IIIIIIII", block, 0)
        if magic0 != 0x0A324655 or magic1 != 0x9E5D5157:
            continue
        blocks.append(
            {
                "block": block_no,
                "block_count": block_count,
                "target_address": target,
                "payload_size": payload_size,
                "flags": flags,
                "family_id": family,
            }
        )
    return {
        "block_count": len(blocks),
        "declared_block_count": max((block["block_count"] for block in blocks), default=0),
        "address_min": min((block["target_address"] for block in blocks), default=None),
        "address_max": max((block["target_address"] + block["payload_size"] for block in blocks), default=None),
        "families": sorted({block["family_id"] for block in blocks}),
        "blocks_preview": blocks[:64],
    }


def analyze_intel_hex(data: bytes) -> dict[str, Any]:
    upper = 0
    records = 0
    ranges = []
    current_start = None
    current_end = None
    errors = []
    data_bytes = 0
    for line_no, raw_line in enumerate(data.decode("ascii", errors="replace").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            errors.append(f"line {line_no}: missing ':'")
            continue
        try:
            record = bytes.fromhex(line[1:])
            length = record[0]
            address = int.from_bytes(record[1:3], "big")
            record_type = record[3]
            payload = record[4:4 + length]
            if sum(record) & 0xFF:
                errors.append(f"line {line_no}: checksum mismatch")
            if record_type == 0x04 and len(payload) == 2:
                upper = int.from_bytes(payload, "big") << 16
            elif record_type == 0x02 and len(payload) == 2:
                upper = int.from_bytes(payload, "big") << 4
            elif record_type == 0x00:
                data_bytes += len(payload)
                absolute = upper + address
                end = absolute + len(payload)
                if current_start is None:
                    current_start, current_end = absolute, end
                elif absolute <= current_end:
                    current_end = max(current_end, end)
                else:
                    ranges.append([current_start, current_end])
                    current_start, current_end = absolute, end
            records += 1
        except (ValueError, IndexError):
            errors.append(f"line {line_no}: invalid record")
    if current_start is not None:
        ranges.append([current_start, current_end])
    return {"records": records, "data_bytes": data_bytes, "ranges": ranges, "errors": errors[:100]}


def analyze_raw(data: bytes, architecture: str | None) -> dict[str, Any]:
    vectors = detect_vector_table(data, architecture)
    return {
        "vector_table": vectors,
        "disassembly": disassemble(data[:65536], 0, architecture, limit=2000) if architecture else [],
    }


def detect_vector_table(data: bytes, architecture: str | None) -> dict[str, Any] | None:
    normalized = (architecture or "").lower()
    if any(name in normalized for name in ("arm", "cortex", "thumb")) and len(data) >= 8:
        stack_pointer, reset_vector = struct.unpack_from("<II", data, 0)
        plausible_stack = 0x10000000 <= stack_pointer <= 0x60000000
        plausible_reset = reset_vector != 0 and reset_vector != 0xFFFFFFFF
        return {
            "type": "arm",
            "initial_stack_pointer": stack_pointer,
            "reset_vector": reset_vector,
            "thumb": bool(reset_vector & 1),
            "plausible": plausible_stack and plausible_reset,
            "entries": [struct.unpack_from("<I", data, offset)[0] for offset in range(0, min(len(data), 256) - (min(len(data), 256) % 4), 4)],
        }
    if "avr" in normalized and len(data) >= 4:
        return {
            "type": "avr",
            "reset_instruction": data[:4].hex(),
            "plausible": data[:2] not in {b"\xff\xff", b"\x00\x00"},
        }
    return None


def infer_architecture(machine: str) -> str | None:
    normalized = machine.lower()
    mapping = {
        "arm": "arm",
        "aarch64": "arm64",
        "x86-64": "x86_64",
        "intel 80386": "x86",
        "risc-v": "riscv32",
        "mips": "mips",
        "powerpc": "ppc",
    }
    return next((value for key, value in mapping.items() if key in normalized), None)


def disassemble(
    data: bytes,
    address: int,
    architecture: str | None,
    *,
    little_endian: bool = True,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    if Cs is None or not architecture:
        return []
    normalized = architecture.lower().replace("-", "_")
    endian = CS_MODE_LITTLE_ENDIAN if little_endian else CS_MODE_BIG_ENDIAN
    spec = None
    if normalized in {"arm", "arm32"}:
        spec = (CS_ARCH_ARM, CS_MODE_ARM | endian)
    elif normalized in {"thumb", "cortex_m", "cortex_m0", "cortex_m3", "cortex_m4", "cortex_m7"}:
        spec = (CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS | endian)
    elif normalized in {"arm64", "aarch64"}:
        spec = (CS_ARCH_ARM64, endian)
    elif normalized in {"x86", "i386"}:
        spec = (CS_ARCH_X86, CS_MODE_32)
    elif normalized in {"x86_64", "amd64"}:
        spec = (CS_ARCH_X86, CS_MODE_64)
    elif normalized in {"x86_16"}:
        spec = (CS_ARCH_X86, CS_MODE_16)
    elif normalized in {"riscv32", "rv32"}:
        spec = (CS_ARCH_RISCV, CS_MODE_RISCV32 | endian)
    elif normalized in {"riscv64", "rv64"}:
        spec = (CS_ARCH_RISCV, CS_MODE_RISCV64 | endian)
    elif normalized.startswith("mips"):
        spec = (CS_ARCH_MIPS, CS_MODE_32 | endian)
    elif normalized.startswith("ppc"):
        spec = (CS_ARCH_PPC, CS_MODE_32 | endian)
    if spec is None:
        return []
    engine = Cs(*spec)
    engine.detail = False
    output = []
    for instruction in engine.disasm(data, address):
        output.append(
            {
                "address": instruction.address,
                "bytes": instruction.bytes.hex(),
                "mnemonic": instruction.mnemonic,
                "operands": instruction.op_str,
            }
        )
        if len(output) >= limit:
            break
    return output


def security_findings(data: bytes, report: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    lowered = data.lower()
    indicators = {
        b"password=": "Possible embedded password assignment",
        b"api_key": "Possible API key identifier",
        b"private key": "Possible private-key marker",
        b"-----begin": "PEM-like embedded material",
        b"http://": "Unencrypted HTTP URL",
        b"telnet": "Telnet functionality marker",
        b"debug": "Debug marker present",
    }
    for needle, message in indicators.items():
        offset = lowered.find(needle)
        if offset >= 0:
            findings.append({"severity": "warning", "offset": offset, "message": message})
    if report.get("entropy", 0) > 7.7:
        findings.append(
            {
                "severity": "info",
                "offset": 0,
                "message": "High overall entropy may indicate compression, encryption or packed data.",
            }
        )
    return findings
