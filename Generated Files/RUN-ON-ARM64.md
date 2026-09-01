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

& '.\Generated Files\run-arm64-copilot.ps1'
```

The launcher fails before starting Copilot unless Windows, the current process,
and Python are native Arm64. Copilot starts in experimental assisted-approval
mode, which uses the safety judge instead of granting blanket tool access.
Successful output is local evidence under
`Generated Files\arm64-evidence\` and a narrated video at
`Generated Files\demo\slidecast\build\final.mp4`.

Do not use this branch as an upstream Shapely pull request. Native findings
must first be reduced to focused, maintainer-approved upstream changes.
