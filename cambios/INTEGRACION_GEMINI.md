# Integración de Gemini

Punto de restauración previo: `ceea421`.

## Cambios

- Sustitución del procesamiento LDA por `gemini-3.6-flash`.
- Clasificación inicial usando únicamente texto web.
- Lectura condicional del PDF solo para licitaciones tecnológicas con información insuficiente.
- Respuesta estructurada con categoría y resumen breve.
- Categorías editables en `config/scraper_config.ini`.
- Timeout, reintentos y fallback local si Google AI Studio no está disponible.
- Esquema final validado por nombre con exactamente 17 columnas.
- Nombres de PDF con identificador estable derivado de fuente, expediente y URL.
- Timeout del fallback estatal ampliado de 3 a 7 segundos.
- Corrección del log invertido de PDFs del scraper estatal.
- Eliminación de spaCy, gensim y NLTK del pipeline.

## Secreto requerido en GitHub

Crear en **Settings → Secrets and variables → Actions**:

```text
GEMINI_API_KEY
```

El valor debe ser la clave de Google AI Studio. El archivo local `.env` no se sube a GitHub.
