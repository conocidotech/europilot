<div align="center">

# Europilot

**openpilot, optimized for European driving.**

A merge-safe fork of [comma.ai's openpilot](https://github.com/commaai/openpilot) that adds EU-specific features while staying fully compatible with upstream updates.

[Website](https://europilot.eu) · [Upstream Docs](https://docs.comma.ai)

</div>

---

## What is Europilot?

Europilot builds on top of openpilot to better support driving conditions across Europe — speed limits, road signage, local regulations, and driving styles that differ from the North American defaults.

It runs on [comma four](https://comma.ai/shop) hardware.

## Merge-safe architecture

All Europilot changes are designed to merge cleanly with upstream openpilot:

- **New files only** — no modifications to existing openpilot source files
- **Config overlays** — EU-specific parameters loaded via environment variables
- **Feature flags** — remote toggles with Ed25519-signed responses for safety

This means Europilot can pull in the latest openpilot improvements without merge conflicts.

## Getting started

Flash your comma four with Europilot using the installer:

```
bash <(curl -fsSL europilot.eu/install)
```

> **Note:** Europilot is under active development. The installer will be available once the first stable release is ready.

## Project

Europilot is built by [Conocido](https://github.com/conocidotech) and tracked in [Avanance](https://avanance.eu).

## License

Europilot inherits the [MIT License](LICENSE) from openpilot.
