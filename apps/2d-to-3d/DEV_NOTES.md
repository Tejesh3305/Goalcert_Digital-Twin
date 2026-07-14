# 2-D → 3-D — Dev Notes (deployment / debugging)

This folder is the **standalone 2-D → 3-D pipeline** for review and deployment.
Keep it transparent and easy to debug; integration with the main platform comes
later, once this pipeline is perfected.

## Run it

```powershell
./start-dev.ps1          # launches backend (:8000) + frontend (:5174) in two windows
```

Then open **http://localhost:5174**.

Start manually if you prefer:

```powershell
# backend
cd backend
python -m uvicorn app:app --reload --port 8000

# frontend (separate terminal)
cd frontend
npm run dev
```

## Two issues that bit us (and the fixes)

1. **"uvicorn not found"** — `uvicorn` is installed but Python's `Scripts\` folder
   isn't on PATH, so the bare command can't be found. **Always launch with
   `python -m uvicorn …`** (the start script does this). Changing the VS Code
   interpreter doesn't help because that only affects the editor, not the shell.

2. **Backend crashed on import: `cannot import name '_internal_dataclass'
   from 'pydantic._internal'`** — a corrupted/half-upgraded **pydantic** install
   (pydantic files didn't match `pydantic-core`). Fixed by reinstalling a matching
   version:

   ```powershell
   python -m pip install --user --force-reinstall --no-deps "pydantic==2.10.4"
   ```

   (`pydantic-core 2.27.2` pairs with `pydantic 2.10.x`.) If the backend ever
   fails to import again, run `python -c "import app"` from `backend/` — it prints
   the real traceback instead of a vague "uvicorn" error.

## Asset Library (review the 3-D assets on their own)

In the app, top-left, switch **Build from plan → Asset Library**:

- Every asset is listed by category with a live 3-D thumbnail.
- Filter by **Hospital / Data Center / All**, by category, or search.
- Click any asset → full viewer: **orbit/zoom**, a **1.7 m human** + **1 m grid**
  for scale (for the VR walk-through), real-world **W×D×H in m and ft**, and
  mesh/triangle counts for debugging.
- With a plan loaded, **"Add to plan"** drops that asset into the current model.

The asset manifest is a single transparent file: [`frontend/src/three/catalog.js`](frontend/src/three/catalog.js).
Dimensions shown there are **computed from the actual model geometry × `PROP_SCALE`
(0.32 m/unit)** — so the numbers always match what renders. The geometry builders
live in [`frontend/src/three/props.jsx`](frontend/src/three/props.jsx).

### Adding a new asset
1. Write a `pYourThing(M)` builder in `props.jsx` (returns a `THREE.Group`).
2. Register it in the `PROP_FOR` map at the bottom of `props.jsx`.
3. Add one entry to `CATALOG` in `catalog.js` (key, label, category, facility, desc).
   It then appears in the Library automatically.
