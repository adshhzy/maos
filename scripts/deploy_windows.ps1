param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Python = "python",
    [string]$HostName = "127.0.0.1",
    [int]$SandboxPort = 8765,
    [int]$SimulatorPort = 8767,
    [int]$AgentServicePort = 8091,
    [int]$TemporalPort = 7233,
    [int]$TemporalUiPort = 8233,
    [string]$DataDir = "D:\dev\MAOS\temporal-data",
    [switch]$SkipAgentService,
    [switch]$UseExternalTemporal,
    [string]$TemporalAddress = "127.0.0.1:7233",
    [switch]$NoTemporalUi,
    [switch]$RestartExisting,
    [string]$MulticaBin = "",
    [string]$MulticaProfile = "desktop-api.multica.ai",
    [string]$MulticaWorkspaceId = "",
    [string]$HermesBin = "",
    [string]$HermesWorkdir = "",
    [string]$HermesGitBashPath = ""
)

$ErrorActionPreference = "Stop"

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Stop-PortOwner($port) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        if ($connection.OwningProcess -and $connection.OwningProcess -ne 0) {
            $proc = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "Stopping PID $($proc.Id) on port $port ($($proc.ProcessName))"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Assert-PortAvailable($port) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        if ($RestartExisting) {
            Stop-PortOwner $port
            Start-Sleep -Seconds 1
            return
        }
        throw "Port $port is already in use. Re-run with -RestartExisting or choose another port."
    }
}

function Wait-HttpOk($url, $name) {
    for ($i = 1; $i -le 30; $i++) {
        try {
            Invoke-RestMethod -Uri $url -TimeoutSec 3 | Out-Null
            Write-Host "$name is healthy: $url" -ForegroundColor Green
            return
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    throw "$name did not become healthy: $url"
}

Set-Location $ProjectRoot
$ProjectRoot = (Resolve-Path ".").Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$TemporalDbFile = Join-Path $DataDir "temporal.db"
$ProviderTaskDbFile = Join-Path $DataDir "maos_runtime.db"
$LogDir = Join-Path $ProjectRoot "runtime_logs"

if ([string]::IsNullOrWhiteSpace($HermesWorkdir)) {
    $HermesWorkdir = $ProjectRoot
}

Write-Step "Preparing directories"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Step "Checking ports"
Assert-PortAvailable $SandboxPort
Assert-PortAvailable $SimulatorPort
if (-not $SkipAgentService) {
    Assert-PortAvailable $AgentServicePort
}
if (-not $UseExternalTemporal) {
    Assert-PortAvailable $TemporalPort
    if (-not $NoTemporalUi) {
        Assert-PortAvailable $TemporalUiPort
    }
}

Write-Step "Creating virtual environment"
if (-not (Test-Path $VenvPython)) {
    & $Python -m venv (Join-Path $ProjectRoot ".venv")
}

Write-Step "Installing Python dependencies"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")

Write-Step "Writing .env if missing"
$envPath = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envPath)) {
    $lines = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($MulticaBin)) {
        $lines.Add("MULTICA_BIN=$MulticaBin")
    }
    $lines.Add("MULTICA_PROFILE=$MulticaProfile")
    if (-not [string]::IsNullOrWhiteSpace($MulticaWorkspaceId)) {
        $lines.Add("MULTICA_WORKSPACE_ID=$MulticaWorkspaceId")
    }
    $lines.Add("AGENT_SERVICE_HOST=$HostName")
    $lines.Add("AGENT_SERVICE_PORT=$AgentServicePort")
    $lines.Add("AGENT_SERVICE_POLL_SECONDS=2")
    $lines.Add("AGENT_SERVICE_COMMAND_TIMEOUT_SECONDS=60")
    $lines.Add("AGENT_SERVICE_HERMES_TIMEOUT_SECONDS=90")
    if (-not [string]::IsNullOrWhiteSpace($HermesBin)) {
        $lines.Add("HERMES_BIN=$HermesBin")
    }
    $lines.Add("HERMES_WORKDIR=$HermesWorkdir")
    if (-not [string]::IsNullOrWhiteSpace($HermesGitBashPath)) {
        $lines.Add("HERMES_GIT_BASH_PATH=$HermesGitBashPath")
    }
    Set-Content -Path $envPath -Value $lines -Encoding UTF8
    Write-Host "Created $envPath"
} else {
    Write-Host "Using existing $envPath"
}

$env:AGENT_SERVICE_API_BASE = "http://${HostName}:$AgentServicePort"
$env:SIMULATOR_API_BASE = "http://${HostName}:$SimulatorPort"
$env:SANDBOX_API_BASE = "http://${HostName}:$SandboxPort"
$env:MAOS_DATA_DIR = $DataDir
$env:A2A_PROVIDER_TASK_DB_FILE = $ProviderTaskDbFile

if (-not $SkipAgentService) {
    Write-Step "Starting Agent Service"
    Start-Process `
        -FilePath $VenvPython `
        -ArgumentList @("-m", "uvicorn", "services.agent_service.main:app", "--host", $HostName, "--port", "$AgentServicePort") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput (Join-Path $LogDir "agent_service.out.log") `
        -RedirectStandardError (Join-Path $LogDir "agent_service.err.log") `
        -WindowStyle Hidden
}

Write-Step "Starting Sandbox, Simulator, Temporal worker, and Temporal server"
$sandboxArgs = @(
    "sandbox_service.py",
    "--host", $HostName,
    "--port", "$SandboxPort",
    "--simulator-host", $HostName,
    "--simulator-port", "$SimulatorPort"
)
if ($UseExternalTemporal) {
    $sandboxArgs += @("--temporal-address", $TemporalAddress)
} else {
    $sandboxArgs += @(
        "--temporal-host", $HostName,
        "--temporal-port", "$TemporalPort",
        "--temporal-ui-port", "$TemporalUiPort",
        "--temporal-db-file", $TemporalDbFile
    )
    if ($NoTemporalUi) {
        $sandboxArgs += "--no-temporal-ui"
    }
}

Start-Process `
    -FilePath $VenvPython `
    -ArgumentList $sandboxArgs `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput (Join-Path $LogDir "sandbox_service.out.log") `
    -RedirectStandardError (Join-Path $LogDir "sandbox_service.err.log") `
    -WindowStyle Hidden

Write-Step "Health checks"
if (-not $SkipAgentService) {
    Wait-HttpOk "http://${HostName}:$AgentServicePort/health" "Agent Service"
}
Wait-HttpOk "http://${HostName}:$SandboxPort/api/health" "Sandbox"

Write-Step "Deployment complete"
Write-Host "Web UI:        http://${HostName}:$SandboxPort/"
Write-Host "Sandbox API:   http://${HostName}:$SandboxPort/api/health"
Write-Host "Simulator API: http://${HostName}:$SimulatorPort"
if (-not $SkipAgentService) {
    Write-Host "Agent Service: http://${HostName}:$AgentServicePort/health"
}
if (-not $UseExternalTemporal -and -not $NoTemporalUi) {
    Write-Host "Temporal UI:   http://${HostName}:$TemporalUiPort"
}
Write-Host "Temporal DB:   $TemporalDbFile"
Write-Host "Provider DB:   $ProviderTaskDbFile"
Write-Host "Logs:          $LogDir"
