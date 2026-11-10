#!/usr/bin/env python3
"""
typst2md.py — Konvertiert Lernzettel-Kapitel (Typst, Template-eigene Syntax)
in einfaches Markdown zum Austausch.

Verwendung:
    python3 scripts/typst2md.py Content/01-sortierverfahren.typ
    python3 scripts/typst2md.py Content/            # alle Kapitel
    python3 scripts/typst2md.py Content/ --out MD/  # eigenes Zielverzeichnis

Vorgehen:
    1. Template-Komponenten (#infobox, #merksatz, #stdtable, #procon, ...)
       werden in Python per Tokenizer + Bracket-Matching erkannt, rekursiv
       konvertiert und durch Markdown-Platzhalter ersetzt (Pandoc kennt die
       Template-Funktionen nicht).
    2. Der verbleibende Standard-Typst-Text (Überschriften, Hervorhebungen,
       Listen, ...) wird mit Pandoc in Markdown übersetzt.
    3. Die Platzhalter werden durch das fertige Komponenten-Markdown ersetzt
       und Inline-Belege (#vgl/#zit) als (Autor Jahr, S. X) gesetzt; die
       Daten stammen aus Bibliography/literatur.bib.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
BIB_FILE = PROJ / "Bibliography" / "literatur.bib"
MAIN_FILE = PROJ / "main.typ"

BOX_COMPONENTS = {  # Funktionsname -> Markdown-Label
    "infobox": "Info",
    "warnbox": "Achtung",
    "merksatz": "Merke",
    "beispiel": "Beispiel",
    "aufgabe": "Aufgabe",
}
CITE_FUNCTIONS = {
    "vgl", "zit", "vgl-kap", "zit-kap", "zitat", "zitat-kap",
    "vgl-nach", "zit-nach", "vgl-nach-kap", "zit-nach-kap",
}
# Aufrufe, die nur der Typst-Struktur dienen und im Markdown entfallen.
STRUCTURE_LINE = re.compile(r"^\s*#(?:import|include|use)\b.*$", re.M)
KNOWN_FUNCTIONS = set(BOX_COMPONENTS) | CITE_FUNCTIONS | {
    "stdtable", "tabelle", "abbildung", "process", "procon",
    "zweispaltig", "konzeptkarte", "abk", "resetaufgaben",
}
# Aufrufe, die Pandocs Typst-Reader selbst versteht (kein Warning, wenn
# sie unverändert durchgereicht werden).
PANDOC_NATIVE = {"footnote"}


# ============================================================================
# Quelltext-Zerlegung (Strings und Kommentare überspringen, Klammern matchen)
# ============================================================================

def skip_string(src: str, i: int) -> int:
    """i zeigt auf '\"'; liefert Index hinter der vollständigen Zeichenkette."""
    i += 1
    while i < len(src):
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == '"':
            return i + 1
        i += 1
    return len(src)


def skip_comment(src: str, i: int) -> int:
    """i zeigt auf '//'; liefert Index hinter dem Zeilenkommentar."""
    end = src.find("\n", i)
    return len(src) if end == -1 else end + 1


def find_matching(src: str, i: int, closer: str) -> int:
    """i zeigt auf den Opener; liefert Index hinter dem passenden Closer.
    Strings, Kommentare und alle Bracket-Typen werden korrekt übersprungen."""
    pairs = {"[": "]", "(": ")", "{": "}"}
    depth = 0
    while i < len(src):
        c = src[i]
        if c == '"':
            i = skip_string(src, i)
            continue
        if c == "/" and src[i : i + 2] == "//":
            i = skip_comment(src, i)
            continue
        if c in pairs:
            depth += 1
        elif c in "])}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(src)


def find_call_end(src: str, start: int) -> int:
    """Liefert Index hinter dem vollständigen Aufruf
    #name(...)(...) oder #name(...)[...][...]. `start` zeigt auf '#'.
    Nachfolgender Leerraum wird NICHT mitkonsumiert."""
    m = re.match(r"#([a-zA-Z][\w-]*)", src[start:])
    if not m:
        return start + 1
    i = start + m.end()
    last = i
    while i < len(src):
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c in "([":
            i = find_matching(src, i, {"(": ")", "[": "]"}[c])
            last = i
            continue
        break
    return last


def split_args(argstr: str) -> list[str]:
    """Zerlegt 'a, b, [x], c' in ['a', ' b', ' [x]', ' c'] — Strings und
    Klammern bleiben intakt."""
    parts, depth, cur, i = [], 0, [], 0
    in_string = False
    while i < len(argstr):
        c = argstr[i]
        if in_string:
            cur.append(c)
            if c == "\\" and i + 1 < len(argstr):
                cur.append(argstr[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            cur.append(c)
        elif c in "([":
            depth += 1
            cur.append(c)
        elif c in ")]":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if cur or parts:
        parts.append("".join(cur))
    return parts


def unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def unbracket(s: str) -> str:
    """Entfernt außenliegende [ ] (Inhaltsblock), sofern sie das Ganze
    umschließen; innere Struktur bleibt erhalten."""
    s = s.strip()
    if len(s) >= 2 and s[0] == "[" and s[-1] == "]":
        inner = s[1:-1]
        depth = 0
        in_string = False
        ok = True
        for ch in inner:
            if in_string:
                if ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth < 0:
                    ok = False
                    break
        if ok and depth == 0:
            return inner.strip()
    return s


def parse_call(src: str, start: int, end: int) -> tuple[str, dict, list]:
    """Analysiert einen Aufruf #name(args...)[body...] und liefert
    (name, kwargs, posargs) als Rohstrings."""
    m = re.match(r"#([\w-]+)", src[start:end])
    name = m.group(1)
    body = src[start + m.end() : end]
    kwargs, posargs = {}, []
    i = 0
    while i < len(body):
        c = body[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c in "([":
            j = find_matching(body, i, {"(": ")", "[": "]"}[c])
            argstr = body[i + 1 : j - 1]
            i = j
            if c == "(":
                for part in split_args(argstr):
                    part = part.strip()
                    if not part:
                        continue
                    km = re.match(r"([a-zA-Z][\w-]*)\s*:\s*(.*)$", part, re.S)
                    if km:
                        kwargs[km.group(1)] = km.group(2).strip()
                    else:
                        posargs.append(part)
            else:
                posargs.append("[" + argstr + "]")
        else:
            i += 1
    return name, kwargs, posargs


# ============================================================================
# BibTeX-Datenbank (einfacher Parser für Autor/Jahr/Titel)
# ============================================================================

def parse_bib(path: Path) -> dict[str, dict]:
    db: dict[str, dict] = {}
    if not path.exists():
        return db
    text = path.read_text(encoding="utf-8")
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        key = m.group(2)
        start = m.end() - 1
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        entry = text[start + 1 : i - 1]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*(\{.*?\}|\w+)", entry, re.S):
            fields[fm.group(1).lower()] = fm.group(2).strip("{}").strip()
        db[key] = fields
    return db


def bib_kurzbeleg(key: str, db: dict[str, dict]) -> str:
    """'cormenIntroductionAlgorithms2022' -> 'Cormen 2022'."""
    if key not in db:
        return key
    fields = db[key]
    author = fields.get("author", "")
    year = fields.get("year", "o. J.")
    if author:
        first = re.split(r"\s+(?:and|und)\s+", author)[0]
        name = re.sub(r"[{}]", "", first).split(",")[0].strip()
        return f"{name} {year}"
    title = fields.get("title", key)
    return f"{title} {year}".strip()


# ============================================================================
# Abkürzungen (aus main.typ lesen)
# ============================================================================

def parse_abkuerzungen(main_file: Path) -> dict[str, str]:
    text = main_file.read_text(encoding="utf-8") if main_file.exists() else ""
    m = re.search(r"abkuerzungen:\s*\((.*?)\n\s*\)", text, re.S)
    mapping: dict[str, str] = {}
    if not m:
        return mapping
    for part in split_args(m.group(1)):
        km = re.match(r'"([^"]+)"\s*:\s*"([^"]*)"', part.strip())
        if km:
            mapping[km.group(1)] = km.group(2)
    return mapping


# ============================================================================
# Pandoc-Brücke
# ============================================================================

def pandoc_typst_to_md(text: str) -> str:
    """Typst-Schnipsel -> Markdown über pandoc (GFM, ohne YAML)."""
    if not text.strip():
        return ""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".typ", delete=False, encoding="utf-8"
    ) as f:
        f.write(text)
        path = f.name
    try:
        res = subprocess.run(
            ["pandoc", "-f", "typst", "-t", "gfm", "--wrap=none", path],
            capture_output=True, text=True, check=False,
        )
        if res.returncode != 0:
            return text  # Fallback: unverändert
        return res.stdout.strip("\n")
    finally:
        Path(path).unlink(missing_ok=True)


def md_inline(text: str) -> str:
    """Macht aus Pandoc-Blockausgabe eine einzeilige Inline-Form."""
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


# ============================================================================
# Konverter
# ============================================================================

def render_pipe_table(rows: list[list[str]], n_cols: int) -> str:
    def esc(s: str) -> str:
        # escapee Pipes für Markdown-Tabellen, mehrfache Backslash vermeiden
        s = s.replace("\\|", "|")
        return s.replace("|", "\\|").replace("\n", " ")

    def fill(row: list[str]) -> list[str]:
        row = [esc(c) for c in row]
        while len(row) < n_cols:
            row.append("")
        return row[:n_cols]

    if not rows:
        return ""
    header = fill(rows[0])
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * n_cols) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(fill(row)) + " |")
    return "\n".join(lines)


class Converter:
    def __init__(self, bib: dict, abk: dict, warnings: list):
        self.bib = bib
        self.abk = abk
        self.abk_used: set[str] = set()
        self.warnings = warnings
        self.blocks: list[str] = []  # fertiges Markdown pro Platzhalter

    # ---------- Platzhalter ----------

    def placeholder(self, md: str) -> str:
        self.blocks.append(md)
        return f"\n\nPLACEHOLDER-{len(self.blocks) - 1}-ENDPLACEHOLDER\n\n"

    def placeholder_inline(self, md: str) -> str:
        self.blocks.append(md)
        return f"PLACEHOLDER-INLINE-{len(self.blocks) - 1}-ENDPLACEHOLDER"

    # ---------- Inline-Sonderfälle ----------

    def abk_md(self, kuerzel: str, lang: bool) -> str:
        if kuerzel not in self.abk:
            self.warnings.append(f"Unbekannte Abkürzung: {kuerzel}")
            return kuerzel
        if kuerzel in self.abk_used and not lang:
            return kuerzel
        self.abk_used.add(kuerzel)
        return f"{self.abk[kuerzel]} ({kuerzel})"

    def cite_md(self, name: str, kwargs: dict, posargs: list) -> str:
        keys = re.findall(r"<([^>]+)>", " ".join(posargs))
        if not keys:
            self.warnings.append(f"Kein Beleg-Schlüssel erkannt in #{name}")
            return ""
        praefix = "vgl. " if name.startswith("vgl") else ""
        if "nach" in name and len(keys) >= 2:
            orig = bib_kurzbeleg(keys[0], self.bib)
            sek = bib_kurzbeleg(keys[1], self.bib)
            sek_seite = unquote(kwargs.get("seite-sek") or "")
            stelle = f", S. {sek_seite}" if sek_seite else ""
            return f"({praefix}{orig}, zitiert nach {sek}{stelle})"
        beleg = bib_kurzbeleg(keys[0], self.bib)
        wert = unquote(kwargs.get("seite") or kwargs.get("kap") or "")
        art = "Kap. " if ("kap" in name and "seite" not in kwargs) else "S. "
        stelle = f", {art}{wert}" if wert else ""
        return f"({praefix}{beleg}{stelle})"

    # ---------- Blockkomponenten ----------

    def convert_call(self, src: str, start: int, end: int) -> str:
        """Übersetzt einen Template-Aufruf; liefert Inline-Text oder
        Block-Platzhalter. Inhaltsblöcke werden rekursiv konvertiert."""
        name, kwargs, posargs = parse_call(src, start, end)

        if name in CITE_FUNCTIONS:
            return self.cite_md(name, kwargs, posargs)

        if name == "abk":
            kuerzel = unquote(posargs[0]) if posargs else ""
            lang = kwargs.get("lang", "").strip() == "true"
            return self.abk_md(kuerzel, lang)

        if name == "resetaufgaben":
            return ""

        # ---- Boxen: #infobox(title: [T])[Inhalt] -> Blockquote ----
        if name in BOX_COMPONENTS:
            label = BOX_COMPONENTS[name]
            title_raw = unbracket(kwargs.get("title", "")) if kwargs.get("title") else ""
            title = md_inline(pandoc_typst_to_md(self.preprocess(title_raw)))
            body = unbracket(posargs[-1]) if posargs else ""
            body_md = pandoc_typst_to_md(self.preprocess(body))
            kopf = f"{label}: {title}" if title else label
            lines = [f"> **{kopf}**", ">"]
            lines += [f"> {line}".rstrip() for line in body_md.splitlines()]
            return self.placeholder("\n".join(lines))

        if name == "konzeptkarte":
            title = unbracket(kwargs.get("title", "")) if kwargs.get("title") else ""
            body = unbracket(posargs[-1]) if posargs else ""
            inner = pandoc_typst_to_md(self.preprocess(body))
            out = f"### {title}\n\n{inner}" if title else inner
            return self.placeholder(out)

        if name == "process":
            steps = [
                md_inline(pandoc_typst_to_md(self.preprocess(unbracket(s))))
                for s in posargs
                if s.strip()
            ]
            md = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
            return self.placeholder(md)

        if name == "procon":
            def items(argstr: str) -> list[str]:
                inner = argstr.strip()
                if inner.startswith("(") and inner.endswith(")"):
                    inner = inner[1:-1]
                return [
                    md_inline(pandoc_typst_to_md(self.preprocess(unbracket(p))))
                    for p in split_args(inner)
                    if p.strip()
                ]

            pro = items(kwargs.get("pro", ""))
            con = items(kwargs.get("con", ""))
            out = ["**Vorteile**", ""]
            out += [f"- {p}" for p in pro] or ["- "]
            out += ["", "**Nachteile**", ""]
            out += [f"- {c}" for c in con] or ["- "]
            return self.placeholder("\n".join(out))

        if name == "zweispaltig":
            parts = [
                pandoc_typst_to_md(self.preprocess(unbracket(p)))
                for p in posargs
                if p.strip()
            ]
            return self.placeholder("\n\n".join(parts))

        if name == "abbildung":
            img = ""
            if posargs:
                im = re.search(r'image\s*\(\s*"([^"]+)"', posargs[0])
                if im:
                    img = im.group(1)
            titel_raw = unbracket(kwargs.get("titel", "")) if kwargs.get("titel") else ""
            titel = md_inline(pandoc_typst_to_md(self.preprocess(titel_raw)))
            quelle = self.quelle_md(kwargs.get("quelle"))
            out = f"![{titel}]({img})"
            if quelle:
                out += f"\n\n*Quelle: {quelle}*"
            return self.placeholder(out)

        if name in ("stdtable", "tabelle"):
            return self.table_md(name, kwargs, posargs)

        # ---- Unbekannt ----
        self.warnings.append(f"Unbekannte Funktion #{name} — unverändert übernommen.")
        return src[start:end]

    # ---------- stdtable / tabelle ----------

    def quelle_md(self, raw: str | None) -> str:
        """Quellenangabe -> Inline-Markdown; 'Eigene Darstellung' -> ''."""
        if raw is None:
            return ""
        raw = raw.strip()
        if raw in ("none", ""):
            return ""
        if raw.startswith('"'):
            text = unquote(raw)
        else:
            text = unbracket(raw)
            if text and not text.startswith('"'):
                text = md_inline(pandoc_typst_to_md(self.preprocess(text)))
        if text == "Eigene Darstellung":
            return ""
        return text

    def table_md(self, name: str, kwargs: dict, posargs: list) -> str:
        # Metadaten des #tabelle-Wrappers (titel/quelle) sichern, bevor
        # kwargs auf die inneren stdtable-Argumente umgebogen werden.
        titel = unbracket(kwargs.get("titel", "")) if kwargs.get("titel") else ""
        quelle = self.quelle_md(kwargs.get("quelle"))
        if name == "tabelle":
            inner = posargs[0].strip() if posargs else ""
            tm = re.match(r"stdtable\s*\(", inner)
            if not tm:
                self.warnings.append("Nicht-stdtable in #tabelle übersprungen.")
                return ""
            # runde Klammer des stdtable-Aufrufs abtrennen
            open_idx = tm.end() - 1
            close_idx = find_matching(inner, open_idx, ")")
            argstr = inner[open_idx + 1 : close_idx - 1]
            parts = split_args(argstr)
            t_kwargs, t_posargs = {}, []
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                km = re.match(r"([a-zA-Z][\w-]*)\s*:\s*(.*)$", part, re.S)
                if km:
                    t_kwargs[km.group(1)] = km.group(2).strip()
                else:
                    t_posargs.append(part)
            kwargs, posargs = t_kwargs, t_posargs

        n_cols = count_table_columns(kwargs.get("columns", "()"))
        header_str = kwargs.get("header", "()").strip()
        if header_str.startswith("(") and header_str.endswith(")"):
            header_str = header_str[1:-1]
        header_cells = [
            md_inline(pandoc_typst_to_md(self.preprocess(unbracket(h))))
            for h in split_args(header_str)
            if h.strip()
        ]
        if n_cols == 0:
            n_cols = len(header_cells) or 1

        data: list[str] = []
        for cell in posargs:
            data.append(
                md_inline(pandoc_typst_to_md(self.preprocess(unbracket(cell))))
            )

        rows: list[list[str]] = [header_cells or [""] * n_cols]
        for r in range(0, len(data), n_cols):
            rows.append(data[r : r + n_cols])

        md = render_pipe_table(rows, n_cols)
        if titel:
            titel = md_inline(pandoc_typst_to_md(self.preprocess(titel)))
            md = f"**{titel}**\n\n{md}"
        if quelle:
            md += f"\n\n*Quelle: {quelle}*"
        return self.placeholder(md)

    # ---------- Vorverarbeitung ----------

    def preprocess(self, text: str) -> str:
        """Ersetzt alle Template-Aufrufe im Text durch (Inline-Belege bzw.)
        Platzhalter, die Pandoc unangetastet lässt."""
        out = []
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c == '"':
                j = skip_string(text, i)
                out.append(text[i:j])
                i = j
                continue
            if c == "/" and text[i : i + 2] == "//":
                j = skip_comment(text, i)
                out.append(text[i:j])
                i = j
                continue
            if c == "#" and re.match(r"#([a-zA-Z][\w-]*)", text[i:]):
                end = find_call_end(text, i)
                call = text[i:end]
                fname = re.match(r"#([\w-]+)", call).group(1)
                if fname in KNOWN_FUNCTIONS:
                    out.append(self.convert_call(text, i, end))
                elif fname in PANDOC_NATIVE:
                    out.append(call)  # Pandoc versteht das selbst
                else:
                    self.warnings.append(f"Unbekannte Funktion #{fname}.")
                    out.append(call)
                i = end
                continue
            out.append(c)
            i += 1
        return "".join(out)

    # ---------- Nachverarbeitung ----------

    def postprocess(self, md: str) -> str:
        """Setzt die Platzhalter ein und normalisiert Leerzeilen."""
        def repl(m: re.Match) -> str:
            return self.blocks[int(m.group(1))]

        # Platzhalter durch fertiges Markdown ersetzen (rekursiv, falls
        # ein Block selbst wieder Platzhalter enthält).
        for _ in range(3):
            if "PLACEHOLDER-" not in md:
                break
            md = re.sub(
                r"\n*PLACEHOLDER-(?:INLINE-)?(\d+)-ENDPLACEHOLDER\n*",
                lambda m: "\n\n" + repl(m) + "\n\n",
                md,
            )

        # Leerzeilen um Block-Elemente konsolidieren (max. eine)
        md = re.sub(r"\n{3,}", "\n\n", md)
        # Typst-Labels hinter Überschriften erzeugen <span>-Anker: entfernen
        md = re.sub(r'<span id="[^"]*"></span>\n*', "", md)
        # Kein Leerzeichen unmittelbar vor Satzzeichen
        md = re.sub(r"[ \t]+([.,;:!?])", r"\1", md)
        # Pandoc-GFM-Math in einfache $...$-Syntax überführen (breiter lesbar)
        md = re.sub(r"\$`(.+?)`\$", r"$\1$", md, flags=re.S)
        return md.strip() + "\n"

    # ---------- Gesamtlauf ----------

    def convert(self, text: str) -> str:
        text = STRUCTURE_LINE.sub("", text)
        preprocessed = self.preprocess(text)
        md = pandoc_typst_to_md(preprocessed)
        return self.postprocess(md)


def count_table_columns(colspec: str) -> int:
    inner = colspec.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    if not inner.strip():
        return 0
    return len([p for p in split_args(inner) if p.strip()])


# ============================================================================
# Hauptprogramm
# ============================================================================

def convert_path(src: Path, out_dir: Path, bib: dict, abk: dict, warnings: list) -> Path:
    text = src.read_text(encoding="utf-8")
    conv = Converter(bib, abk, warnings)
    md = conv.convert(text)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (src.stem + ".md")
    target.write_text(md, encoding="utf-8")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Konvertiert Lernzettel-Kapitel von Typst nach Markdown."
    )
    ap.add_argument("input", type=Path, help=".typ-Datei oder Verzeichnis mit .typ-Dateien")
    ap.add_argument("--out", type=Path, default=PROJ / "Markdown",
                    help="Zielverzeichnis (Standard: Markdown/)")
    ap.add_argument("--bib", type=Path, default=BIB_FILE,
                    help="BibTeX-Datei für Kurzbelege")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"Fehler: {args.input} existiert nicht.", file=sys.stderr)
        return 1
    if shutil.which("pandoc") is None:
        print("Fehler: pandoc ist nicht installiert (brew install pandoc).", file=sys.stderr)
        return 1

    files = sorted(args.input.glob("*.typ")) if args.input.is_dir() else [args.input]
    if not files:
        print(f"Keine .typ-Dateien in {args.input} gefunden.", file=sys.stderr)
        return 1

    bib = parse_bib(args.bib)
    abk = parse_abkuerzungen(MAIN_FILE)
    warnings: list[str] = []

    for f in files:
        target = convert_path(f, args.out, bib, abk, warnings)
        print(f"{f} -> {target}")

    if warnings:
        print("\nHinweise:", file=sys.stderr)
        for w in dict.fromkeys(warnings):  # dedupliziert, Reihenfolge bleibt
            print(f"  - {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())