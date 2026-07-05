# Linux portable app packaging

Build a no-Docker Linux bundle:

```bash
packaging/linux/build-linux-app.sh
```

Output:

- `dist/linux/LittleAlphaxiv/`
- `dist/linux/LittleAlphaxiv-<version>-linux-x86_64.tar.gz`

The generated app includes:

- built Vite frontend served by FastAPI
- backend source and Alembic migrations
- Python virtual environment with backend dependencies
- `AppRun` launcher
- optional `install-desktop-entry.sh`

Run the bundle:

```bash
cd dist/linux/LittleAlphaxiv
./AppRun
```

Build requirements:

- Linux x86_64
- Node.js/npm
- Python 3.10+ with `venv`
- network access to install npm and pip dependencies, unless already cached

Runtime data defaults to `data/` inside the app directory when writable, and
falls back to `$XDG_DATA_HOME/little-alphaxiv` or `~/.local/share/little-alphaxiv`
when the app directory is read-only.
