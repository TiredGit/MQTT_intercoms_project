FROM python:3.12

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync

COPY . .

CMD ["uv", "run", "python", "/app/main.py"]