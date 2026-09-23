"""Normalización de texto compartida por los lectores de archivos.

La misma función estaba copiada en `excel_import` y en `flight_history_import`,
y los scripts de carga masiva la importaban con su nombre privado (`_norm`)
desde uno de los dos. Vive acá para que los cuatro usen la misma, y con nombre
público: es parte del contrato entre módulos, no un detalle interno.
"""
from __future__ import annotations

import unicodedata


def normalizar(s: object) -> str:
    """MAYÚSCULAS, espacios colapsados y sin tildes.

    Se aplica a los encabezados y a los valores de celda antes de compararlos:
    "NÚMERO", "numero" y "Numero " tienen que matchear igual, sin importar cómo
    haya exportado el Excel cada quien.
    """
    text = " ".join(str(s or "").split()).upper()
    descompuesto = unicodedata.normalize("NFKD", text)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))
