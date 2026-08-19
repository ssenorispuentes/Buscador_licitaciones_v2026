# Soporte multilingüe, CPV e IVA

## Cambios

- El esquema final contiene 20 columnas y comienza por `Nº Expediente` y
  `Código CPV`.
- Se conserva el importe base sin IVA y se añade `Importe con IVA (€)`. No se
  calcula un IVA supuesto: si el portal no publica el total o un tipo aplicable,
  se muestra `-`.
- `src/functions.py` coalesce etiquetas equivalentes en español, inglés,
  catalán/valenciano y euskera antes de aplicar el mapeo específico del portal.
- El parser de fechas reconoce meses españoles, ingleses y catalanes, además de
  formatos numéricos con o sin hora.
- La detección de documentos usa términos multilingües centralizados en
  `src/document_keywords.py`. España, Andalucía, Euskadi y Madrid pueden guardar
  el primer pliego reconocido con un identificador estable.
- Streamlit presenta los importes como euros, `K €` o `M €`, pero conserva los
  valores `float` originales para filtros y descargas.

## Validación

La prueba completa de los cuatro scrapers del 19/08/2026 generó 111 registros y
20 columnas: 111 expedientes, 107 con CPV, 60 con importe con IVA, 107 con valor
estimado, 111 URL y 103 documentos asociados. Andalucía produjo 30 registros,
España 47, Euskadi 4 y Madrid 30. La prueba se ejecutó sin Gemini para validar
exclusivamente extracción, normalización, documentos y presentación.

La ficha de prueba de Jumilla se comprobó directamente y publica los valores
`1128780A`, `CON/45/25`, CPV `45216111`, presupuesto sin impuestos
`1.782.751,93 €` y enlaces identificados como `Pliego` / `Veure documents`. No
publica un importe total con IVA, por lo que esa celda debe mostrarse como `-`.
