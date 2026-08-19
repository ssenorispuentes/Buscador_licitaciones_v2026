import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
from time import sleep
import re
from urllib.parse import urljoin
import configparser
import os
import re
import unicodedata
import hashlib
from src.document_keywords import download_document, find_document_link

class ScraperMadrid:
    def __init__(self, fecha, config_file="./config/scraper_config.ini", fecha_minima=None):
        self.fecha = fecha

        config = configparser.ConfigParser()
        config.optionxform = str  
        config.read(config_file)

        # Leer parámetros desde el ini
        urls = "urls"
        params = "mad_params"
        filters = "mad_filters"
        paths = "input_output_path"

        self.base_url = config.get(urls, "base_mad", fallback="https://contratos-publicos.comunidad.madrid")
        self.OUTPUT_DIR = config.get(paths, "output_dir", fallback="./datos")
        self.OUTPUT_DIR_PDF = config.get(paths, "output_dir_pdf", fallback="./pdfs")
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)
        os.makedirs(self.OUTPUT_DIR_PDF, exist_ok=True)

        max_paginas_str = config.get(params, "max_paginas", fallback="1")
        self.MAX_PAGINAS = int(max_paginas_str) if max_paginas_str.lower() != "none" else None

        self.TIMEOUT = config.getint(params, "timeout", fallback=30)
        self.DELAY = config.getint(params, "delay", fallback=2)
        self.FECHA_MINIMA = fecha_minima

        # Filtros desde ini
        self.params = {k: v for k, v in config.items(filters)}
        for key, value in self.params.items():
            if 'None' in value or  value == '':  # Si el valor es el string 'None'
                self.params[key] = None
        self.params['page'] = 0

        # Sesión HTTP
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'DNT': '1',
            'Connection': 'keep-alive',
        })

    def extraer_detalle(self, enlace):
        sleep(self.DELAY)
        try:
            response = self.session.get(enlace, timeout=self.TIMEOUT)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            detalle = {}

            fields = soup.find_all('div', class_='field')
            for field in fields:
                label_elem = field.find(class_='field__label')
                value_elem = field.find(class_='field__item')

                if label_elem and value_elem:
                    label = label_elem.get_text(strip=True).replace(':', '')
                    content = value_elem.get_text(" ", strip=True)

                    campo_limpio = re.sub(r'[^\w\s]', '', label.lower())
                    campo_limpio = re.sub(r'\s+', '_', campo_limpio.strip())

                    if campo_limpio == 'fecha_y_hora_limite_de_presentacion_de_ofertas_o_solicitudes_de_participacion':
                        fecha_dt = pd.to_datetime(content, dayfirst=True, errors='coerce')
                        if pd.notnull(fecha_dt) and fecha_dt < self.FECHA_MINIMA:
                            return None

                    detalle[campo_limpio] = content

            document_url = find_document_link(soup, enlace)
            if document_url:
                identifier = hashlib.sha256(enlace.encode("utf-8")).hexdigest()[:12]
                filename = f"mad_{identifier}_pliego_tecnico.pdf"
                try:
                    detalle["pdf_prescripciones_tecnicas"] = download_document(
                        self.session, document_url, self.OUTPUT_DIR_PDF, filename, self.TIMEOUT
                    )
                except Exception as exc:
                    print(f"⚠️ No se pudo descargar el pliego de Madrid: {exc}")

            return detalle

        except Exception as e:
            print(f"⚠️ Error extrayendo detalle: {e}")
            return {}

    def extraer_pagina(self):
        try:
            response = self.session.get(f"{self.base_url}/contratos", params=self.params, timeout=self.TIMEOUT)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            contratos = []

            contract_items = soup.select('div.contratos-result li')
            for item in contract_items:
                link_elem = item.find('a')
                if not link_elem:
                    continue

                enlace = urljoin(self.base_url, link_elem['href'])
                titulo = link_elem.get_text(strip=True)

                contrato = {
                    'titulo': titulo,
                    'enlace_detalle': enlace
                }

                detalle = self.extraer_detalle(enlace)
                if detalle is not None:
                    contrato.update(detalle)
                    contratos.append(contrato)
                    print(f"✅ Extraído: {titulo[:50]}...")

            return contratos

        except Exception as e:
            print(f"⚠️ Error extrayendo página: {e}")
            return []

    def siguiente_pagina(self):
        self.params['page'] += 1
        return True

    def scraping(self):
        todos_contratos = []
        pagina = 0

        while True:
            print(f"📄 Procesando página {pagina + 1}")
            contratos = self.extraer_pagina()

            if not contratos:
                print(f"ℹ️ No se encontraron contratos en página {pagina + 1}")
                break

            for contrato in contratos:
                contrato['pagina'] = pagina + 1

            todos_contratos.extend(contratos)

            if self.MAX_PAGINAS is not None and (pagina + 1) >= self.MAX_PAGINAS:
                break

            self.siguiente_pagina()
            sleep(self.DELAY)
            pagina += 1

        return todos_contratos
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
        if not datos:
            print("No hay datos.")
            return

        df = pd.DataFrame(datos)
        # Limpieza de nombres de columnas
        nuevas_columnas = [self.limpiar_nombre_columna(col) for col in df.columns]
        df.columns = nuevas_columnas
        filename = os.path.join(self.OUTPUT_DIR, f"licitaciones_madrid_{self.fecha}.csv")
        df.to_csv(filename, index=False,sep="\t", encoding='utf-8-sig')
        print(f"✅ Archivo guardado: {filename}")

    def ejecutar(self):
        try:
            datos = self.scraping()
            self.guardar(datos)
            return pd.DataFrame(datos)
        except Exception as e:
            print(f"❌ Error durante la ejecución: {e}")
