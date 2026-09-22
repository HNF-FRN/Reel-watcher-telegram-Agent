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
# The plugin needs the real bun.exe on PATH (npm's bun.cmd shim can't be spawned by Claude Code)
$env:Path = "$env:APPDATA\npm\node_modules\bun\bin;$env:Path"
$maint = Join-Path $PSScriptRoot ".claude\skills\reel-watch\scripts\maintain.py"
# The plugin can't read its token file if it has Windows line endings (CRLF) or a BOM, e.g. after editing it in
# Notepad: it exits at startup and the bot never answers, not even with a pairing code. Rewrite it as plain LF.
$tokenFile = "$HOME\.claude\channels\telegram\.env"
if (Test-Path $tokenFile) {
    $raw = [IO.File]::ReadAllBytes($tokenFile)
    if ($raw -contains 13 -or ($raw.Length -ge 3 -and $raw[0] -eq 0xEF -and $raw[1] -eq 0xBB -and $raw[2] -eq 0xBF)) {
        $text = [Text.Encoding]::UTF8.GetString($raw).TrimStart([char]0xFEFF) -replace "`r", ''
        [IO.File]::WriteAllText($tokenFile, $text)  # .NET default: UTF-8 without BOM
        Write-Host 'Fixed the Telegram token file (Windows line endings the plugin cannot read).' -ForegroundColor Yellow
    }
} else {
    Write-Host 'No Telegram bot token saved yet: run .\setup first. The bot cannot connect without it.' -ForegroundColor Red
}

while ($true) {
    # Jobs a previous run left half-done are marked "interrupted", and old videos are cleaned up
    python $maint startup
    Write-Host "Starting reel agent... ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))" -ForegroundColor Cyan
    # The Telegram plugin is switched off in the global settings so no other Claude session on this PC grabs
    # the bot (only one connection per bot works). It is switched on here, for this session only.
    claude --channels plugin:telegram@claude-plugins-official --settings "$PSScriptRoot\.claude\bot-settings.json"
    Write-Host "Claude exited (code $LASTEXITCODE). Restarting in 10 seconds - Ctrl+C to stop." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
