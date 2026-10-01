# Runs every minute via the SlimLIMS-HealthWatchdog scheduled task (see
# setup_watchdog.ps1). Restarts the service if it's unresponsive -- NSSM's
# own recovery (see setup_service.ps1) already handles a crashed/exited
# process; this catches the OTHER failure mode, a hung-but-still-running
# process, which Windows' own service recovery doesn't detect on its own.

$Port = 5000
$LogPath = "C:\nssm\slim-lims-watchdog.log"

try {
    $r = Invoke-WebRequest -Uri "http://localhost:$Port/health" -TimeoutSec 10 -UseBasicParsing
    if ($r.StatusCode -ne 200) { throw "non-200 health response: $($r.StatusCode)" }
} catch {
    Add-Content -Path $LogPath -Value "$(Get-Date -Format o) unhealthy: $_"
    Restart-Service SlimLIMS -Force
}
