' Launches health_alert_check.ps1 with a genuinely invisible window.
'
' PowerShell's own "-WindowStyle Hidden" is NOT reliable for this when a
' Scheduled Task runs inside a real interactive desktop session (as
' opposed to an isolated SYSTEM session) -- confirmed real, not
' theoretical: it was flashing a console window and stealing keyboard
' focus every single minute once this task started running. WScript.Shell
' .Run's window-style parameter (0 = hidden) goes through a different,
' more reliable path (real hidden CreateProcess startup info, not a
' console host window that has to be hidden after the fact), which is
' also the same technique modReportEngineHook.bas already relies on
' elsewhere in this project for launching something without disrupting
' the technician's foreground window.
Set objShell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\health_alert_check.ps1"""
objShell.Run cmd, 0, False
