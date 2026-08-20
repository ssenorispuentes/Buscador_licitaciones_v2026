import configparser
import hashlib
import html
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from unidecode import unidecode

import src.functions as functions
from src.gemini_processor import LicitacionGeminiProcessor, extraer_paginas_clave_pdf


TECH_EXCLUDED = {"no tecnológica", "no tecnologica", "no clasificada", "notfound", ""}
ANALYSIS_TYPES = {
    "stack": (
        "🏷️ Clasificar en detalle y detectar Stack Tecnológico",
        "Asigna una subcategoría tecnológica muy precisa y enumera las tecnologías, "
        "arquitecturas, lenguajes, plataformas y certificaciones técnicas requeridas.",
    ),
    "resumen": (
        "📋 Generar Resumen Ejecutivo",
        "Resume en 4 o 5 viñetas qué pide el cliente, alcance, entregables y duración.",
    ),
    "solvencia": (
        "🛡️ Extraer Requisitos de Solvencia",
        "Extrae solvencia económica y técnica: facturación mínima, proyectos "
        "similares, certificaciones obligatorias y titulaciones o perfiles del equipo.",
    ),
    "criterios": (
        "⚖️ Ver Criterios de Puntuación y Penalizaciones",
        "Detalla el reparto de puntos, fórmulas económicas, juicio de valor, criterios "
        "de desempate y penalizaciones por incumplimiento.",
    ),
}
MAX_PDF_CONTEXT_CHARS = 30000


def cargar_config(config_file="./config/scraper_config.ini"):
    config = configparser.ConfigParser()
    config.optionxform = str
    with open(config_file, encoding="utf-8") as file:
        config.read_file(file)
    return config.get("input_output_path", "output_dir_final", fallback="./datos_licitaciones_final")


def cargar_columns_ini(columns_file="./config/scraper_columns.ini"):
    config = configparser.ConfigParser()
    config.optionxform = str
    with open(columns_file, encoding="utf-8") as file:
        config.read_file(file)
    source = functions.get_columns_dict(config["final_columns_order_st"])
    display = functions.get_columns_dict(config["final_columns_st"])
    display_by_index = {index: name for name, index in display.items()}
    return {internal: display_by_index[index] for internal, index in source.items() if index in display_by_index}


@st.cache_data
def cargar_datos(output_dir):
    csv_path = os.path.join(output_dir, "licitaciones.csv")
    if not os.path.exists(csv_path):
        return None, csv_path
    data = pd.read_csv(csv_path, sep="\t", encoding="utf-8-sig")
    return data.loc[:, ~data.columns.str.contains("^Unnamed")], csv_path


@st.cache_data
def cargar_analytics(analytics_dir="./analytics"):
    history = Path(analytics_dir) / "historico_ejecuciones.csv"
    if not history.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(history)
    except (OSError, pd.errors.ParserError):
        return pd.DataFrame()


def _texto_valido(value):
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "notfound", "-1"} else text


def clave_expediente(row):
    expediente = _texto_valido(row.get("Nº Expediente"))
    fallback = "|".join((_texto_valido(row.get("Título")), _texto_valido(row.get("URL"))))
    return hashlib.sha256((expediente or fallback).encode("utf-8")).hexdigest()


def ruta_cache_analisis(row, cache_dir="./.gemini_cache/analisis"):
    return Path(cache_dir) / f"{clave_expediente(row)}.json"


def cargar_cache_analisis(row, cache_dir="./.gemini_cache/analisis"):
    path = ruta_cache_analisis(row, cache_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def guardar_cache_analisis(row, data, cache_dir="./.gemini_cache/analisis"):
    path = ruta_cache_analisis(row, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def localizar_pdf(row, pdf_dir="./pdfs", cache_dir="./.gemini_cache/pdfs"):
    """Localiza el pliego o descarga una URL PDF a la caché permanente."""
    reference = _texto_valido(row.get("PDF / Ruta"))
    if not reference:
        return None
    direct = Path(reference)
    candidates = [direct, Path(pdf_dir) / direct.name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if reference.lower().startswith(("http://", "https://")):
        target_dir = Path(cache_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{clave_expediente(row)}.pdf"
        if target.exists():
            return target
        response = requests.get(reference, timeout=30)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if not response.content.startswith(b"%PDF") and "pdf" not in content_type:
            raise ValueError("El enlace asociado no devuelve un documento PDF.")
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(response.content)
        os.replace(temporary, target)
        return target
    return None


def seleccionar_registros_preferentes(df, pdf_dir="./pdfs"):
    """Agrupa duplicados por expediente prefiriendo la fuente que tenga pliego."""
    selected = {}
    for _, row in df.iterrows():
        key = clave_expediente(row)
        current = selected.get(key)
        reference = _texto_valido(row.get("PDF / Ruta"))
        has_local_pdf = bool(reference) and (
            Path(reference).is_file() or (Path(pdf_dir) / Path(reference).name).is_file()
        )
        if current is None:
            selected[key] = row
            continue
        current_reference = _texto_valido(current.get("PDF / Ruta"))
        current_has_local_pdf = bool(current_reference) and (
            Path(current_reference).is_file()
            or (Path(pdf_dir) / Path(current_reference).name).is_file()
        )
        if has_local_pdf and not current_has_local_pdf:
            selected[key] = row
    return selected


def construir_dossier(row, cache):
    lines = [f"# Dossier de licitación: {_texto_valido(row.get('Nº Expediente')) or 'Sin expediente'}", ""]
    fields = (
        "Nº Expediente", "Título", "Órgano de contratación", "Importe (€)",
        "Importe con IVA (€)", "Valor estimado contrato (€)",
        "Fecha Límite Presentación", "Código CPV", "Duración del contrato",
        "Clasificación", "URL",
    )
    lines.extend(["## Datos generales", ""])
    lines.extend(f"- **{field}:** {_texto_valido(row.get(field)) or 'No disponible'}" for field in fields)
    analyses = cache.get("analisis", {})
    for kind, (label, _) in ANALYSIS_TYPES.items():
        if analyses.get(kind):
            lines.extend(["", f"## {label}", "", analyses[kind]])
    if cache.get("chat"):
        lines.extend(["", "## Chat sobre el pliego", ""])
        for item in cache["chat"]:
            lines.extend([f"### Pregunta", item.get("pregunta", ""), "", "### Respuesta", item.get("respuesta", ""), ""])
    return "\n".join(lines)


def exportar_excel_enriquecido(df, cache_dir="./.gemini_cache/analisis"):
    enriched = df.copy()
    for kind in ANALYSIS_TYPES:
        enriched[f"Análisis Gemini - {kind}"] = [
            cargar_cache_analisis(row, cache_dir).get("analisis", {}).get(kind, "")
            for _, row in enriched.iterrows()
        ]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        enriched.to_excel(writer, index=False, sheet_name="Licitaciones")
    return output.getvalue()


def normalizar_importes(series):
    # Los scrapers históricos usan -1 para indicar importe desconocido.
    return pd.to_numeric(series, errors="coerce").fillna(0.0).clip(lower=0.0)


def formatear_importe_compacto(value):
    """Formatea para pantalla sin alterar el valor numérico usado por filtros."""
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric) or numeric < 0:
        return "-"
    if numeric >= 1_000_000:
        amount, suffix = numeric / 1_000_000, "M €"
        decimals = 2
    elif numeric >= 1_000:
        amount, suffix = numeric / 1_000, "K €"
        decimals = 1
    else:
        amount, suffix = numeric, "€"
        decimals = 2 if numeric % 1 else 0
    formatted = f"{amount:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} {suffix}"


def es_tecnologica(series):
    normalized = series.fillna("").astype(str).map(lambda value: unidecode(value).lower().strip())
    return ~normalized.isin(TECH_EXCLUDED)


def normalizar_estado(value, deadline, today=None):
    """Combina el estado del portal con la fecha límite para mostrar uno útil."""
    today = today or datetime.today().date()
    original = str(value).strip()
    normalized = unidecode(original).lower()
    terminal_states = (
        (("anulad", "cancelad"), "Anulada"),
        (("formaliz",), "Formalizada"),
        (("adjudic",), "Adjudicada"),
        (("desiert",), "Desierta"),
        (("desist",), "Desistida"),
        (("suspend",), "Suspendida"),
    )
    for terms, label in terminal_states:
        if any(term in normalized for term in terms):
            return label

    parsed_deadline = pd.to_datetime(deadline, errors="coerce")
    if not pd.isna(parsed_deadline):
        return "En plazo" if parsed_deadline.date() >= today else "Fuera de plazo"
    if any(term in normalized for term in ("abiert", "en plazo")):
        return "Abierta (plazo sin confirmar)"
    if "publicad" in normalized:
        return "Publicada (plazo sin confirmar)"
    if normalized in {"", "nan", "none", "notfound", "-1"}:
        return "Estado no disponible"
    return original


def aplicar_filtros(df, expediente="", palabras="", importe_min=None, importe_max=None,
                    fecha_desde=None, valor_estimado=None, tipos=None, fuentes=None,
                    estados=None, grupo_clasificacion="Todas", etiquetas=None):
    result = df.copy()
    if expediente.strip() and "Nº Expediente" in result:
        needle = unidecode(expediente).lower().strip()
        result = result[result["Nº Expediente"].fillna("").astype(str).map(
            lambda value: needle in unidecode(value).lower())]

    terms = [unidecode(term).lower().strip() for term in palabras.split(",") if term.strip()]
    if terms:
        searchable = result.astype(str).apply(lambda column: column.map(lambda value: unidecode(value).lower()))
        mask = pd.Series(False, index=result.index)
        for term in terms:
            mask |= searchable.apply(lambda column: column.str.contains(term, regex=False)).any(axis=1)
        result = result[mask]

    if "Importe (€)" in result:
        amounts = normalizar_importes(result["Importe (€)"])
        if importe_min is not None:
            result = result[amounts >= importe_min]
            amounts = amounts.loc[result.index]
        if importe_max is not None:
            result = result[amounts <= importe_max]
    if fecha_desde is not None and "Fecha Límite Presentación" in result:
        dates = pd.to_datetime(result["Fecha Límite Presentación"], errors="coerce")
        result = result[dates.dt.date >= fecha_desde]
    if valor_estimado is not None and "Valor estimado contrato (€)" in result:
        values = normalizar_importes(result["Valor estimado contrato (€)"])
        result = result[(values >= valor_estimado[0]) & (values <= valor_estimado[1])]
    if tipos and "Tipo de contrato" in result:
        result = result[result["Tipo de contrato"].isin(tipos)]
    if fuentes and "Fuente" in result:
        result = result[result["Fuente"].isin(fuentes)]
    if estados and "Estado" in result:
        result = result[result["Estado"].isin(estados)]
    if "Clasificación" in result:
        tech_mask = es_tecnologica(result["Clasificación"])
        if grupo_clasificacion == "Tecnológicas":
            result = result[tech_mask]
        elif grupo_clasificacion == "No tecnológicas":
            result = result[~tech_mask]
        if etiquetas:
            result = result[result["Clasificación"].isin(etiquetas)]
    return result


def buscar_actualizaciones_favs(favoritos_df):
    from web_scraping.WS_licitaciones_favs import ScraperLicFav
    date_column = "Fecha de ejecución del proceso"
    if date_column not in favoritos_df:
        st.warning(f"No se encontró '{date_column}' en las filas favoritas.")
        return None
    scraper = ScraperLicFav(
        df=favoritos_df,
        fecha_ultima_eje=pd.to_datetime(favoritos_df[date_column], errors="coerce").max(),
        fecha=datetime.today().date(), url_col="URL", fuente_col="Fuente",
        config_file="./config/scraper_config.ini",
    )
    return scraper.ejecutar()


def aplicar_estilos():
    st.markdown("""
        <style>
        :root { --navy:#172554; --indigo:#4338ca; --cyan:#0891b2;
          --coral:#f97360; --mist:#f4f7ff; --line:#dfe5f2; }
        .stApp { background:
          radial-gradient(circle at 92% 3%, rgba(8,145,178,.12), transparent 24rem),
          radial-gradient(circle at 44% -8%, rgba(99,102,241,.10), transparent 28rem),
          #f8faff; color:#18243f; }
        [data-testid="stHeader"] { background:transparent; }
        .block-container { padding-top:2rem; padding-bottom:3rem; max-width:1500px; }
        h1 { color:var(--navy); font-weight:780 !important; letter-spacing:-.035em !important; }
        h2, h3 { color:#24386b; letter-spacing:-.018em; }
        [data-testid="stSidebar"] {
          background:linear-gradient(180deg,#eef2ff 0%,#f5f3ff 50%,#ecfeff 100%);
          border-right:1px solid #d9def0; box-shadow:8px 0 28px rgba(30,41,89,.06); }
        [data-testid="stSidebar"] h2 { color:var(--navy); font-weight:750; }
        [data-baseweb="input"], [data-baseweb="select"] > div,
        [data-testid="stNumberInput"] input, [data-testid="stDateInput"] input {
          border-radius:11px !important; border-color:#cbd5ea !important;
          background:rgba(255,255,255,.92) !important; }
        [data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within {
          border-color:var(--cyan) !important; box-shadow:0 0 0 3px rgba(8,145,178,.12) !important; }
        [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:16px;
          overflow:hidden; box-shadow:0 12px 35px rgba(30,41,89,.08); background:white; }
        .stButton > button, .stDownloadButton > button, [data-testid="stPopover"] > button {
          border-radius:11px !important; border:1px solid #c8d1e5 !important;
          font-weight:650 !important; transition:all .18s ease !important; }
        .stButton > button:hover, .stDownloadButton > button:hover,
        [data-testid="stPopover"] > button:hover {
          border-color:var(--indigo) !important; color:var(--indigo) !important;
          transform:translateY(-1px); box-shadow:0 7px 18px rgba(67,56,202,.13); }
        [data-testid="stDownloadButton"] button[kind="secondary"] {
          background:linear-gradient(135deg,var(--indigo),#6366f1) !important;
          color:white !important; border:none !important; }
        .run-banner { display:inline-flex; align-items:center; gap:.45rem;
          padding:.65rem .95rem; border-radius:999px; color:#253268;
          background:rgba(238,242,255,.92); border:1px solid #cdd5fa;
          box-shadow:0 5px 16px rgba(67,56,202,.08); margin:.5rem 0 .7rem 0; }
        .run-banner span { color:var(--cyan); font-weight:700; }
        .tender-count { color:var(--navy); font-size:1.18rem; font-weight:720;
          margin:0 0 1.1rem 0; }
        .tender-count::before { content:'◆'; color:var(--coral); font-size:.75rem; margin-right:.5rem; }
        .eyebrow { color:var(--cyan); font-size:.76rem; font-weight:800;
          letter-spacing:.13em; margin-bottom:.2rem; }
        .tender-card { margin:.5rem 0 1rem; padding:1.35rem 1.45rem;
          border:1px solid var(--line); border-radius:16px; background:white;
          box-shadow:0 12px 35px rgba(30,41,89,.08); }
        .tender-card__title { color:var(--navy); font-size:clamp(1.2rem,2vw,1.65rem);
          line-height:1.25; font-weight:780; letter-spacing:-.025em;
          overflow-wrap:anywhere; margin:0 0 .65rem; }
        .tender-card__exp { color:#34446b; font-size:.98rem; line-height:1.45;
          overflow-wrap:anywhere; word-break:break-word; white-space:normal; margin-bottom:1rem; }
        .tender-card__exp strong { color:var(--navy); }
        .tender-card__operational { display:flex; flex-wrap:wrap; gap:.65rem;
          align-items:stretch; margin-bottom:1rem; }
        .tender-card__highlight { flex:1 1 15rem; padding:.75rem .9rem;
          border-radius:11px; background:#f5f7ff; border:1px solid #dfe4f5; }
        .tender-card__label { display:block; color:#69758e; font-size:.72rem;
          font-weight:800; letter-spacing:.07em; text-transform:uppercase; margin-bottom:.2rem; }
        .tender-card__value { color:#24345f; font-size:1rem; font-weight:720;
          overflow-wrap:anywhere; }
        .tender-card__status { display:inline-flex; align-items:center; width:fit-content;
          padding:.28rem .65rem; border-radius:999px; color:#14532d;
          background:#dcfce7; border:1px solid #bbf7d0; font-weight:760; }
        .tender-card__status--closed { color:#7f1d1d; background:#fee2e2; border-color:#fecaca; }
        .tender-card__status--neutral { color:#374151; background:#f3f4f6; border-color:#e5e7eb; }
        .tender-card__details { display:grid;
          grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr));
          gap:.85rem 1.25rem; padding-top:.95rem; border-top:1px solid #e8ecf5; }
        .tender-card__detail { min-width:0; }
        .tender-card__detail--wide { grid-column:1 / -1; }
        [data-testid="stCaptionContainer"] { color:#66728d; }
        hr { border-color:#dce3f1 !important; }
        </style>
    """, unsafe_allow_html=True)


def construir_ficha_html(row):
    """Construye la tarjeta resumen sin componentes que trunquen el expediente."""
    clean = lambda value, fallback="No disponible": html.escape(
        _texto_valido(value) or fallback
    )
    title = clean(row.get("Título"), "Licitación sin título")
    expediente = clean(row.get("Nº Expediente"), "Sin expediente")
    deadline = clean(row.get("Fecha Límite Presentación"))
    status_raw = _texto_valido(row.get("Estado")) or "No disponible"
    status = html.escape(status_raw)
    normalized_status = unidecode(status_raw).lower()
    if any(term in normalized_status for term in ("fuera", "anulad", "cancelad", "desist", "desiert")):
        status_class = "tender-card__status--closed"
    elif any(term in normalized_status for term in ("en plazo", "abiert", "publicad")):
        status_class = ""
    else:
        status_class = "tender-card__status--neutral"
    without_tax = html.escape(formatear_importe_compacto(row.get("Importe (€)")))
    with_tax = html.escape(formatear_importe_compacto(row.get("Importe con IVA (€)")))
    estimated = html.escape(formatear_importe_compacto(row.get("Valor estimado contrato (€)")))
    authority = clean(row.get("Órgano de contratación"))
    return f"""
    <section class="tender-card" aria-label="Ficha técnica de la licitación">
      <div class="tender-card__title">{title}</div>
      <div class="tender-card__exp"><strong>Expediente:</strong> {expediente}</div>
      <div class="tender-card__operational">
        <div class="tender-card__highlight">
          <span class="tender-card__label">Estado</span>
          <span class="tender-card__status {status_class}">{status}</span>
        </div>
        <div class="tender-card__highlight">
          <span class="tender-card__label">Fecha límite de presentación</span>
          <span class="tender-card__value">{deadline}</span>
        </div>
      </div>
      <div class="tender-card__details">
        <div class="tender-card__detail">
          <span class="tender-card__label">Importe sin IVA</span>
          <span class="tender-card__value">{without_tax}</span>
        </div>
        <div class="tender-card__detail">
          <span class="tender-card__label">Importe con IVA</span>
          <span class="tender-card__value">{with_tax}</span>
        </div>
        <div class="tender-card__detail">
          <span class="tender-card__label">Valor estimado</span>
          <span class="tender-card__value">{estimated}</span>
        </div>
        <div class="tender-card__detail tender-card__detail--wide">
          <span class="tender-card__label">Órgano de contratación</span>
          <span class="tender-card__value">{authority}</span>
        </div>
      </div>
    </section>
    """


def mostrar_panel_licitacion(row, df_completo, output_dir):
    expediente = _texto_valido(row.get("Nº Expediente")) or "sin-expediente"
    cache = cargar_cache_analisis(row)
    cache.setdefault("expediente", expediente)
    cache.setdefault("analisis", {})
    cache.setdefault("chat", [])
    for kind, value in cache["analisis"].items():
        st.session_state.setdefault(f"analisis_{expediente}_{kind}", value)
    st.session_state.setdefault(f"chat_{expediente}", cache["chat"])

    st.divider()
    st.subheader("📌 Ficha Técnica de la Licitación")
    st.markdown(construir_ficha_html(row), unsafe_allow_html=True)
    if _texto_valido(row.get("URL")):
        st.link_button("Abrir licitación oficial ↗", row["URL"])

    try:
        pdf_path = localizar_pdf(row)
        pdf_error = None
    except (OSError, requests.RequestException, ValueError) as exc:
        pdf_path, pdf_error = None, str(exc)
    if pdf_path:
        st.success(f"Pliego localizado: {pdf_path.name}")
        try:
            pdf_text = extraer_paginas_clave_pdf(
                pdf_path, max_caracteres=MAX_PDF_CONTEXT_CHARS
            )
        except (OSError, RuntimeError, ValueError) as exc:
            pdf_text, pdf_error = "", str(exc)
    else:
        pdf_text = ""
    if pdf_error:
        st.warning(f"No se pudo preparar el pliego: {pdf_error}")
    elif not pdf_path:
        st.warning("No hay un PDF local o una URL PDF asociada a esta licitación.")

    st.markdown("### Información específica del pliego")
    st.caption("Genera solo los apartados que necesites; cada consulta se guarda para no repetir llamadas.")
    button_cols = st.columns(2)
    for position, (kind, (label, instruction)) in enumerate(ANALYSIS_TYPES.items()):
        session_key = f"analisis_{expediente}_{kind}"
        with button_cols[position % 2]:
            if st.button(label, key=f"btn_{clave_expediente(row)}_{kind}",
                         disabled=not bool(pdf_text), use_container_width=True):
                if session_key not in st.session_state:
                    with st.spinner("Analizando las páginas relevantes del pliego..."):
                        try:
                            processor = LicitacionGeminiProcessor(pd.DataFrame(), usar_gemini=True)
                            answer = processor.consultar_pliego(pdf_text, instruction)
                            st.session_state[session_key] = answer
                            cache["analisis"][kind] = answer
                            guardar_cache_analisis(row, cache)
                        except RuntimeError as exc:
                            st.error(str(exc))
            answer = st.session_state.get(session_key)
            if answer:
                with st.expander(f"Resultado: {label}", expanded=True):
                    st.markdown(answer)

    st.markdown("### 💬 Chat interactivo sobre el pliego")
    chat_key = f"chat_{expediente}"
    for message in st.session_state[chat_key]:
        with st.chat_message("user"):
            st.markdown(message.get("pregunta", ""))
        with st.chat_message("assistant"):
            st.markdown(message.get("respuesta", ""))
    question = st.chat_input(
        "Haz una pregunta específica sobre este pliego...",
        key=f"chat_input_{clave_expediente(row)}",
        disabled=not bool(pdf_text),
    )
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Consultando el pliego..."):
                try:
                    processor = LicitacionGeminiProcessor(pd.DataFrame(), usar_gemini=True)
                    response = processor.consultar_pliego(
                        pdf_text, f"Responde de forma concreta a esta pregunta: {question}"
                    )
                    st.markdown(response)
                    item = {"pregunta": question, "respuesta": response}
                    st.session_state[chat_key].append(item)
                    cache["chat"] = st.session_state[chat_key]
                    guardar_cache_analisis(row, cache)
                except RuntimeError as exc:
                    st.error(str(exc))

    # Reconstruir desde sesión para que la descarga incluya resultados recién creados.
    cache["analisis"] = {
        kind: st.session_state[f"analisis_{expediente}_{kind}"]
        for kind in ANALYSIS_TYPES if f"analisis_{expediente}_{kind}" in st.session_state
    }
    cache["chat"] = st.session_state[chat_key]
    dossier = construir_dossier(row, cache)
    safe_exp = re.sub(r"[^\w.-]+", "_", expediente, flags=re.UNICODE).strip("_") or "licitacion"
    export_cols = st.columns(2)
    export_cols[0].download_button(
        "📥 Descargar dossier completo (Markdown)", dossier.encode("utf-8"),
        f"dossier_{safe_exp}.md", "text/markdown", use_container_width=True,
    )
    try:
        excel = exportar_excel_enriquecido(df_completo)
        export_cols[1].download_button(
            "📊 Exportar tabla enriquecida (Excel)", excel,
            "licitaciones_enriquecidas.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    except (ImportError, OSError, ValueError) as exc:
        export_cols[1].warning(f"Exportación Excel no disponible: {exc}")


def main():
    st.set_page_config(page_title="Buscador de Licitaciones", page_icon="🔎",
                       layout="wide", initial_sidebar_state="expanded")
    aplicar_estilos()
    st.markdown('<div class="eyebrow">CONTRATACIÓN PÚBLICA · ESPAÑA</div>', unsafe_allow_html=True)
    st.title("Buscador de Licitaciones")
    st.caption("Búsqueda, clasificación tecnológica y seguimiento de oportunidades públicas.")

    output_dir = cargar_config()
    rename_dict = cargar_columns_ini()
    raw_df, csv_path = cargar_datos(output_dir)
    if raw_df is None or raw_df.empty:
        st.warning(f"No hay datos disponibles en {csv_path}.")
        st.stop()
    available = [column for column in rename_dict if column in raw_df.columns]
    df = raw_df[available].rename(columns=rename_dict)
    monetary_columns = ("Importe (€)", "Importe con IVA (€)", "Valor estimado contrato (€)")
    for numeric in monetary_columns:
        if numeric in df:
            df[numeric] = pd.to_numeric(df[numeric], errors="coerce")
    if "Estado" in df:
        deadlines = df.get("Fecha Límite Presentación", pd.Series(None, index=df.index))
        df["Estado"] = [
            normalizar_estado(state, deadline)
            for state, deadline in zip(df["Estado"], deadlines)
        ]

    run_dates = pd.to_datetime(df.get("Fecha de ejecución del proceso", pd.Series(dtype=str)),
                               errors="coerce", utc=True).dropna()
    run_text = (run_dates.max().tz_convert("Europe/Madrid").strftime("%d/%m/%Y · %H:%M:%S")
                if not run_dates.empty else "No disponible")
    st.markdown(f'<div class="run-banner"><strong>Fecha de ejecución del scraping:</strong><span>{run_text}</span></div>',
                unsafe_allow_html=True)
    st.markdown(f'<div class="tender-count">{len(df):,} licitaciones encontradas</div>',
                unsafe_allow_html=True)
    palabras = st.text_input(
        "Buscar por palabra clave",
        placeholder="Ej. software, datos, mantenimiento",
        help="Puedes introducir varias palabras separadas por comas.",
    )

    analytics = cargar_analytics()

    st.sidebar.header("Filtros")
    expediente = st.sidebar.text_input("Número de expediente", placeholder="Ej. 2026/123")
    estados = st.sidebar.multiselect(
        "Estado de la licitación",
        sorted(df.get("Estado", pd.Series(dtype=str)).dropna().astype(str).unique()),
    )
    amount_data = normalizar_importes(df.get("Importe (€)", pd.Series(0, index=df.index)))
    a_min, a_max = float(amount_data.min()), float(amount_data.max())
    amount_cols = st.sidebar.columns(2)
    importe_min = amount_cols[0].number_input("Importe mín.", min_value=0.0, value=a_min, step=1000.0)
    importe_max = amount_cols[1].number_input("Importe máx.", min_value=0.0, value=a_max, step=1000.0)
    valid_dates = pd.to_datetime(df.get("Fecha Límite Presentación"), errors="coerce").dropna()
    default_date = valid_dates.min().date() if not valid_dates.empty else datetime.today().date()
    fecha_desde = st.sidebar.date_input("Fecha límite igual o posterior a", value=default_date)
    estimated = normalizar_importes(df.get("Valor estimado contrato (€)", pd.Series(0, index=df.index)))
    e_min, e_max = float(estimated.min()), float(estimated.max())
    if e_max > e_min:
        valor_estimado = st.sidebar.slider("Valor estimado del contrato (€)", min_value=e_min,
            max_value=e_max, value=(e_min, e_max), step=max((e_max - e_min) / 200, 1.0), format="%.0f €")
    else:
        st.sidebar.caption(f"Valor estimado único: {e_min:,.0f} €")
        valor_estimado = (e_min, e_max)
    tipos = st.sidebar.multiselect("Tipo de contrato",
        sorted(df.get("Tipo de contrato", pd.Series(dtype=str)).dropna().astype(str).unique()))
    fuentes = st.sidebar.multiselect("Fuente",
        sorted(df.get("Fuente", pd.Series(dtype=str)).dropna().astype(str).unique()))
    grupo = st.sidebar.radio("Clasificación principal", ["Todas", "Tecnológicas", "No tecnológicas"])
    tech_categories = (sorted(df.loc[es_tecnologica(df["Clasificación"]), "Clasificación"]
                              .dropna().astype(str).unique()) if "Clasificación" in df else [])
    etiquetas = st.sidebar.multiselect("Etiquetas tecnológicas", tech_categories,
        disabled=grupo == "No tecnológicas",
        help="Opcional. Permite concretar una o varias categorías tecnológicas.")

    filtered = aplicar_filtros(
        df, expediente=expediente, palabras=palabras,
        importe_min=importe_min, importe_max=importe_max,
        fecha_desde=fecha_desde, valor_estimado=valor_estimado,
        tipos=tipos, fuentes=fuentes, estados=estados,
        grupo_clasificacion=grupo, etiquetas=etiquetas,
    )
    st.subheader("Licitaciones")
    st.caption(f"{len(filtered):,} resultados de {len(df):,}")
    option_rows = seleccionar_registros_preferentes(filtered)
    option_keys = list(option_rows)
    current_key = st.session_state.get("licitacion_seleccionada")
    selector_index = option_keys.index(current_key) + 1 if current_key in option_keys else 0

    def format_option(key):
        if key is None:
            return "Busca o selecciona una licitación..."
        item = option_rows[key]
        return " - ".join((
            _texto_valido(item.get("Nº Expediente")) or "Sin expediente",
            _texto_valido(item.get("Título")) or "Sin título",
            formatear_importe_compacto(item.get("Importe (€)")),
        ))

    selected_from_search = st.selectbox(
        "Seleccionar licitación por expediente o título",
        [None] + option_keys,
        index=selector_index,
        format_func=format_option,
        key="selector_licitacion",
    )
    if selected_from_search is not None:
        st.session_state["licitacion_seleccionada"] = selected_from_search
    display = filtered.copy()
    for monetary in monetary_columns:
        if monetary in display:
            display[monetary] = display[monetary].map(formatear_importe_compacto)
    status_icons = {"en plazo":"🟢", "adjudicada":"🔵", "formalizada":"🔵",
                    "anulada":"🔴", "cancelada":"🔴", "desistida":"🔴",
                    "suspendida":"🟠", "desierta":"⚫", "fuera de plazo":"⚫",
                    "cerrada":"⚫"}
    if "Estado" in display:
        display["Estado"] = display["Estado"].map(lambda value:
            f"{next((icon for key, icon in status_icons.items() if key in str(value).lower()), '🟡')} {value}")
    table_event = st.dataframe(display, column_config={
        "URL": st.column_config.LinkColumn("Licitación", display_text="Abrir ficha ↗"),
        "Título": st.column_config.TextColumn("Título", width="large"),
        "Resumen breve": st.column_config.TextColumn("Resumen breve", width="large"),
        "PDF / Ruta": st.column_config.TextColumn("Nombre del PDF", width="medium"),
        "Importe (€)": st.column_config.TextColumn("Importe (€)"),
        "Importe con IVA (€)": st.column_config.TextColumn("Importe con IVA (€)"),
        "Valor estimado contrato (€)": st.column_config.TextColumn("Valor estimado (€)"),
    }, hide_index=True, width="stretch", height=600,
       on_select="rerun", selection_mode="single-row", key="tabla_licitaciones")
    selected_positions = table_event.selection.rows
    if selected_positions:
        selected_row = filtered.iloc[selected_positions[0]]
        st.session_state["licitacion_seleccionada"] = clave_expediente(selected_row)

    download_left, download_right = st.columns(2)
    download_left.download_button("Descargar resultados", filtered.to_csv(index=False).encode("utf-8-sig"),
        "licitaciones_filtradas.csv", "text/csv", use_container_width=True)
    with download_right.popover("Gestionar favoritas", use_container_width=True):
        favorite_text = st.text_input("Expedientes favoritos, separados por comas")
        favorites = [value.strip() for value in favorite_text.split(",") if value.strip()]
        favorite_df = df[df["Nº Expediente"].astype(str).isin(favorites)] if favorites else df.iloc[0:0]
        st.download_button("Descargar favoritas", favorite_df.to_csv(index=False).encode("utf-8-sig"),
            "licitaciones_favoritas.csv", "text/csv", disabled=favorite_df.empty)
        if st.button("Buscar actualizaciones", disabled=favorite_df.empty):
            with st.spinner("Consultando los portales..."):
                try:
                    updates = buscar_actualizaciones_favs(favorite_df)
                    if updates is not None:
                        st.dataframe(updates, hide_index=True, width="stretch")
                except Exception as exc:
                    st.error(f"No se pudieron buscar actualizaciones: {exc}")

    selected_key = st.session_state.get("licitacion_seleccionada")
    if selected_key in option_rows:
        mostrar_panel_licitacion(option_rows[selected_key], df, output_dir)

    if not analytics.empty:
        with st.expander("Histórico local de ejecuciones"):
            labels = {"fecha_hora_inicio":"Inicio", "duracion_segundos":"Duración (s)",
                "total_licitaciones":"Licitaciones", "pdf_descargados":"Con PDF",
            }
            columns = [column for column in labels if column in analytics]
            st.dataframe(analytics[columns].rename(columns=labels).iloc[::-1],
                         hide_index=True, width="stretch")
    st.divider()
    st.caption("Fuentes: Plataforma de Contratación del Sector Público, Junta de Andalucía, "
               "Comunidad de Madrid y Contratación Pública de Euskadi. Verifica siempre la ficha oficial.")


if __name__ == "__main__":
    main()
