[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$outputDir = Join-Path $projectRoot 'output\delivery'
$manifestPath = Join-Path $outputDir 'processes.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    Write-Output 'No delivery process manifest. No processes were changed.'
    return
}
$lockStream = $null
try {
    try {
        $lockStream = [IO.File]::Open((Join-Path $outputDir 'processes.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch { throw 'Another delivery start/stop operation is running.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if (-not [string]::Equals($manifest.project_root, $projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The process manifest belongs to another project. No processes were changed.'
    }
    $remaining = [Collections.Generic.List[object]]::new()
    $entries = @($manifest.processes)
    for ($index = $entries.Count - 1; $index -ge 0; $index--) {
        $entry = $entries[$index]
        try {
            # Stop the recorded server child before its virtualenv launcher.
            if ($entry.runtime) {
                $runtime = $entry.runtime
                $runtimeProcess = Get-Process -Id ([int]$runtime.pid) -ErrorAction SilentlyContinue
                if ($runtimeProcess) {
                    $runtimeDetails = Get-CimInstance Win32_Process -Filter "ProcessId = $($runtime.pid)" -ErrorAction Stop
                    $runtimeTime = ([DateTimeOffset]$runtime.started_at).UtcDateTime
                    if ($runtimeDetails -and $runtimeDetails.ExecutablePath -and
                        $runtimeDetails.ParentProcessId -eq [int]$entry.pid -and
                        [string]::Equals($runtimeDetails.ExecutablePath, $runtime.executable, [StringComparison]::OrdinalIgnoreCase) -and
                        $runtimeDetails.CommandLine.Contains([string]$entry.marker) -and
                        [Math]::Abs(($runtimeProcess.StartTime.ToUniversalTime() - $runtimeTime).TotalSeconds) -le 1) {
                        Stop-Process -Id $runtimeProcess.Id -Force -ErrorAction Stop
                        Write-Output "Stopped delivery $($entry.name) runtime (PID $($runtimeProcess.Id))."
                    } else {
                        Write-Warning "Runtime PID $($runtime.pid) no longer matches; skipped."
                    }
                }
            }
            $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
            if (-not $process) { continue }
            $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.pid)" -ErrorAction Stop
            $expectedTime = ([DateTimeOffset]$entry.started_at).UtcDateTime
            if (-not $details -or -not $details.ExecutablePath -or
                -not [string]::Equals($details.ExecutablePath, $python, [StringComparison]::OrdinalIgnoreCase) -or
                -not $details.CommandLine.Contains([string]$entry.marker) -or
                [Math]::Abs(($process.StartTime.ToUniversalTime() - $expectedTime).TotalSeconds) -gt 1) {
                Write-Warning "PID $($entry.pid) no longer matches the recorded delivery process; skipped."
                continue
            }
            Stop-Process -Id $process.Id -Force -ErrorAction Stop
            Write-Output "Stopped delivery $($entry.name) (PID $($entry.pid))."
        } catch {
            $remaining.Add($entry)
            Write-Warning "Could not verify or stop PID $($entry.pid); left untouched."
        }
    }
    if ($remaining.Count -gt 0) {
        @{ project_root = $projectRoot; processes = @($remaining) } |
            ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        throw 'Some recorded processes could not be verified or stopped. Their manifest entries were kept.'
    }
    Remove-Item -LiteralPath $manifestPath
    Write-Output 'Delivery processes stopped. Database services, data and other processes were preserved.'
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
