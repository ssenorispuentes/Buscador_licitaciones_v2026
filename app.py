import configparser
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from unidecode import unidecode

import src.functions as functions


TECH_EXCLUDED = {"no tecnológica", "no tecnologica", "no clasificada", "notfound", ""}


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


def normalizar_importes(series):
    # Los scrapers históricos usan -1 para indicar importe desconocido.
    return pd.to_numeric(series, errors="coerce").fillna(0.0).clip(lower=0.0)


def es_tecnologica(series):
    normalized = series.fillna("").astype(str).map(lambda value: unidecode(value).lower().strip())
    return ~normalized.isin(TECH_EXCLUDED)


def aplicar_filtros(df, expediente="", palabras="", importe_min=None, importe_max=None,
                    fecha_desde=None, valor_estimado=None, tipos=None, fuentes=None,
                    grupo_clasificacion="Todas", etiquetas=None):
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
        .stApp { background: #f6f8fb; }
        .block-container { padding-top: 1.5rem; padding-bottom: 3rem; }
        [data-testid="stMetric"] { background:white; border:1px solid #e4e9f0;
          border-radius:14px; padding:1rem 1.15rem; box-shadow:0 4px 18px rgba(22,34,51,.05); }
        [data-testid="stSidebar"] { background:#fff; border-right:1px solid #e4e9f0; }
        .run-banner { display:flex; justify-content:space-between; align-items:center;
          padding:.85rem 1rem; border-radius:12px; color:#14324a;
          background:linear-gradient(90deg,#e8f4ff,#effaf6); border:1px solid #cfe2ef;
          margin:.5rem 0 1.2rem 0; }
        .eyebrow { color:#44708f; font-size:.78rem; font-weight:700; letter-spacing:.08em; }
        </style>
    """, unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Observatorio de Licitaciones", page_icon="📊",
                       layout="wide", initial_sidebar_state="expanded")
    aplicar_estilos()
    st.markdown('<div class="eyebrow">CONTRATACIÓN PÚBLICA · ESPAÑA</div>', unsafe_allow_html=True)
    st.title("Observatorio de licitaciones")
    st.caption("Búsqueda, clasificación tecnológica y seguimiento de oportunidades públicas.")

    output_dir = cargar_config()
    rename_dict = cargar_columns_ini()
    raw_df, csv_path = cargar_datos(output_dir)
    if raw_df is None or raw_df.empty:
        st.warning(f"No hay datos disponibles en {csv_path}.")
        st.stop()
    available = [column for column in rename_dict if column in raw_df.columns]
    df = raw_df[available].rename(columns=rename_dict)
    for numeric in ("Importe (€)", "Valor estimado contrato (€)"):
        if numeric in df:
            df[numeric] = pd.to_numeric(df[numeric], errors="coerce")

    run_dates = pd.to_datetime(df.get("Fecha de ejecución del proceso", pd.Series(dtype=str)),
                               errors="coerce", utc=True).dropna()
    run_text = (run_dates.max().tz_convert("Europe/Madrid").strftime("%d/%m/%Y · %H:%M:%S")
                if not run_dates.empty else "No disponible")
    st.markdown(f'<div class="run-banner"><strong>Último scraping</strong><span>🕒 {run_text}</span></div>',
                unsafe_allow_html=True)

    analytics = cargar_analytics()
    latest = analytics.iloc[-1] if not analytics.empty else None
    pdf_values = df.get("PDF / Ruta", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
    pdf_present = ~pdf_values.isin({"", "nan", "none", "notfound", "no disponible"})
    metric_cols = st.columns(4)
    metric_cols[0].metric("Licitaciones", int(latest["total_licitaciones"]) if latest is not None else len(df))
    metric_cols[1].metric("Con PDF", int(latest["pdf_descargados"]) if latest is not None else int(pdf_present.sum()))
    metric_cols[2].metric("Gemini requeridas", int(latest["gemini_requeridas"]) if latest is not None else "Sin métrica local")
    metric_cols[3].metric("Gemini completadas", int(latest["gemini_analizadas"]) if latest is not None else "Sin métrica local")

    st.sidebar.header("Filtros")
    expediente = st.sidebar.text_input("Número de expediente", placeholder="Ej. 2026/123")
    palabras = st.sidebar.text_input("Palabra clave", placeholder="software, datos, mantenimiento")
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

    filtered = aplicar_filtros(df, expediente, palabras, importe_min, importe_max,
        fecha_desde, valor_estimado, tipos, fuentes, grupo, etiquetas)
    st.subheader("Licitaciones")
    st.caption(f"{len(filtered):,} resultados de {len(df):,}")
    display = filtered.copy()
    status_icons = {"abierta":"🟢", "en plazo":"🟢", "publicada":"🟢",
                    "adjudicada":"🔵", "anulada":"🔴", "cancelada":"🔴", "cerrada":"⚫"}
    if "Estado" in display:
        display["Estado"] = display["Estado"].map(lambda value:
            f"{next((icon for key, icon in status_icons.items() if key in str(value).lower()), '🟡')} {value}")
    st.dataframe(display, column_config={
        "URL": st.column_config.LinkColumn("Licitación", display_text="Abrir ficha ↗"),
        "Título": st.column_config.TextColumn("Título", width="large"),
        "Resumen breve": st.column_config.TextColumn("Resumen breve", width="large"),
        "PDF / Ruta": st.column_config.TextColumn("Nombre del PDF", width="medium"),
        "Importe (€)": st.column_config.NumberColumn("Importe (€)", format="%.2f €"),
        "Valor estimado contrato (€)": st.column_config.NumberColumn("Valor estimado (€)", format="%.2f €"),
    }, hide_index=True, width="stretch", height=600)

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

    if not analytics.empty:
        with st.expander("Histórico local de ejecuciones"):
            labels = {"fecha_hora_inicio":"Inicio", "duracion_segundos":"Duración (s)",
                "total_licitaciones":"Licitaciones", "pdf_descargados":"Con PDF",
                "gemini_requeridas":"Gemini requeridas", "gemini_analizadas":"Gemini completadas",
                "gemini_cache_reutilizadas":"Respuestas desde caché",
                "gemini_api_solicitudes":"Peticiones nuevas a Gemini",
                "pdf_analizados_gemini":"PDF leídos por Gemini"}
            columns = [column for column in labels if column in analytics]
            st.dataframe(analytics[columns].rename(columns=labels).iloc[::-1],
                         hide_index=True, width="stretch")
    st.divider()
    st.caption("Fuentes: Plataforma de Contratación del Sector Público, Junta de Andalucía, "
               "Comunidad de Madrid y Contratación Pública de Euskadi. Verifica siempre la ficha oficial.")


if __name__ == "__main__":
    main()
