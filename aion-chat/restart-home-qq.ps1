param([switch]$CheckOnly)

# User-run reload helper. Only the verified processes on this home's two ports qualify.
$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $workspacePath '.venv\Scripts\python.exe'
$serverPath = Join-Path $workspacePath 'aion-chat'
$targets = @()
foreach ($servicePort in @(8080, 8788)) {
    $listener = Get-NetTCPConnection -LocalPort $servicePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { continue }
    $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($serviceProcess.CommandLine -notmatch [regex]::Escape($workspacePath) -or
        ($servicePort -eq 8080 -and $serviceProcess.CommandLine -notmatch '(?:main\.py|uvicorn\s+main:app)') -or
        ($servicePort -eq 8788 -and $serviceProcess.CommandLine -notmatch 'qq_social\.worker')) {
        throw "Port $servicePort belongs to an unexpected process; nothing was stopped."
    }
    $targets += $serviceProcess
}
if ($CheckOnly) {
    Write-Host "Verified $($targets.Count) home/QQ service processes. No changes made."
    exit 0
}
if ($targets.Count -gt 0) {
    $status = Invoke-RestMethod 'http://127.0.0.1:8080/api/qq-social/status' -TimeoutSec 10
    if ($status.pending -gt 0) { throw 'QQ is replying. Wait until replies finish, then run this again.' }
}
Write-Host 'Reloading home and QQ. Please avoid sending messages during this brief restart.'
# Stop the supervisor first so it cannot restart an old worker during the transition.
foreach ($serviceProcess in $targets) { Stop-Process -Id $serviceProcess.ProcessId }
$outputPath = Join-Path $serverPath 'data\home-reload.stdout.log'
$errorPath = Join-Path $serverPath 'data\home-reload.stderr.log'
Start-Process -FilePath $pythonPath -ArgumentList '-u','main.py' -WorkingDirectory $serverPath -WindowStyle Hidden -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 2
    try {
        $status = Invoke-RestMethod 'http://127.0.0.1:8080/api/qq-social/status' -TimeoutSec 2
        if ($null -ne $status.settings.bring_home) {
            Write-Host 'Updated services are ready. Refresh the QQ page.'
            exit 0
        }
    } catch { }
}
throw 'Startup needs attention. Check data\home-reload.stderr.log.'
