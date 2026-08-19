
import os
import time
import configparser
from urllib.parse import urlencode, urlparse
import re
import pandas as pd
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from webdriver_manager.chrome import ChromeDriverManager
import unicodedata
import hashlib


class ScraperAndalucia:
    """
    ScraperAndalucia

    Esta clase permite realizar scraping de licitaciones publicadas en el perfil
    de contratante de la Junta de Andalucía. Utiliza Selenium (con Chrome headless)
    y BeautifulSoup para extraer información de la tabla principal y de los
    detalles de cada licitación (incluyendo datos y PDF de prescripciones técnicas).

    Características principales:
    - Permite recorrer múltiples páginas de resultados (configurable con max_paginas).
    - Extrae y unifica datos de la tabla y del detalle de cada licitación.
    - Descarga el PDF de prescripciones técnicas asociado si existe.
    - Guarda los resultados en un archivo CSV en el directorio especificado.
    """

    def __init__(self, fecha, fecha_minima, config_file="./config/scraper_config.ini"):
        """
        Inicializa el scraper:
        - Lee la configuración desde un archivo INI.
        - Configura el navegador Chrome en modo headless.
        - Prepara la URL de inicio con filtros aplicados.
        """
        config = configparser.ConfigParser()
        config.optionxform = str  
        config.read(config_file)

        paths = "input_output_path"
        urls = "urls"
        params = "and_params"
        filters = 'and_filters' 

        self.OUTPUT_DIR = config.get(paths, "output_dir", fallback="./datos")
        self.OUTPUT_DIR_PDF = config.get(paths, "output_dir_pdf", fallback="./datos")
        max_paginas_str = config.get(params, "max_paginas", fallback="None")
        self.MAX_PAGINAS = None if (max_paginas_str.strip().lower() in ["none", ""]) else int(max_paginas_str)
        self.TIMEOUT = config.getint(params, "timeout", fallback=30)
        self.BASE = config.get(urls, "base_and")

        if not self.BASE:
            raise ValueError("❌ La URL de Andalucía no está definida en el archivo de configuración")

        self.params = {k: v for k, v in config.items(filters)}
        self.params["fechaDesde"] = fecha_minima
        self.BASE_URL = f"{self.BASE}?{urlencode(self.params)}"
        self.fecha = fecha 

        options = Options()
        options.add_argument('--headless')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--window-size=1920,1080')

        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        self.wait = WebDriverWait(self.driver, self.TIMEOUT)
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)


    def extraer_info_licitacion_y_pdf_and(self, html: str, url_base: str, carpeta_destino="pdfs") -> dict:
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin
        import unicodedata
        import os
        import requests

        def normalizar(texto):
            texto = texto.lower()
            texto = unicodedata.normalize("NFD", texto)
            return ''.join(c for c in texto if unicodedata.category(c) != 'Mn')

        def descargar_pdf(url_pdf, nombre_archivo):
            os.makedirs(self.OUTPUT_DIR_PDF, exist_ok=True)
            ruta_local = os.path.join(self.OUTPUT_DIR_PDF, nombre_archivo)
            try:
                r = requests.get(url_pdf, stream=True)
                if r.status_code == 200:
                    with open(ruta_local, "wb") as f:
                        for chunk in r.iter_content(1024):
                            f.write(chunk)
                    print(f"✅ PDF descargado: {ruta_local}")
                    return nombre_archivo
                else:
                    print(f"❌ Error al descargar: {url_pdf}")
            except Exception as e:
                print(f"⚠️ Excepción al descargar {url_pdf}: {e}")
            return None

        try:
            soup = BeautifulSoup(html, "html.parser")

            # Quitar sección "Información de lotes"
            for h2 in soup.select("h2.seccion-indice"):
                if "información de lotes" in h2.get_text(strip=True).lower():
                    div_lotes = h2.find_next_sibling("div", class_="contenido")
                    if div_lotes:
                        div_lotes.decompose()

            resultado = {}

            # --- MODO 2: div.field ---
            for field in soup.select("div.field"):
                label = field.select_one(".field__label")
                item = field.select_one(".field__item")
                if label and item:
                    clave = label.get_text(strip=True)
                    valor = item.get_text(strip=True)
                    if clave and valor and clave not in resultado:
                        resultado[clave] = valor

            # --- MODO 1: div.block.ng-star-inserted ---
            for bloque in soup.select("div.block.ng-star-inserted"):
                label = bloque.select_one(".field__label")
                item = bloque.select_one(".field__item")
                if label and item:
                    clave = label.get_text(strip=True)
                    valor = item.get_text(strip=True)
                    if clave and valor and clave not in resultado:
                        resultado[clave] = valor

            # --- MODO 3: <b>: <span> ---
            for div in soup.select("div.contenido"):
                for tag in div.find_all(["b", "strong"]):
                    clave = tag.get_text(strip=True).rstrip(":")
                    span = None
                    for sib in tag.next_siblings:
                        if getattr(sib, "name", None) == "span":
                            span = sib
                            break
                    if span:
                        valor = span.get_text(strip=True)
                    else:
                        textos = list(tag.parent.stripped_strings)
                        valor = ' '.join(textos[1:]) if len(textos) > 1 else ""
                    if clave and valor and clave not in resultado:
                        resultado[clave] = valor

            # --- Buscar PDF prescripciones técnicas ---
            for h2 in soup.select("h2.seccion-indice"):
                if "documentacion complementaria" in normalizar(h2.get_text(strip=True)):
                    contenedor = h2.find_next("div")
                    if contenedor:
                        for link in contenedor.find_all("a", href=True):
                            titulo = normalizar(link.get("title", ""))
                            texto_link = normalizar(link.get_text(strip=True))
                            if "prescripciones tecnicas" in titulo or "prescripciones tecnicas" in texto_link \
                            or "ppt" in titulo or "ppt" in texto_link:
                                url_pdf = urljoin(url_base, link["href"])
                                identificador = hashlib.sha256(
                                    url_base.encode("utf-8")
                                ).hexdigest()[:12]
                                nombre_archivo = f"and_{identificador}_pliego_tecnico.pdf"
                                nombre_guardado = descargar_pdf(url_pdf, nombre_archivo)
                                if nombre_guardado:
                                    resultado["PDF Prescripciones Técnicas"] = nombre_guardado
                                break  # solo queremos el primero

            return resultado

        except Exception as e:
            print(f"⚠️ Error procesando HTML de {url_base}: {e}")
            return {}

    def extraer_info_completa(self, enlace_completo):
        """
        Abre un enlace de detalle de licitación en una nueva pestaña,
        espera que cargue el contenido, y extrae información adicional
        mediante el parser externo.
        """
        self.driver.execute_script("window.open('');")
        self.driver.switch_to.window(self.driver.window_handles[1])
        self.driver.get(enlace_completo)

        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.field, div.block.ng-star-inserted, div.contenido b"))
            )
            print("✅ Contenido cargado correctamente")
        except:
            print("❌ Timeout: no se encontró contenido estructurado")

        detalle_dict = self.extraer_info_licitacion_y_pdf_and(html=self.driver.page_source, url_base=enlace_completo)

        self.driver.close()
        self.driver.switch_to.window(self.driver.window_handles[0])
        return detalle_dict
    
    def scraping(self):
        """
        Realiza el scraping de las páginas de resultados:
        - Recorre las páginas hasta max_paginas o hasta no haber más datos.
        - Extrae y enriquece cada fila con datos de detalle.
        """
        self.driver.get(self.BASE_URL)
        try:
            resultado_span = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "span.view-header__summary"))
            )
            print(f"🔎 Total licitaciones encontradas: {resultado_span.text}")
        except Exception as e:
            print(f"⚠️ No se pudo localizar el span de resultados: {e}")
        all_rows = []
        pagina = 1

        while True:
            try:
                self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.p-datatable-table")))
            except TimeoutException:
                print(f"❌ No se encontró la tabla en la URL actual ({self.driver.current_url}).")
                break
            time.sleep(1.5)
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            tabla = soup.select_one('table.p-datatable-table')
            if not tabla:
                print("ℹ️ No se encontró la tabla de resultados.")
                break

            cabeceras = [th.get_text(strip=True) for th in tabla.select('thead th')]
            filas = tabla.select('tbody tr')
            if not filas:
                print("ℹ️ La tabla existe pero no contiene filas.")
                break
            
            print(f"📄 Página {pagina}")
            for fila in filas:
                celdas = fila.find_all('td')
                if len(celdas) < len(cabeceras):
                    continue

                fila_dict = {cabeceras[i]: celdas[i].get_text(strip=True) for i in range(len(cabeceras))}
                enlace_tag = celdas[0].find('a', href=True)
                dom_base = f"{urlparse(self.BASE).scheme}://{urlparse(self.BASE).netloc}"
                enlace_completo = (dom_base + enlace_tag['href']) if enlace_tag else ''
                fila_dict['URL'] = enlace_completo

                detalle_dict = self.extraer_info_completa(enlace_completo)
                for clave, valor in detalle_dict.items():
                    if not fila_dict.get(clave):
                        fila_dict[clave] = valor

                all_rows.append(fila_dict)

            try:
                boton = self.driver.find_element(By.XPATH, "//div[@id='divPaginador']//button[contains(text(), 'SIGUIENTE')]")
                if (not boton.is_enabled() or (self.MAX_PAGINAS is not None and pagina >= self.MAX_PAGINAS)):
                    break
                self.driver.execute_script("arguments[0].scrollIntoView(true);", boton)
                time.sleep(0.5)
                boton.click()
                time.sleep(1)
                pagina += 1
            except Exception:
                print("⚠️ No se pudo avanzar de página.")
                break

        self.driver.quit()
        return all_rows
    
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

    def guardar(self, datos):
        """
        Guarda los datos extraídos en un archivo CSV en el directorio de salida.
        Limpia los nombres de las columnas.
        """
        if not datos:
            print("No hay datos.")
            return

        df = pd.DataFrame(datos)

        # Limpieza de nombres de columnas
        nuevas_columnas = [self.limpiar_nombre_columna(col) for col in df.columns]
        df.columns = nuevas_columnas
        nulos = df['pdf_prescripciones_tecnicas'].isna().sum()
        # Cantidad de no nulos (con valor)
        no_nulos = df['pdf_prescripciones_tecnicas'].notna().sum()
        print(f"🟡 PDFs descargados con éxito en la página de Andalucía: {no_nulos}/{nulos + no_nulos} ")
        filename = f"licitaciones_andalucia_{self.fecha}.csv" 
        path = os.path.join(self.OUTPUT_DIR, filename)
        df.to_csv(path, index=False, sep="\t", encoding="utf-8-sig")
        print(f"✅ Archivo guardado: {path}")
        self.df_final = df.copy()

    def ejecutar(self):
        """
        Ejecuta el scraping y guarda los datos en CSV.
        Devuelve un DataFrame con los datos extraídos.
        """
        try:
            datos = self.scraping()
            self.guardar(datos)
            return self.df_final
        finally:
            self.driver.quit()
