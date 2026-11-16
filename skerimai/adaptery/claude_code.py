"""Adapter transkrypcji Claude Code: ~/.claude/projects/<projekt>/<sesja>.jsonl."""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path, PureWindowsPath

from ..model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK, Rozmowa, Wiadomosc, Zalacznik
from .wspolne import czytaj_jsonl, nazwa_katalogu, tytul_z_pierwszej

_PRZYPOMNIENIE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
# Wklejony tekst: <pasted_content id="82e3">…</pasted_content id="82e3"> – znaczniki precz, treść zostaje.
_ZNACZNIK_WKLEJENIA = re.compile(r'</?pasted_content(?:\s+id="[^"]*")?>')
_OBRAZ_ZE_SCIEZKI = re.compile(r"\[Image: source: ([^\]\r\n]+)\]")
_POMIJANE_PREFIKSY = (
    "<local-command-",
    "<command-name>",
    "<command-message>",
    "<task-notification",
    "[Request interrupted",
    "Caveat: The messages below were generated",
)

class ClaudeCode:
    nazwa = "claude_code"

    def __init__(self, katalog: Path | None = None):
        self.katalog = katalog or Path.home() / ".claude" / "projects"

    def znajdz_sesje(self) -> dict[str, list[Path]]:
        """Pliki sesji głównych; transkrypcje subagentów (<sesja>/subagents/) są pomijane."""
        sesje: dict[str, list[Path]] = {}
        if not self.katalog.is_dir():
            return sesje
        for plik in sorted(self.katalog.glob("*/*.jsonl")):
            sesje.setdefault(plik.stem, []).append(plik)
        return sesje

    def wczytaj(self, sesja: str, pliki: list[Path]) -> Rozmowa:
        rekordy = [r for plik in pliki for r in czytaj_jsonl(plik)]
        rekordy.sort(key=lambda r: str(r.get("timestamp") or ""))  # sesja przeniesiona (relocated) = kilka plików

        rozmowa = Rozmowa(zrodlo=self.nazwa, sesja=sesja, pliki=list(pliki))
        tytul_wlasny: str | None = None
        biezacy_asystent: Wiadomosc | None = None

        for rekord in rekordy:
            typ = rekord.get("type")
            if typ == "custom-title" and rekord.get("customTitle"):
                tytul_wlasny = rekord["customTitle"]
            elif typ == "agent-name" and rekord.get("agentName") and not tytul_wlasny:
                tytul_wlasny = rekord["agentName"]

            if typ == "attachment" and not rekord.get("isSidechain"):
                rekord = _z_kolejki(rekord)
                typ = "user" if rekord else None
            if typ not in ("user", "assistant") or rekord.get("isSidechain"):
                continue
            if rozmowa.cwd is None and rekord.get("cwd"):
                rozmowa.cwd = rekord["cwd"]
                rozmowa.galaz = rekord.get("gitBranch") or None

            if typ == "assistant":
                tekst = _tekst_asystenta(rekord)
                if not tekst:
                    continue
                if biezacy_asystent is None:
                    biezacy_asystent = Wiadomosc(ROLA_ASYSTENT, rekord.get("timestamp"), tekst)
                    rozmowa.wiadomosci.append(biezacy_asystent)
                else:
                    biezacy_asystent.tekst += "\n\n" + tekst
                continue

            wiadomosc = _wiadomosc_uzytkownika(rekord)
            if wiadomosc is not None:
                rozmowa.wiadomosci.append(wiadomosc)
                biezacy_asystent = None

        rozmowa.projekt = _nazwa_projektu(rozmowa.cwd, pliki)
        rozmowa.tytul = tytul_wlasny or tytul_z_pierwszej(rozmowa)
        return rozmowa


def _z_kolejki(rekord: dict) -> dict | None:
    """Wiadomość napisana, gdy agent jeszcze pracował: nie ma rekordu `user`, jest tylko w `attachment` typu
    `queued_command` – tu jako rekord `user`. Tą samą kolejką idą komunikaty techniczne (koniec zadania w tle,
    odpowiedź subagenta); wiadomość człowieka ma `commandMode: prompt` i `origin.kind: human`."""
    zalacznik = rekord.get("attachment")
    if not isinstance(zalacznik, dict) or zalacznik.get("type") != "queued_command":
        return None
    pochodzenie = zalacznik.get("origin")
    if zalacznik.get("commandMode") != "prompt" or zalacznik.get("isMeta"):
        return None
    if not isinstance(pochodzenie, dict) or pochodzenie.get("kind") != "human":
        return None
    return {**rekord, "type": "user", "message": {"content": zalacznik.get("prompt")},
            "timestamp": zalacznik.get("timestamp") or rekord.get("timestamp")}


def _tresc(rekord: dict) -> object:
    """`message.content`; rekord w nietypowym kształcie (np. `message: null`) to brak treści, nie błąd całej sesji."""
    wiadomosc = rekord.get("message")
    return wiadomosc.get("content") if isinstance(wiadomosc, dict) else None


def _tekst_asystenta(rekord: dict) -> str:
    tresc = _tresc(rekord)
    if isinstance(tresc, str):
        return tresc.strip()
    if not isinstance(tresc, list):
        return ""
    return "\n\n".join(b["text"].strip() for b in tresc
                       if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
                       and b["text"].strip())


def _wiadomosc_uzytkownika(rekord: dict) -> Wiadomosc | None:
    """Wiadomość napisana przez człowieka; None dla wyników narzędzi, komend i wpisów technicznych."""
    if rekord.get("isMeta") or rekord.get("isCompactSummary"):
        return None

    tresc = _tresc(rekord)
    bloki = [{"type": "text", "text": tresc}] if isinstance(tresc, str) else tresc
    if not isinstance(bloki, list):
        return None

    teksty: list[str] = []
    zalaczniki: list[Zalacznik] = []
    for blok in bloki:
        if not isinstance(blok, dict):
            continue
        typ = blok.get("type")
        if typ == "text" and isinstance(blok.get("text"), str):
            teksty.append(blok["text"])
        elif typ == "image":
            obraz = _obraz_base64(blok, len(zalaczniki) + 1)
            if obraz is not None:
                zalaczniki.append(obraz)

    tekst = _PRZYPOMNIENIE.sub("", "\n".join(teksty)).strip()
    if tekst.startswith(_POMIJANE_PREFIKSY):  # przed zdjęciem znaczników: wklejone „<command-name>” to treść
        return None
    tekst = _ZNACZNIK_WKLEJENIA.sub("", tekst).strip()

    for sciezka in _OBRAZ_ZE_SCIEZKI.findall(tekst):
        sciezka = sciezka.strip()
        zalaczniki.append(Zalacznik(nazwa=PureWindowsPath(sciezka).name, sciezka=Path(sciezka)))
    tekst = _OBRAZ_ZE_SCIEZKI.sub("", tekst).strip()

    if not tekst and not zalaczniki:
        return None
    return Wiadomosc(ROLA_UZYTKOWNIK, rekord.get("timestamp"), tekst, zalaczniki)


def _obraz_base64(blok: dict, numer: int) -> Zalacznik | None:
    zrodlo = blok.get("source")
    if not isinstance(zrodlo, dict) or zrodlo.get("type") != "base64" or not isinstance(zrodlo.get("data"), str):
        return None
    try:
        dane = base64.b64decode(zrodlo["data"], validate=False)
    except (binascii.Error, ValueError):
        return None
    mime = zrodlo.get("media_type")
    return Zalacznik(nazwa=f"obraz-{numer}", mime=mime, dane=dane)


def _nazwa_projektu(cwd: str | None, pliki: list[Path]) -> str | None:
    return nazwa_katalogu(cwd) or (pliki[0].parent.name if pliki else None)
