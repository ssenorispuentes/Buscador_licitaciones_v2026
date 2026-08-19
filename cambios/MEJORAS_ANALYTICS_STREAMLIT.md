# Analítica local y renovación de Streamlit

## Métricas por ejecución

Al finalizar el pipeline, `main_scraping.py` guarda en `analytics/`:

- Un JSON independiente con el detalle de la ejecución.
- Una fila acumulada en `analytics/historico_ejecuciones.csv`.

Se registran fecha y hora de inicio y fin, duración, total de licitaciones, PDF
descargados, licitaciones para las que el pipeline pidió una clasificación,
licitaciones que recibieron realmente una respuesta del modelo, PDF leídos
para completar esa respuesta y disponibilidad de la API. La carpeta `/analytics/` está incluida
en `.gitignore`; sus datos son locales y nunca deben añadirse al repositorio.

En el fichero técnico, `gemini_requeridas` significa «licitaciones que el
pipeline quería enviar al modelo» y `gemini_analizadas` significa «licitaciones
para las que se obtuvo una respuesta válida de Gemini, nueva o desde caché».
La diferencia permite detectar falta de clave, cuota agotada o errores de Google
sin confundir el fallback local con un análisis realizado por IA. Estas métricas
son de diagnóstico y no se muestran en la web pública.

## Interfaz

`app.py` incorpora una cabecera permanente con fecha y hora del último scraping,
tarjetas resumen, histórico local y filtros explícitos para:

- Número de expediente y palabras clave.
- Importe mínimo y máximo.
- Fecha límite igual o posterior a la fecha seleccionada.
- Rango de valor estimado mediante slider.
- Tipo de contrato y fuente.
- Todas, tecnológicas o no tecnológicas, con un segundo selector opcional de
  etiquetas tecnológicas.

La tabla muestra el nombre del PDF y una columna `URL` clicable que abre la ficha
oficial. El esquema final pasa de 17 a 18 columnas para conservar ese enlace.

El estado visible se normaliza dinámicamente. Los estados administrativos
definitivos (anulada, adjudicada, formalizada, desierta, desistida o suspendida)
tienen prioridad. Para el resto, una fecha límite igual o posterior a hoy se
muestra como `En plazo` y una fecha anterior como `Fuera de plazo`. Si falta la
fecha, se conserva `Abierta` o `Publicada` indicando `plazo sin confirmar`.

## Validación del 19/08/2026

Se reutilizaron los ficheros producidos por la prueba real de los cuatro
scrapers, sin repetir las consultas a los portales. El pipeline completo generó
120 licitaciones finales, 120 URL válidas y 72 PDF asociados. La validación se
ejecutó sin clave de Gemini para comprobar el fallback y produjo correctamente
las métricas locales: 120 análisis requeridos y 0 completados por la API.

También se corrigió la reutilización de PDF: si una ejecución anterior ya había
renombrado el fichero con su identificador estable, una nueva ejecución lo
reconoce en lugar de marcarlo erróneamente como no disponible.

## Caché de Gemini

Las respuestas válidas del modelo se guardan en `.gemini_cache/`, directorio
excluido de Git. La clave de caché combina versión, modelo, fase web/PDF y un
SHA-256 del texto enviado. Una licitación sin cambios reutiliza su respuesta;
si cambia el contenido o el modelo, se consulta de nuevo automáticamente.

El fallback y los errores de API no se guardan. GitHub Actions restaura la caché
de ejecuciones anteriores con `actions/cache`, evitando gastar solicitudes en
licitaciones que Gemini ya analizó. Las métricas distinguen respuestas
reutilizadas (`gemini_cache_reutilizadas`) y peticiones nuevas
(`gemini_api_solicitudes`).
