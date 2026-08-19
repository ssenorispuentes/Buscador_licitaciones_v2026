# Resumen de cambios y diagnóstico del despliegue

Fecha de revisión: 19 de agosto de 2026.

## Situación

La aplicación desplegada en Streamlit Community Cloud permanecía sin cargar o mostraba indefinidamente el estado de preparación. El despliegue está conectado a:

- Repositorio: `ssenorispuentes/Buscador_licitaciones_v2026`
- Rama: `main`
- Archivo de entrada: `app.py`
- Aplicación: `https://buscadorlicitacionesv2026.streamlit.app/`

## Problemas encontrados

### 1. Dependencias de scraping instaladas en la aplicación

El `requirements.txt` original incluía dependencias pesadas que la carga inicial de `app.py` no necesita, entre ellas spaCy, gensim, Selenium y PyMuPDF. Estas dependencias corresponden principalmente al scraper ejecutado mediante GitHub Actions y pueden aumentar mucho el tiempo y la memoria del build de Streamlit Cloud.

Se separaron en dos archivos:

- `requirements.txt`: dependencias de la aplicación.
- `requirements-scraping.txt`: dependencias adicionales del scraper.

El workflow `.github/workflows/update-licitaciones.yml` se modificó para instalar `requirements-scraping.txt`.

### 2. Jinja2 no estaba declarado

Después de aligerar las dependencias, la aplicación fallaba realmente al ejecutar esta operación de `app.py`:

```python
df_style.style.format(formato_numerico).apply(resaltar_filas, axis=1)
```

El error reproducido fue:

```text
AttributeError: The '.style' accessor requires jinja2
```

Pandas necesita Jinja2 para utilizar `DataFrame.style`. Se añadió al archivo de dependencias:

```text
Jinja2>=3.1.2,<4
```

### 3. Pin innecesario de NumPy y posible incompatibilidad con Python de Cloud

`requirements.txt` fijaba `numpy==1.26.4`, pero `app.py` importaba NumPy sin utilizarlo. NumPy 1.26.4 no es una elección compatible con todos los runtimes modernos, especialmente Python 3.13. Si un despliegue nuevo usa esa versión de Python, el instalador puede intentar compilar NumPy o fallar durante el build.

Se eliminó:

- El import `import numpy as np` de `app.py`.
- El pin `numpy==1.26.4` de `requirements.txt`.

Pandas sigue instalando NumPy como dependencia y selecciona una versión compatible con el Python del entorno.

### 4. `runtime.txt` no controlaba el Python de Community Cloud

El repositorio contenía un `runtime.txt` con el valor `3.10`. Streamlit Community Cloud selecciona la versión de Python desde las opciones avanzadas del despliegue y no debe darse por hecho que respete este archivo. Se eliminó para evitar una falsa sensación de que Cloud estaba usando Python 3.10.

La versión de Python de la app alojada debe comprobarse o seleccionarse desde la configuración de Streamlit Community Cloud.

### 5. Mezcla entre identidades de GitHub

El remoto y la clave SSH ya apuntaban correctamente a `ssenorispuentes`, pero la identidad de autor de Git estaba heredada de la configuración global como `ssenoris`.

La configuración local del repositorio quedó establecida como:

```text
user.name  = ssenorispuentes
user.email = s.senoris.puentes@gmail.com
```

La autenticación SSH comprobada responde como `ssenorispuentes` y `origin` apunta a:

```text
git@github.com:ssenorispuentes/Buscador_licitaciones_v2026.git
```

Los commits antiguos conservan su autor original porque no se ha reescrito el historial de `main`.

## Commits realizados

```text
40753be Separar dependencias de la app de las del scraping
67d8df7 Añadir Jinja2 para estilos de pandas
b5778d0 Corregir compatibilidad del despliegue en Streamlit
```

El commit `b5778d0` ya utiliza el autor `ssenorispuentes <s.senoris.puentes@gmail.com>`.

## Pruebas realizadas

### Validaciones básicas

- Compilación de `app.py` y `src/functions.py` con `py_compile`.
- Importación de `app.py`.
- Lectura de `config/scraper_config.ini` y `config/scraper_columns.ini`.
- Lectura de `datos_licitaciones_final/licitaciones.csv`.
- CSV encontrado con 123 filas y 27 columnas.
- Confirmación de los commits publicados en `origin/main`.
- Confirmación de que el repositorio de GitHub es público.

### Primera prueba de servidor

Se levantó el servidor Streamlit y quedó escuchando correctamente. Esta prueba por sí sola no era suficiente, porque Streamlit puede levantar el servidor antes de ejecutar completamente la sesión de la aplicación.

### Prueba de ejecución con `AppTest`

Se utilizó `streamlit.testing.v1.AppTest`, que sí ejecuta la aplicación. Esta prueba descubrió el error de Jinja2. Después de declarar Jinja2:

- Excepciones: 0.
- Dataframes renderizados: 1.
- Resultado mostrado: 110 licitaciones disponibles.

### Entornos limpios empleados inicialmente

Las primeras pruebas limpias se crearon desde el entorno Conda `base`, que usa Python 3.9.12. Se probaron:

- Streamlit 1.46.0.
- Pandas 2.3.0.
- Jinja2 3.1.6.
- NumPy 1.26.4 antes de retirar el pin.
- NumPy 2.0.2 después de retirar el pin.

Ambas pruebas corregidas terminaron sin excepciones. No obstante, Python 3.9 no es el entorno local habitual del proyecto.

### Prueba en el entorno Conda correcto: `streamlit`

Posteriormente se comprobó y ejecutó expresamente el entorno indicado para el proyecto:

```text
Entorno:    /home/sara/anaconda3/envs/streamlit
Python:     3.10.18
Streamlit:  1.61.1
Pandas:     2.3.0
NumPy:      1.24.4
Jinja2:     3.1.2
Unidecode:  1.4.0
```

Resultado de `AppTest` en este entorno:

```text
Excepciones: 0
Dataframes:  1
Resultado:   110 licitaciones disponibles
```

Por tanto, la aplicación funciona con Python 3.10.18 en el entorno Conda `streamlit`.

## Diferencias de versión relevantes

El entorno Conda local tiene Streamlit 1.61.1, pero `requirements.txt` fija Streamlit 1.46.0 para Cloud. La app ha sido probada satisfactoriamente tanto con Streamlit 1.46.0 en un entorno limpio como con Streamlit 1.61.1 en el entorno Conda `streamlit`.

La diferencia de Python sí podía afectar al build anterior por el pin de NumPy 1.26.4. Al retirar ese pin innecesario, el instalador puede resolver una versión compatible. Aun así, conviene seleccionar Python 3.10 o 3.12 explícitamente en las opciones avanzadas de Streamlit Cloud para acercar Cloud al entorno validado.

## Estado y limitación del diagnóstico remoto

El código actual se ejecuta correctamente en las pruebas completas disponibles. La URL pública devuelve el frontend de Streamlit con HTTP 200 usando el modo embebido. Sin acceso a los logs internos del propietario no se puede afirmar si queda un fallo del build, del backend o de la sesión WebSocket de Community Cloud.

Si continúa bloqueada, el siguiente dato necesario es copiar el primer error completo de **Manage app → Logs**. Ese traceback permitirá distinguir un problema del código de un problema del servicio o de configuración del runtime.

## Configuración recomendada del despliegue

Al crear o revisar la aplicación en Community Cloud:

```text
Repository:     ssenorispuentes/Buscador_licitaciones_v2026
Branch:         main
Main file path: app.py
Python:         3.10 o 3.12
```

Después de cambiar la versión de Python puede ser necesario eliminar y volver a desplegar la aplicación, ya que Community Cloud no siempre permite cambiar el Python de un despliegue existente en el mismo entorno.
