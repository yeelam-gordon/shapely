[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$promptPath = Join-Path $PSScriptRoot 'arm64-copilot-prompt.md'

if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.git'))) {
    throw "Repository root not found at $repoRoot."
}
if (-not (Test-Path -LiteralPath $promptPath)) {
    throw "Copilot prompt not found at $promptPath."
}

$arm64 = [System.Runtime.InteropServices.Architecture]::Arm64
$os = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
$process = [System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture
$pythonMachine = (& python -c 'import platform; print(platform.machine())').Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Python architecture probe failed with exit code $LASTEXITCODE."
}
if ($os -ne $arm64 -or $process -ne $arm64 -or $pythonMachine -notmatch '^(ARM64|aarch64)$') {
    throw "Native Arm64 required. OS=$os process=$process python=$pythonMachine"
}

$slidecastRoot = Join-Path $HOME '.copilot\skills\slidecast'
if (-not (Test-Path -LiteralPath (Join-Path $slidecastRoot 'SKILL.md'))) {
    $slidecastMatches = @(
        Get-ChildItem `
            -Path (Join-Path $HOME 'OneDrive*\Documents\.copilot\skills\slidecast\SKILL.md') `
            -File `
            -ErrorAction SilentlyContinue
    )
    switch ($slidecastMatches.Count) {
        1 { $slidecastRoot = Split-Path -Parent $slidecastMatches[0].FullName }
        0 {
            throw "Slidecast skill not found below `$HOME\.copilot\skills or `$HOME\OneDrive*\Documents\.copilot\skills."
        }
        default {
            throw "Slidecast skill match was ambiguous: $($slidecastMatches.FullName -join '; ')."
        }
    }
}

$prompt = Get-Content -LiteralPath $promptPath -Raw
$arguments = @(
    '--autopilot'
    '--experimental'
    '--assisted-approval'
    '--add-dir'
    $slidecastRoot
    '--context'
    'long_context'
    '--reasoning-effort'
    'high'
    '-C'
    $repoRoot
    '-i'
    $prompt
)

& copilot @arguments
exit $LASTEXITCODE

