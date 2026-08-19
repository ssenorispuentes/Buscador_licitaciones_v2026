# Buscador de licitaciones

Aplicación Streamlit y pipeline de scraping para consultar licitaciones públicas.

## Ejecución local de la app

```bash
conda activate streamlit
streamlit run app.py
```

## Procesamiento con Gemini

El scraping clasifica primero el texto disponible en la web con el modelo
configurado en `[gemini]` dentro de `config/scraper_config.ini`. Solo lee el
pliego PDF cuando la licitación es tecnológica y la información web resulta
insuficiente.

Para ejecutar el pipeline localmente, crea un `.env` no versionado:

```env
GEMINI_API_KEY=tu_clave
```

```bash
python main_scraping.py --usar_scraping
```

En GitHub Actions debe configurarse el secreto de repositorio
`GEMINI_API_KEY`. Si la clave o la API no están disponibles, el proceso utiliza
un clasificador local de respaldo y conserva la generación del CSV.

Las categorías se pueden ampliar o cambiar editando únicamente la sección
`[gemini_categorias]` de `config/scraper_config.ini`.

## Pruebas

```bash
python -m unittest discover -s tests -v
```
