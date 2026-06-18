# Installation und erste Schritte

Diese Anleitung richtet sich an Personen, die `course-sync` noch nicht kennen.

## Voraussetzungen

- Python 3.11 oder neuer
- Node.js mit `npx`
- Git
- optional: Moodle-Zugang mit H5P-Rechten

Fuer Moodle-Uploads nutzt `course-sync` Playwright. Beim ersten Upload kann Playwright
Browser-Dateien nachinstallieren.

## Einrichten

```bash
git clone <repo-url>
cd course-sync
source prepare.sh
```

`prepare.sh` erledigt:

- virtuelle Umgebung `.venv` anlegen
- `course-sync` im Editable-Modus installieren
- Python-Abhaengigkeiten installieren
- H5P-Libraries nach `libraries/` laden, falls sie fehlen
- `PYTHONPATH` fuer die aktuelle Shell setzen

Nach dem Setup sollten diese Befehle funktionieren:

```bash
course-sync --help
inv -l
```

In jeder neuen Shell reicht danach:

```bash
source prepare.sh
```

## Bestehenden Kurs bauen und ansehen

```bash
course-sync list-courses --verbose
course-sync sync h5p-demo
course-sync serve
```

Oeffne anschliessend <http://127.0.0.1:8765/>.

## Eigenen Kurs anlegen

```bash
course-sync new-course info-2026 --title "Informatik 2026"
course-sync sync info-2026
course-sync serve
```

Der neue Kurs enthaelt ein Kapitel und eine lauffaehige PythonQuestion. Bearbeite danach:

- `courses/info-2026/index.mdx`
- `courses/info-2026/chapters/001-einstieg.mdx`
- `courses/info-2026/h5p/001-einstieg/hello-world/content.mdx`
- `courses/info-2026/h5p/001-einstieg/hello-world/settings.yml`
- `courses/info-2026/h5p/001-einstieg/hello-world/h5p.json`

## H5P-Dateien exportieren

```bash
course-sync sync info-2026
course-sync export-chapter info-2026 001-einstieg
```

Ohne `--output` landen die Dateien unter `courses/<kurs>/exports/<kapitel>/`.

## Moodle konfigurieren

```bash
cp .env.example .env
```

Trage mindestens die Werte ein, die du fuer deinen Weg brauchst:

- `MOODLE_BASE_URL` und `MOODLE_TOKEN` fuer API-Import, Status und Ping
- `MOODLE_USERNAME`, `MOODLE_PASSWORD` und `MOODLE_COURSE_URL` fuer Browser-Uploads

Verbindung pruefen:

```bash
course-sync moodle-ping
```

Kapitel hochladen:

```bash
course-sync upload-chapter-moodle info-2026 001-einstieg
```

Mehrere Ziele fuer denselben Kurs werden ueber `--target` gewaehlt. Beispiel:

```env
MOODLE_INFO_2026_SCHULE_COURSE_URL=https://moodle.example.org/course/view.php?id=42
MOODLE_INFO_2026_SCHULE_USERNAME=mein-login
MOODLE_INFO_2026_SCHULE_PASSWORD=mein-passwort
```

```bash
course-sync upload-chapter-moodle info-2026 001-einstieg --target=schule
```

## Moodle-Upload: Zwei Nutzer erforderlich

 Fuer Browser-Uploads (Playwright) und API-Zugriffe werden **zwei separate Moodle-Nutzer** bentigt:

- **Playwright-Nutzer**: Muss als Trainer/in oder aehnlich im Kurs eingeschrieben sein
  (Browser-Login fuer den Upload)
- **Webservice-Nutzer** (Token): Muss ebenfalls im Kurs eingeschrieben sein
  (API-Zugriffe fuer den Upload)

Es reicht nicht, nur einen der beiden einzuschreiben — sonst scheitert der Upload
mit einem Login-Fehler.

## Typische Probleme

`course-sync: command not found`

Fuehre `source prepare.sh` im Projektordner aus.

`Kurs '...' wurde nicht gefunden`

Nutze `course-sync list-courses` und pruefe den Ordnernamen unter `courses/`.

Fehlende H5P-Libraries

```bash
course-sync update-h5p-libraries
```

Preview-Port belegt

```bash
course-sync serve --port 8770
```
