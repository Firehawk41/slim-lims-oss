# Runs every minute via the SlimLIMS-HealthAlert scheduled task (see
# setup_health_alert.ps1). Independent of watchdog_check.ps1/
# SlimLIMS-HealthWatchdog on purpose -- that one runs as SYSTEM (needed to
# restart the service without anyone logged in) and this one needs a real
# interactive user profile with a configured Outlook mailbox, which SYSTEM
# doesn't have. Two small single-purpose checks instead of one script
# trying to do both jobs.
#
# Edge-triggered, not level-triggered: sends exactly one email when the
# service transitions from up to down, and one more when it transitions
# back -- not a repeat email every minute it stays down. Requires 2
# consecutive contrary checks (2 minutes) before flipping state, so a
# single blip (a slow response, a momentary hiccup) doesn't fire a false
# alarm -- same reasoning as the abandoned-resolution-page heartbeat
# threshold elsewhere in this project: absence of the expected signal
# twice in a row is a much more reliable "actually down" signal than once.

$Port = 5000
$StateFile = "C:\nssm\slim-lims-alert-state.txt"

function Get-State {
    if (-not (Test-Path $StateFile)) { return @{ status = "up"; streak = 0 } }
    $lines = Get-Content $StateFile
    $status = ($lines | Where-Object { $_ -like "status=*" }) -replace "status=", ""
    $streak = ($lines | Where-Object { $_ -like "streak=*" }) -replace "streak=", ""
    if (-not $status) { $status = "up" }
    if (-not $streak) { $streak = 0 }
    return @{ status = $status; streak = [int]$streak }
}

function Set-State($status, $streak) {
    @("status=$status", "streak=$streak") | Set-Content -Path $StateFile
}

function Send-Alert($subject, $bodyText) {
    # Best-effort -- a failed email must never be treated as a reason to
    # re-flag the service as down or crash this check.
    try {
        $outlook = New-Object -ComObject Outlook.Application
        $namespace = $outlook.GetNamespace("MAPI")
        if ($namespace.Accounts.Count -eq 0) { return }
        $recipient = $namespace.Accounts.Item(1).SmtpAddress
        $mail = $outlook.CreateItem(0)  # olMailItem
        $mail.To = $recipient
        $mail.Subject = $subject
        $mail.Body = $bodyText
        $mail.Send()
    } catch {
        # Nothing further to do -- see comment above.
    }
}

$isUp = $true
try {
    $r = Invoke-WebRequest -Uri "http://localhost:$Port/health" -TimeoutSec 10 -UseBasicParsing
    if ($r.StatusCode -ne 200) { $isUp = $false }
} catch {
    $isUp = $false
}
$currentReading = if ($isUp) { "up" } else { "down" }

$state = Get-State

if ($currentReading -eq $state.status) {
    # No change from the last CONFIRMED state -- reset the streak (this
    # also self-heals from a lone blip: one contrary reading followed by
    # a normal one never accumulates toward a flip).
    Set-State $state.status 0
} else {
    $streak = $state.streak + 1
    if ($streak -ge 2) {
        # Confirmed transition -- alert once, then adopt the new state.
        if ($currentReading -eq "down") {
            Send-Alert "Slim-LIMS is DOWN" `
                "The Slim-LIMS report-generation service stopped responding to health checks as of $(Get-Date -Format 'yyyy-MM-dd HH:mm'). Reports are still being generated -- the VBA hook automatically falls back to the legacy Blank_Report_Creator macro whenever the server is unreachable -- but this needs investigating."
        } else {
            Send-Alert "Slim-LIMS has recovered" `
                "The Slim-LIMS report-generation service is responding to health checks again as of $(Get-Date -Format 'yyyy-MM-dd HH:mm')."
        }
        Set-State $currentReading 0
    } else {
        Set-State $state.status $streak
    }
}
