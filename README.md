# FMP_SISTEMA

Sistema de gestión de afluencia de tránsito aéreo (ATFM) de la Función de
Gestión de Afluencia (FMP) de CORPAC, aeropuerto Jorge Chávez (SPJC), Lima.

Reemplaza un libro de Excel —hojas *FormatoFMP SUR*, *FormatoFMP NOR*, *INFO*,
*Cálculo CTOT* y *BITACORA*— por una aplicación web multiusuario con base de
datos, tiempo real y auditoría.

> **Este repositorio contiene solo el código.** La documentación técnica, las
> guías de despliegue, el registro de decisiones y los scripts de operación
> están en `SISTEMA_CTOT_CORPAC`, que es el repositorio desde el que se
> despliega en producción.

## Arquitectura

Tres contenedores Docker sobre una red, con un único puerto expuesto.

| Servicio | Tecnología | Responsabilidad |
|---|---|---|
| `web` | Nginx + React 19 / TypeScript / Vite | Sirve la interfaz y hace de proxy inverso a `/api` |
| `api` | FastAPI + SQLAlchemy 2 (Python 3.12) | Endpoints, reglas de dominio, autenticación, tiempo real |
| `db` | PostgreSQL 16 | Base `fmu_lima`, esquema `public`, versionada con Alembic |

La API y la base publican solo en `127.0.0.1`: desde la red únicamente se
alcanza el puerto 80 de Nginx.

## Estructura

| Carpeta | Responsabilidad |
|---|---|
| `backend/app/routers/` | Endpoints, permisos de acceso y validación de fechas |
| `backend/app/services/` | Cálculo CTOT, importadores, acceso al itinerario |
| `backend/app/models.py` | Modelos SQLAlchemy |
| `backend/alembic/` | Migraciones versionadas; no modificar las ya aplicadas |
| `backend/scripts/` | Inicialización, carga histórica y administración por consola |
| `backend/tests/` | Pruebas y archivos de muestra |
| `frontend/src/` | Componentes por función, hooks, servicios y una hoja de estilos por zona |
| `docker/` | Dockerfiles y configuración de Nginx |
| `ops/` | Respaldo y restauración |

## Puesta en marcha

Copiar `.env.example` a `.env` y completarlo. La cabecera del archivo explica
cómo generar la contraseña y los PIN; **no reutilizar los de otra instalación**.

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

La interfaz queda en `http://localhost` o el puerto elegido en `WEB_PORT`.

`ENVIRONMENT` falla cerrada: solo `development` o `test` publican `/docs`,
`/redoc` y `/openapi.json`. Tras instalar en producción, `/api/docs` tiene que
devolver 404.

## Verificación

Desde `backend/`:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

La suite usa SQLite en memoria y un reloj controlado. Las pruebas que requieren
`TEST_POSTGRES_URL` recrean esquemas: apuntarlas solo a una base desechable
dedicada.

Desde `frontend/`:

```bash
npm run test
npm run lint
npm run build
```

Los endpoints `/health` y `/ready` comprueban respuesta del proceso y acceso a
datos. `/server-time` devuelve la hora UTC y si está verificada contra NTP.

## Desarrollo

Se puede mantener la API y la base en Docker y ejecutar solo la interfaz con
Vite, que reenvía `/api` y sus WebSockets a `http://127.0.0.1:8000`:

```bash
docker compose up -d db api
cd frontend
npm ci
npm run dev
```

FastAPI no carga automáticamente el `.env` de Compose: para correr la API
fuera de Docker hay que definir `DATABASE_URL`, `ENVIRONMENT` y los PIN
iniciales en el entorno del proceso.

## Nota sobre los comentarios del código

Varios citan `DECISIONES.md` (por ejemplo *"ver DECISIONES.md §5"*). Ese
registro de decisiones de negocio está en `SISTEMA_CTOT_CORPAC`, junto con el
resto de la documentación.
