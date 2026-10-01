# Registers a SECOND, independent Scheduled Task that hits /health every
# minute and emails on an up<->down transition. Deliberately NOT folded
# into SlimLIMS-HealthWatchdog/watchdog_check.ps1: that task runs as
# SYSTEM (needed so it can restart the service with nobody logged in),
# but Outlook COM automation (the same credential-free technique already
# used by send_weekly_mappings_email.py) needs a real interactive user
# profile with a configured mailbox, which SYSTEM does not have. Two
# small single-purpose tasks instead of one task trying to do both jobs.
#
# NOT run automatically -- review before running. Run from a normal
# (non-elevated) PowerShell prompt, as the account whose Outlook should
# send the alert -- unlike setup_service.ps1/setup_watchdog.ps1, this one
# must NOT be run elevated/as a different account, since the task is
# created to run AS the account invoking this script.

$ErrorActionPreference = "Stop"

$TaskName = "SlimLIMS-HealthAlert"

# See setup_watchdog.ps1's own comment for why this points at a real
# file rather than a stringified -Command one-liner (a real, already-hit
# nested-quoting bug class).
#
# Launched via a small .vbs wrapper (run_health_alert_hidden.vbs), NOT
# powershell.exe directly with -WindowStyle Hidden -- confirmed real,
# live, and disruptive: that flashed a console window and stole keyboard
# focus every single minute once this task started running, because
# (unlike SlimLIMS-HealthWatchdog, which runs as SYSTEM in an isolated,
# non-interactive session) this task runs inside a real interactive
# desktop session, where PowerShell's own window-hiding isn't reliable.
# WScript.Shell.Run's window-style parameter goes through a different,
# genuinely invisible path. See the .vbs file's own header for detail.
$HiddenLauncher = Join-Path $PSScriptRoot "run_health_alert_hidden.vbs"

$TaskRun = "wscript.exe /B /Nologo `"$HiddenLauncher`""
$RunAsUser = "$env:USERDOMAIN\$env:USERNAME"

# No /RP (password) on purpose -- omitting it for a normal (non-SYSTEM)
# account registers the task as "run only when user is logged on"
# (interactive token, no stored password needed), which is exactly the
# right semantic here: Outlook automation only works in a real logged-in
# session anyway, so there's nothing useful this task could do while
# $RunAsUser is logged off.
#
# No /RL HIGHEST either -- this task only ever does an HTTP GET and
# (on a real transition) sends an Outlook email, neither of which needs
# elevation, unlike the watchdog's Restart-Service call.
& schtasks.exe /Create /TN $TaskName /TR $TaskRun /SC MINUTE /MO 1 /RU $RunAsUser /F
if ($LASTEXITCODE -ne 0) {
    Write-Host "schtasks.exe failed (exit code $LASTEXITCODE) -- the task was NOT registered." -ForegroundColor Red
    exit 1
}

Write-Host "Task '$TaskName' registered -- checks /health every minute as $RunAsUser,"
Write-Host "sends one email on an up->down transition and one more on down->up,"
Write-Host "via Outlook (must be signed in under this account)."
Write-Host ""
Write-Host "This only runs while $RunAsUser is logged on -- by design, since Outlook"
Write-Host "automation needs that anyway. It does NOT replace SlimLIMS-HealthWatchdog"
Write-Host "(still SYSTEM-run, still handles the actual service restart)."
