# course-sync

`course-sync` verwaltet interaktive Programmieraufgaben als Textdateien und baut daraus
H5P-Pakete. Die Pakete koennen lokal im Browser geprueft, als Dateien exportiert oder in
Moodle-Kurse hochgeladen werden.

Das Projekt ist fuer Lehrkraefte und Autorinnen gedacht, die Kursinhalte lieber in Git
pflegen als in Moodle-Formularen. Ein Kurs besteht aus MDX-Dateien, kleinen YAML/JSON-
Konfigurationen und optionalen Bildern.

## Schnellstart

```bash
git clone <repo-url>
cd course-sync
source prepare.sh

course-sync list-courses
course-sync new-course mein-kurs --title "Mein Kurs"
course-sync sync mein-kurs
course-sync export-site mein-kurs
```

Danach liegt die statische Kursseite unter `public/index.html`. Sie kann direkt im
Browser geoeffnet oder mit einem einfachen lokalen Webserver ausgeliefert werden:

```bash
python -m http.server 8000 --directory public
```

Dann ist die statische Ansicht unter <http://127.0.0.1:8000/> erreichbar.

Wenn du lieber `invoke` nutzt, funktionieren die bestehenden Kurzbefehle weiterhin:

```bash
inv sync --course=mein-kurs
inv export-site --course=mein-kurs
```

## Was wird unterstuetzt?

- H5P-Pakete aus MDX-Kursen bauen
- lokale Browser-Vorschau mit H5P-Runtime starten
- vorhandene `.h5p`-Aktivitaeten und Moodle-Backups importieren
- Kapitel als H5P-Dateien exportieren
- Kapitel per Playwright in Moodle hochladen
- mehrere Moodle-Ziele pro lokalem Kurs konfigurieren

Der Hauptfokus liegt auf `H5P.PythonQuestion`; Demo-Kurse zeigen zusaetzlich SQL-, Java-
und Automata-Fragen.

## Wichtige Kommandos

```bash
course-sync --help
course-sync list-courses --verbose
course-sync new-course info-2026 --title "Informatik 2026"
course-sync sync info-2026
course-sync build info-2026
course-sync export-site info-2026 --output public
course-sync export-chapter info-2026 001-einstieg
course-sync serve --port 8765
course-sync import-mbz imported-course backup.mbz
course-sync moodle-ping
course-sync upload-chapter-moodle info-2026 001-einstieg --target=schule
```

Die alten Task-Namen bleiben als Komfortschicht verfuegbar:

```bash
inv -l
inv smoke --course=info-2026
inv export-site --course=info-2026
```

## Statische Website und GitHub Pages

`course-sync export-site` erzeugt eine rein statische Website. Kursseiten werden als HTML
geschrieben, H5P-Aufgaben werden als `.h5p`-Downloads verlinkt. Die Bearbeitung passiert
weiterhin in den Kursdateien unter `courses/`.

Der Standard-Output ist `public/`. Das Repository enthaelt einen GitHub-Actions-Workflow
unter `.github/workflows/pages.yml`, der bei jedem Push auf `main` die H5P-Libraries
laedt, `course-sync export-site --output public` ausfuehrt und `public/` nach GitHub
Pages deployt.

## Dokumentation

- [Installation und erste Schritte](docs/getting-started.md)
- [Kursformat und Dateistruktur](docs/course-format.md)
- [Beispiel fuer das alte `content.yml`-Format](docs/content-example.yml)
- [Release-Workflow fuer Question-Libraries](QUESTIONS_RELEASE_WORKFLOW.md)

## Moodle-Zugangsdaten

Kopiere `.env.example` nach `.env` und trage lokale Zugangsdaten ein. `.env` bleibt auf
deinem Rechner und wird nicht committet.

```bash
cp .env.example .env
```

Globale Werte:

```env
MOODLE_BASE_URL=https://moodle.example.org
MOODLE_TOKEN=...
MOODLE_USERNAME=...
MOODLE_PASSWORD=...
```

Kurs- oder zielbezogene Werte folgen dem Muster `MOODLE_<KURS>_<TARGET>_<SETTING>`, zum
Beispiel `MOODLE_INFO_2026_SCHULE_COURSE_URL`.

## Lizenz

Werkzeugcode wie `scripts/`, `tasks.py` und `prepare.sh`: [MIT](LICENSE)

Kursinhalt in `courses/`: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.de),
sofern im jeweiligen Kurs nichts anderes angegeben ist.
