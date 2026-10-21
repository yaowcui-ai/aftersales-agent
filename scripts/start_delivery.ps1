[CmdletBinding()]
param([ValidateRange(5, 300)][int]$TimeoutSeconds = 90)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$outputDir = Join-Path $projectRoot 'output\delivery'
$manifestPath = Join-Path $outputDir 'processes.json'
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $python)) { throw 'Missing .venv. Run uv sync --frozen first.' }

function Get-OwnedProcess($entry) {
    $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.pid)" -ErrorAction Stop
    $expectedTime = ([DateTimeOffset]$entry.started_at).UtcDateTime
    if (-not $details -or -not $details.ExecutablePath -or
        -not [string]::Equals($details.ExecutablePath, $python, [StringComparison]::OrdinalIgnoreCase) -or
        -not $details.CommandLine.Contains([string]$entry.marker) -or
        [Math]::Abs(($process.StartTime.ToUniversalTime() - $expectedTime).TotalSeconds) -gt 1) {
        return $null
    }
    return $process
}

function Get-OwnedRuntime($entry) {
    if (-not $entry.runtime) { return $null }
    $runtime = $entry.runtime
    $process = Get-Process -Id ([int]$runtime.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($runtime.pid)" -ErrorAction Stop
    $expectedTime = ([DateTimeOffset]$runtime.started_at).UtcDateTime
    if (-not $details -or -not $details.ExecutablePath -or
        $details.ParentProcessId -ne [int]$entry.pid -or
        -not [string]::Equals($details.ExecutablePath, $runtime.executable, [StringComparison]::OrdinalIgnoreCase) -or
        -not $details.CommandLine.Contains([string]$entry.marker) -or
        [Math]::Abs(($process.StartTime.ToUniversalTime() - $expectedTime).TotalSeconds) -gt 1) {
        return $null
    }
    return $process
}

function Test-Ready([string]$url) {
    $handler = $null
    $client = $null
    $response = $null
    try {
        # Local readiness must reach the owned listener directly, regardless of
        # machine/terminal proxy configuration. This does not alter global proxy settings.
        Add-Type -AssemblyName System.Net.Http
        $handler = [Net.Http.HttpClientHandler]::new()
        $handler.UseProxy = $false
        $client = [Net.Http.HttpClient]::new($handler)
        $client.Timeout = [TimeSpan]::FromSeconds(3)
        $response = $client.GetAsync($url).GetAwaiter().GetResult()
        return [int]$response.StatusCode -eq 200
    } catch { return $false }
    finally {
        if ($response) { $response.Dispose() }
        if ($client) { $client.Dispose() }
        elseif ($handler) { $handler.Dispose() }
    }
}

function Save-Manifest {
    @{ project_root = $projectRoot; processes = @($script:started) } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}

$lockStream = $null
$started = [Collections.Generic.List[object]]::new()
$previousEnv = @{}
try {
    try {
        $lockStream = [IO.File]::Open((Join-Path $outputDir 'processes.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch { throw 'Another delivery start/stop operation is running.' }

    if (Test-Path -LiteralPath $manifestPath) {
        $old = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        if (-not [string]::Equals($old.project_root, $projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'The process manifest belongs to another project; refusing to overwrite it.'
        }
        $owned = @($old.processes | Where-Object {
            $null -ne (Get-OwnedProcess $_) -or $null -ne (Get-OwnedRuntime $_)
        })
        if ($owned.Count -gt 0) {
            if ($owned.Count -eq 3 -and (Test-Ready 'http://127.0.0.1:8091/health') -and
                (Test-Ready 'http://127.0.0.1:8090/health') -and
                (Test-Ready 'http://127.0.0.1:8510/_stcore/health')) {
                Write-Output 'Delivery is already running: http://127.0.0.1:8510'
                return
            }
            throw 'Some recorded delivery processes are still running. Run scripts/stop_delivery.ps1 first.'
        }
    }

    foreach ($port in @(8090, 8091, 8510)) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
        try { $listener.Start() }
        catch { throw "Port $port is already in use. Existing processes were not changed." }
        finally { $listener.Stop() }
    }

    Push-Location $projectRoot
    try {
        & $python (Join-Path $PSScriptRoot 'setup_delivery.py')
        if ($LASTEXITCODE -ne 0) { throw 'Delivery setup failed. No services were started.' }
    } finally { Pop-Location }
    $basePython = (& $python -c 'import sys; print(sys._base_executable)').Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $basePython)) {
        throw 'Could not identify the virtual environment base interpreter.'
    }

    $settings = @{
        PYTHONPATH = (Join-Path $projectRoot 'src')
        PYTHONUNBUFFERED = '1'
        DELIVERY_API_URL = 'http://127.0.0.1:8090'
        DELIVERY_MCP_URL = 'http://127.0.0.1:8091/mcp'
        DELIVERY_CHECKPOINT_BACKEND = 'mysql'
    }
    foreach ($name in $settings.Keys) {
        $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $services = @(
        @{ name = 'mcp'; marker = 'aftersales_delivery.mcp_server:app'; port = 8091;
           arguments = @('-m', 'uvicorn', 'aftersales_delivery.mcp_server:app', '--host', '127.0.0.1', '--port', '8091', '--workers', '1');
           health = 'http://127.0.0.1:8091/health' },
        @{ name = 'api'; marker = 'aftersales_delivery.api:app'; port = 8090;
           arguments = @('-m', 'uvicorn', 'aftersales_delivery.api:app', '--host', '127.0.0.1', '--port', '8090', '--workers', '1');
           health = 'http://127.0.0.1:8090/health' },
        @{ name = 'ui'; marker = (Join-Path $projectRoot 'src\aftersales_delivery\ui.py'); port = 8510;
           arguments = @('-m', 'streamlit', 'run', ('"' + (Join-Path $projectRoot 'src\aftersales_delivery\ui.py') + '"'), '--server.port', '8510', '--server.address', '127.0.0.1', '--server.headless', 'true', '--browser.gatherUsageStats', 'false');
           health = 'http://127.0.0.1:8510/_stcore/health' }
    )
    foreach ($service in $services) {
        $stdout = Join-Path $outputDir "$stamp-$($service.name).stdout.log"
        $stderr = Join-Path $outputDir "$stamp-$($service.name).stderr.log"
        $process = Start-Process -FilePath $python -ArgumentList $service.arguments -WorkingDirectory $projectRoot `
            -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $entry = [pscustomobject]@{
            name = $service.name; pid = $process.Id; started_at = $process.StartTime.ToUniversalTime().ToString('o')
            executable = $python; marker = $service.marker; port = $service.port; stdout = $stdout; stderr = $stderr
            runtime = $null
        }
        $started.Add($entry)
        Save-Manifest
        # Windows virtualenv Python is a launcher; record its actual server child.
        if (-not [string]::Equals($basePython, $python, [StringComparison]::OrdinalIgnoreCase)) {
            $childDeadline = [DateTime]::UtcNow.AddSeconds(5)
            while ([DateTime]::UtcNow -lt $childDeadline) {
                $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.Id)" |
                    Where-Object {
                        $_.ExecutablePath -and $_.CommandLine -and
                        [string]::Equals($_.ExecutablePath, $basePython, [StringComparison]::OrdinalIgnoreCase) -and
                        $_.CommandLine.Contains([string]$service.marker)
                    })
                if ($children.Count -eq 1) {
                    $runtimeProcess = Get-Process -Id $children[0].ProcessId -ErrorAction Stop
                    $entry.runtime = [pscustomobject]@{
                        pid = $runtimeProcess.Id; executable = $basePython
                        started_at = $runtimeProcess.StartTime.ToUniversalTime().ToString('o')
                    }
                    Save-Manifest
                    break
                }
                $process.Refresh()
                if ($process.HasExited) { throw "$($service.name) exited. Inspect $stderr" }
                Start-Sleep -Milliseconds 100
            }
            if (-not $entry.runtime) { throw "Could not identify the $($service.name) Python runtime child." }
        }
        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        $ready = $false
        while ([DateTime]::UtcNow -lt $deadline) {
            $process.Refresh()
            if ($process.HasExited) { throw "$($service.name) exited. Inspect $stderr" }
            if (Test-Ready $service.health) { $ready = $true; break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw "$($service.name) readiness timed out. Inspect $stderr" }
        Write-Output "$($service.name) ready on port $($service.port)."
    }
    Write-Output 'UI: http://127.0.0.1:8510'
    Write-Output 'API docs: http://127.0.0.1:8090/docs'
    Write-Output "Credentials file (local only): $(Join-Path $outputDir 'credentials.txt')"
    Write-Output "Logs and process manifest: $outputDir"
} catch {
    # Roll back only processes whose executable, module and start time still match.
    $failure = $_
    for ($index = $started.Count - 1; $index -ge 0; $index--) {
        try {
            $ownedRuntime = Get-OwnedRuntime $started[$index]
            if ($ownedRuntime) { Stop-Process -Id $ownedRuntime.Id -Force -ErrorAction Stop }
            $ownedProcess = Get-OwnedProcess $started[$index]
            if ($ownedProcess) { Stop-Process -Id $ownedProcess.Id -Force -ErrorAction Stop }
        } catch { Write-Warning "Could not stop recorded $($started[$index].name); inspect the manifest." }
    }
    throw $failure
} finally {
    foreach ($name in $previousEnv.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnv[$name], 'Process')
    }
    if ($lockStream) { $lockStream.Dispose() }
}
