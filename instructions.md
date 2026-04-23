# H5P-Struktur

- `courses/<kurs>/h5p/` ist die Source of Truth fuer lokale H5P-Inhalte.
- Jede Aufgabe liegt unter `courses/<kurs>/h5p/<identifier>/`.
- In diesem Ordner liegen mindestens `h5p.json` und `content.yml`.
- `content.yml` ist die lesbare Editieransicht mit formatierten Sonderzeichen und dient als lokale Quelle fuer den Content.
- `content/content.json` wird nur im `.h5p`-Archiv benoetigt und bei Bedarf aus `content.yml` generiert.
- Assets aus dem H5P-`content/`-Bereich liegen direkt unterhalb des Aufgabenordners, zum Beispiel `courses/<kurs>/h5p/experimente-mit-schleifen/images/...`.
- Die Zwischenebene `content/` wird lokal nicht verwendet.
- H5P-Libraries werden nicht unter `courses/<kurs>/h5p/` gespeichert.
- Zentrale Libraries liegen nur unter `libraries/`.

# Refactoring-Konventionen fuer `scripts/main.py`

## Fassaden-Regel
Eine Funktion in `main.py` wird nur behalten, wenn sie direkt aus `main()`, `serve_preview()` oder `tasks.py` aufgerufen wird. Rein test-interne Fassaden werden entfernt; Tests rufen Service-Methoden direkt auf (z. B. ueber den Provider `module.component_syncer()`).

## Modul-Struktur
- `scripts/main.py` ist eine duenne Provider-/Fassadenschicht. Gesamte Logik liegt in `scripts/classes/`.
- `scripts/classes/content_types/_helpers.py` enthaelt reine Hilfsfunktionen (kein I/O): `clone_json_value`, `merge_json_values`, `escape_h5p_value`, `default_from_semantics_field`, `default_object_from_semantics`, `compact_by_semantics`, `unescape_display_value`, `normalize_whitespace`, `render_template_literal`, `render_jsx_value`.
- Reine Kalkulations-Hilfsfunktionen werden **nicht** in `ComponentSyncer` (oder andere Services) injiziert, sondern direkt aus `_helpers.py` importiert.
- I/O-abhaengige Funktionen (z. B. `load_python_question_semantics`, `load_h5p_payload_from_source_package`) bleiben injectable, damit Tests sie ersetzen koennen.

## Service-Verantwortlichkeiten
| Service | Zustaendig fuer |
|---|---|
| `ComponentSyncer` | MDX-↔-H5P-Payload-Synchronisation; beinhaltet `_build_default_*`-Methoden fuer PythonQuestion |
| `CourseOrchestrator` | Kurs-Orchestrierung, Preview, Status-Bericht (`build_course_status`) |
| `MoodleSyncer` | Moodle-Import, `build_moodle_ping_report` als `@staticmethod` |
| `H5PLibraryManager` | Library-Verwaltung und -Verzeichnisse |
| `MoodleClientResolver` | Moodle-API-Client-Aufloesung |
| `H5PFileService` | H5P-Dateioperationen |
| `RuntimeHtmlRewriter` | Preview-HTML-Rewriting |
| `TextOperations` | Text-/Zeichenketten-Hilfsfunktionen |

## Duplikat-Regel
Keine Funktion darf in `main.py` und gleichzeitig in einem Service-Modul existieren. Bei Duplikaten wird die `main.py`-Version entfernt oder zu einer Delegation umgewandelt.
