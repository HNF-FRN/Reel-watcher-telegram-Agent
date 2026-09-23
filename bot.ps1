# bot.ps1 - control the Reel Agent bot from the PC.
#
#   .\bot status     is it running, is Telegram connected properly, what's building
#   .\bot start      start it (in its own window, like the Startup shortcut does)
#   .\bot stop       stop it completely: restart loop, Claude window, Telegram connection
#   .\bot restart    stop + start (use after changing CLAUDE.md or settings)
#   .\bot fix        remove extra Telegram connections held by other Claude sessions
#   .\bot tasks      what's being watched or built right now
#   .\bot quota      Gemini and Claude usage left
#   .\bot autostart on|off
#   .\bot update     update yt-dlp (reel downloads) and Claude Code
#
# Run it from the project folder (bot.cmd lets you type just "bot" in cmd or PowerShell there).
param([Parameter(Position = 0)][string]$Command = 'status', [Parameter(Position = 1)][string]$Arg = '')

$root = $PSScriptRoot
$scripts = Join-Path $root '.claude\skills\reel-watch\scripts'
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Reel agent.lnk'

function Get-Proc($id) { if ($id) { Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue } }
function Get-Bot { Get-CimInstance Win32_Process -Filter "Name='claude.exe'" | Where-Object { $_.CommandLine -match '--channels\s+plugin:telegram' } }
function Get-Pollers { Get-CimInstance Win32_Process -Filter "Name='bun.exe'" | Where-Object { $_.CommandLine -match 'server\.ts' -and $_.CommandLine -notmatch '--cwd' } }
function Get-OwnerClaude($proc) {
    $p = $proc
    for ($i = 0; $i -lt 5 -and $p; $i++) {
        $p = Get-Proc $p.ParentProcessId
        if ($p -and $p.Name -eq 'claude.exe') { return $p }
    }
    return $null
}
function Get-Loop($bot) {
    $parent = Get-Proc $bot.ParentProcessId
    if ($parent -and $parent.CommandLine -match 'start\.ps1') { return $parent }
    return $null
}
function Show-Uptime($proc) {
    $span = (Get-Date) - $proc.CreationDate
    '{0}d {1}h {2}m' -f $span.Days, $span.Hours, $span.Minutes
}

function Invoke-Status {
    $bot = @(Get-Bot)
    if ($bot.Count -eq 0) {
        Write-Host 'Bot: STOPPED' -ForegroundColor Yellow
        Write-Host '  start it with:  .\bot start'
    } else {
        foreach ($b in $bot) { Write-Host ("Bot: RUNNING  (pid {0}, up {1})" -f $b.ProcessId, (Show-Uptime $b)) -ForegroundColor Green }
        if ($bot.Count -gt 1) { Write-Host '  More than one bot is running. Run: .\bot restart' -ForegroundColor Red }
    }
    $pollers = @(Get-Pollers)
    $mine = @($pollers | Where-Object { $o = Get-OwnerClaude $_; $o -and ($bot.ProcessId -contains $o.ProcessId) })
    if ($pollers.Count -eq 1 -and $mine.Count -eq 1) {
        Write-Host 'Telegram: connected (one connection, owned by the bot)' -ForegroundColor Green
    } elseif ($pollers.Count -eq 0) {
        Write-Host 'Telegram: not connected' -ForegroundColor Yellow
        if ($bot.Count -gt 0) { Write-Host '  The bot runs but its Telegram plugin did not start. Run .\bot restart; if it stays, see docs\fix-no-pairing-code.md' }
    } else {
        Write-Host ("Telegram: PROBLEM - {0} connections, {1} owned by the bot. Messages may vanish. Run: .\bot fix" -f $pollers.Count, $mine.Count) -ForegroundColor Red
    }
    Write-Host ('Autostart: ' + $(if (Test-Path $lnk) { 'on' } else { 'off' }))
    python (Join-Path $scripts 'build.py') tasks
}

function Invoke-Stop {
    $bot = @(Get-Bot)
    if ($bot.Count -eq 0) { Write-Host 'Bot is not running.'; return }
    foreach ($b in $bot) {
        $loop = Get-Loop $b
        $target = if ($loop) { $loop.ProcessId } else { $b.ProcessId }
        taskkill /PID $target /T /F | Out-Null
    }
    Start-Sleep -Seconds 2
    if (@(Get-Bot).Count -eq 0) {
        Write-Host 'Bot stopped. Builds that were running are marked interrupted on next start (/resume N continues them).' -ForegroundColor Green
    } else {
        Write-Host 'Bot did not stop. Close its window by hand.' -ForegroundColor Red
    }
}

function Invoke-Start {
    if (@(Get-Bot).Count -gt 0) { Write-Host 'Bot is already running. Use .\bot restart to restart it.'; return }
    # Explorer starts it with your normal Windows environment, never as a child of another Claude session.
    if (Test-Path $lnk) {
        Start-Process explorer.exe -ArgumentList "`"$lnk`""
    } else {
        Start-Process powershell.exe -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', "`"$root\start.ps1`"" -WorkingDirectory $root
    }
    Write-Host 'Starting' -NoNewline
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        Write-Host '.' -NoNewline
        if (@(Get-Pollers | Where-Object { $o = Get-OwnerClaude $_; $o -and $o.CommandLine -match '--channels' }).Count -gt 0) {
            Write-Host ''
            Write-Host 'Bot started and connected to Telegram. Send /menu to check.' -ForegroundColor Green
            return
        }
    }
    Write-Host ''
    Write-Host 'The bot window opened but Telegram is not connected yet. Look at the window, then run .\bot status.' -ForegroundColor Yellow
}

function Invoke-Fix {
    $bot = @(Get-Bot)
    $killed = 0
    foreach ($p in @(Get-Pollers)) {
        $o = Get-OwnerClaude $p
        if (-not $o -or -not ($bot.ProcessId -contains $o.ProcessId)) {
            $wrapper = Get-Proc $p.ParentProcessId
            $target = if ($wrapper -and $wrapper.Name -eq 'bun.exe') { $wrapper.ProcessId } else { $p.ProcessId }
            taskkill /PID $target /T /F | Out-Null
            $killed++
        }
    }
    if ($killed -eq 0) { Write-Host 'Nothing to fix: no extra Telegram connections.' -ForegroundColor Green }
    else {
        Write-Host "Removed $killed extra connection(s)." -ForegroundColor Green
        Write-Host 'If the Telegram plugin is on in another Claude session it will come back: keep it off in %USERPROFILE%\.claude\settings.json.'
        if ($bot.Count -gt 0) { Write-Host 'Restarting the bot so it reconnects cleanly...'; Invoke-Stop; Invoke-Start }
    }
}

switch ($Command.ToLower()) {
    'status'    { Invoke-Status }
    'start'     { Invoke-Start }
    'stop'      { Invoke-Stop }
    'restart'   { Invoke-Stop; Start-Sleep -Seconds 1; Invoke-Start }
    'fix'       { Invoke-Fix }
    'tasks'     { python (Join-Path $scripts 'build.py') tasks }
    'quota'     { python (Join-Path $scripts 'build.py') quota }
    'autostart' {
        if ($Arg -eq 'off') { & (Join-Path $root 'install-autostart.ps1') -Remove }
        elseif ($Arg -eq 'on') { & (Join-Path $root 'install-autostart.ps1') }
        else { Write-Host 'Use: .\bot autostart on   or   .\bot autostart off' }
    }
    'update'    {
        python -m pip install -U yt-dlp
        claude update
        Write-Host 'Done. Run .\bot restart so the bot uses the new versions.'
    }
    default     { Get-Content $PSCommandPath -TotalCount 13 | Select-Object -Skip 1 | ForEach-Object { $_ -replace '^# ?', '' } }
}
