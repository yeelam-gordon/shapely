# Run the Windows Arm64 validation and demo

This branch is a fork-only hackathon handoff. It does not claim that a
Windows Arm64 wheel has shipped, and it must not publish to PyPI.

## Prerequisites

- A physical Windows Arm64 machine.
- Git, GitHub CLI, GitHub Copilot CLI, Python, Node.js, and FFmpeg on `PATH`.
- GitHub CLI and Copilot CLI authenticated.
- The Slidecast skill available at either:
  - `$HOME\.copilot\skills\slidecast`
  - exactly one `slidecast\SKILL.md` match beneath `$HOME\OneDrive*\Documents\.copilot\skills`

## Launch

```powershell
git clone https://github.com/yeelam-gordon/shapely.git
Set-Location shapely
git checkout hackathon/windows-arm64-evidence

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

$prompt = Get-Content -Raw '.\Generated Files\arm64-copilot-prompt.md'
copilot --autopilot --assisted-approval `
  --add-dir $slidecastRoot `
  --context long_context --reasoning-effort high `
  -C . -i $prompt
```

Assisted approval judges tool requests instead of blanket-allowing them.
The agent must stop immediately if Windows, the process, or Python is not
native Arm64. Successful output is local evidence under
`Generated Files\arm64-evidence\` and a narrated video at
`Generated Files\demo\slidecast\build\final.mp4`.

Do not use this branch as an upstream Shapely pull request. Native findings
must first be reduced to focused, maintainer-approved upstream changes.
