
import os
import time
import configparser
import re
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
import requests
from src.document_keywords import download_document, find_document_link

class ScraperEuskadi:
    """
    ScraperEuskadi

    Scraper para la Agencia Vasca del Agua (URA). Recorre las páginas de anuncios abiertos,
    extrae datos de la tabla y detalles de cada licitación.
    """

    def __init__(self, fecha, fecha_minima, config_file="./config/scraper_config.ini"):
        """
        Inicializa el scraper:
        - Lee la configuración desde el archivo INI.
        - Configura el navegador Chrome headless.
        """
        config = configparser.ConfigParser()
        config.optionxform = str  
        config.read(config_file)

        paths = "input_output_path"
        urls = "urls"
        params = "eus_params"

        self.OUTPUT_DIR = config.get(paths, "output_dir", fallback="./datos")
        self.OUTPUT_DIR_PDF = config.get(paths, "output_dir_pdf", fallback="./pdfs")
        self.BASE = config.get(urls, "base_eus")

        if not self.BASE:
            raise ValueError("❌ La URL de Euskadi no está definida en el archivo de configuración")
        
        max_paginas_str = config.get(params, "max_paginas", fallback="None")
        self.MAX_PAGINAS = None if (max_paginas_str.strip().lower() in ["none", ""]) else int(max_paginas_str)
        self.TIMEOUT = config.getint(params, "timeout", fallback=30)
        self.FECHA_MINIMA = fecha_minima
        self.fecha = fecha 

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")

        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        self.wait = WebDriverWait(self.driver, self.TIMEOUT)
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)
        os.makedirs(self.OUTPUT_DIR_PDF, exist_ok=True)

    def extraer_pagina(self):
        """
        Extrae la tabla de la página actual y los detalles de cada licitación.
        """
        tabla = self.driver.find_element(By.ID, "tablaWidget")
        filas = tabla.find_elements(By.XPATH, ".//tbody//tr")

        licitaciones = []
        for fila in filas:
            try:
                celdas = fila.find_elements(By.TAG_NAME, "td")
                if not celdas:
                    continue

                codigo = celdas[0].text.strip()
                enlace_elem = fila.find_element(By.TAG_NAME, "a")
                enlace = enlace_elem.get_attribute("href")
                titulo = enlace_elem.text.strip()

                licitacion = {
                    'codigo_expediente': codigo,
                    'titulo': titulo,
                    'enlace_detalle': enlace
                }

                detalle = self.extraer_detalle(enlace)
                if detalle is not None:
                    licitacion.update(detalle)
                    licitaciones.append(licitacion)
                    print(f"Extraída: {titulo[:50]}...")

            except:
                continue

        return licitaciones

    def extraer_detalle(self, url):
        """
        Visita el detalle de la licitación y extrae la información disponible.
        Filtra por FECHA_MINIMA si corresponde.
        """
        self.driver.get(url)
        time.sleep(2)

        detalle = {}
        try:
            cabecera = self.driver.find_element(By.CLASS_NAME, "cabeceraDetalle")
            soup = BeautifulSoup(cabecera.get_attribute('innerHTML'), 'html.parser')

            for dt in soup.find_all('dt'):
                campo = dt.get_text(strip=True).replace(':', '').strip()
                dd = dt.find_next_sibling('dd')
                if dd:
                    valor = dd.get_text(strip=True)
                    campo_limpio = re.sub(r'[^\w\s]', '', campo.lower())
                    campo_limpio = re.sub(r'\s+', '_', campo_limpio.strip())

                    if campo_limpio == 'fecha_de_publicacion':
                        try:
                            fecha_valor = pd.to_datetime(valor, dayfirst=True)
                            if fecha_valor < self.FECHA_MINIMA:
                                return None
                        except:
                            pass

                    detalle[campo_limpio] = valor

            page_soup = BeautifulSoup(self.driver.page_source, "html.parser")
            document_url = find_document_link(page_soup, url)
            if document_url:
                identifier = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
                filename = f"eus_{identifier}_pliego_tecnico.pdf"
                try:
                    detalle["pdf_prescripciones_tecnicas"] = download_document(
                        requests.Session(), document_url, self.OUTPUT_DIR_PDF, filename, self.TIMEOUT
                    )
                except Exception as exc:
                    print(f"⚠️ No se pudo descargar el pliego de Euskadi: {exc}")
        except:
            pass

        self.driver.back()
        time.sleep(1)
        return detalle

    def siguiente_pagina(self):
        """
        Intenta pasar a la siguiente página del paginador.
        """
        try:
            boton = self.driver.find_element(By.ID, "tablaWidget_next")
            if "paginate_disabled_next" in boton.get_attribute("class"):
                return False
            self.driver.execute_script("arguments[0].click();", boton)
            time.sleep(3)
            return True
        except:
            return False

    def scraping(self):
        """
        Ejecuta el scraping completo recorriendo las páginas según configuración.
        """
        self.driver.get(self.BASE)
        self.wait.until(EC.presence_of_element_located((By.ID, "tablaWidget")))
        time.sleep(5)

        todas_licitaciones = []
        pagina = 1

        while True:
            print(f"📄 Página {pagina}")
            licitaciones = self.extraer_pagina()

            for lic in licitaciones:
                lic['pagina'] = pagina

            todas_licitaciones.extend(licitaciones)

            if (self.MAX_PAGINAS is not None and pagina >= self.MAX_PAGINAS):
                break
            if not self.siguiente_pagina():
                break

            pagina += 1

        return todas_licitaciones
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
        filename = f"licitaciones_euskadi_{self.fecha}.csv"
        path = os.path.join(self.OUTPUT_DIR, filename)
        df.to_csv(path, index=False,sep="\t", encoding="utf-8-sig")
        print(f"✅ Archivo guardado: {path}")
    
    def ejecutar(self):
        """
        Ejecuta el scraping y guarda los datos.
        Devuelve un DataFrame con los datos obtenidos.
        """
        try:
            datos = self.scraping()
            self.guardar(datos)
            return pd.DataFrame(datos)
        except Exception as e:
            print(f"❌ Error durante la ejecución: {e}")
        finally:
            self.driver.quit()
