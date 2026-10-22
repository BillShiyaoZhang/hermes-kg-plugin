FROM node:22-bookworm-slim AS frontend
WORKDIR /ui
COPY console/package*.json ./
RUN npm ci
COPY console/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/plugins:/app:/opt/hermes-agent
WORKDIR /app
COPY requirements-console.txt ./
COPY requirements-console.lock ./
COPY plugins/kg/requirements.in plugins/kg/requirements.in
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-console.lock
COPY plugins/ plugins/
COPY scripts/ scripts/
COPY skills/ skills/
COPY --from=frontend /ui/dist /app/static
EXPOSE 8765
CMD ["python", "-m", "kg.console_cli", "serve", "--host", "0.0.0.0", "--container"]
