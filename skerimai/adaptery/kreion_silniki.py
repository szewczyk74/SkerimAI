"""Adapter roboczych sesji silników KreionAI: Claude Code w ~/.kreion/projects, Codex w ~/.kreion_codex/sessions.

KreionAI prowadzi te sesje za użytkownika – to te same rozmowy co w kartridżu, ale z prefiksami
harnessa, jego komunikatami (pingi grzałki cache, koniec zadań w tle) i wynikami narzędzi (te ostatnie
tylko w surowej kopii). Archiwizowane dla kompletu; wyszukiwanie pomija je, dopóki nie wskaże się
tego źródła wprost (duplikaty kartridża).
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

from ..model import ROLA_UZYTKOWNIK, Rozmowa, Wiadomosc, Zalacznik
from .claude_code import ClaudeCode
from .codex import Codex
from .kreion_kartridz import katalog_kartridza, projekty_kartridza
from .wspolne import tytul_z_pierwszej

# [jobContextId:…][PRZECZYTAJ ZASADY PROJEKTU! …][TS:22:40:52 sobota][myname:Mirek][limit subagentów: …] treść
_PREFIKS = re.compile(r"(?:\[(?:jobContextId:|PRZECZYTAJ ZASADY|TS:|myname:|limit subagentów)[^\]]*\]\s*)+")
_PLIK_ZASAD = re.compile(r"user_project_[\d_-]+\.md")
# Komunikaty harnessa wysyłane jako wiadomość użytkownika: [CACHE-PING] …, [BACKGROUND_JOB_DONE] jobId=…
_KOMUNIKAT_HARNESSA = re.compile(r"\[[A-Z]+(?:[-_][A-Z]+)+\]")
_PING = "[CACHE-PING]"
# Nazwa pobranego pliku bywa z nawiasem („CTL400a[1].pdf”) – ścieżkę kończy dopiero „]” przed końcem wiersza
# albo przed następnym załącznikiem.
_ZALACZNIK = re.compile(r"\[załącznik: ([^\r\n]+?)\](?=[ \t]*(?:\r?\n|$|\[załącznik: ))")


class KreionSilniki:
    nazwa = "kreion_silniki"

    def __init__(self, claude: Path | None = None, codex: Path | None = None, kartridz: Path | None = None):
        self._claude = ClaudeCode(claude or Path.home() / ".kreion" / "projects")
        self._codex = Codex(codex or Path.home() / ".kreion_codex" / "sessions")
        self._kartridz = kartridz or katalog_kartridza()

    def znajdz_sesje(self) -> dict[str, list[Path]]:
        return {**self._claude.znajdz_sesje(), **self._codex.znajdz_sesje()}

    def wczytaj(self, sesja: str, pliki: list[Path]) -> Rozmowa:
        silnik = self._codex if pliki and self._codex.katalog in pliki[0].parents else self._claude
        rozmowa = silnik.wczytaj(sesja, pliki)
        rozmowa.zrodlo = self.nazwa

        pliki_zasad: list[str] = []
        wiadomosci: list[Wiadomosc] = []
        po_pingu = False
        for wiadomosc in rozmowa.wiadomosci:
            if wiadomosc.rola != ROLA_UZYTKOWNIK:
                if not po_pingu:  # odpowiedź na ping to sama kropka; po końcu zadania – zwykła praca agenta
                    wiadomosci.append(wiadomosc)
                po_pingu = False
                continue
            pliki_zasad += _PLIK_ZASAD.findall(wiadomosc.tekst)
            wiadomosc.tekst = _PREFIKS.sub("", wiadomosc.tekst, count=1).strip()
            po_pingu = wiadomosc.tekst.startswith(_PING)
            if _KOMUNIKAT_HARNESSA.match(wiadomosc.tekst):
                continue
            for sciezka in _ZALACZNIK.findall(wiadomosc.tekst):
                sciezka = sciezka.strip()
                wiadomosc.zalaczniki.append(Zalacznik(nazwa=PureWindowsPath(sciezka).name, sciezka=Path(sciezka)))
            wiadomosc.tekst = _ZALACZNIK.sub("", wiadomosc.tekst).strip()
            if wiadomosc.tekst or wiadomosc.zalaczniki:
                wiadomosci.append(wiadomosc)
        rozmowa.wiadomosci = wiadomosci

        # Wszystkie sesje mają cwd = katalog danych KreionAI; projekt wskazuje plik zasad z prefiksu.
        projekty = projekty_kartridza(self._kartridz) if pliki_zasad else {}
        rozmowa.projekt = next((projekty[p] for p in reversed(pliki_zasad) if p in projekty), None)
        rozmowa.tytul = tytul_z_pierwszej(rozmowa)
        return rozmowa
