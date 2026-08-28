# Windows Arm Python Wheels: Evidence Before Release

## Submission status

**Demo-ready as an evidence-first hackathon result. Not yet PR-ready or
shipment-ready.**

This project investigated whether the Windows Arm Python wheel foundation
needed new framework code. The evidence showed that `cibuildwheel` already
supports Windows Arm64 and Shapely already has a native
`windows-11-arm` build lane. The remaining problem is release-grade proof:
authenticated native dependencies, retained inspectable wheels, matching-ABI
tests, and package-owned publication evidence.

The project deliberately reports **BLOCKED / NO-GO** instead of converting a
green historical CI signal into an unsupported shipment claim.

## What the project delivers

- An immutable, reviewed architecture separating framework, package, prior-art,
  and external-upstream ownership.
- A concrete Shapely evidence design covering GEOS integrity, wheel retention,
  provenance, wheel tags, Arm64 PE binaries, repaired dependencies, licenses,
  clean installation, upgrade/uninstall, installed-wheel tests, and official
  PyPI verification.
- A fail-closed Windows Arm64 Copilot CLI handoff prompt.
- An executable Windows Arm build/test runbook.
- A 2-4 minute evidence-grounded demo package with matching narration and
  subtitles.
- A fork-only review PR hardened through repeated Copilot code-review rounds:
  <https://github.com/yeelam-gordon/shapely/pull/1>.

No Shapely or cibuildwheel product source was modified in the planning
workspace.

## Evidence

| Evidence | Result | Meaning |
| --- | --- | --- |
| Historical Shapely GitHub Actions run `32615526656` | Native `windows-11-arm` job succeeded on 2026-08-23 | The native lane is viable; this alone is not wheel or release proof |
| Matching Arm artifact | 12,940,021 bytes, expired on 2026-08-28 | There is no retained binary to inspect |
| Official Shapely 2.1.2 PyPI metadata | Zero `win_arm64` files | No official Windows Arm64 wheel is claimed |
| Current `ci\install_geos.cmd` | HTTP download without SHA-256 verification | Production release remains blocked on authenticated, verified GEOS input |
| Terminal implementation review | One open high finding, `HV-301-003` | The complete executable evidence helper is not present |
| Local execution host | AMD64 | Physical Arm64 build/runtime closure was not claimed |

## Architecture and reusable workflow

```text
Manager
  -> source and upstream investigation
  -> independent design/risk review
  -> bounded design convergence
  -> implementation specification
  -> adversarial implementation review
  -> Windows Arm readiness assessment
  -> evidence-grounded demo
```

The target package flow remains package-owned:

```text
Authenticated GEOS source
  -> native Windows Arm64 build
  -> delvewheel repair
  -> retained wheel + producer metadata
  -> matching-ABI native verification
  -> tag/PE/license/dependency/install/test checks
  -> final provenance reconciliation
  -> Shapely-owned tagged publication
  -> official PyPI filename and SHA-256 comparison
```

The framework repository never receives package policy, and no central wheel
service receives Shapely's publication authority.

## Run on a physical Windows Arm64 machine

The fork contains the executable handoff:

```powershell
git clone https://github.com/yeelam-gordon/shapely.git
Set-Location shapely
git checkout hackathon/windows-arm64-evidence
Get-Content '.\Generated Files\RUN-ON-ARM64.md'
```

`RUN-ON-ARM64.md` launches Copilot CLI with assisted approval and the checked-in
`arm64-copilot-prompt.md`. The prompt fails closed on non-Arm64, records
evidence, refuses publication, and renders the pre-generated demo through the
local Slidecast skill.

## Demo assets

- `Generated Files\demo\demo-script.md`
- `Generated Files\demo\shot-list.md`
- `Generated Files\demo\narration.txt`
- `Generated Files\demo\subtitles.srt`
- `Generated Files\demo\impact-evidence.md`

The primary narrative is 3:45. It distinguishes historical lane viability from
retained binary proof and explicitly disclaims unproved physical Arm testing,
performance, power, and shipment claims.

## Impact evidence and limits

- 17,760 GitHub code-search hits for `tool.cibuildwheel`: code-hit proxy, not
  unique repositories.
- 692 hits for `win_arm64` with `tool.cibuildwheel`: Windows Arm code-hit proxy,
  not a dependency graph.
- 140 packages in the unofficial `win_arm64-wheels` 2025.7.7 catalog: prior
  art, not total demand or official Shapely publication.
- The 12-finalist planning split is 1 foundation-only, 1 foundation plus
  agent-resolvable blockers, 6 with external/vendor blockers, and 4 not
  materially gated. These are planning categories, not adoption forecasts.

## Required closure before an upstream release claim

1. Add and review the complete executable evidence helper.
2. Obtain authoritative GEOS checksum provenance; use HTTPS and verify SHA-256
   before extraction.
3. Run all configured ABI rows on physical Windows Arm64.
4. Retain and reconcile wheel, test, repair, and provenance evidence.
5. Publish only through Shapely's package-owned tag workflow.
6. Match official PyPI `win_arm64` filenames and SHA-256 values.

Until those gates close, the correct status remains **BLOCKED / NO-GO**.
