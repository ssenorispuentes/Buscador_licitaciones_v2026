import csv
import json
import os
from datetime import datetime
from pathlib import Path


ANALYTICS_COLUMNS = [
    "ejecucion_id",
    "fecha_hora_inicio",
    "fecha_hora_fin",
    "duracion_segundos",
    "total_licitaciones",
    "pdf_descargados",
    "gemini_requeridas",
    "gemini_analizadas",
    "gemini_cache_reutilizadas",
    "gemini_api_solicitudes",
    "pdf_analizados_gemini",
    "gemini_api_disponible",
]


def guardar_metricas(metricas, analytics_dir="./analytics"):
    """Persiste una ejecución en JSON y en el histórico CSV local."""
    directory = Path(analytics_dir)
    directory.mkdir(parents=True, exist_ok=True)
    record = {column: metricas.get(column) for column in ANALYTICS_COLUMNS}
    execution_id = str(record.get("ejecucion_id") or datetime.now().strftime("%Y%m%d_%H%M%S"))

    json_path = directory / f"ejecucion_{execution_id}.json"
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(record, file, ensure_ascii=False, indent=2)

    history_path = directory / "historico_ejecuciones.csv"
    write_header = not history_path.exists() or history_path.stat().st_size == 0
    with history_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=ANALYTICS_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(record)
    return json_path, history_path


def contar_pdfs(df, output_dir_pdf):
    """Cuenta licitaciones cuyo nombre de PDF apunta a un fichero existente."""
    if "pdf" not in df.columns:
        return 0
    valid = 0
    for value in df["pdf"]:
        name = str(value).strip()
        if name.lower() in {"", "nan", "none", "notfound", "no disponible"}:
            continue
        if os.path.isfile(os.path.join(output_dir_pdf, os.path.basename(name))):
            valid += 1
    return valid
