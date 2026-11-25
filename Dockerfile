FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir . && useradd --create-home worker && mkdir /state && chown worker /state
USER worker
ENV STATE_DB=/state/state.sqlite
EXPOSE 8000
CMD ["uvicorn", "member_services.api:create_app", "--factory", "--host", "0.0.0.0"]

