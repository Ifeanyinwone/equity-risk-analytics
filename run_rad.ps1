param(
    [ValidateSet("dashboard", "api", "fred-check", "live-check")]
    [string]$Mode = "dashboard"
)

$ErrorActionPreference = "Stop"
$RadRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RadRoot ".venv\Scripts\python.exe"
$Backend = Join-Path $RadRoot "backend"
$OriginalNoProxy = [Environment]::GetEnvironmentVariable("NO_PROXY", [EnvironmentVariableTarget]::Process)
$OriginalNoProxyLower = [Environment]::GetEnvironmentVariable("no_proxy", [EnvironmentVariableTarget]::Process)
$OriginalPythonPath = $env:PYTHONPATH
$OriginalApiBase = $env:RAD_API_BASE_URL
$ApiPort = $null
$DashboardPort = $null
$BackendProcess = $null

function Get-AvailableLoopbackPort([int]$FirstPort, [int]$LastPort) {
    foreach ($CandidatePort in $FirstPort..$LastPort) {
        $Listener = $null
        try {
            $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $CandidatePort)
            $Listener.Start()
            return $CandidatePort
        } catch {
            continue
        } finally {
            if ($Listener) { $Listener.Stop() }
        }
    }
    throw "No available loopback port found between $FirstPort and $LastPort."
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "RAD virtual environment not found at $Python. Create it and install backend\requirements.txt first."
}

# Merge both spellings because Windows environment variable names are
# case-insensitive. Preserve existing destinations; bypass the broken proxy
# only for the FRED API and Yahoo Finance hosts used by RAD market-data calls.
$NoProxyEntries = [System.Collections.Generic.List[string]]::new()
foreach ($ExistingValue in @(
    [Environment]::GetEnvironmentVariable("NO_PROXY", [EnvironmentVariableTarget]::Process),
    [Environment]::GetEnvironmentVariable("no_proxy", [EnvironmentVariableTarget]::Process)
)) {
    if (-not [string]::IsNullOrWhiteSpace($ExistingValue)) {
        foreach ($Entry in $ExistingValue.Split(',')) {
            $TrimmedEntry = $Entry.Trim()
            if ($TrimmedEntry -and -not ($NoProxyEntries | Where-Object { $_ -ieq $TrimmedEntry })) {
                $NoProxyEntries.Add($TrimmedEntry)
            }
        }
    }
}
foreach ($NoProxyHost in @("api.stlouisfed.org", "yahoo.com", "localhost", "127.0.0.1")) {
    if (-not ($NoProxyEntries | Where-Object { $_ -ieq $NoProxyHost })) {
        $NoProxyEntries.Add($NoProxyHost)
    }
}
$MergedNoProxy = $NoProxyEntries -join ","
[Environment]::SetEnvironmentVariable("NO_PROXY", $MergedNoProxy, [EnvironmentVariableTarget]::Process)
[Environment]::SetEnvironmentVariable("no_proxy", $MergedNoProxy, [EnvironmentVariableTarget]::Process)

Push-Location $RadRoot
$ExitCode = 0
try {
    if ($Mode -in @("dashboard", "api", "live-check")) {
        $ApiPort = Get-AvailableLoopbackPort 8765 8790
    }
    if ($Mode -in @("dashboard", "live-check")) {
        if ($Mode -eq "dashboard") { $DashboardPort = Get-AvailableLoopbackPort 8501 8530 }
        $ExistingPythonPath = $env:PYTHONPATH
        $env:PYTHONPATH = if ($ExistingPythonPath) { "$Backend$([IO.Path]::PathSeparator)$ExistingPythonPath" } else { $Backend }
        $env:RAD_API_BASE_URL = "http://127.0.0.1:$ApiPort"
        $LogDir = Join-Path $RadRoot "reports\runtime"
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        $LogStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
        $StdoutLog = Join-Path $LogDir "api-$LogStamp.out.log"
        $StderrLog = Join-Path $LogDir "api-$LogStamp.err.log"
        $BackendProcess = Start-Process -FilePath $Python -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port $ApiPort" -WorkingDirectory $RadRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
        $Ready = $false
        for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
            if ($BackendProcess.HasExited) { break }
            try {
                $Health = Invoke-RestMethod -Uri "$($env:RAD_API_BASE_URL)/health" -TimeoutSec 2
                if ($Health.status -eq "ok") { $Ready = $true; break }
            } catch { Start-Sleep -Milliseconds 500 }
            Start-Sleep -Milliseconds 250
        }
        if (-not $Ready) { throw "RAD API failed to become ready. See $StderrLog" }

        if ($Mode -eq "live-check") {
            & $Python (Join-Path $RadRoot "scripts\live_check.py")
            $ExitCode = $LASTEXITCODE
        } else {
            Write-Host "RAD is starting at http://localhost:$DashboardPort"
            & $Python -m streamlit run (Join-Path $RadRoot "frontend\app.py") --server.address 127.0.0.1 --server.port $DashboardPort --server.headless true
            $ExitCode = $LASTEXITCODE
        }
    } elseif ($Mode -eq "api") {
        $ExistingPythonPath = $env:PYTHONPATH
        $env:PYTHONPATH = if ($ExistingPythonPath) { "$Backend$([IO.Path]::PathSeparator)$ExistingPythonPath" } else { $Backend }
        Write-Host "RAD API docs: http://127.0.0.1:$ApiPort/docs"
        & $Python -m uvicorn app.main:app --reload --host 127.0.0.1 --port $ApiPort
        $ExitCode = $LASTEXITCODE
    } elseif ($Mode -eq "fred-check") {
        $env:PYTHONPATH = if ($env:PYTHONPATH) { "$Backend$([IO.Path]::PathSeparator)$env:PYTHONPATH" } else { $Backend }
        & $Python (Join-Path $RadRoot "scripts\fred_check.py")
        $ExitCode = $LASTEXITCODE
    }
}
finally {
    if ($BackendProcess -and -not $BackendProcess.HasExited) {
        Stop-Process -Id $BackendProcess.Id -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $BackendProcess.Id -Timeout 5 -ErrorAction SilentlyContinue
    }
    [Environment]::SetEnvironmentVariable("NO_PROXY", $OriginalNoProxy, [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("no_proxy", $OriginalNoProxyLower, [EnvironmentVariableTarget]::Process)
    $env:PYTHONPATH = $OriginalPythonPath
    $env:RAD_API_BASE_URL = $OriginalApiBase
    Pop-Location
}
exit $ExitCode
