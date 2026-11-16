# Instalacja SkerimAI – w PowerShell:
#   irm https://raw.githubusercontent.com/szewczyk74/SkerimAI/main/instaluj.ps1 | iex
# Instaluje uv (gdy go nie ma), potem najnowsze wydanie SkerimAI i wpisy w Claude Code, Codex i OpenCode.
# Ten sam skrypt uruchomiony ponownie aktualizuje SkerimAI (przy zamkniętych sesjach Claude Code i Codex).

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Instaluję uv – program, który pobiera Pythona i instaluje SkerimAI…"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# Program instalujący pochodzi z gałęzi main (zawsze pobierany od nowa – adres się nie zmienia, treść tak);
# instaluje najnowsze wydanie (tag) jako narzędzie uv.
# --python: zależności mają gotowe paczki dla Windows tylko do pewnej wersji Pythona – bez tego uv bierze najnowszą.
uv tool run --python 3.13 --python-preference only-managed --refresh-package skerimai `
    --from https://github.com/szewczyk74/SkerimAI/archive/refs/heads/main.zip skerimai instaluj
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalacja się nie udała – komunikat powyżej." -ForegroundColor Red
    return
}
# Polecenie `skerimai` w nowych oknach terminala (uv dopisuje swój katalog programów do PATH).
uv tool update-shell 2>$null | Out-Null
