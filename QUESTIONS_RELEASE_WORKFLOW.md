# Questions-Release-Workflow

Dieser Workflow baut die H5P-Question-Libraries in `../h5p-dev`, veroeffentlicht die Releases, zieht die neuen Libraries nach `course-sync` und prueft den lokalen `h5p-demo`-Kurs.

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

## Wichtige Optionen

```bash
.venv/bin/invoke release-questions-workflow --dry-run
.venv/bin/invoke release-questions-workflow --tag v6.90.0
.venv/bin/invoke release-questions-workflow --release-target pythonquestion
.venv/bin/invoke release-questions-workflow --skip-release
.venv/bin/invoke release-questions-workflow --h5p-dev-dir ../h5p-dev --course h5p-demo
```

- `--dry-run` reicht an `h5p-dev` weiter und prueft den Release-Aufruf ohne GitHub-Release zu schreiben.
- `--tag` zieht in `course-sync` ein bestimmtes GitHub-Release der Libraries statt des neuesten Defaults.
- `--release-target` begrenzt den Release in `h5p-dev`; Standard ist `all`.
- `--skip-release` baut Pakete und aktualisiert/prueft `course-sync`, ohne GitHub-Releases anzulegen.

## Nachbereitung

Pruefe danach die Git-Staende:

```bash
git -C /home/sbl/code-git/course-sync status --short
git -C /home/sbl/code-git/h5p-dev/libraries/H5P.PythonQuestion-6.0 status --short
git -C /home/sbl/code-git/h5p-dev/libraries/H5P.SQLQuestion-5.17 status --short
git -C /home/sbl/code-git/h5p-dev/libraries/H5P.JavaQuestion-1.0 status --short
```

Committe Library-Aenderungen in den jeweiligen Library-Repos und die aktualisierten Kurs-/Workflow-Dateien in `course-sync`.
