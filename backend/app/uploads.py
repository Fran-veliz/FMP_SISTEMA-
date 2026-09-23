"""Lectura acotada de archivos subidos.

El patrón anterior era `content = await file.read()` y recién después
comprobar `len(content) > MAX`: para cuando se rechazaba, el archivo entero
ya había entrado. Starlette vuelca a disco lo que pasa de ~1 MB, así que no
era memoria, pero sí espacio en disco sin techo — un usuario con turno
abierto podía llenar el disco del servidor subiendo algo enorme.

`read_upload_limited` lee de a bloques y corta apenas se pasa del límite,
así que nunca entra más de `max_bytes` (más un bloque).
"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile

CHUNK_SIZE = 64 * 1024


async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Devuelve el contenido del archivo, o 413 si supera `max_bytes`.

    Se aborta en el primer bloque que cruza el límite: no se sigue leyendo lo
    que ya se sabe que se va a rechazar."""
    partes: list[bytes] = []
    total = 0
    while True:
        bloque = await file.read(CHUNK_SIZE)
        if not bloque:
            break
        total += len(bloque)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Archivo demasiado grande (máx. {max_bytes // (1024 * 1024)} MB)",
            )
        partes.append(bloque)
    return b"".join(partes)
