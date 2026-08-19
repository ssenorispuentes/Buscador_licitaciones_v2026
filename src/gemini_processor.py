import configparser
import hashlib
import json
import os
import re
import time
from pathlib import Path

import fitz
from dotenv import load_dotenv


class LicitacionGeminiProcessor:
    """Clasifica y resume licitaciones usando primero el texto de la web.

    El PDF solo se lee cuando Gemini considera tecnológica la licitación y la
    información web no es suficiente. Si la API no está disponible, se conserva
    el pipeline mediante un fallback determinista basado en palabras clave.
    """

    def __init__(self, df, config_file="./config/scraper_config.ini"):
        self.df = df.copy()
        self.config = configparser.ConfigParser()
        self.config.optionxform = str
        self.config.read(config_file, encoding="utf-8")

        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = self.config.get("gemini", "model", fallback="gemini-3.6-flash")
        self.pdf_dir = Path(
            self.config.get("input_output_path", "output_dir_pdf", fallback="./pdfs")
        )
        self.max_web_chars = self.config.getint("gemini", "max_web_chars", fallback=8000)
        self.max_pdf_chars = self.config.getint("gemini", "max_pdf_chars", fallback=24000)
        self.request_timeout_ms = self.config.getint(
            "gemini", "request_timeout_ms", fallback=45000
        )
        self.max_retries = self.config.getint("gemini", "max_retries", fallback=3)
        self.retry_delay = self.config.getfloat("gemini", "retry_delay_seconds", fallback=2)
        self.cache_dir = Path(
            self.config.get("gemini", "cache_dir", fallback="./.gemini_cache")
        )
        self.cache_version = self.config.get("gemini", "cache_version", fallback="1")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.categories = self._load_categories()
        self.tech_keywords = self._load_keywords("palabras_clave_tecnologia")
        self.non_tech_keywords = self._load_keywords("palabras_descarte_tecnologia")
        self.client = self._create_client()
        self.stats = {
            "gemini_requeridas": 0,
            "gemini_analizadas": 0,
            "gemini_cache_reutilizadas": 0,
            "gemini_api_solicitudes": 0,
            "pdf_analizados_gemini": 0,
            "gemini_api_disponible": self.client is not None,
        }
        self._quota_exhausted = False

    def _cache_path(self, prompt, namespace):
        payload = "\n".join((self.cache_version, self.model, namespace, prompt))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return self.cache_dir / namespace / f"{digest}.json"

    def _cache_get(self, prompt, namespace):
        path = self._cache_path(prompt, namespace)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _cache_set(self, prompt, namespace, value):
        if not isinstance(value, dict):
            return
        path = self._cache_path(prompt, namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)

    def _create_client(self):
        if not self.api_key:
            print("⚠️ GEMINI_API_KEY no configurada; se usará clasificación local de respaldo.")
            return None
        try:
            from google import genai
            from google.genai import types

            return genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=self.request_timeout_ms),
            )
        except Exception as exc:
            print(f"⚠️ No se pudo inicializar Google GenAI: {exc}")
            return None

    def _load_categories(self):
        if "gemini_categorias" not in self.config:
            return ["Otra tecnológica"]
        return [name.strip() for name in self.config["gemini_categorias"] if name.strip()]

    def _load_keywords(self, section):
        if section not in self.config:
            return []
        return [key.replace("_", " ").lower() for key in self.config[section]]

    @staticmethod
    def _clean_value(value):
        text = "" if value is None else str(value).strip()
        return "" if text.lower() in {"nan", "none", "notfound", "-1"} else text

    def _web_text(self, row):
        fields = [
            ("Título", "titulo"),
            ("Descripción", "descripcion"),
            ("Tipo de contrato", "tipo_contrato"),
            ("Código CPV", "codigo_cpv"),
            ("Órgano", "organo_contratacion"),
            ("Procedimiento", "procedimiento_contratacion"),
        ]
        parts = []
        for label, column in fields:
            value = self._clean_value(row.get(column))
            if value:
                parts.append(f"{label}: {value}")
        return "\n".join(parts)[: self.max_web_chars]

    def _pdf_text(self, pdf_name):
        pdf_name = self._clean_value(pdf_name)
        if not pdf_name:
            return ""
        path = self.pdf_dir / Path(pdf_name).name
        if not path.exists():
            print(f"⚠️ PDF no encontrado para análisis: {path}")
            return ""
        try:
            with fitz.open(path) as document:
                chunks = []
                total = 0
                for page in document:
                    text = page.get_text()
                    chunks.append(text)
                    total += len(text)
                    if total >= self.max_pdf_chars:
                        break
            return "".join(chunks)[: self.max_pdf_chars]
        except Exception as exc:
            print(f"⚠️ No se pudo leer {path}: {exc}")
            return ""

    @staticmethod
    def _parse_json(text):
        cleaned = (text or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Gemini no devolvió un objeto JSON")
        return json.loads(cleaned[start : end + 1])

    def _request(self, prompt, namespace):
        cached = self._cache_get(prompt, namespace)
        if cached is not None:
            self.stats["gemini_cache_reutilizadas"] += 1
            return cached
        if self.client is None or self._quota_exhausted:
            return None
        from google.genai import types

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self.stats["gemini_api_solicitudes"] += 1
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                parsed = self._parse_json(response.text)
                self._cache_set(prompt, namespace, parsed)
                return parsed
            except Exception as exc:
                last_error = exc
                print(f"⚠️ Gemini intento {attempt}/{self.max_retries}: {exc}")
                if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                    self._quota_exhausted = True
                    self.client = None
                    print("⚠️ Cuota de Gemini agotada; se usará el fallback local en el resto del lote.")
                    break
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
        print(f"❌ Gemini no disponible tras reintentos: {last_error}")
        return None

    def _web_prompt(self, text):
        categories = ", ".join(self.categories)
        return f"""
Eres especialista en contratación pública española. Analiza exclusivamente la
información web siguiente. Determina si el objeto principal de la licitación es
tecnológico. No clasifiques como tecnológica una obra o suministro convencional
solo porque mencione software o equipos de forma accesoria.

Categorías tecnológicas permitidas: {categories}.

Devuelve SOLO JSON válido con esta estructura:
{{
  "es_tecnologica": true,
  "categoria": "una categoría permitida o No tecnológica",
  "resumen_breve": "máximo 240 caracteres",
  "informacion_web_suficiente": true
}}

La información es suficiente si permite crear un resumen claro del objeto y
asignar la categoría sin consultar el pliego.

INFORMACIÓN WEB:
{text}
""".strip()

    def _pdf_prompt(self, web_text, pdf_text):
        categories = ", ".join(self.categories)
        return f"""
La información web de esta licitación tecnológica era insuficiente. Completa el
análisis con el extracto del pliego. Categorías permitidas: {categories}.

Devuelve SOLO JSON válido:
{{
  "categoria": "una categoría permitida",
  "resumen_breve": "máximo 240 caracteres"
}}

INFORMACIÓN WEB:
{web_text}

EXTRACTO DEL PLIEGO:
{pdf_text}
""".strip()

    def _fallback(self, web_text):
        normalized = web_text.lower()
        contains = lambda phrase: re.search(
            rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized
        )
        tech_hits = [word for word in self.tech_keywords if contains(word)]
        non_tech_hits = [word for word in self.non_tech_keywords if contains(word)]
        is_tech = bool(tech_hits) and not non_tech_hits
        if is_tech:
            category = "Otra tecnológica"
            category_rules = [
                ({"inteligencia artificial", "machine learning", "big data", "datos", "analitica", "data warehouse", "etl", "mineria de datos", "modelos predictivos", "vision artificial"}, "Datos e inteligencia artificial"),
                ({"ciberseguridad"}, "Ciberseguridad"),
                ({"cloud", "nube", "servidores", "virtualizacion", "hosting", "bases de datos", "infraestructura tecnologica"}, "Infraestructura, cloud y sistemas"),
                ({"redes"}, "Telecomunicaciones y redes"),
                ({"mantenimiento informatico"}, "Servicios y mantenimiento TI"),
                ({"software", "erp", "crm", "automatizacion", "robotizacion", "rpa", "bots"}, "Software y desarrollo"),
            ]
            available = set(self.categories)
            for keywords, candidate in category_rules:
                if candidate in available and keywords.intersection(tech_hits):
                    category = candidate
                    break
        else:
            category = "No tecnológica"
        description_match = re.search(
            r"^Descripción:\s*(.+)$", web_text, flags=re.IGNORECASE | re.MULTILINE
        )
        title_match = re.search(
            r"^Título:\s*(.+)$", web_text, flags=re.IGNORECASE | re.MULTILINE
        )
        plain = (
            description_match.group(1)
            if description_match
            else title_match.group(1) if title_match else web_text
        )
        plain = re.sub(r"\s+", " ", plain).strip()
        return {
            "es_tecnologica": is_tech,
            "categoria": category,
            "resumen_breve": plain[:237] + "..." if len(plain) > 240 else plain,
            "informacion_web_suficiente": True,
        }

    def _normalize_result(self, result, fallback):
        if not isinstance(result, dict):
            return fallback
        is_tech = result.get("es_tecnologica", fallback["es_tecnologica"])
        if isinstance(is_tech, str):
            is_tech = is_tech.strip().lower() in {"true", "sí", "si", "1"}
        category = self._clean_value(result.get("categoria"))
        if not is_tech:
            category = "No tecnológica"
        elif category not in self.categories:
            category = "Otra tecnológica"
        summary = self._clean_value(result.get("resumen_breve")) or fallback["resumen_breve"]
        sufficient = result.get("informacion_web_suficiente", True)
        if isinstance(sufficient, str):
            sufficient = sufficient.strip().lower() in {"true", "sí", "si", "1"}
        return {
            "es_tecnologica": bool(is_tech),
            "categoria": category,
            "resumen_breve": summary[:240],
            "informacion_web_suficiente": bool(sufficient),
        }

    def procesar_completo(self):
        summaries = []
        categories = []
        print(f"🤖 Clasificando {len(self.df)} licitaciones con {self.model}...")
        for position, (_, row) in enumerate(self.df.iterrows(), start=1):
            self.stats["gemini_requeridas"] += 1
            web_text = self._web_text(row)
            fallback = self._fallback(web_text)
            web_response = self._request(self._web_prompt(web_text), "web")
            if isinstance(web_response, dict):
                self.stats["gemini_analizadas"] += 1
            result = self._normalize_result(web_response, fallback)

            if result["es_tecnologica"] and not result["informacion_web_suficiente"]:
                pdf_text = self._pdf_text(row.get("pdf"))
                if pdf_text:
                    self.stats["pdf_analizados_gemini"] += 1
                    enriched = self._request(self._pdf_prompt(web_text, pdf_text), "pdf")
                    if isinstance(enriched, dict):
                        candidate = dict(result)
                        candidate.update(enriched)
                        candidate["es_tecnologica"] = True
                        result = self._normalize_result(candidate, fallback)

            summaries.append(result["resumen_breve"] or "Sin resumen disponible")
            categories.append(result["categoria"])
            print(f"  [{position}/{len(self.df)}] {result['categoria']}")

        self.df["resumen_breve"] = summaries
        self.df["clasificacion"] = categories
        return self.df
