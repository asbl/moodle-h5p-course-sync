# Questions-Release-Workflow

Dieser Workflow baut die H5P-Question-Libraries in `../h5p-dev`, veroeffentlicht die Releases, zieht die neuen Libraries nach `course-sync` und prueft die lokalen Demo-Kurse `h5p-demo` und `h5p-demo-en`.

## Standardlauf

```bash
cd /home/sbl/code-git/course-sync
.venv/bin/invoke release-questions-workflow
```

Der Task fuehrt der Reihe nach aus:

1. `.venv/bin/invoke pack-all` in `../h5p-dev` (oder ein globales `invoke`/`inv`, falls kein lokales Binary existiert)
2. `.venv/bin/invoke deploy.release --all` in `../h5p-dev`
3. `python -m scripts.main update-h5p-libraries` in `course-sync`
4. `python -m unittest discover -s tests -p 'test_*.py'`
5. `python -m scripts.main build h5p-demo`
6. `python -m scripts.main build h5p-demo-en`

## Wichtige Optionen

```bash
.venv/bin/invoke release-questions-workflow --dry-run
.venv/bin/invoke release-questions-workflow --tag v6.90.0
.venv/bin/invoke release-questions-workflow --release-target pythonquestion
.venv/bin/invoke release-questions-workflow --skip-release
.venv/bin/invoke release-questions-workflow --h5p-dev-dir ../h5p-dev --course h5p-demo --english-course h5p-demo-en
```

- `--dry-run` reicht an `h5p-dev` weiter und prueft den Release-Aufruf ohne GitHub-Release zu schreiben.
- `--tag` zieht in `course-sync` ein bestimmtes GitHub-Release der Libraries statt des neuesten Defaults.
- `--release-target` begrenzt den Release in `h5p-dev`; Standard ist `all`.
- `--skip-release` baut Pakete und aktualisiert/prueft `course-sync`, ohne GitHub-Releases anzulegen.
- `--course` und `--english-course` steuern, welche Demo-Kurse am Ende gebaut werden. Standard ist `h5p-demo` plus `h5p-demo-en`.

## Demo-Kurse synchron halten

`h5p-demo-en` ist die englische Fassung von `h5p-demo`. Wenn im deutschen Demo-Kurs ein Kapitel oder H5P-Beispiel hinzugefuegt, entfernt oder verschoben wird, muss dieselbe Struktur im englischen Kurs angepasst werden:

```bash
.venv/bin/python -m unittest tests.test_h5p_demo_workflow
.venv/bin/python -m scripts.main build h5p-demo
.venv/bin/python -m scripts.main build h5p-demo-en
```

Die Tests vergleichen Kapiteldateien und H5P-Beispiele beider Kurse und pruefen, dass die englischen H5P-Metadaten `language`/`defaultLanguage` auf `en` setzen.

## Demo-Kurse nach Moodle synchronisieren

Der deutsche Demo-Kurs ist mit `https://www.opencoding.de/course/view.php?id=2` verknuepft, der englische mit `https://www.opencoding.de/course/view.php?id=9`. Beide Kurse werden nicht per API angelegt; der Task erwartet vorhandene Moodle-Kurse und synchronisiert alle Kapitel als H5P-Aktivitaeten:

```bash
.venv/bin/invoke sync-h5p-demo-courses-moodle
```

Optional koennen Kurs-URLs ueberschrieben oder ein Headless-Lauf mit vorhandenem Storage-State genutzt werden:

```bash
.venv/bin/invoke sync-h5p-demo-courses-moodle --english-course-url https://www.opencoding.de/course/view.php?id=9
.venv/bin/invoke sync-h5p-demo-courses-moodle --headless
```

## Nachbereitung

Pruefe danach die Git-Staende:

```bash
git -C /home/sbl/code-git/course-sync status --short
git -C /home/sbl/code-git/h5p-dev/libraries/H5P.PythonQuestion-6.0 status --short
git -C /home/sbl/code-git/h5p-dev/libraries/H5P.SQLQuestion-5.17 status --short
git -C /home/sbl/code-git/h5p-dev/libraries/H5P.JavaQuestion-1.0 status --short
```

Committe Library-Aenderungen in den jeweiligen Library-Repos und die aktualisierten Kurs-/Workflow-Dateien in `course-sync`.
