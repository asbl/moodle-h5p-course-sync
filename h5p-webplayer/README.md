# H5P Webplayer

Minimaler lokaler Webplayer fuer `.h5p`-Pakete aus diesem Workspace.

Der Player nutzt die bereits vorhandene `.h5p-runtime` und die installierten H5P-Bibliotheken. Ein Paket wird per `h5p-cli import` in die Runtime importiert und dann in einer eigenen Vollbild-Seite angezeigt.

## Start

```bash
python h5p-webplayer/server.py \
  --package courses/h5p-demo/build/h5p/004-python-tests/python-tests.h5p
```

Danach im Browser öffnen:

```text
http://127.0.0.1:8091
```

## Optionen

```bash
python h5p-webplayer/server.py --help
```

Wichtige Optionen:

- `--package`: Pfad zu einem `.h5p`-Paket.
- `--content-id`: Runtime-ID; standardmäßig aus dem Dateinamen abgeleitet.
- `--port`: Port des Webplayers, Standard `8091`.
- `--runtime-port`: Port der H5P-CLI-Runtime, Standard `8080`.
- `--no-import`: vorhandenen Runtime-Content wiederverwenden.

Beispiel fuer die neue miniworlds-Frage:

```bash
python h5p-webplayer/server.py \
  --package courses/h5p-demo/build/h5p/014-python-miniworlds/python-miniworlds.h5p \
  --content-id demo-miniworlds \
  --port 8092
```
