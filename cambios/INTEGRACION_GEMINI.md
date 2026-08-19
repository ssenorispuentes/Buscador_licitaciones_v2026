# Sustitución de LDA por Gemini

Punto de restauración anterior al cambio: commit `ceea421`. Integración de
Gemini: commit `3c0f206`.

## Qué se cambió y dónde

- `main_scraping.py`: se eliminó la llamada al procesador LDA y ahora, después
  de unificar y normalizar los resultados de los cuatro portales, crea un
  `LicitacionGeminiProcessor` y ejecuta `procesar_completo()`.
- `src/gemini_processor.py`: contiene todo el análisis con Google AI Studio.
  Este archivo reemplaza al antiguo `src/lda_processor.py`, que fue eliminado.
- `config/scraper_config.ini`: define el modelo, límites de texto, timeout,
  reintentos, categorías permitidas y palabras del mecanismo de respaldo.
- `requirements-scraping.txt`: incorpora `google-genai`, `python-dotenv` y
  `PyMuPDF`; ya no necesita `gensim`, `spaCy` ni `NLTK`.
- `.github/workflows/scraping.yml`: entrega al proceso el secreto
  `GEMINI_API_KEY` guardado en GitHub Actions.

También se validó la salida final por nombre para impedir el antiguo problema
de desplazamiento entre valores y columnas. Los PDF descargados reciben un
identificador estable formado a partir de la fuente, el expediente y la URL.
En `WS_espana.py` se aumentó de 3 a 7 segundos la espera del fallback y se
corrigió el mensaje invertido que decía que no había pliego aun habiéndose
descargado correctamente.

## Flujo de trabajo

1. Los scrapers de Andalucía, España, Euskadi y Madrid obtienen los datos web y,
   cuando están disponibles, descargan los PDF.
2. `main_scraping.py` adapta cada portal al esquema común, concatena los
   DataFrames y asegura que el nombre del PDF contenga el identificador único.
3. Para cada licitación, `_web_text()` compone un texto con título, descripción,
   tipo de contrato, CPV, órgano y procedimiento. Se limita a 8.000 caracteres.
4. Ese texto se envía a `gemini-3.6-flash`. Se exige una respuesta JSON con:
   `es_tecnologica`, `categoria`, `resumen_breve` e
   `informacion_web_suficiente`.
5. Si Gemini decide que no es tecnológica, no se abre el pliego y se asigna
   `No tecnológica`.
6. Si es tecnológica y la web es suficiente, se conservan directamente la
   categoría y el resumen devueltos.
7. Solo si es tecnológica y la web es insuficiente se extraen hasta 24.000
   caracteres del PDF con PyMuPDF y se hace una segunda petición para completar
   categoría y resumen.
8. Finalmente, `construir_salida_final()` ordena y completa por nombre las 18
   columnas de salida antes de guardar el CSV tabulado.

Este diseño evita enviar todos los pliegos a Google y reduce tiempo, lecturas de
PDF y consumo de tokens.

## Cómo se clasifican las licitaciones

Gemini recibe instrucciones para decidir si el objeto **principal** del contrato
es tecnológico. Una obra o suministro convencional no debe convertirse en
tecnológico solo porque mencione software o equipos de manera accesoria.

Si es tecnológica, debe elegir exactamente una de las categorías declaradas en
`[gemini_categorias]` de `config/scraper_config.ini`:

- Software y desarrollo
- Datos e inteligencia artificial
- Infraestructura, cloud y sistemas
- Ciberseguridad
- Telecomunicaciones y redes
- Servicios y mantenimiento TI
- Hardware y equipamiento tecnológico
- Otra tecnológica

Las categorías pueden ampliarse o cambiarse editando esa sección. El código
normaliza la respuesta: una categoría desconocida se convierte en `Otra
tecnológica`, y una licitación no tecnológica siempre queda como `No
tecnológica`.

Si falta la clave o Google no responde después de tres intentos, el proceso no
se interrumpe: usa un fallback determinista. Este busca palabras completas de
`[palabras_clave_tecnologia]`, descarta coincidencias de
`[palabras_descarte_tecnologia]` y asigna una categoría mediante reglas locales.
El fallback es una medida de continuidad, no un segundo modelo de IA.

## Columnas creadas con el modelo

Gemini se utiliza exactamente para crear estas dos columnas finales:

1. `resumen_breve`: resumen del objeto del contrato, limitado a 240 caracteres.
2. `clasificacion`: `No tecnológica` o una categoría tecnológica permitida.

Los campos auxiliares `es_tecnologica` e `informacion_web_suficiente` solo
controlan el flujo dentro del procesador y **no se guardan** como columnas. Las
otras columnas finales —estado, fechas, importes, título, expediente, órgano,
tipo, lugar, duración, financiación, presentación, fuente, PDF y fecha del
proceso— proceden del scraping y del normalizado, no de Gemini. La URL de la
ficha oficial también procede directamente del scraper.

## Configuración de la clave

En local se lee `GEMINI_API_KEY` desde `.env`, archivo ignorado por Git. En
GitHub debe existir como secreto en **Settings → Secrets and variables →
Actions**, con este nombre exacto:

```text
GEMINI_API_KEY
```

La clave nunca debe escribirse en el código ni subirse al repositorio.

## Resultado de la prueba real del 19/08/2026

Se ejecutó una única prueba local con `python main_scraping.py
--usar_scraping` dentro del entorno Conda `streamlit`. Los cuatro portales
terminaron su extracción y generaron estos ficheros intermedios:

- Andalucía: 30 licitaciones.
- España: 101 licitaciones; se descargaron 42 PDF de 56 fichas que ofrecían
  candidatos a pliego durante el recorrido.
- Euskadi: 4 licitaciones.
- Madrid: 30 licitaciones.

La suma de los cuatro ficheros intermedios fue de 165 registros. La fase de
Gemini comenzó a procesar el conjunto, pero la clave de prueba devolvió
`429 RESOURCE_EXHAUSTED`: el nivel gratuito
permitía 20 peticiones diarias para `gemini-3.6-flash`. Por tanto, la prueba se
detuvo para no repetir peticiones ni sobrescribir el CSV final que ya estaba
actualizado en GitHub.

Esto confirma que el scraping funciona, pero también que una ejecución completa
no puede clasificar todas las licitaciones con una cuota diaria de 20 llamadas.
Para ejecutar el lote íntegro con Gemini hace falta ampliar la cuota/facturación,
reducir el número de registros enviados, procesarlos en varios días o incorporar
una caché que no vuelva a consultar expedientes ya analizados. Mientras la API
no está disponible, el código conserva el fallback local descrito anteriormente.
