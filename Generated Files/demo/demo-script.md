# Windows Arm Python Wheels — evidence, not hype

**Planned duration:** 3:45. `len(narration.split())` returns 466 whitespace-delimited
tokens. At the final 225-second SRT timeline, the computed pacing is
`466 * 60 / 225 = 124.266666...` words per minute (approximately 124.27).
This is inside the required 2–4 minute range. Every storyboard time derives
from `Generated Files\demo\subtitles.srt`.

> Production guardrail: say **“BLOCKED / NO-GO”** on the opening and final
> frame. Do not show a wheel as built, tested, published, or installed unless
> the current native evidence proves it. Never claim performance or power
> results without measurements.

## Storyboard

| Time | Visual and shot direction | Spoken source | Evidence on screen |
| --- | --- | --- | --- |
| 00:00–00:16 | Title card: **Windows Arm Python Wheels: evidence, not hype**. Animate a green check turning into a gated decision. | `narration.txt`, cues 1–2 | Current `Generated Files\arm64-evidence\` verdict |
| 00:16–00:40 | Show the historical public-job evidence. Place a red **EXPIRED** stamp over the artifact line. | cues 3–4 | `impact-evidence.md`, historical run row |
| 00:40–00:56 | Show the official PyPI result: zero `win_arm64` files for Shapely 2.1.2. | cues 5–6 | `impact-evidence.md`, PyPI row |
| 00:56–01:18 | Four connected cards: Manager → Design → Review → Native evidence. | cues 7–8 | Current checkout and evidence directory |
| 01:18–01:40 | Show the complete-helper gate; update only if the current branch supplies and tests it. | cues 9–10 | Current source and validation log |
| 01:40–01:57 | Supply-chain gate: HTTP → blocked; HTTPS + authoritative SHA-256 → required. | cues 11–12 | `ci\install_geos.cmd` and current evidence |
| 01:57–02:49 | Impact dashboard labeled **proxy, not demand**. | cues 13–16 | `impact-evidence.md` |
| 02:49–03:19 | Evidence conveyor: helper → GEOS → physical Arm64 → ABI rows → retained records → inspect → package-owned release. | cues 17–19 | Current run logs |
| 03:19–03:45 | Final card states the exact current verdict and next gate. | cues 20–21 | `verdict.md` |

## Presenter direction

Keep all dates, byte counts, versions, proxy labels, and zero-file results
visible long enough to read. End on the first unclosed gate. Do not turn a
non-publishing native success into an official-shipment claim.
