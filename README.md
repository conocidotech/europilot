<div align="center" style="text-align: center;">

<h1>Dutchpilot</h1>

<p>
  <b>A Dutch fork of <a href="https://github.com/commaai/openpilot">openpilot</a>, optimized for driving in the Netherlands and Europe.</b>
  <br>
  Based on openpilot — the open-source driver assistance system that upgrades 300+ supported cars.
</p>

<h3>
  <a href="https://github.com/commaai/openpilot">Upstream openpilot</a>
  <span> · </span>
  <a href="https://docs.comma.ai">Docs</a>
  <span> · </span>
  <a href="docs/CARS.md">Supported cars</a>
  <span> · </span>
  <a href="https://discord.comma.ai">Community</a>
</h3>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

## What is Dutchpilot?

Dutchpilot is a fork of [comma.ai's openpilot](https://github.com/commaai/openpilot) with modifications for the Dutch and European market:

- **Dutch UI** — Full Dutch translation of the interface
- **EU optimizations** — Tuning for European roads, speed signs, and lane widths
- **Own data backend** — Drive data to our own servers instead of comma.ai
- **Vehicle-specific tuning** — Optimized parameters for cars popular in the Netherlands

## Tested cars

Dutchpilot is actively tested on:

| Car | Status | Notes |
|---|---|---|
| Lexus NX300h | 🟡 In development | Steering angle optimization in progress |

All [300+ cars supported by openpilot](docs/CARS.md) are also supported.

## Installation

You need:
1. **comma four** — available at [comma.ai/shop](https://comma.ai/shop/comma-four)
2. **Car harness** — for your car, via [comma.ai/shop](https://comma.ai/shop/car-harness)
3. **Supported car** — see the [car list](docs/CARS.md)

### Installing Dutchpilot

During comma four setup, enter this custom software URL:

```
installer.comma.ai/conocidotech/dutchpilot
```

## Differences from upstream openpilot

Dutchpilot follows upstream openpilot as closely as possible. Our changes are designed to be merge-safe:

| Change | Method | Merge conflict risk |
|---|---|---|
| Dutch translation | New file (`app_nl.po`) | None |
| Branding | Overlay / build-time replacement | None |
| Parameters | Config overrides | None |
| Data backend | Environment variables | None |

We sync regularly with upstream openpilot to receive new features and bugfixes.

## Development

```bash
# Clone the repository
git clone https://github.com/conocidotech/dutchpilot.git
cd dutchpilot

# Initialize submodules
git submodule update --init

# Add upstream remote (for syncing)
git remote add upstream https://github.com/commaai/openpilot.git
```

### Upstream sync

```bash
git fetch upstream
git merge upstream/master
```

Our merge-safe approach ensures upstream merges are nearly always conflict-free.

## Safety

Dutchpilot inherits all safety measures from openpilot:

- Follows [ISO26262](https://en.wikipedia.org/wiki/ISO_26262) guidelines — see [SAFETY.md](docs/SAFETY.md)
- Safety-critical code in panda is written in C with [extensive tests](https://github.com/commaai/panda#code-rigor)
- Software-in-the-loop tests run on every commit

**NOTE:** Any modifications to steering parameters (such as torque limits) are thoroughly tested before release. Always use the latest release branch for daily driving.

## Credits

Dutchpilot is built on the work of [comma.ai](https://comma.ai) and the openpilot community. All credit for the base system goes to them.

- [openpilot](https://github.com/commaai/openpilot) — the upstream project
- [comma.ai](https://comma.ai) — creators of openpilot and comma hardware
- Dutchpilot is maintained by [Conocido](https://conocido.nl)

<details>
<summary>MIT Licensed</summary>

openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.

Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys' fees and costs) which arise out of, relate to or result from any use of this software by user.

**THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
NO WARRANTY EXPRESSED OR IMPLIED.**
</details>

<details>
<summary>User Data and comma Account</summary>

By default, openpilot uploads driving data to comma.ai servers. Dutchpilot can be configured to send data to your own servers instead.

openpilot logs the road-facing cameras, CAN, GPS, IMU, magnetometer, thermal sensors, crashes, and operating system logs. The driver-facing camera and microphone are only logged if you explicitly opt-in in settings.

By using openpilot, you agree to [comma.ai's Privacy Policy](https://comma.ai/privacy).
</details>
