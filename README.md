# course-sync

**course-sync** ist ein Werkzeug für Lehrkräfte, um interaktive Programmieraufgaben als
[H5P](https://h5p.org/)-Pakete zu verwalten und auf [Moodle](https://moodle.org/) zu
synchronisieren. Kursinhalt liegt als lesbare Textdateien (MDX) vor und wird versioniert
in Git gepflegt – wie Quellcode, nicht wie Word-Dokumente.

Der Kern: Ein Kurs ist eine Sammlung von `.mdx`-Dateien. `inv sync` baut daraus H5P-Pakete,
`inv serve` öffnet eine lokale Vorschau, `inv upload-chapter-moodle` lädt ein Kapitel direkt
auf eine Moodle-Instanz hoch.

Entwickelt für das H5P-Format [PythonQuestion](https://github.com/andreas-siebel/h5p-python-question),
das interaktive Python-Aufgaben mit Skulpt oder Pyodide direkt im Browser ausführt.

---

## Voraussetzungen

- Python 3.11+
- Node.js mit `npx` (für die H5P-Vorschau)
- Moodle-Instanz mit H5P-Plugin (für den Upload)

---

## Einrichten

```bash
git clone <repo-url>
cd course-sync

# .env anlegen (Moodle-Zugangsdaten)
cp .env.example .env
# .env mit einem Editor öffnen und Werte eintragen

# Virtuelle Umgebung, Abhängigkeiten und H5P-Libraries installieren
source prepare.sh
```

`prepare.sh` legt eine virtuelle Python-Umgebung an, installiert alle Abhängigkeiten
und lädt die H5P-Libraries automatisch herunter. Danach ist `inv` im Terminal verfügbar.

---

## Tägliche Arbeit

```bash
# Virtuelle Umgebung aktivieren (einmal pro Terminal-Session)
source prepare.sh

# H5P-Pakete aus den MDX-Quellen bauen
inv sync

# Lokale Vorschau starten (http://127.0.0.1:8765/)
inv serve

# Ein Kapitel auf Moodle hochladen
inv upload-chapter-moodle 012-miniworlds

# Alle verfügbaren Tasks anzeigen
inv -l
```

---

## Kursstruktur

```
courses/python-2026/
  index.mdx                        ← Kapitelübersicht
  chapters/
    001-einfuehrung.mdx            ← Kapitel mit PythonQuestion-Blöcken
    012-miniworlds.mdx
  h5p/
    012-miniworlds/
      miniworlds-tutorial/
        content.mdx                ← sichtbarer Inhalt der Aufgabe
        settings.yml               ← Runner, Grading, Optionen
        h5p.json                   ← H5P-Metadaten
  build/                           ← generiert, nicht im Repo
```

Eine Aufgabe wird im Kapitel als `<PythonQuestion identifier="..." />` referenziert.
`identifier` muss innerhalb eines Kurses eindeutig sein und dient als stabiler Schlüssel
beim Moodle-Upload.

---

## Neue Aufgabe anlegen

1. Ordner anlegen: `courses/<kurs>/h5p/<kapitel>/<identifier>/`
2. Drei Dateien anlegen: `content.mdx`, `settings.yml`, `h5p.json`
3. Im Kapitel referenzieren: `<PythonQuestion identifier="<identifier>" />`
4. Bauen und vorschauen: `inv sync && inv serve`

Eine Vorlage für `content.yml` (älteres Format, wird weiterhin unterstützt) liegt unter
`docs/content-example.yml`.

---

## Moodle-Synchronisation

### Zugangsdaten in `.env` eintragen

```env
MOODLE_BASE_URL=https://meine-schule.moodle.org
MOODLE_TOKEN=webservice-token

MOODLE_PYTHON_2026_COURSE_URL=https://meine-schule.moodle.org/course/view.php?id=42
MOODLE_PYTHON_2026_USERNAME=mein-login
MOODLE_PYTHON_2026_PASSWORD=mein-passwort
```

Für mehrere Moodle-Instanzen (z.B. eigene Schule + zweite Schule eines Kollegen) wird
ein numerisches Suffix verwendet: `MOODLE_PYTHON_2026_2_COURSE_URL`, `..._2_USERNAME` usw.

### Verbindung prüfen

```bash
inv moodle-ping
```

### Kurs aus bestehendem Moodle importieren

```bash
inv import-moodle --course python-2026 --remote-course-id 42
```

Legt `index.mdx` und die Kursstruktur aus den H5P-Aktivitäten an.

### Kapitel hochladen oder aktualisieren

```bash
inv upload-chapter-moodle 012-miniworlds --course python-2026
```

Bestehende Aktivitäten werden anhand des `identifier` erkannt und aktualisiert,
neue werden angelegt. Kein manuelles Klicken in Moodle nötig.

---

## Für Kollegen: kollaborativer Betrieb

Alle inhaltlichen Änderungen (MDX-Dateien) können per Git geteilt werden.
Moodle-Zugangsdaten bleiben **immer lokal** in `.env` – diese Datei wird
niemals committet.

Empfohlener Workflow für mehrere Lehrkräfte:

```bash
# Neue Inhalte holen
git pull

# Eigene Änderungen in einem Feature-Branch
git checkout -b feature/neue-aufgabe-strings
# ... Dateien bearbeiten ...
git push origin feature/neue-aufgabe-strings
# → Pull Request stellen
```

Jede Lehrkraft konfiguriert in `.env` ihre eigene Moodle-Instanz. Die
Synchronisation läuft jeweils lokal; das Repo enthält nur den Quellinhalt.

Klausuren und Prüfungsaufgaben gehören **nicht** in dieses Repository – sie
liegen in einem separaten, privaten Repo.

---

## H5P-Libraries aktualisieren

Die H5P-Libraries sind nicht im Repository enthalten und werden lokal geladen:

```bash
# Aktuelle Version laden
inv update-h5p-libraries

# Bestimmten Release-Tag laden
inv update-h5p-libraries --tag v6.88.0
```

---

## Lizenz

**Werkzeugcode** (`scripts/`, `tasks.py`, `prepare.sh` u.ä.): [MIT](LICENSE)

**Kursinhalt** (`courses/`): [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.de) –
Weiterverwendung und Anpassung erlaubt, solange Herkunft genannt und das Ergebnis
unter gleicher Lizenz geteilt wird.
