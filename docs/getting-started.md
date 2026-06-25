# Installation und erste Schritte

Diese Anleitung richtet sich an Personen, die `course-sync` noch nicht kennen.

## Voraussetzungen

- Python 3.11 oder neuer
- Node.js mit `npx`
- Git
- optional: Moodle-Zugang mit H5P-Rechten

Für Moodle-Uploads nutzt `course-sync` Playwright. Beim ersten Upload kann Playwright
Browser-Dateien nachinstallieren.

## Einrichten

```bash
git clone <repo-url>
cd course-sync
source prepare.sh
```

`prepare.sh` erledigt:

- Virtuelle Umgebung `.venv` anlegen
- `course-sync` im Editable-Modus installieren
- Python-Abhängigkeiten installieren
- H5P-Libraries nach `libraries/` laden, falls sie fehlen
- `PYTHONPATH` für die aktuelle Shell setzen

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
course-sync export-site h5p-demo
```

Öffne anschließend `public/index.html` im Browser oder starte einen einfachen lokalen
Webserver:

```bash
python -m http.server 8000 --directory public
```

Die statische Ansicht ist dann unter <http://127.0.0.1:8000/> erreichbar.

## Eigenen Kurs anlegen

```bash
course-sync new-course info-2026 --title "Informatik 2026"
course-sync export-site info-2026
```

Der neue Kurs enthält ein Kapitel und eine lauffähige PythonQuestion. Bearbeite danach:

- `courses/info-2026/index.mdx`
- `courses/info-2026/chapters/001-einstieg.mdx`
- `courses/info-2026/h5p/001-einstieg/hello-world/content.mdx`
- `courses/info-2026/h5p/001-einstieg/hello-world/settings.yml`
- `courses/info-2026/h5p/001-einstieg/hello-world/h5p.json`

## H5P-Dateien exportieren

```bash
course-sync sync info-2026
course-sync audit info-2026
course-sync export-chapter info-2026 001-einstieg
```

Ohne `--output` landen die Dateien unter `courses/<kurs>/exports/<kapitel>/`.

## Statische Website exportieren

```bash
course-sync export-site info-2026 --output public
inv export-site --course=info-2026
```

Der Export schreibt HTML-Seiten und kopiert die gebauten `.h5p`-Pakete nach `public/`.
Die Seite ist rein statisch und funktioniert lokal genauso wie auf GitHub Pages. Im
Repository liegt ein Workflow unter `.github/workflows/pages.yml`, der bei Push auf
`main` automatisch `public/` baut und nach GitHub Pages deployed.

## Moodle konfigurieren

```bash
cp .env.example .env
```

Trage mindestens die Werte ein, die du für deinen Weg brauchst:

- `MOODLE_BASE_URL` und `MOODLE_TOKEN` für API-Import, Status und Ping
- `MOODLE_USERNAME`, `MOODLE_PASSWORD` und `MOODLE_COURSE_URL` für Browser-Uploads

Verbindung prüfen:

```bash
course-sync moodle-ping
```

Ganzen Kurs hochladen und Moodle-Abschnitte mit `index.mdx` abgleichen:

```bash
course-sync upload-course-moodle info-2026 --verify-remote
```

Ein einzelnes Kapitel gezielt hochladen:

```bash
course-sync upload-chapter-moodle info-2026 001-einstieg
```

Headless-Uploads brauchen entweder einen gültigen Storage-State oder Moodle-Zugangsdaten. Für einen Kurs `info-2026`
werden zuerst die kursbezogenen Variablen gelesen:

```env
MOODLE_INFO_2026_COURSE_URL=https://moodle.example.org/course/view.php?id=42
MOODLE_INFO_2026_USERNAME=mein-login
MOODLE_INFO_2026_PASSWORD=mein-passwort
```

Danach funktioniert der Upload ohne sichtbaren Browser:

```bash
course-sync upload-course-moodle info-2026 --headless --verify-remote
```

Mehrere Ziele für denselben Kurs werden über `--target` gewählt. Beispiel:

```env
MOODLE_INFO_2026_SCHULE_COURSE_URL=https://moodle.example.org/course/view.php?id=42
MOODLE_INFO_2026_SCHULE_USERNAME=mein-login
MOODLE_INFO_2026_SCHULE_PASSWORD=mein-passwort
```

```bash
course-sync upload-course-moodle info-2026 --target=schule
```

## Qualitaet pruefen und veroeffentlichen

Der lokale Audit baut den Kurs und prueft typische Probleme in Quellen und H5P-Paketen:

```bash
course-sync audit info-2026
```

Nach einem Upload kann `verify-moodle` die eingebetteten H5P-Frames im Remote-Kurs pruefen. Dabei zaehlt nicht nur der Moodle-Titel, sondern sichtbarer Inhalt im H5P-Frame.

```bash
course-sync verify-moodle info-2026 --headless
```

Der Komfortbefehl `publish` fuehrt Audit, Upload, Remote-Verifikation und Statusausgabe in einem Lauf aus:

```bash
course-sync publish info-2026 --headless
```

## Moodle-Upload: Zwei Nutzer erforderlich

Für Browser-Uploads (Playwright) und API-Zugriffe werden **zwei separate Moodle-Nutzer** benötigt:

- **Playwright-Nutzer**: Muss als Trainer/in oder ähnlich im Kurs eingeschrieben sein
  (Browser-Login für den Upload)
- **Webservice-Nutzer** (Token): Muss ebenfalls im Kurs eingeschrieben sein
  (API-Zugriffe für den Upload)

Es reicht nicht, nur einen der beiden einzuschreiben — sonst scheitert der Upload
mit einem Login-Fehler.

## Typische Probleme

`course-sync: command not found`

Führe `source prepare.sh` im Projektordner aus.

`Kurs '...' wurde nicht gefunden`

Nutze `course-sync list-courses` und prüfe den Ordnernamen unter `courses/`.

Fehlende H5P-Libraries

```bash
course-sync update-h5p-libraries
```

Preview-Port belegt, falls du die alte dynamische Preview nutzt

```bash
course-sync serve --port 8770
```
