# Starts the reel agent: a Claude Code session listening on your Telegram bot.
# Keep this window open. Messages only arrive while it's running.
# If Claude exits or crashes it restarts by itself after 10 seconds; press Ctrl+C during the countdown to stop.
# install-autostart.ps1 makes this window open by itself when you log in to Windows.
Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = "Reel agent (Telegram bot)"
# If this was started from inside another Claude session (e.g. the desktop app), it inherits that session's
# variables and runs as its "child": no transcript, and Telegram messages never arrive. Drop every Claude-related
# variable that isn't part of your own Windows environment settings.
$keep = @{}
foreach ($scope in 'User', 'Machine') {
    [Environment]::GetEnvironmentVariables($scope).Keys | ForEach-Object { $keep[$_.ToUpper()] = $true }
}
Get-ChildItem Env: | Where-Object {
    $_.Name -match '^(CLAUDE|ANTHROPIC_|MCP_|DISABLE_AUTOUPDATER$|DISABLE_MICROCOMPACT$)' -and -not $keep.ContainsKey($_.Name.ToUpper())
} | ForEach-Object { Remove-Item "Env:$($_.Name)" }
# At login Windows can hand Startup programs a cut-off PATH when the saved one is long, which drops
# %USERPROFILE%\.local\bin (where claude.exe lives) and the bot fails with "claude is not recognized".
# Rebuild PATH from the saved Windows settings instead of trusting the one we were started with.
$env:Path = ([Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User'), $env:Path) -join ';'
# The plugin needs the real bun.exe on PATH (npm's bun.cmd shim can't be spawned by Claude Code)
$env:Path = "$env:APPDATA\npm\node_modules\bun\bin;$env:USERPROFILE\.local\bin;$env:Path"
$maint = Join-Path $PSScriptRoot ".claude\skills\reel-watch\scripts\maintain.py"
$claude = (Get-Command claude.exe -ErrorAction SilentlyContinue).Source
if (-not $claude) { $claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe' }

$quickFails = 0
$alerted = $false
while ($true) {
    # Jobs a previous run left half-done are marked "interrupted", and old videos are cleaned up
    python $maint startup
    Write-Host "Starting reel agent... ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))" -ForegroundColor Cyan
    $started = Get-Date
    # The Telegram plugin is switched off in the global settings so no other Claude session on this PC grabs
    # the bot (only one connection per bot works). It is switched on here, for this session only.
    if (Test-Path $claude) {
        & $claude --channels plugin:telegram@claude-plugins-official --settings "$PSScriptRoot\.claude\bot-settings.json"
        $why = "Claude exited (code $LASTEXITCODE)"
        Write-Host "$why. Restarting in 10 seconds - Ctrl+C to stop." -ForegroundColor Yellow
    } else {
        $why = "claude.exe not found at $claude (is Claude Code installed?)"
        Write-Host "$why. Retrying in 10 seconds - Ctrl+C to stop." -ForegroundColor Red
    }
    # A bot that dies within a minute, three times in a row, is not going to fix itself: tell the phone once
    # (the bot can't, it isn't running). A run that lasts longer resets the count.
    if (((Get-Date) - $started).TotalSeconds -lt 60) { $quickFails++ } else { $quickFails = 0; $alerted = $false }
    if ($quickFails -ge 3 -and -not $alerted) {
        python $maint alert "⚠️ Reel agent can't start on the PC: $why. It keeps retrying every 10 s; check the 'Reel agent' window."
        $alerted = $true
    }
    Start-Sleep -Seconds 10
}
