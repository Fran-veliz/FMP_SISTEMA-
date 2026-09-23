FROM python:3.12-slim

WORKDIR /app

# pip se actualiza antes de instalar nada: la versión que trae la imagen base
# arrastra avisos de seguridad propios y aparece en cualquier auditoría de
# dependencias, aunque en ejecución la aplicación no lo use.
RUN pip install --no-cache-dir --upgrade pip

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini .
COPY backend/scripts ./scripts

RUN useradd --create-home --shell /usr/sbin/nologin appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Deja la base al día antes de levantar la API. No es `alembic upgrade head` a
# secas: en una base vacía la cadena de migraciones no se puede recorrer y el
# contenedor no arrancaba nunca. Ver scripts/init_db.py.
CMD ["sh", "-c", "python scripts/init_db.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
