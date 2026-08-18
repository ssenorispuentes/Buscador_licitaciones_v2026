import configparser
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import re
import unicodedata
import os
import time
import requests
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class ScraperEspana:
    def __init__(self, fecha, config_file="./config/scraper_config.ini", fecha_minima=None):
        config = configparser.ConfigParser()
        config.optionxform = str  
        config.read(config_file)

        self.config_file = config_file
        self.OUTPUT_DIR = config.get("input_output_path", "output_dir", fallback="./datos")
        self.OUTPUT_DIR_PDF = config.get('input_output_path', "output_dir_pdf", fallback="./datos")
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)
        os.makedirs(self.OUTPUT_DIR_PDF, exist_ok=True)
        self.url = config.get("urls", "base_esp")
        if not self.url:
            raise ValueError("❌ La URL de la Plataforma de Contratación del Estado no está definida en el .ini")
        max_paginas_str = config.get('esp_params', "max_paginas", fallback="None")
        self.MAX_PAGINAS = None if (max_paginas_str.strip().lower() in ["none", ""]) else int(max_paginas_str)
        self.TIMEOUT = config.getint("esp_params", "timeout", fallback=30)
        self.fecha_minima = fecha_minima
        try:
            ini_fecha_minima = pd.to_datetime(self.fecha_minima, dayfirst=True, format='%d/%m/%Y')
        except:
            valor_fecha = '01/01/2025'
            try:
                ini_fecha_minima_datetime= datetime.strptime(valor_fecha, '%d/%m/%Y')
                ini_fecha_minima = pd.to_datetime(ini_fecha_minima_datetime)
                if pd.isna(ini_fecha_minima):
                    raise ValueError(f"Fecha inválida: '{valor_fecha}'")
            except Exception as e:
                print(f"⚠️ Error interpretando 'fecha_minima': {e}")

        self.filters = {k: v for k, v in config.items("esp_filters")}
        # Tras cargar los filtros
        for key, value in self.filters.items():
            if 'None' in value or  value == '':  # Si el valor es el string 'None'
                self.filters[key] = None

        self.fecha = fecha
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")

        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        self.wait = WebDriverWait(self.driver, self.TIMEOUT)

    def configurar_filtros(self):
        self.driver.get(self.url)

        # La URL directa del formulario dejó de ser estable. Desde 2026 hay que
        # entrar por la página de buscadores y abrir el formulario de licitaciones.
        abrir_formulario = self.wait.until(EC.element_to_be_clickable((
            By.ID,
            "viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:linkFormularioBusqueda",
        )))
        self.driver.execute_script("arguments[0].click();", abrir_formulario)

        Select(self.wait.until(EC.presence_of_element_located(
            (By.NAME, "viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:menu1MAQ1")
        ))).select_by_value(self.filters.get("pais", "ES"))

        if self.filters.get("estado_licitacion"):
            Select(self.driver.find_element(
                By.NAME, "viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:estadoLici"
            )).select_by_value(self.filters["estado_licitacion"])

        campos_fecha = [
            ("viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:textMinFecAnuncioMAQ2", self.filters.get("fecha_inicio")),
            ("viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:textMaxFecAnuncioMAQ", self.filters.get("fecha_fin")),
            ("viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:textMinFecLimite", self.filters.get("fecha_inicio_presentacion")),
            ("viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:textMaxFecLimite", self.filters.get("fecha_fin_presentacion")),
        ]

        for name, valor in campos_fecha:
            if valor:
                campo = self.driver.find_element(By.NAME, name)
                self.driver.execute_script("arguments[0].removeAttribute('readonly')", campo)
                campo.clear()
                campo.send_keys(valor)

        if self.filters.get("forma_presentacion"):
            Select(self.driver.find_element(
                By.NAME, "viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:menuFormaPresentacionMAQ1_SistPresent"
            )).select_by_value(self.filters["forma_presentacion"])


        buscar = self.wait.until(EC.element_to_be_clickable((
            By.NAME,
            "viewns_Z7_AVEQAI930OBRD02JPMTPG21004_:form1:button1Arriba",
        )))
        self.driver.execute_script("arguments[0].click();", buscar)

        time.sleep(10)
        print(f"🔗 URL de resultados: {self.driver.current_url}")

    def extraer_detalle(self, enlace):
        detalle = {}
        wait = WebDriverWait(self.driver, 20)

        try:
            # 👉 Abrir nueva pestaña con el enlace de detalle
            self.driver.execute_script("window.open(arguments[0]);", enlace)
            self.driver.switch_to.window(self.driver.window_handles[-1])  # ✅ más seguro que [1]
            time.sleep(3)
            print(f"➡️ Detalle abierto correctamente: {enlace}")

            # 👉 Extraer campos generales
            bloques = self.driver.find_elements(By.CSS_SELECTOR, "ul.altoDetalleLicitacion")
            for ul in bloques:
                try:
                    label = ul.find_element(By.CSS_SELECTOR, "span.tipo3")
                    value = ul.find_element(By.CSS_SELECTOR, "span.outputText")
                    clave = label.get_attribute("title") or label.text.strip()
                    valor = value.get_attribute("title") or value.text.strip()

                    if "fecha" in clave.lower() and "límite" in clave.lower():
                        try:
                            fecha_limite = pd.to_datetime(valor, dayfirst=True)
                            if fecha_limite < self.fecha_minima:
                                print("🛑 Fecha fuera del rango permitido")
                                raise Exception("Fecha fuera de rango")
                        except Exception as e:
                            print(f"⚠️ Error parseando fecha: {e}")
                    detalle[clave] = valor
                except Exception as e:
                    print(f"⚠️ Error extrayendo datos del bloque: {e}")
                    continue

            # 👉 Buscar fila con 'pliego' en tabla de documentos
            fila_pliego = None
            try:
                tabla = wait.until(EC.presence_of_element_located((By.ID, "myTablaDetalleVISUOE")))
                filas = tabla.find_elements(By.XPATH, ".//tr[contains(@class, 'rowClass')]")
                print(f"📊 Filas en tabla de documentos: {len(filas)}")
                for idx, fila in enumerate(filas):
                    texto = fila.text.lower()
                    print(f"🧾 Fila {idx}: {texto}")
                    if "pliego" in texto:
                        fila_pliego = fila
                        break
            except Exception as e:
                print(f"❌ Error localizando la tabla de documentos: {e}")

            if fila_pliego:
                print("📥 Encontrado PDF en la primera tabla, descargando...")
                try:
                    enlace_pdf = fila_pliego.find_element(By.TAG_NAME, "a")
                    href = enlace_pdf.get_attribute("href")
                    if href:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        nombre_pdf = f"esp_pliego_prescripciones_{timestamp}.pdf"
                        ruta = os.path.join(self.OUTPUT_DIR_PDF, nombre_pdf)
                        r = requests.get(href, stream=True)
                        if r.status_code == 200:
                            with open(ruta, 'wb') as f:
                                for chunk in r.iter_content(1024):
                                    f.write(chunk)
                            print(f"✅ PDF guardado en: {ruta}")
                            detalle["PDF Pliego Prescripciones Técnicas"] = nombre_pdf
                        else:
                            print(f"❌ Error HTTP al descargar PDF: {r.status_code}")
                    else:
                        print("❌ Enlace al PDF no tiene href")
                except Exception as e:
                    print(f"⚠️ Error al descargar PDF de la primera tabla: {e}")
            else:
                print("❌ No se encontró fila con 'pliego' en la primera tabla, buscando en la segunda...")

                try:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.TextAlignCenter.celdaTam2")))
                    enlace_pdf = self.driver.find_element(By.CSS_SELECTOR, "a.TextAlignCenter.celdaTam2")
                    pdf_url = enlace_pdf.get_attribute("href")
                    if pdf_url:
                        print(f"📥 Encontrado PDF en la segunda tabla: {pdf_url}")
                        response = requests.get(pdf_url)
                        if response.status_code == 200:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            nombre_pdf = f"esp_pliego_prescripciones_{timestamp}.pdf"
                            ruta = os.path.join(self.OUTPUT_DIR_PDF, nombre_pdf)
                            with open(ruta, 'wb') as f:
                                f.write(response.content)
                            print(f"✅ PDF descargado: {ruta}")
                            detalle["PDF Pliego Prescripciones Técnicas"] = nombre_pdf
                        else:
                            print(f"❌ Error HTTP al descargar PDF: {response.status_code}")
                    else:
                        print("❌ No se encontró href válido en segunda tabla")
                except Exception as e:
                    print(f"❌ Error en la segunda tabla: {e}")

            # ✅ Cierre seguro de pestaña
            try:
                if len(self.driver.window_handles) > 1:
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except Exception as e:
                print(f"⚠️ Error al cerrar pestaña o volver: {e}")

        except Exception as e:
            print(f"❌ Error general en extracción de detalle: {e}")
            # ✅ Último intento de recuperar la sesión base
            try:
                if len(self.driver.window_handles) > 1:
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except Exception as e2:
                print(f"⚠️ Error al intentar cerrar o volver tras fallo: {e2}")

        return detalle




    def extraer_pagina(self):
        licitaciones = []
        rows = self.driver.find_elements(
            By.XPATH,
            "//tr[.//a[contains(@href, 'detalle_licitacion')]]",
        )

        for row in rows:
            try:
                row1 = row.find_elements(By.TAG_NAME, "td")
                enlace = row1[0].find_element(By.XPATH, ".//a[contains(@href, 'detalle_licitacion')]").get_attribute("href")

                base = {
                    "descripcion": row1[0].text.strip(),
                    "tipo_contrato": row1[1].text.strip(),
                    "estado": row1[2].text.strip(),
                    "importe": row1[3].text.strip(),
                    "fecha_limite": row1[4].text.strip(),
                    "organo_contratacion": row1[5].text.strip(),
                    "enlace": enlace
                }

                detalle = self.extraer_detalle(enlace)
                if detalle is not None:
                    licitacion = {**base, **detalle}
                    licitaciones.append(licitacion)
                    print(f"✅ Extraída: {base['descripcion'][:50]}...")

            except:
                continue
        return licitaciones

    def scraping(self):
        self.configurar_filtros()
        todas_licitaciones = []
        pagina = 1

        while True:
            print(f"📄 Procesando página {pagina}")
            licitaciones = self.extraer_pagina()
            for lic in licitaciones:
                lic["pagina"] = pagina
            todas_licitaciones.extend(licitaciones)

            if (self.MAX_PAGINAS is not None) and (pagina >= self.MAX_PAGINAS):
                break
            if not self.siguiente_pagina():
                break
            pagina += 1
        return todas_licitaciones

    def siguiente_pagina(self):
        try:
            siguiente = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@type='submit' and @value='Siguiente']")))
            self.driver.execute_script("arguments[0].click();", siguiente)
            time.sleep(3)
            return True
        except TimeoutException:
            return False
    def limpiar_nombre_columna(self, nombre):
        """
        Limpia un nombre de columna:
        - Minúsculas
        - Sin acentos
        - Espacios y guiones convertidos a guion bajo
        """
        nombre = nombre.lower()
        nombre = unicodedata.normalize("NFD", nombre).encode("ascii", "ignore").decode("utf-8")
        nombre = nombre.replace(" ", "_").replace("-", "_")
        nombre = re.sub(r'[()]', '', nombre)
        # Elimina todos los caracteres no alfanuméricos + guiones bajos al inicio y fin
        nombre = re.sub(r'^[^\w]+|[^\w]+$|^_+|_+$', '', nombre)
        nombre = re.sub(r'[^\w]+$', '', nombre)  # limpia cualquier no alfanumérico al final
        return nombre
    def normalizar_texto(self,texto):
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
    
    def define_expediente(self,df, col_descripcion  = 'descripcion'):
        col_desc = None
        for col in df.columns:
            if col_descripcion in self.normalizar_texto(col):
                col_desc = col
                break
        if col_desc:
            nuevos_expedientes = []
            nuevas_descripciones = []
            for desc in df[col_desc].fillna(""):
                partes = desc.split("\n", 1)
                expediente = partes[0].strip() if partes else ""
                resto_desc = partes[1].strip() if len(partes) > 1 else ""
                nuevos_expedientes.append(expediente)
                nuevas_descripciones.append(resto_desc)
            df["numero_expediente"] = nuevos_expedientes
            df[col_desc] = nuevas_descripciones
        return df


    def ejecutar(self):
        try:
            datos = self.scraping()
            df = pd.DataFrame(datos)
            # Limpia columna descripcion y define columna numero_expediente
            df = self.define_expediente(df)
            # Limpieza de nombres de columnas
            nuevas_columnas = [self.limpiar_nombre_columna(col) for col in df.columns]
            df.columns = nuevas_columnas
            filename = os.path.join(self.OUTPUT_DIR, f"licitaciones_espana_{self.fecha}.csv")
            df.to_csv(filename, index=False,sep="\t", encoding="utf-8-sig")
            print(f"✅ Archivo guardado: {filename}")
            # Cantidad de NaNs (vacíos)
            if 'pdf_pliego_prescripciones_tecnicas' not in df.columns:
                df['pdf_pliego_prescripciones_tecnicas'] = None
                nulos = df['pdf_pliego_prescripciones_tecnicas'].isna().sum()
                # Cantidad de no nulos (con valor)
                no_nulos = df['pdf_pliego_prescripciones_tecnicas'].notna().sum()
                total = nulos + no_nulos
                print(f"🟡 PDFs descargados con éxito en la página de gobierno de España: {no_nulos}/{total} ")
            else:
                print('No se encuentra información de Pliego en el detalle de la licitación')
            return df
        finally:
            self.driver.quit()
