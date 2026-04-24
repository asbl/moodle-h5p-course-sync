# course-sync

Erster Prototyp für die lokale Synchronisation von PythonQuestion-H5P-Inhalten aus einer MDX-Datei.

## Format in `index.mdx`

Die MDX-Datei enthält beliebig viele `PythonQuestion`-Blöcke plus optionale Codeblöcke, die über den `identifier` an die jeweilige Aufgabe gebunden sind. Die `identifier` müssen innerhalb eines Kurses eindeutig sein.

```mdx
# Python Kurs

<PythonQuestion
  identifier="12eck"
  title="Zwölfeck zeichnen"
  instructions="Schreibe ein Programm, das ein Zwölfeck zeichnet."
  runner="pyodide"
  packages="miniworlds"
  gradingMethod="ioTestCases"
/>

~~~python question:12eck starter
import miniworlds

world = miniworlds.World(400, 400)
world.run()
~~~

~~~python question:12eck solution
print("fertig")
~~~

~~~json question:12eck testcase
{"hidden": false, "inputs": [], "outputs": ["fertig"]}
~~~

<PythonQuestion
  identifier="quadrat"
  title="Quadrat zeichnen"
  instructions="Schreibe ein Programm, das ein Quadrat zeichnet."
  runner="pyodide"
  packages="miniworlds"
  gradingMethod="ioTestCases"
/>

~~~python question:quadrat starter
import miniworlds

world = miniworlds.World(300, 300)
pen = miniworlds.Turtle()
world.run()
~~~

~~~python question:quadrat solution
print("quadrat")
~~~

~~~json question:quadrat testcase
{"hidden": true, "inputs": [], "outputs": ["quadrat"]}
~~~
```

Unterstützte Rollen:

- `starter`
- `solution`
- `pre`
- `post`
- `testcase`
- `file:helper.py`

Importierte H5Ps koennen weiterhin als verschachteltes `h5p={...}`-Prop direkt im `PythonQuestion`-Tag beschrieben werden. Dabei stehen nur Werte im Objekt, die vom PythonQuestion-Standard abweichen. Dadurch bleibt die Struktur deutlich lesbarer als ein rohes `content.json`, ist aber vollstaendig editierbar.

```mdx
<PythonQuestion
  identifier="farben"
  title="Einführung: Farben"
  instructions="Schau dir den folgenden Code an."
  runner="skulpt"
  h5p={{
    "contentType": "text_only",
    "contents": [
      {
        "text": "Schau dir den folgenden Code an."
      },
      {
        "type": "code",
        "code": "import turtle\nturtle.forward(10)\n"
      }
    ]
  }}
/>
```

## Kommandos

Entwicklungsumgebung vorbereiten:

```bash
source prepare.sh
```

Danach stehen die Invoke-Tasks zur Verfuegung:

```bash
inv -l
```

H5P-Dateien erzeugen:

```bash
inv sync
```

Browser-Vorschau starten:

```bash
inv serve
```

Weitere nuetzliche Tasks:

- `inv test`
- `inv smoke`
- `inv clean`
- `inv clean-runtime`
- `inv import-moodle --course python-2026 --remote-course-id 5`
- `inv status --course python-2026`

Die Vorschau ist dann unter `http://127.0.0.1:8765/` erreichbar und synchronisiert die H5P-Dateien beim Laden automatisch.

Jede Aufgabe wird in der Browser-Vorschau als kompakte Zeile dargestellt und oeffnet Vorschau, Edit oder Split View erst auf Klick in einem Popup.

Die Browser-Vorschau rendert ein vollständiges H5P. Dafür bootstrapped `scripts/main.py` beim ersten Start automatisch eine lokale H5P-Laufzeit unter `.h5p-runtime/`, lädt die benötigten Library-Pakete und startet den H5P-Server selbst. Eine separat installierte `h5p-cli`-Entwicklungsumgebung ist nicht nötig.

Voraussetzung dafür ist nur, dass entweder `npx` oder ein vorhandenes `h5p`-Binary im `PATH` verfügbar ist.

## Ausgabe

Beim Synchronisieren werden die H5P-Artefakte direkt unter `courses/<kurs>/h5p/` erzeugt. Der Ordner ist die Source of Truth.

- `courses/<kurs>/h5p/<identifier>.h5p`
- `courses/<kurs>/h5p/<identifier>/h5p.json`
- `courses/<kurs>/h5p/<identifier>/content.yml`
- `courses/<kurs>/h5p/<identifier>/...` fuer Assets wie `images/`

`content.yml` ist die lokale Quelle fuer den H5P-Content und ist fuer manuelle Bearbeitung lesbar, inklusive formatierten Sonderzeichen. Das `content/content.json` im `.h5p`-Archiv wird bei Bedarf aus `content.yml` generiert. Assets aus dem H5P-`content/`-Bereich liegen lokal ohne zusaetzliche Zwischenebene direkt im Aufgabenordner, also zum Beispiel unter `courses/<kurs>/h5p/<identifier>/images/`.

Eine vollstaendige Vorlage fuer neue `content.yml`-Dateien (inklusive Runner-Alternativen `pyodide`/`skulpt`) liegt unter `docs/content-example.yml`.

Die benoetigten H5P-Libraries werden nicht mehr kurslokal unter `courses/<kurs>/h5p/libraries/` abgelegt. Stattdessen liegen zentrale Kopien nur unter `libraries/`. Beim Bauen der `.h5p`-Archive werden die benoetigten Libraries von dort in das Paket aufgenommen.

## Moodle-Import und Status

Der erste Remote-Sync-Schritt ist als API-basierter Import und Statuslauf implementiert.

Voraussetzungen:

- `.env` im Projektwurzelverzeichnis enthaelt die Moodle-Zugangsdaten
- alternativ koennen `MOODLE_BASE_URL` und `MOODLE_TOKEN` als Umgebungsvariablen gesetzt werden

Beispiel fuer `.env`:

```env
MOODLE_BASE_URL=https://www.opencoding.de
MOODLE_TOKEN=hier-token-einfuegen
```

Verbindung und Token direkt pruefen:

```bash
inv moodle-ping
```

Die Ausgabe zeigt, ob die Moodle-Instanz erreichbar ist, welcher Webservice-Benutzer verwendet wird und ob `core_course_get_contents` fuer den Import freigegeben ist.

Vorhandenen Moodle-Kurs lokal abbilden:

```bash
inv import-moodle --course python-2026 --remote-course-id 5
```

Das erzeugt aktuell:

- `courses/<kurs>/index.mdx` als lokales Autoren-Grundgeruest aus den gefundenen H5P-Aktivitaeten
- bei oeffentlich erreichbaren H5P-Aktivitaeten werden Startercode, Tests, Runner und weitere PythonQuestion-Daten direkt aus dem `.h5p`-Paket uebernommen
- `courses/<kurs>/.course-sync.json` mit der Zuordnung `identifier <-> remote activity id`

Den aktuellen lokalen Sync-Status anzeigen:

```bash
inv status --course python-2026
```

Der Status unterscheidet derzeit zwischen:

- `tracked`
- `modified-local`
- `local-only`
- `remote-only`

Schreibender Push nach Moodle ist noch nicht implementiert; dieser Import-/Status-Pfad ist die Grundlage dafuer.
