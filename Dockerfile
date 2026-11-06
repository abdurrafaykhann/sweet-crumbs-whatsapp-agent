FROM node:22-slim AS dashboard
WORKDIR /dashboard
COPY dashboard/package*.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard .
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY data data
COPY --from=dashboard /dashboard/dist dashboard/dist
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
