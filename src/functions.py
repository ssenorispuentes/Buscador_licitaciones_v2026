
import pandas as pd 
import re 
import os
import unicodedata
import hashlib
from datetime import datetime
from unidecode import unidecode


FINAL_OUTPUT_COLUMNS = [
    "estado_licitacion",
    "fecha_limite_presentacion",
    "resumen_breve",
    "importe_licitacion",
    "valor_estimado_contrato",
    "titulo",
    "numero_expediente",
    "organo_contratacion",
    "tipo_contrato",
    "lugar_ejecucion",
    "duracion_contrato",
    "financiacion_ue",
    "forma_presentacion",
    "clasificacion",
    "fuente",
    "pdf",
    "fecha_proceso",
]

def get_columns_dict(section):
    """
    Convierte una sección de configparser en un dict {clave: int(valor)}
    """
    return {k: int(v) for k, v in section.items()}


# Función limpieza

def limpiar_importe(valor):
    if pd.isna(valor):
        return valor
    
    valor = str(valor)
    valor = re.sub(r"(euros|€)", "", valor, flags=re.IGNORECASE).strip()

    # Si el número usa coma como decimal -> ej: 1.234,56
    if re.search(r"\d+\.\d+,\d+", valor) or re.search(r"\d+,\d{2}$", valor):
        # Eliminar los puntos de miles
        valor = valor.replace('.', '')
        # Reemplazar la coma decimal por punto
        valor = valor.replace(',', '.')
    else:
        # Eliminar espacios extra, no tocar el punto decimal válido
        valor = valor.replace(',', '')
    
    try:
        return float(valor)
    except:
        return valor


def parsear_fechas_inteligente(columna, fecha_fallback="2100-12-31"):
    """
    Intenta parsear fechas en español traduciendo meses y aplicando varios formatos.
    """
    meses = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }

    def normalizar_fecha(valor):
        if pd.isna(valor):
            return pd.to_datetime(fecha_fallback).date()

        valor = str(valor).strip().lower()
        valor = re.sub(r'\s+', ' ', valor)

        # Detectar formato "26 de junio del 2025 23:59"
        match = re.match(r'(\d{1,2}) de (\w+) del (\d{4}) ?(\d{2}:\d{2})?', valor)
        if match:
            dia, mes, anio, hora = match.groups()
            mes_num = meses.get(mes, "01")
            hora = hora if hora else "00:00"
            fecha_str = f"{anio}-{mes_num}-{int(dia):02d} {hora}"
            try:
                return pd.to_datetime(fecha_str).date()
            except:
                return pd.to_datetime(fecha_fallback).date()

        # Intentar otros formatos
        formatos = [
            "%d/%m/%Y",
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%d %H:%M:%S",
        ]
        for fmt in formatos:
            try:
                return pd.to_datetime(valor, format=fmt, dayfirst=True).date()
            except:
                continue

        # Intento final con mixed
        try:
            return pd.to_datetime(valor, format="mixed", dayfirst=True).date()
        except:
            return pd.to_datetime(fecha_fallback).date()

    return columna.apply(normalizar_fecha)

def combinar_duplicados_por_expediente(df, col_exp):
    """
    Elimina duplicados por Nº Expediente combinando datos de varias fuentes:
    - Para columnas comunes: se queda con el valor no nulo (si hay varios, el primero).
    - Para 'fuente', 'URL' y 'pdf': se concatenan separados por coma, eliminando duplicados.
    """
    def combinar_grupo(grupo):
        combinado = {}
        for col in grupo.columns:
            if col in ['fuente', 'enlace', 'pdf']:
                # Combina valores únicos no nulos separados por coma
                valores_unicos = grupo[col].dropna().astype(str).unique()
                combinado[col] = ", ".join(valores_unicos)
            else:
                # Se queda con el primer valor no nulo si hay
                primer_valor = grupo[col].dropna()
                combinado[col] = primer_valor.iloc[0] if not primer_valor.empty else None
        return pd.Series(combinado)

    if col_exp not in df.columns:
        raise ValueError(f"El DataFrame debe contener la columna {col_exp}.")

    df_sin_duplicados = df.groupby(col_exp, as_index=False).apply(combinar_grupo).reset_index(drop=True)
    return df_sin_duplicados


def extraer_localizacion_final(lugar, fuente, df_nuts):
    """
    Extrae y normaliza la localización (provincia o comunidad autónoma) a partir del campo 'Lugar de ejecución'.

    Devuelve un diccionario con:
    - 'ubicacion': texto principal (provincia, localidad, etc.)
    - 'comunidad_autonoma': comunidad si se puede inferir, o 'fuente' como fallback
    """
    def normalizar(texto):
        if not isinstance(texto, str):
            return "NotFound"
        return unidecode(texto).strip().lower().title()

    ubicacion = None
    comunidad = None

    if not isinstance(lugar, str) or lugar.strip().lower() == "notfound":
        fuente_val = normalizar(fuente) if isinstance(fuente, str) else "NotFound"
        return {"ubicacion": fuente_val, "comunidad_autonoma": fuente_val}

    # Buscar código NUTS (ej. ES61 o ES616)
    match = re.search(r'ES\d{2,3}', lugar)
    if match:
        codigo_nuts = match.group(0)[2:]  # quitar 'ES'

        # Buscar provincia (nut_3)
        mask_nut3 = df_nuts['nut_3'].notna()
        nut3_convertida = df_nuts.loc[mask_nut3, 'nut_3'].astype(int).astype(str)
        mask_codigo_3 = pd.Series(False, index=df_nuts.index)
        mask_codigo_3.loc[mask_nut3] = nut3_convertida == codigo_nuts

        prov = df_nuts[mask_codigo_3]
        if not prov.empty:
            ubicacion = prov.iloc[0]['provincia']
            comunidad = prov.iloc[0]['comunidad_autonoma']
            return {
                "ubicacion": normalizar(ubicacion),
                "comunidad_autonoma": normalizar(comunidad if isinstance(comunidad, str) else fuente)
            }

        # Buscar comunidad (nut_2)
        mask_nut2 = df_nuts['nut_2'].notna()
        nut2_convertida = df_nuts.loc[mask_nut2, 'nut_2'].astype(int).astype(str)
        mask_codigo_2 = pd.Series(False, index=df_nuts.index)
        mask_codigo_2.loc[mask_nut2] = nut2_convertida == codigo_nuts

        ccaa = df_nuts[mask_codigo_2]
        if not ccaa.empty:
            comunidad = ccaa.iloc[0]['comunidad_autonoma']
            return {
                "ubicacion": normalizar(comunidad),
                "comunidad_autonoma": normalizar(comunidad if isinstance(comunidad, str) else fuente)
            }

    # Si no hay código NUTS, aplicar limpieza del texto
    partes = [p.strip() for p in lugar.split(" - ") if p.strip()]
    if len(partes) == 3:
        _, provincia, localidad = partes
        if provincia.lower() == localidad.lower():
            ubicacion = localidad
        else:
            ubicacion = f"{provincia} ({localidad})"
        # Buscar comunidad asociada
        prov_match = df_nuts[df_nuts['provincia'].str.lower() == provincia.lower()]
        if not prov_match.empty:
            comunidad = prov_match.iloc[0]['comunidad_autonoma']
    elif len(partes) == 2:
        _, localidad = partes
        ubicacion = localidad
    elif len(partes) == 1:
        ubicacion = partes[0]
    else:
        fuente_val = normalizar(fuente) if isinstance(fuente, str) else "NotFound"
        return {"ubicacion": fuente_val, "comunidad_autonoma": fuente_val}

    comunidad_final = comunidad if isinstance(comunidad, str) else fuente
    return {
        "ubicacion": normalizar(ubicacion),
        "comunidad_autonoma": normalizar(comunidad_final)
    }

def filtrar_renombrar_dataframe(df, comunidad, filename_codigo_nuts, columnas_finales, columnas_iniciales_comunidad, fecha_proceso):
    """
    Filtra y transforma un DataFrame de licitaciones según la comunidad autónoma.

    - Renombra y ordena columnas según mapeos definidos.
    - Limpia fechas e importes.
    - Añade columnas 'fuente' y 'fecha_proceso'.
    - Normaliza 'lugar_ejecucion' usando códigos NUTS si el archivo existe.

    Returns:
        DataFrame transformado y listo para análisis o exportación.
    """

    # Invertir columnas_finales para buscar por índice
    index_to_final_name = {v: k for k, v in columnas_finales.items()}
    map_comunidad = {'and':'Andalucía','esp':'España','eus':'Euskadi','mad':'Comunidad de Madrid'}
    rename_dict = {}
    for col_real, idx in columnas_iniciales_comunidad.items():
        if idx in index_to_final_name:
            col_final = index_to_final_name[idx]
            rename_dict[col_real] = col_final

    # Filtrar y renombrar
    columnas_a_usar = [col for col in rename_dict if col in df.columns]
    df_filtrado = df[columnas_a_usar].rename(columns=rename_dict)
    
    # Ordenar según columnas_finales
    final_order = list(columnas_finales.keys())
    # Depuración opcional
    if df_filtrado.columns.duplicated().any():
        print("⚠️ Hay columnas duplicadas antes del reindex:", df_filtrado.columns[df_filtrado.columns.duplicated()])
        df_filtrado = df_filtrado.loc[:, ~df_filtrado.columns.duplicated()]
    df_final = df_filtrado.reindex(columns=final_order)
     # Formatear columna fecha fin presentacion
    # Intentar convertir formatos conocidos
    for  col in [col for col in df_final.columns if 'fecha' in col]:
        df_final[col] = parsear_fechas_inteligente(df_final[col])
    # Limpiar columnas de importe
    for col in df_final.columns:
        if any(kw in col.lower() for kw in ['importe', 'valor', 'presupuesto']):
            df_final[col] = df_final[col].apply(limpiar_importe)
    # Añadir columnas extra
    df_final["fuente"] = map_comunidad.get(comunidad,'')
    df_final["fecha_proceso"] = fecha_proceso   
    if os.path.exists(filename_codigo_nuts):
        df_nuts = pd.read_csv(filename_codigo_nuts, sep = ';')
        df_final[["provincia_ejecucion", "comunidad_autonoma_ejecucion"]] = df_final.apply(
            lambda row: pd.Series(extraer_localizacion_final(row["lugar_ejecucion"], row["fuente"], df_nuts)),
            axis=1
        )        
        df_final.drop("lugar_ejecucion",axis = 1,inplace = True)
    
    else:
        print("⚠️ No se encontró el archivo de códigos NUTS. Se mostrará la ubicación original sin procesar.")
        df_final["lugar_ejecucion"] = df_final["lugar_ejecucion"]
    return df_final

def normalizar_texto(texto):
    """
    Convierte una cadena de texto a minúsculas y elimina acentos.
    
    Args:
        texto (str): La cadena original.
    
    Returns:
        str: La cadena normalizada.
    """
    # Convertir a minúsculas
    texto = texto.lower()
    # Eliminar acentos
    texto = unicodedata.normalize("NFD", texto)
    texto = texto.encode("ascii", "ignore").decode("utf-8")
    return texto


def crear_identificador_licitacion(row):
    """Crea un identificador estable sin añadir columnas a la salida final."""
    fuente = str(row.get("fuente", "sin_fuente"))
    expediente = str(row.get("numero_expediente", ""))
    enlace = str(row.get("enlace", ""))
    base = "|".join((fuente, expediente, enlace))
    slug = unidecode(f"{fuente}_{expediente}").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:60]
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:10]
    return f"{slug or 'licitacion'}-{digest}"


def asegurar_identificador_en_pdfs(df, output_dir_pdf):
    """Renombra PDFs para incluir el identificador estable de su licitación."""
    result = df.copy()
    os.makedirs(output_dir_pdf, exist_ok=True)
    for idx, row in result.iterrows():
        pdf_name = str(row.get("pdf", "")).strip()
        if not pdf_name or pdf_name.lower() in {"nan", "none", "notfound"}:
            continue
        source = os.path.join(output_dir_pdf, os.path.basename(pdf_name))
        if not os.path.isfile(source):
            print(f"⚠️ No se puede identificar un PDF inexistente: {source}")
            result.at[idx, "pdf"] = "No disponible"
            continue
        identifier = crear_identificador_licitacion(row)
        extension = os.path.splitext(source)[1].lower() or ".pdf"
        target_name = f"{identifier}_pliego_tecnico{extension}"
        target = os.path.join(output_dir_pdf, target_name)
        if os.path.abspath(source) != os.path.abspath(target):
            if os.path.exists(target):
                os.remove(source)
            else:
                os.replace(source, target)
        result.at[idx, "pdf"] = target_name
    return result


def construir_salida_final(df):
    """Construye por nombre el esquema contractual de 17 columnas.

    Esta función evita el antiguo desplazamiento causado por índices duplicados
    en los ficheros INI y falla de forma explícita si el orden se altera.
    """
    result = df.copy()
    provincia = result.get("provincia_ejecucion", pd.Series("", index=result.index))
    comunidad = result.get(
        "comunidad_autonoma_ejecucion", pd.Series("", index=result.index)
    )

    def build_location(province, region):
        values = []
        for value in (province, region):
            value = str(value).strip()
            if value.lower() not in {"", "nan", "none", "notfound"} and value not in values:
                values.append(value)
        return " / ".join(values) if values else "No disponible"

    result["lugar_ejecucion"] = [
        build_location(province, region) for province, region in zip(provincia, comunidad)
    ]
    defaults = {
        "resumen_breve": "Sin resumen disponible",
        "clasificacion": "No clasificada",
        "pdf": "No disponible",
    }
    for column in FINAL_OUTPUT_COLUMNS:
        if column not in result.columns:
            result[column] = defaults.get(column, None)

    result["pdf"] = result["pdf"].apply(
        lambda value: "No disponible"
        if str(value).strip().lower() in {"", "nan", "none", "notfound"}
        else os.path.basename(str(value).strip())
    )

    def normalize_funding(value):
        text = str(value).strip()
        lowered = text.lower()
        if lowered in {"", "nan", "none", "notfound", "no aplica"}:
            return "No disponible"
        if "no hay financi" in lowered or lowered == "no":
            return "No"
        if "%" in text:
            return text
        if any(term in lowered for term in ("sí", "si", "fondo europeo", "unión europea", "union europea", "next generation", "plan de recuperación")):
            return "Sí"
        return text

    result["financiacion_ue"] = result["financiacion_ue"].apply(normalize_funding)

    result = result.loc[:, FINAL_OUTPUT_COLUMNS].copy()
    if list(result.columns) != FINAL_OUTPUT_COLUMNS:
        raise AssertionError("El esquema final no coincide con las 17 columnas requeridas")
    return result

def leer_fichero_licitaciones(input_dir, comunidad,sep = '\t', fecha_proceso=None):
    """
    Lee el fichero CSV de licitaciones para la comunidad y fecha indicadas.
    Si no se pasa fecha_proceso, busca la fecha más reciente disponible.

    Args:
        input_dir (str): Directorio donde están los ficheros CSV.
        comunidad (str): Comunidad ('andalucia', 'espana', 'euskadi', 'madrid').
        fecha_proceso (str, optional): Fecha en formato 'YYYY-MM-DD'. Defaults a None.

    Returns:
        DataFrame: El dataframe leído, o None si no se pudo cargar.
    """
    patron = re.compile(rf"licitaciones_{comunidad}_(\d{{4}}-\d{{2}}-\d{{2}})\.csv")
    
    if not fecha_proceso:
        fechas = []
        for file in os.listdir(input_dir):
            match = patron.match(file)
            if match:
                fechas.append(match.group(1))
        
        if fechas:
            fecha_proceso = max(fechas)
            print(f"🟢 {comunidad.capitalize()}: usando la fecha más reciente encontrada: {fecha_proceso}")
        else:
            print(f"❌ No se encontraron ficheros de {comunidad} en {input_dir}")
            return None

    # Construir el path
    file_path = os.path.join(input_dir, f"licitaciones_{comunidad}_{fecha_proceso}.csv")
    
    try:
        df = pd.read_csv(file_path, sep = sep)
        print(f"✅ {comunidad.capitalize()}: fichero cargado con fecha {fecha_proceso}")
        return df
    except Exception as e:
        print(f"⚠️ Error cargando {comunidad} para fecha {fecha_proceso}: {e}")
        return None
