# Registers a Scheduled Task that hits the Slim-LIMS API's /health
# endpoint every minute. A crashed process is already handled by NSSM's
# own Recovery action (see setup_service.ps1) -- this catches the OTHER
# failure mode, a hung-but-still-running process, which Windows' service
# recovery does not detect on its own.
#
# NOT run automatically -- review before running. Run from an elevated
# PowerShell prompt, after setup_service.ps1.

$ErrorActionPreference = "Stop"

$TaskName = "SlimLIMS-HealthWatchdog"
$Port = 5000
$LogPath = "C:\nssm\slim-lims-watchdog.log"

# The actual check lives in watchdog_check.ps1, next to this script --
# pointing the scheduled task at a real file instead of building a
# stringified -Command one-liner avoids a whole class of nested-quoting
# bugs (an earlier version of this line had exactly that bug: `\"` isn't
# a valid escape in PowerShell -- backslash doesn't escape anything here,
# only backtick does -- so the string just ended early and everything
# after it failed to parse).
$CheckScript = Join-Path $PSScriptRoot "watchdog_check.ps1"

# Using schtasks.exe directly, NOT Register-ScheduledTask/
# New-ScheduledTaskPrincipal -- confirmed the hard way that the latter
# writes a broken task for this exact scenario (SYSTEM + RunLevel
# Highest). Despite requesting `-LogonType ServiceAccount`, the actual
# XML written to C:\Windows\System32\Tasks ended up with
# <LogonType>InteractiveToken</LogonType> -- wrong for an account with no
# real interactive session, so every single execution attempt failed
# with LastTaskResult 1 even though the task file itself was completely
# intact (right permissions, valid XML -- confirmed via Get-Content and
# Get-Acl, ruling out corruption or an ACL problem before landing on
# this). schtasks.exe is the older, more battle-tested tool for exactly
# this SYSTEM-account case and doesn't have this quirk.
#
# /SC MINUTE /MO 1 is a genuinely indefinite recurring schedule -- no
# artificial duration cap needed here, unlike New-ScheduledTaskTrigger's
# -RepetitionDuration (which has its own real upper limit; see git log
# for that earlier fix).
#
# NOTE: $CheckScript is assumed to contain no spaces (true for the
# current real path) -- deliberately not quoted within $TaskRun below,
# since embedding quotes inside a native command's own argument string
# is exactly the class of bug that bit this script twice already. If
# this ever gets deployed under a space-containing path, this will need
# real quoting handled carefully, not just wrapping it in `"..."`.
#
# -ExecutionPolicy Bypass is required here, not optional -- confirmed by
# running watchdog_check.ps1 manually and hitting "running scripts is
# disabled on this system" (UnauthorizedAccess), which is exactly what
# LastTaskResult 1 turned out to mean even AFTER the LogonType fix above
# was verified correct (SYSTEM SID, no LogonType element, confirmed via
# both `schtasks /Query /XML` and reading the task file directly). This
# is a second, independent bug from the LogonType one -- the machine's
# default execution policy blocks unsigned scripts for every account,
# SYSTEM included, and nothing about registering the task under SYSTEM
# changes that. -Scope Process on that flag would be pointless here
# (this is a fresh process every run, not a persistent session), so it
# has to be a per-invocation command-line flag, same as every manual
# setup_service.ps1/setup_watchdog.ps1 run this project has needed.
$TaskRun = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $CheckScript"

# $ErrorActionPreference = "Stop" (top of this script) does NOT catch a
# native command's non-zero exit code -- only PowerShell/cmdlet errors.
# Confirmed the hard way: an earlier version of this script printed its
# "registered successfully" message even when schtasks.exe had just
# failed outright (Access is denied, not elevated) -- exactly the kind
# of misleading feedback that made this watchdog's actual state so hard
# to pin down in the first place. Check $LASTEXITCODE explicitly.
& schtasks.exe /Create /TN $TaskName /TR $TaskRun /SC MINUTE /MO 1 /RU SYSTEM /RL HIGHEST /F
if ($LASTEXITCODE -ne 0) {
    Write-Host "schtasks.exe failed (exit code $LASTEXITCODE) -- the task was NOT registered." -ForegroundColor Red
    Write-Host "Common cause: this needs to run from an elevated (Administrator) PowerShell prompt."
    exit 1
}

Write-Host "Watchdog task '$TaskName' registered -- checks /health every minute,"
Write-Host "restarts the SlimLIMS service (and logs to $LogPath) on failure."
