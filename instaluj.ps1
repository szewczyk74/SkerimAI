# Instalacja SkerimAI – w PowerShell:
#   irm https://raw.githubusercontent.com/szewczyk74/SkerimAI/main/instaluj.ps1 | iex
# Instaluje uv (gdy go nie ma), potem najnowsze wydanie SkerimAI i wpisy w Claude Code, Codex i OpenCode.
# Ten sam skrypt uruchomiony ponownie aktualizuje SkerimAI (przy zamkniętych sesjach Claude Code i Codex).

# Własny zakres: `iex` wykonuje skrypt w oknie użytkownika – zmienne i ustawienia nie zostają w nim po instalacji.
& {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "Instaluję uv: program, który pobiera Pythona i instaluje SkerimAI..."
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Host "Nie udało się zainstalować uv, komunikat powyżej." -ForegroundColor Red
            return
        }
    }

    # Program instalujący pochodzi z gałęzi main (zawsze pobierany od nowa – adres się nie zmienia, treść tak);
    # instaluje najnowsze wydanie (tag) jako narzędzie uv.
    # --python: zależności mają gotowe paczki dla Windows tylko do pewnej wersji Pythona – bez tego uv bierze najnowszą.
    uv tool run --python 3.13 --python-preference only-managed --refresh-package skerimai `
        --from https://github.com/szewczyk74/SkerimAI/archive/refs/heads/main.zip skerimai instaluj
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Instalacja się nie udała, komunikat powyżej." -ForegroundColor Red
        return
    }
    # Polecenie `skerimai` w nowych oknach terminala (uv dopisuje swój katalog programów do PATH). Przez cmd:
    # Windows PowerShell 5.1 zamienia każdy komunikat programu na stderr w czerwony błąd, także „już w PATH”.
    cmd /c "uv tool update-shell >nul 2>&1"
    Write-Host "`nSkerimAI zainstalowany. Stan sprawdzisz w nowym oknie terminala: skerimai sprawdz" -ForegroundColor Green
}
