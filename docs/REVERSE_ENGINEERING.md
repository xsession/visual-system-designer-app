# Firmware reverse engineering

The analyzer works entirely on local files or content-addressed assets.

## Supported containers

- ELF
- Intel HEX
- UF2
- Motorola S-record detection
- PE detection
- raw binary

## ELF output

When `pyelftools` is installed, reports include headers, entry point, architecture, endianness, sections, segments and symbols. Executable sections can be disassembled with Capstone using the supplied architecture hint.

## Raw image output

Raw images include entropy, byte histogram, strings, hex preview, ARM or AVR vector heuristics, and optional Capstone disassembly. The analyzer does not infer a load address automatically; supply architecture and platform context when interpreting addresses.

## Triage findings

The report highlights plaintext credentials/tokens, private-key markers, unencrypted protocols, debug strings and high-entropy regions. These are heuristics, not proof of a vulnerability or encryption.

## Emulation-assisted analysis

Upload firmware plus a Renode `.repl` or `.resc`, then start a Renode session. For hardware-backed work, start OpenOCD from a local configuration and connect a separately launched debugger. All process output is logged, and specially formatted firmware telemetry appears in the live plot UI.

Custom process execution is intentionally available for local reverse-engineering tools. Keep the server localhost-only unless a bearer token and trusted network boundary are in place.
