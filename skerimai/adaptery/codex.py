"""Adapter sesji Codex: ~/.codex/sessions/RRRR/MM/DD/rollout-<czas>-<id>.jsonl (zamknięte: .jsonl.zst).

Rozmowa jest w rekordach `response_item` typu `message` – mają je wszystkie wersje Codex
(zdarzeń `user_message`/`agent_message` nowsze wersje już nie zapisują).
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

from ..model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK, Rozmowa, Wiadomosc, Zalacznik
from .wspolne import czytaj_jsonl, nazwa_katalogu, tytul_z_pierwszej

_ID_SESJI = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Kontekst, który Codex wysyła modelowi jako wiadomość użytkownika: <environment_context>…</environment_context>,
# <turn_aborted>, <recommended_plugins> itp. Podkreślnik w nazwie odróżnia je od wklejonego HTML.
_WSTAWKA = re.compile(r"\s*<([a-z]+(?:_[a-z]+)+)>.*</\1>\s*", re.DOTALL)
_INSTRUKCJE_AGENTS = "# AGENTS.md instructions"  # dawniej z „ for <katalog>”, od października 2026 bez
_OBRAZ_DATA_URL = re.compile(r"data:([\w/+.-]+);base64,(.*)", re.DOTALL)


class Codex:
    nazwa = "codex"

    def __init__(self, katalog: Path | None = None):
        self.katalog = katalog or Path.home() / ".codex" / "sessions"

    def znajdz_sesje(self) -> dict[str, list[Path]]:
        sesje: dict[str, list[Path]] = {}
        if not self.katalog.is_dir():
            return sesje
        niespakowane = set(self.katalog.rglob("rollout-*.jsonl"))
        # Zamkniętą sesję Codex pakuje do .jsonl.zst; w trakcie pakowania są oba pliki – wtedy niespakowany.
        spakowane = {p for p in self.katalog.rglob("rollout-*.jsonl.zst") if p.with_suffix("") not in niespakowane}
        for plik in sorted(niespakowane | spakowane):
            nazwa = plik.name.removesuffix(".zst").removesuffix(".jsonl")
            id_sesji = _ID_SESJI.search(nazwa)
            sesje.setdefault(id_sesji.group() if id_sesji else nazwa, []).append(plik)
        return sesje

    def wczytaj(self, sesja: str, pliki: list[Path]) -> Rozmowa:
        rekordy = [r for plik in pliki for r in czytaj_jsonl(plik)]
        rekordy.sort(key=lambda r: str(r.get("timestamp") or ""))

        rozmowa = Rozmowa(zrodlo=self.nazwa, sesja=sesja, pliki=list(pliki))
        biezacy_asystent: Wiadomosc | None = None

        for rekord in rekordy:
            ladunek = rekord.get("payload")
            if not isinstance(ladunek, dict):
                continue
            if rekord.get("type") == "session_meta":
                rozmowa.cwd = rozmowa.cwd or ladunek.get("cwd")
                rozmowa.galaz = rozmowa.galaz or (ladunek.get("git") or {}).get("branch")
                continue
            if rekord.get("type") != "response_item" or ladunek.get("type") != "message":
                continue

            bloki = ladunek.get("content")
            if not isinstance(bloki, list):
                continue
            if ladunek.get("role") == "assistant":
                tekst = "\n\n".join(
                    b["text"].strip() for b in bloki
                    if isinstance(b, dict) and b.get("type") == "output_text" and isinstance(b.get("text"), str)
                    and b["text"].strip()
                )
                if not tekst:
                    continue
                if biezacy_asystent is None:  # komentarze w trakcie pracy i odpowiedź końcowa – jedna tura
                    biezacy_asystent = Wiadomosc(ROLA_ASYSTENT, rekord.get("timestamp"), tekst)
                    rozmowa.wiadomosci.append(biezacy_asystent)
                else:
                    biezacy_asystent.tekst += "\n\n" + tekst
            elif ladunek.get("role") == "user":
                wiadomosc = _wiadomosc_uzytkownika(bloki, rekord.get("timestamp"))
                if wiadomosc is not None:
                    rozmowa.wiadomosci.append(wiadomosc)
                    biezacy_asystent = None

        rozmowa.projekt = nazwa_katalogu(rozmowa.cwd)
        rozmowa.tytul = tytul_z_pierwszej(rozmowa)
        return rozmowa


def _wiadomosc_uzytkownika(bloki: list, ts: str | None) -> Wiadomosc | None:
    teksty: list[str] = []
    zalaczniki: list[Zalacznik] = []
    for blok in bloki:
        if not isinstance(blok, dict):
            continue
        if blok.get("type") == "input_text":
            tekst = blok.get("text") if isinstance(blok.get("text"), str) else ""
            if not _WSTAWKA.fullmatch(tekst) and not tekst.startswith(_INSTRUKCJE_AGENTS):
                teksty.append(tekst)
        elif blok.get("type") == "input_image":
            obraz = _obraz(blok.get("image_url") or "", len(zalaczniki) + 1)
            if obraz is not None:
                zalaczniki.append(obraz)

    tekst = "\n".join(teksty).strip()
    if not tekst and not zalaczniki:
        return None
    return Wiadomosc(ROLA_UZYTKOWNIK, ts, tekst, zalaczniki)


def _obraz(adres: str, numer: int) -> Zalacznik | None:
    dopasowanie = _OBRAZ_DATA_URL.fullmatch(adres)
    if dopasowanie is None:
        return None
    try:
        dane = base64.b64decode(dopasowanie.group(2), validate=False)
    except (binascii.Error, ValueError):
        return None
    return Zalacznik(nazwa=f"obraz-{numer}", mime=dopasowanie.group(1), dane=dane)
