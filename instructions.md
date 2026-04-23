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
