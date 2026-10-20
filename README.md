# MovieBox Cinema

MovieBox Cinema is a React + TypeScript catalog and streaming interface backed by a FastAPI service. The backend handles catalog/search requests, title and episode metadata, stream resolution, signed media proxying, and subtitles. The player supports available video qualities, captions, and audio tracks exposed by a stream.

## Run locally

Install the dependencies:

```powershell
npm ci
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` if you need to change local settings. Run the API and Vite in separate terminals:

```powershell
uvicorn api:app --host 0.0.0.0 --port 8000
npm run dev
```

Open `http://localhost:3000`. In development, Vite proxies `/backend` API calls to port 8000. A combined production deployment uses the same origin for the UI, API, and proxied media.

## Deploy to Render Free

This repository includes a Dockerfile and `render.yaml` for a single Render web service. Create a GitHub repository, push this project, then choose **New + → Blueprint** in Render and select the repository. Render will build the React frontend into the Docker image and run it alongside FastAPI. The blueprint configures the Free plan, `/health` health check, a generated proxy-signing secret, and the requested smart ad link. `VITE_*` values are embedded at frontend build time.

The app keeps streaming requests on the deployed service origin, so catalog, playback resolution, media proxying, subtitle, and audio/video track features do not depend on a second separately configured API host. Actual playable tracks and qualities depend on what the upstream source supplies for each title.

## Configuration

`VITE_API_BASE_URL` is empty for the combined production service and can be set when deploying the frontend separately. `VITE_ENABLE_ADS` controls banner placements. `VITE_ENABLE_PREPLAY_AD` enables the click-to-open pre-play sponsor gate. `VITE_SMART_LINK_URL` sets that sponsor URL; it opens only after the viewer clicks **Watch ad**. `VITE_AD_COUNTDOWN_SECONDS` controls the unlock countdown. Optional banner URL, image, and label values customize ad placements. `PROXY_SIGNING_SECRET` and upstream credentials must remain server-side.

## Project files

- `src/`: frontend, player, catalog, and search UI
- `api.py`, `main.py`: FastAPI application and API routes
- `Dockerfile`, `render.yaml`: single-service Render deployment
- `requirements.txt`, `package-lock.json`: backend and frontend dependencies
