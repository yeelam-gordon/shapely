# Run the Windows Arm64 validation and demo

This branch is a fork-only hackathon handoff. It does not claim that a
Windows Arm64 wheel has shipped, and it must not publish to PyPI.

## Prerequisites

- A physical Windows Arm64 machine.
- Git, GitHub CLI, GitHub Copilot CLI, Python, Node.js, and FFmpeg on `PATH`.
- GitHub CLI and Copilot CLI authenticated.
- The Slidecast skill available at either:
  - `$HOME\.copilot\skills\slidecast`
  - `C:\Users\yeelam\OneDrive - Microsoft\Documents\.copilot\skills\slidecast`

## Launch

```powershell
git clone https://github.com/yeelam-gordon/shapely.git
Set-Location shapely
git checkout hackathon/windows-arm64-evidence

$slidecastCandidates = @(
  (Join-Path $HOME '.copilot\skills\slidecast'),
  'C:\Users\yeelam\OneDrive - Microsoft\Documents\.copilot\skills\slidecast'
)
$slidecastRoot = $slidecastCandidates |
  Where-Object { Test-Path -LiteralPath (Join-Path $_ 'SKILL.md') } |
  Select-Object -First 1
if (-not $slidecastRoot) {
  throw 'Slidecast skill not found in either documented location.'
}

$prompt = Get-Content -Raw '.\Generated Files\arm64-copilot-prompt.md'
copilot --autopilot --allow-all-tools --allow-all-urls `
  --add-dir $slidecastRoot `
  --context long_context --reasoning-effort high `
  -C . -p $prompt
```

The agent must stop immediately if Windows, the process, or Python is not
native Arm64. Successful output is local evidence under
`Generated Files\arm64-evidence\` and a narrated video at
`Generated Files\demo\slidecast\build\final.mp4`.

Do not use this branch as an upstream Shapely pull request. Native findings
must first be reduced to focused, maintainer-approved upstream changes.
