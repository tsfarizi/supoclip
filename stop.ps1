#requires -Version 5.1
<#
  Stop all SupoClip native processes started by run.ps1.
  Uses the PID files under .local\pids; taskkill /T also terminates
  child processes (e.g. next dev spawns node).
#>
$ErrorActionPreference = 'SilentlyContinue'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidDir = Join-Path $repo '.local\pids'

Get-ChildItem $pidDir -Filter '*.pid' | ForEach-Object {
    $name = $_.BaseName
    $pidValue = [int](Get-Content $_.FullName)
    Write-Host "Stopping $name (pid $pidValue)"
    & taskkill.exe /PID $pidValue /T /F | Out-Null
    Remove-Item $_.FullName -Force
}

# Fallback: kill anything still bound to our ports (frontend/API/worker)
$ports = 3107, 8000, 9100, 8765
foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -notmatch 'redis|postgres') {
            Write-Host "Port $port held by $($proc.ProcessName) (pid $($proc.Id)) - terminating"
            & taskkill.exe /PID $proc.Id /T /F | Out-Null
        }
    }
}

Write-Host "SupoClip stack stopped."
