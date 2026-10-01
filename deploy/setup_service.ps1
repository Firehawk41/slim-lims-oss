# Sets up the Slim-LIMS report-generation API as a Windows Service on
# this machine (LAB-SERVER, per the current hosting decision), using NSSM.
#
# NOT run automatically as part of building this repo -- this makes real,
# persistent changes (installs a third-party tool, registers a Windows
# Service, opens a firewall port, creates a Scheduled Task). Review before
# running. Run from an elevated (Administrator) PowerShell prompt.

$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $PSScriptRoot
$ServiceName = "SlimLIMS"
$Port = 5000
$NssmDir = "C:\nssm"
$NssmExe = "$NssmDir\nssm.exe"

# Office LAN subnet -- the firewall rule below only accepts inbound
# connections from this range, not from anywhere that can route to this
# machine (a VPN client, a bridged guest network, anything upstream).
# CONFIRM this matches the real office subnet before running -- adjust if
# it doesn't (this default matches the server's own known LAN IP,
# 192.0.2.10, from prior testing).
$OfficeSubnet = "192.0.2.0/24"

# nssm.exe doesn't raise a PowerShell error on failure -- it's a native
# exe, so $ErrorActionPreference = "Stop" (above) does nothing for it,
# same class of gap already found and fixed in setup_watchdog.ps1's own
# schtasks.exe call. Every nssm invocation below goes through this so a
# failed `install`/`set` doesn't silently leave a half-configured service
# that this script then reports as "installed and started" anyway.
function Invoke-Nssm {
    & $NssmExe @args
    if ($LASTEXITCODE -ne 0) {
        Write-Host "nssm $($args -join ' ') failed (exit code $LASTEXITCODE) -- aborting." -ForegroundColor Red
        exit 1
    }
}

# --- 1. Install NSSM, if not already present ---
if (-not (Test-Path $NssmExe)) {
    Write-Host "NSSM not found at $NssmExe -- download it manually from"
    Write-Host "https://nssm.cc/download, extract the win64 nssm.exe to $NssmDir,"
    Write-Host "and re-run this script. (Not auto-downloaded here since that's a"
    Write-Host "third-party binary running with Administrator rights.)"
    exit 1
}

# --- 2. Confirm the venv + dependencies exist ---
$PythonExe = Join-Path $RepoDir "venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "No venv found at $PythonExe -- set it up first:"
    Write-Host "  cd $RepoDir"
    Write-Host "  python -m venv venv"
    Write-Host "  .\venv\Scripts\pip install -r requirements.txt"
    Write-Host "  .\venv\Scripts\pip install -e ..\slim-domain -e ..\report-engine"
    exit 1
}
if (-not (Test-Path (Join-Path $RepoDir ".env"))) {
    Write-Host "No .env found at $RepoDir\.env -- copy .env.template to .env and set"
    Write-Host "the real DB_URL before starting the service."
    exit 1
}

# --- 3. Register the service (uvicorn run via the venv's own python.exe) ---
Invoke-Nssm install $ServiceName $PythonExe "-m uvicorn app:app --host 0.0.0.0 --port $Port"
Invoke-Nssm set $ServiceName AppDirectory $RepoDir
Invoke-Nssm set $ServiceName DisplayName "Slim-LIMS Report Generation API"
Invoke-Nssm set $ServiceName Description "FastAPI server wrapping slim_report_engine for lab report generation."

# Recovery: restart on 1st/2nd/subsequent failure, 5s delay each time --
# most crashes then self-heal with no one needing to notice.
Invoke-Nssm set $ServiceName AppExit Default Restart
Invoke-Nssm set $ServiceName AppRestartDelay 5000

# --- 3b. Service account ---
# Without this, NSSM defaults to Local System, which has no network
# identity at all and can't see any mapped or real network share --
# every real request would fail with "file not found". Using this
# machine's own interactive account for now (deliberately NOT a new
# dedicated service account yet -- see the plan notes: creating a new
# local Windows account is one of the most commonly security-monitored
# events there is, and that conversation with IT hasn't happened yet).
# Get-Credential prompts securely (masked, not echoed) rather than
# taking a password as a script argument or hardcoding it here.
$Cred = Get-Credential -UserName "$env:COMPUTERNAME\$env:USERNAME" `
    -Message "Enter this account's Windows password -- the service will run as this identity"
$PlainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Cred.Password)
)
Invoke-Nssm set $ServiceName ObjectName $Cred.UserName $PlainPassword
Remove-Variable PlainPassword

# --- 3c. Log capture ---
# Nothing captured this before -- server.log/server.err.log already exist
# as filenames from this session's manual testing (gitignored), reused
# here for consistency. Rotate at 10MB so this can't grow unbounded.
Invoke-Nssm set $ServiceName AppStdout (Join-Path $RepoDir "server.log")
Invoke-Nssm set $ServiceName AppStderr (Join-Path $RepoDir "server.err.log")
Invoke-Nssm set $ServiceName AppRotateFiles 1
Invoke-Nssm set $ServiceName AppRotateOnline 1
Invoke-Nssm set $ServiceName AppRotateBytes 10485760

# PYTHONUNBUFFERED=1 -- without this, server.log (stdout, uvicorn's
# access log) can sit arbitrarily stale while server.err.log (stderr,
# where Python's own logging module writes -- unbuffered by the C
# runtime regardless of redirection) stays current. Confirmed the hard
# way: after a real 3-report test, server.err.log showed all three
# SUCCESS lines immediately, but server.log's access-log lines for the
# same requests never appeared at all -- classic full-block-buffering of
# stdout when it's redirected to a file instead of a terminal, not a bug
# in uvicorn or this app. Without this fix, server.log can't be trusted
# for real-time troubleshooting.
Invoke-Nssm set $ServiceName AppEnvironmentExtra "PYTHONUNBUFFERED=1"

# --- 4. Open the firewall for inbound traffic on $Port, office subnet only ---
# Required for any lab machine OTHER than this one to reach the service.
# Second-machine reachability against a manually-started instance was
# already confirmed once (real form, real round trip) -- this specific
# NSSM-wrapped, different-service-account instance still needs its own
# real-request retest, not assumed to behave identically.
# -RemoteAddress scopes this to $OfficeSubnet -- without it the rule
# accepts a connection from anything that can route to this machine at
# all (a VPN client, a bridged guest network), not just the lab LAN.
#
# Checked for an existing rule first (idempotent re-run), rather than
# swallowing New-NetFirewallRule's own errors with -ErrorAction
# SilentlyContinue as this used to -- a genuine failure here (blocked by
# group policy, firewall service down) needs to stop the script loudly,
# not let it report "installed and started" while every other lab
# machine silently can't reach it.
$FirewallRuleName = "Slim-LIMS API ($Port)"
if (-not (Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $FirewallRuleName `
        -Direction Inbound -Protocol TCP -LocalPort $Port -RemoteAddress $OfficeSubnet -Action Allow | Out-Null
}

# --- 5. Start it ---
Invoke-Nssm start $ServiceName

Write-Host ""
Write-Host "Service '$ServiceName' installed and started."
Write-Host "Check status:   nssm status $ServiceName"
Write-Host "Restart:        Restart-Service $ServiceName"
Write-Host "View in GUI:    services.msc"
Write-Host ""
Write-Host "Next: register the health-check watchdog with setup_watchdog.ps1 (already"
Write-Host "written, no changes needed -- just run it), then verify:"
Write-Host "  1. GET http://localhost:$Port/health responds"
Write-Host "  2. A real report-generation request via the actual VBA hook succeeds"
Write-Host "  3. Kill the process in Task Manager -- NSSM should restart it automatically"
Write-Host "  4. Reachability from a second machine -- confirmed once already for a"
Write-Host "     manually-started instance, but retest THIS service/account combination"
