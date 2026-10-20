FROM node:22-alpine AS frontend
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=
ARG VITE_ENABLE_ADS=true
ARG VITE_ENABLE_PREPLAY_AD=true
ARG VITE_SMART_LINK_URL=https://auctionr.org/4/c69074fbf2990ba792cfbf352a8e12ff
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_ENABLE_ADS=$VITE_ENABLE_ADS \
    VITE_ENABLE_PREPLAY_AD=$VITE_ENABLE_PREPLAY_AD \
    VITE_SMART_LINK_URL=$VITE_SMART_LINK_URL
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py main.py ./
COPY --from=frontend /app/dist ./dist
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
