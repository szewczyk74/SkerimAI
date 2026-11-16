from __future__ import annotations

import base64
import io
import json
import os
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from skerimai.baza import polacz
from skerimai.konfiguracja import Sciezki

# najmniejszy poprawny PNG (1x1)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def bez_powiadomien(monkeypatch):
    """Testy nie pokazują toastów – także procesy w tle, które dziedziczą środowisko."""
    monkeypatch.setenv("SKERIMAI_POWIADOMIENIA", "0")


@pytest.fixture(autouse=True)
def bez_githuba(monkeypatch):
    """Testy nie pytają GitHuba o najnowszą wersję – jak bez sieci."""
    from skerimai import instalacja

    def bez_sieci(*args, **kwargs):
        raise OSError("testy nie korzystają z sieci")

    monkeypatch.setattr(instalacja, "urlopen", bez_sieci)


KLUCZ_TESTOW = r"Software\SkerimAI-testy"


@pytest.fixture(autouse=True)
def autostart_w_kluczu_testowym(monkeypatch):
    """Testy nie dotykają prawdziwego autostartu Windows – wpisy trafiają do osobnego klucza, usuwanego po teście."""
    if os.name != "nt":
        yield
        return
    import winreg

    from skerimai import zasobnik

    klucz = rf"{KLUCZ_TESTOW}\{uuid.uuid4().hex}"
    monkeypatch.setattr(zasobnik, "KLUCZ_AUTOSTARTU", rf"{klucz}\Run")
    monkeypatch.setattr(zasobnik, "KLUCZ_ZATWIERDZEN", rf"{klucz}\StartupApproved")
    yield
    for podklucz in (rf"{klucz}\Run", rf"{klucz}\StartupApproved", klucz, KLUCZ_TESTOW):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, podklucz)
        except OSError:  # nie było go albo (KLUCZ_TESTOW) ma jeszcze klucz równoległego testu
            pass


def uzytkownik(tekst, ts, **dodatkowe):
    return {"type": "user", "timestamp": ts, "cwd": r"C:\Projekty\sklep.pl", "gitBranch": "main",
            "message": {"role": "user", "content": tekst}, **dodatkowe}


def asystent(bloki, ts, **dodatkowe):
    return {"type": "assistant", "timestamp": ts, "cwd": r"C:\Projekty\sklep.pl",
            "message": {"role": "assistant", "content": bloki}, **dodatkowe}


def zapisz_jsonl(plik: Path, rekordy: list[dict]) -> Path:
    plik.parent.mkdir(parents=True, exist_ok=True)
    plik.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rekordy) + "\n", encoding="utf-8")
    return plik


def pdf(*linie: str) -> bytes:
    """Jednostronicowy PDF z liniami tekstu (tylko ASCII); bez linii – strona bez tekstu, jak skan."""
    tresc = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({linia}) Tj 0 -14 Td" for linia in linie) + " ET" if linie else ""
    obiekty = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(tresc)} >>\nstream\n{tresc}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    dane = b"%PDF-1.4\n"
    przesuniecia = []
    for nr, obiekt in enumerate(obiekty, 1):
        przesuniecia.append(len(dane))
        dane += f"{nr} 0 obj\n{obiekt}\nendobj\n".encode("ascii")
    xref = len(dane)
    dane += f"xref\n0 {len(obiekty) + 1}\n0000000000 65535 f \n".encode("ascii")
    dane += b"".join(f"{p:010d} 00000 n \n".encode("ascii") for p in przesuniecia)
    dane += f"trailer\n<< /Size {len(obiekty) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    return dane


def docx(*akapity: str, xml_akapitow: str = "") -> bytes:
    """Minimalny DOCX: same akapity tekstu (albo gotowy XML akapitów)."""
    tresc = xml_akapitow or "".join(f"<w:p><w:r><w:t>{escape(a)}</w:t></w:r></w:p>" for a in akapity)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{tresc}</w:body></w:document>"
    )
    bufor = io.BytesIO()
    with zipfile.ZipFile(bufor, "w") as archiwum:
        archiwum.writestr("word/document.xml", xml)
    return bufor.getvalue()


@pytest.fixture
def kartridz_z_dokumentami(tmp_path: Path) -> Path:
    """Kartridż KreionAI z rozmową, do której dołączono dokumenty (schemat dwa razy)."""
    katalog = tmp_path / "cartridge_dok"
    zalaczniki = katalog / "attachments"
    zalaczniki.mkdir(parents=True)
    (zalaczniki / "schemat.pdf").write_bytes(pdf("Przekaznik K3 zasila cewke hamulca", "Bezpiecznik F7 10A"))
    (zalaczniki / "grafik.docx").write_bytes(docx("Grafik dyżurów na październik", "Poniedziałek: Kowalski"))
    (zalaczniki / "skan.pdf").write_bytes(pdf())
    (zalaczniki / "zepsuty.pdf").write_bytes(b"%PDF-1.4 to nie jest prawdziwy PDF")

    def zalacz(*nazwy):
        return [{"fname": n, "path": n} for n in nazwy]

    zapisz_jsonl(katalog / "logs" / "Schemat budzika.jsonl", [
        {"kind": "project", "title": "Żuraw"},
        {"kind": "user", "text": "Zobacz dokumentację", "timestamp": "2026-08-28 piątek 15:15:38",
         "attachments": zalacz("schemat.pdf", "grafik.docx", "skan.pdf", "zepsuty.pdf")},
        {"kind": "agent", "text": "Widzę dokumenty.", "timestamp": "2026-08-28 piątek 15:16:00"},
        {"kind": "user", "text": "Jeszcze raz to samo", "timestamp": "2026-08-28 piątek 15:20:00",
         "attachments": zalacz("schemat.pdf")},
    ])
    return katalog


@pytest.fixture
def obraz_na_dysku(tmp_path: Path) -> Path:
    plik = tmp_path / "temp" / "images" / "1.png"
    plik.parent.mkdir(parents=True)
    plik.write_bytes(PNG + b"inny")
    return plik


@pytest.fixture
def projekty(tmp_path: Path, obraz_na_dysku: Path) -> Path:
    """Katalog ~/.claude/projects z jedną pełną sesją i jedną sesją innego projektu."""
    katalog = tmp_path / "projects"
    png_b64 = base64.b64encode(PNG).decode()
    zapisz_jsonl(
        katalog / "C--Projekty-sklep-pl" / "sesja-1.jsonl",
        [
            {"type": "custom-title", "customTitle": "Problem z NodeJsTools", "sessionId": "sesja-1"},
            uzytkownik("<system-reminder>\ntajne instrukcje\n</system-reminder>\nNie działa instalacja NodeJsTools w KreionAI",
                       "2026-09-27T19:00:00.000Z"),
            asystent([{"type": "thinking", "thinking": "myślę..."}, {"type": "text", "text": "Sprawdzam log instalacji."}],
                     "2026-09-27T19:00:05.000Z"),
            asystent([{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}], "2026-09-27T19:00:06.000Z"),
            uzytkownik([{"type": "tool_result", "tool_use_id": "t1", "content": "wynik narzędzia"}], "2026-09-27T19:00:07.000Z"),
            asystent([{"type": "text", "text": "Winny jest limit czasu pobierania paczki codex."}], "2026-09-27T19:00:08.000Z"),
            uzytkownik("<command-name>/model</command-name>", "2026-09-27T19:01:00.000Z"),
            uzytkownik("meta", "2026-09-27T19:01:01.000Z", isMeta=True),
            uzytkownik("wiadomość subagenta", "2026-09-27T19:01:02.000Z", isSidechain=True),
            uzytkownik(
                [
                    {"type": "text", "text": f"[Image: source: {obraz_na_dysku}]"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}},
                    {"type": "text", "text": "Sprawdź moją pocztę w Outlook"},
                ],
                "2026-09-27T19:02:00.000Z",
            ),
            asystent([{"type": "text", "text": "Na zrzucie widać błąd instalacji."}], "2026-09-27T19:02:05.000Z"),
        ],
    )
    zapisz_jsonl(
        katalog / "C--Projekty-budzik" / "sesja-2.jsonl",
        [
            {**uzytkownik("Grzałka utrzymuje cache budzika", "2026-09-20T10:00:00.000Z"), "cwd": r"C:\Projekty\budzik"},
            asystent([{"type": "text", "text": "Rozumiem, grzałki pingują co 50 minut."}], "2026-09-20T10:00:05.000Z"),
        ],
    )
    # transkrypcja subagenta – ma być pominięta
    zapisz_jsonl(katalog / "C--Projekty-sklep-pl" / "sesja-1" / "subagents" / "agent-x.jsonl",
                 [uzytkownik("subagent", "2026-09-27T19:03:00.000Z")])
    return katalog


@pytest.fixture
def sciezki(tmp_path: Path) -> Sciezki:
    return Sciezki(tmp_path / "skerimai")


@pytest.fixture
def polaczenie(sciezki: Sciezki):
    p = polacz(sciezki.baza)
    yield p
    p.close()
