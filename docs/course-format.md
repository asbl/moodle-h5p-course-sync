# Kursformat und Dateistruktur

Ein `course-sync`-Kurs liegt unter `courses/<kurs>/`. Der Ordnername ist der technische
Kurs-Slug und wird in CLI-Kommandos verwendet.

## Minimalstruktur

```text
courses/info-2026/
  index.mdx
  chapters/
    001-einstieg.mdx
  h5p/
    001-einstieg/
      hello-world/
        content.mdx
        settings.yml
        h5p.json
```

`course-sync new-course info-2026` erzeugt genau diese Struktur als Startpunkt.

## `index.mdx`

`index.mdx` definiert die Reihenfolge der Kapitel:

```mdx
# Informatik 2026

<Chapter src="./chapters/001-einstieg.mdx" title="Einstieg" />
<Chapter src="./chapters/002-variablen.mdx" title="Variablen" />
```

Der Dateiname ohne `.mdx` ist der Kapitel-Slug. Dazu passend sucht `course-sync` H5P-
Quellen standardmaessig unter `h5p/<kapitel-slug>/`.

## Kapiteldateien

Kapitel enthalten normalen Markdown/MDX-Text und Frage-Platzhalter:

````mdx
## Einstieg

<PythonQuestion
  identifier="hello-world"
  title="Erste Python-Aufgabe"
  instructions="Aendere den Text und fuehre das Programm aus."
/>

```python question:hello-world starter
print("Hello, world!")
```
````

`identifier` ist der stabile Schluessel der Aufgabe. Er sollte nach dem ersten Upload
nicht mehr geaendert werden, weil Moodle-Aktivitaeten darueber wiedererkannt werden.

## Aufgabenordner

Eine Aufgabe liegt unter:

```text
h5p/<kapitel-slug>/<identifier>/
```

### `content.mdx`

`content.mdx` beschreibt den sichtbaren Inhalt und optionale Editor-Bloecke:

````mdx
<Instructions>
Aendere den Text in der print-Anweisung und fuehre das Programm aus.
</Instructions>

```python editor:startingCode
print("Hello, world!")
```

```yaml grading
gradingMethod: please_choose
dueDateGroup:
  enableDueDate: false
  duedate: 01.01.1970
testCases: []
```
````

### `settings.yml`

`settings.yml` enthaelt Laufzeit- und Editoroptionen:

```yaml
contentType: ide_only
pythonRunner: skulpt
pyodideOptions:
  packages: []
```

Wichtige Runner:

- `skulpt`: schneller Start, gut fuer einfache Python-Aufgaben
- `pyodide`: groesser, aber naeher an CPython und mit Paketunterstuetzung

### `h5p.json`

`h5p.json` enthaelt H5P-Metadaten:

```json
{
  "title": "Erste Python-Aufgabe",
  "language": "de",
  "defaultLanguage": "de",
  "mainLibrary": "H5P.PythonQuestion",
  "embedTypes": ["div"],
  "license": "U",
  "preloadedDependencies": [
    {"machineName": "H5P.PythonQuestion", "majorVersion": 6, "minorVersion": 90},
    {"machineName": "H5P.CodeQuestion", "majorVersion": 6, "minorVersion": 90},
    {"machineName": "H5P.LibCodeTools", "majorVersion": 6, "minorVersion": 90}
  ],
  "majorVersion": 6,
  "minorVersion": 90
}
```

## Generierte Dateien

Diese Ordner sind lokal oder generiert und sollten nicht als Kursquelle bearbeitet werden:

- `courses/<kurs>/build/`
- `courses/<kurs>/exports/`
- `public/`
- `.h5p-runtime/`
- `temp/`
- `uploads/`

## Zusammenarbeit

Kursquellen werden per Git geteilt. Zugangsdaten bleiben in `.env` lokal.

Empfohlener Ablauf:

```bash
git pull
git checkout -b feature/neue-aufgabe
course-sync sync info-2026
course-sync export-site info-2026
git add courses/info-2026
git commit -m "Add variables exercise"
```
