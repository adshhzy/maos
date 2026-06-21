param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Python = "python",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8091,
    [switch]$Reload,
    [switch]$InstallDependencies,
    [string]$MulticaBin = "",
    [string]$MulticaProfile = "",
    [string]$MulticaWorkspaceId = ""
)

$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot
$ProjectRoot = (Resolve-Path ".").Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    & $Python -m venv (Join-Path $ProjectRoot ".venv")
}

if ($InstallDependencies) {
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
}

if (-not [string]::IsNullOrWhiteSpace($MulticaBin)) {
    $env:MULTICA_BIN = $MulticaBin
}
if (-not [string]::IsNullOrWhiteSpace($MulticaProfile)) {
    $env:MULTICA_PROFILE = $MulticaProfile
}
if (-not [string]::IsNullOrWhiteSpace($MulticaWorkspaceId)) {
    $env:MULTICA_WORKSPACE_ID = $MulticaWorkspaceId
}

$codexBin = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin") `
    -Recurse `
    -Filter "codex.exe" `
    -File `
    -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($codexBin) {
    $codexDir = Split-Path -Parent $codexBin.FullName
    $env:PATH = "$codexDir;$env:PATH"
}

$env:AGENT_SERVICE_HOST = $HostName
$env:AGENT_SERVICE_PORT = "$Port"

$argsList = @("-m", "services.agent_service", "--host", $HostName, "--port", "$Port")
if ($Reload) {
    $argsList += "--reload"
}

Write-Host "Starting Multica Agent Service at http://${HostName}:$Port"
& $VenvPython $argsList
