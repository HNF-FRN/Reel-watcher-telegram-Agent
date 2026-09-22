# One-time setup (run again any time; it just overwrites):
#   1. a shortcut in your Startup folder, so the reel agent window opens when you log in
#   2. a Task Scheduler task that sends the weekly reel digest to Telegram (Sundays 10:00)
# Undo: run with -Remove
param([switch]$Remove)

$root = $PSScriptRoot
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) "Reel agent.lnk"
$task = "ReelAgentWeeklyDigest"

if ($Remove) {
    Remove-Item $lnk -ErrorAction SilentlyContinue
    schtasks /Delete /TN $task /F 2>$null | Out-Null
    Write-Host "Removed autostart shortcut and weekly digest task."
    return
}

$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = (Get-Command powershell.exe).Source
$s.Arguments = "-NoExit -ExecutionPolicy Bypass -File `"$root\start.ps1`""
$s.WorkingDirectory = $root
$s.Description = "Reel agent: Claude Code listening on the Telegram bot"
$s.Save()
Write-Host "Autostart shortcut: $lnk"

$python = (Get-Command python.exe).Source
$script = Join-Path $root ".claude\skills\reel-watch\scripts\maintain.py"
schtasks /Create /TN $task /SC WEEKLY /D SUN /ST 10:00 /F /TR "`"$python`" `"$script`" digest --send" | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host "Weekly digest task: '$task' (Sundays 10:00)" } else { Write-Host "Could not create the digest task" }
