# Windows Arm64 validation and Slidecast recording task

You are the accountable execution agent on a physical Windows Arm64 machine.
Keep repository modifications and generated evidence inside this checkout's declared paths. Read-only access to the resolved Slidecast directory under HOME, and dependency installation there when required, are explicitly permitted. Matching-ABI test environments outside the checkout are a temporary exception only when they live under a timestamped `$env:TEMP\shapely-arm64-evidence\<run-id>\venvs` root (or an exact equivalent path), and all retained logs/results still stay under the declared in-checkout evidence directory. Temporary venvs may be removed only after command logs are persisted; failed retained evidence must not be deleted. Do not launch subagents.

## Goal

Produce honest, reproducible native Windows Arm64 evidence for Shapely wheels,
then render the supplied demo material as a narrated Slidecast MP4. Never
publish to PyPI, create a release, push a tag, modify repository secrets, or
claim success without retained artifacts and command evidence.

## Read first

1. Read `AGENTS.md` if it exists, plus `CONTRIBUTING.md`,
   `.github/workflows/release.yml`, `pyproject.toml`, `ci/install_geos.cmd`,
   and every file under `Generated Files\demo\`.
2. Locate Slidecast in this order:
   `$HOME\.copilot\skills\slidecast`, then a bounded search under
   `$HOME\OneDrive*\Documents\.copilot\skills` for exactly one
   `slidecast\SKILL.md` match. Use:

   ```powershell
   $slidecastRoot = Join-Path $HOME '.copilot\skills\slidecast'
   if (-not (Test-Path -LiteralPath (Join-Path $slidecastRoot 'SKILL.md'))) {
     $slidecastMatches = Get-ChildItem `
       -Path (Join-Path $HOME 'OneDrive*\Documents\.copilot\skills\slidecast\SKILL.md') `
       -File `
       -ErrorAction SilentlyContinue
     switch (@($slidecastMatches).Count) {
       1 { $slidecastRoot = Split-Path -Parent (@($slidecastMatches)[0].FullName) }
       0 { throw "Slidecast skill not found at `$HOME\.copilot\skills\slidecast` or any `$HOME\OneDrive*\Documents\.copilot\skills\slidecast\SKILL.md` match." }
       default { throw "Slidecast skill match was ambiguous: $(@($slidecastMatches).FullName -join '; ')." }
     }
   }
   ```
   Read its `SKILL.md`, `references\storyboard-schema.md`, and
   `references\video-pipeline.md`. Stop with a clear error if unavailable.
3. Record `git rev-parse HEAD`, `git status --short --branch`, all remotes,
   Windows version, processor architecture, Python version/architecture, and
   installed Visual Studio Arm64 components.

## Hard architecture gate

Run:

```powershell
$ErrorActionPreference = 'Stop'
$os = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
$process = [System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture
$pythonMachine = python -c "import platform; print(platform.machine())"
if ($os -ne [System.Runtime.InteropServices.Architecture]::Arm64 -or $process -ne [System.Runtime.InteropServices.Architecture]::Arm64 -or $pythonMachine.Trim() -notmatch '^(ARM64|aarch64)$') {
  throw "Native Arm64 required. OS=$os process=$process python=$pythonMachine"
}
```

Do not continue under x64 emulation.

## Evidence directory and rules

Create only `Generated Files\arm64-evidence\` and
`Generated Files\demo\slidecast\` for generated output. Save:

- `environment.json`
- `commands.jsonl` with command, start/end UTC, exit code, and log path
- raw logs
- built wheels and SHA-256 values
- per-wheel tag, PE, dependency, license, installation, and test results
- `verdict.md`

Do not overwrite failed evidence. Use a new timestamped run directory.
Every failure must remain visible and must make the final verdict blocked.

## Supply-chain gate

Before building, inspect `ci/install_geos.cmd`.

- If it still downloads GEOS over HTTP or does not verify an authoritative
  SHA-256 before extraction, do not run that script against the network.
- Record the exact source lines and mark production readiness blocked.
- Do not invent a digest.
- A local experimental build may proceed only from an explicitly supplied,
  independently verified GEOS archive/digest. Otherwise skip the build and
  continue with a blocked evidence/demo result.

## Native build and wheel checks

Discover and use the repository's existing build commands. Run the smallest
targeted native Arm64 lane first. If prerequisites and an authoritative GEOS
digest are available, build the configured Windows Arm64 wheel set without
publishing.

For every produced wheel:

1. Parse its filename with `packaging.utils.parse_wheel_filename`.
2. Read its single `.dist-info/WHEEL` file and parse every `Tag:` with
   `packaging.tags.parse_tag`.
3. Require exact filename/metadata tag-set equality and platform
   `win_arm64`.
4. Inspect every `.pyd` and bundled `.dll`; require PE machine `0xAA64`.
5. Run `python -m delvewheel show <wheel>` and retain one report per wheel.
6. Verify required GEOS and MSVC license files byte-for-byte.
7. Create a clean, matching native interpreter environment outside the
   checkout, using the temporary venv exception above only; install the wheel,
   run `pip check`, an isolated Shapely import, and `pytest --pyargs
   shapely.tests`. Keep the command logs/results in the in-checkout evidence
   directory, and do not delete failed retained evidence.
8. Test clean uninstall. Test upgrade only when a compatible prior official
   Arm64 wheel exists; otherwise mark upgrade untested rather than using x64.

Never let a successful build substitute for installation and runtime tests.
Do not claim seven-ABI closure unless all configured CPython and free-threaded
rows actually run with matching native interpreters.

## Final evidence verdict

`verdict.md` must be one of:

- `PASS FOR NON-PUBLISHING NATIVE EVIDENCE` only when every produced wheel
  passes tag, PE, repair, license, clean-install, runtime, package-test, and
  uninstall gates.
- `BLOCKED / NO-GO` for any missing prerequisite, skipped required row,
  failed command, absent artifact, supply-chain issue, or architecture
  mismatch.

List exact tested and untested items. Do not claim official shipment: that
requires a package-owned tagged release and matching official PyPI filenames
and SHA-256 values, which this task forbids.

## Update the demo truthfully

The files in `Generated Files\demo\` are pre-generated blocked-state inputs.
Preserve all demand-proxy limitations.

- If native evidence is blocked, retain the BLOCKED / NO-GO narrative and add
  the current run's exact blocker and evidence path.
- If the non-publishing evidence verdict passes, update only statements
  directly disproved by this run. Continue to say that no official wheel
  shipped and that no performance or power result was measured.
- Keep narration and subtitle spoken text exactly equal.
- Keep the final video between two and four minutes.

## Build the Slidecast package

Following the installed Slidecast skill literally:

1. Copy the complete Slidecast `templates\` directory to
   `Generated Files\demo\slidecast\`.
2. Author an engineering-focused dark animated deck from
   `demo-script.md`, `shot-list.md`, `impact-evidence.md`, and the current
   Arm64 evidence. Preserve source paths at the top of slides and reserve the
   bottom strip for subtitles.
3. Create `storyboard.json` with deterministic `window.master` cue labels and
   narration steps. Use the corrected narration as the spoken source.
4. Validate cue labels, storyboard structure, and exact narration/subtitle
   text before rendering.
5. Create and use a dedicated native Python venv at
   `Generated Files\demo\slidecast\.venv`. This is the only persistent Python
   dependency environment for the Slidecast run. Define `$slidecastVenv` and
   `$slidecastPython`, then install prerequisites only when missing:

```powershell
$slidecastVenv = Join-Path (Get-Location) 'Generated Files\demo\slidecast\.venv'
$null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $slidecastVenv)
if (-not (Test-Path -LiteralPath $slidecastVenv)) {
  python -m venv $slidecastVenv
}
$slidecastPython = Join-Path $slidecastVenv 'Scripts\python.exe'
& $slidecastPython -m pip install -r "$slidecastRoot\scripts\requirements.txt"
npm --prefix "$slidecastRoot\scripts" install
npx --prefix "$slidecastRoot\scripts" playwright install chromium
ffmpeg -version
ffprobe -version
```

6. Render:

```powershell
& $slidecastPython "$slidecastRoot\scripts\build.py" `
  --storyboard '.\Generated Files\demo\slidecast\storyboard.json' `
  --deck '.\Generated Files\demo\slidecast\deck.html' `
  --package-root '.\Generated Files\demo\slidecast' `
  --out '.\Generated Files\demo\slidecast\build'
```

7. Verify `final.mp4` with `ffprobe`: H.264 video, AAC audio, 1920x1080,
   duration between 120 and 240 seconds, and the requested subtitle stream or
   burned captions. Sample frames at every slide midpoint and reject overlap,
   unreadable text, missing source labels, or subtitle collisions.

## Finish

Write a concise final response containing:

- native verdict and evidence directory
- exact wheels and SHA-256 values, if any
- test counts and failures
- explicitly untested items
- Slidecast deck/storyboard paths
- final MP4 path, duration, codecs, resolution, and subtitle mode
- git status

Do not commit, push, publish, or delete evidence. The human owner decides what
to retain and which focused upstream PRs to open.
