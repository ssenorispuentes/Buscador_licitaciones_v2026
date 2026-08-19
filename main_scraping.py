import os
import pandas as pd
import src.functions as functions
from src.gemini_processor import LicitacionGeminiProcessor
import configparser
from web_scraping.WS_andalucia import ScraperAndalucia
from web_scraping.WS_espana import ScraperEspana
from web_scraping.WS_euskadi import ScraperEuskadi
from web_scraping.WS_madrid import ScraperMadrid
from datetime import datetime, timedelta
import time
import sys

def main(fecha_proceso = None, usar_scraping = True):
    # 🕒 Inicio de ejecución y redirección de salida
    start_time = time.time()
    log_filename = f"logs/ejecucion_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    os.makedirs("logs", exist_ok=True)

    original_stdout = sys.stdout  # Guarda la salida original

    with open(log_filename, "w", encoding="utf-8") as log_file:
        sys.stdout = log_file  # Redirige print() al archivo
        # Cargar configs
        config_path = "./config/scraper_config.ini"
        columns_path = "./config/scraper_columns.ini"
        config = configparser.ConfigParser()
        config.read(config_path)
        columns_ini = configparser.ConfigParser()
        columns_ini.read(columns_path)
        filename_codigo_nuts = config.get("input_output_path", "filename_codigo_nuts")
        output_dir = config.get("input_output_path", "output_dir_final", fallback="./output_final")
        output_dir_pdf = config.get("input_output_path", "output_dir_pdf", fallback="./pdfs")
        dias_fecha_min = int(config.get("all_params", "dias_fecha_min"))
        os.makedirs(output_dir, exist_ok=True)

        # Extraer columnas finales y por comunidad
        columnas_finales = functions.get_columns_dict(columns_ini["final_columns_order"])
        columns_and = functions.get_columns_dict(columns_ini["and_columns_order"])
        columns_esp = functions.get_columns_dict(columns_ini["esp_columns_order"])
        columns_eus = functions.get_columns_dict(columns_ini["eus_columns_order"])
        columns_mad = functions.get_columns_dict(columns_ini["mad_columns_order"])
        hoy = datetime.today()
        fecha_ejecucion = fecha_proceso if fecha_proceso else hoy.date()
        print(f'fecha ejecucion {fecha_ejecucion}')

        if usar_scraping:
            fecha_minima = hoy + timedelta(days=dias_fecha_min)
            df_and = df_esp = df_eus = df_mad = None
            # Ejecutar scrapers
            print("🟢 Ejecutando scraper Andalucía...")
            try:
                df_and = ScraperAndalucia(fecha=fecha_ejecucion,
                                          fecha_minima=fecha_minima,
                                          config_file=config_path).ejecutar()
                print("✅ Scraper Andalucía completado!")
            except Exception as e:
                print(f"⚠️ Error en scraper Andalucía: {e}")

            print("🟢 Ejecutando scraper Estado...")
            try:
                df_esp = ScraperEspana(fecha=fecha_ejecucion,
                                       config_file=config_path).ejecutar()
                print("✅ Scraper España completado!")
            except Exception as e:
                print(f"⚠️ Error en scraper Estado: {e}")

            print("🟢 Ejecutando scraper Euskadi...")
            try:
                df_eus = ScraperEuskadi(fecha=fecha_ejecucion,
                                        fecha_minima=fecha_minima,
                                        config_file=config_path).ejecutar()
                print("✅ Scraper Euskadi completado!")
            except Exception as e:
                print(f"⚠️ Error en scraper Euskadi: {e}")

            print("🟢 Ejecutando scraper Madrid...")
            try:
                df_mad = ScraperMadrid(fecha=fecha_ejecucion,
                                       config_file=config_path,
                                       fecha_minima=fecha_minima).ejecutar()
                print("✅ Scraper Madrid completado!")
            except Exception as e:
                print(f"⚠️ Error en scraper Madrid: {e}")
        else:
            print(f"🟢 Leyendo ficheros de licitaciones...")
            # 🟠 Leer datos desde CSVs en carpeta de datos
            input_dir = config.get("input_output_path", "output_dir", fallback="./datos")
            df_and = df_esp = df_eus = df_mad = None
            try:
                print("🟢 Fichero Andalucía...")
                df_and = functions.leer_fichero_licitaciones(input_dir = input_dir,
                                                            comunidad = 'andalucia',
                                                            sep = '\t',
                                                            fecha_proceso = fecha_proceso)

            except Exception as e:
                print(f"⚠️ Error cargando Andalucía: {e}")

            try:
                print("🟢 Fichero España...")
                df_esp = functions.leer_fichero_licitaciones(input_dir = input_dir, 
                                                            comunidad = 'espana', 
                                                            sep = '\t',
                                                            fecha_proceso = fecha_proceso)
            except Exception as e:
                print(f"⚠️ Error cargando España: {e}")

            try:
                print("🟢 Fichero Euskadi...")
                df_eus = functions.leer_fichero_licitaciones(input_dir = input_dir,
                                                            comunidad = 'euskadi',
                                                            sep ="\t",
                                                            fecha_proceso = fecha_proceso)
            except Exception as e:
                print(f"⚠️ Error cargando Euskadi: {e}")

            try:
                print("🟢 Fichero Madrid...")
                df_mad = functions.leer_fichero_licitaciones(input_dir = input_dir,
                                                    comunidad = 'madrid',
                                                    sep ="\t",
                                                    fecha_proceso = fecha_proceso)
            except Exception as e:
                print(f"⚠️ Error cargando Madrid: {e}")

        # Filtrar, renombrar y añadir info
        df_and_final = df_esp_final = df_eus_final = df_mad_final = None

        if df_and is not None:
            print("🔹 Filtrando y renombrando DataFrame Andalucía...")
            df_and_final = functions.filtrar_renombrar_dataframe(df_and, "and",filename_codigo_nuts, columnas_finales, columns_and, fecha_ejecucion)
            print(f"✅ Andalucía procesada: {df_and_final.shape[0]} registros")

        if df_esp is not None:
            print("🔹 Filtrando y renombrando DataFrame Estado...")
            df_esp_final = functions.filtrar_renombrar_dataframe(df_esp, "esp",filename_codigo_nuts, columnas_finales, columns_esp, fecha_ejecucion)
            print(f"✅ Estado procesado: {df_esp_final.shape[0]} registros")
            
        if df_eus is not None:
            print("🔹 Filtrando y renombrando DataFrame Euskadi...")
            df_eus_final = functions.filtrar_renombrar_dataframe(df_eus, "eus",filename_codigo_nuts, columnas_finales, columns_eus, fecha_ejecucion)
            print(f"✅ Euskadi procesado: {df_eus_final.shape[0]} registros")

        if df_mad is not None:
            print("🔹 Filtrando y renombrando DataFrame Madrid...")
            df_mad_final = functions.filtrar_renombrar_dataframe(df_mad, "mad",filename_codigo_nuts, columnas_finales, columns_mad, fecha_ejecucion)
            print(f"✅ Madrid procesado: {df_mad_final.shape[0]} registros")

        # Unificar
        print("🔹 Unificando los DataFrames de las distintas comunidades...")

        # Filtra los DataFrames que no sean None antes de concatenar
        dfs_a_unir = [df for df in [df_and_final, df_esp_final, df_eus_final, df_mad_final] if df is not None]

        if dfs_a_unir:
            print("🔹 Unificando información...")
            print(f'uniendo {len(dfs_a_unir)} dataframes...')
            for df_ in dfs_a_unir:
                print(f'shape dataset {df_.shape}')
            df_unificado = pd.concat(dfs_a_unir, ignore_index=True)
            # df_unificado = functions.combinar_duplicados_por_expediente(df_unificado, col_exp = 'numero_expediente')
            print(f"✅ Unificación completada. Total registros: {df_unificado.shape[0]}")
        else:
            raise RuntimeError(
                "No se obtuvo ninguna licitación; se conserva el CSV anterior."
            )

        df_unificado = df_unificado.dropna(subset=["titulo"]).reset_index(drop=True)
        df_unificado = functions.asegurar_identificador_en_pdfs(
            df_unificado, output_dir_pdf
        )

        # Clasificar primero con texto web; Gemini solo lee el PDF cuando hace falta.
        print("🟢 Clasificación y resumen con Gemini...")
        processor = LicitacionGeminiProcessor(
            df_unificado, config_file="./config/scraper_config.ini"
        )
        df_final = processor.procesar_completo()
        df_final = functions.construir_salida_final(df_final)

        # Guardar
        output_file = os.path.join(output_dir, f"licitaciones.csv")
        df_final[df_final.select_dtypes(include=['object']).columns] = df_final.select_dtypes(include=['object']).fillna('NotFound')
        df_final[df_final.select_dtypes(include=['float','int']).columns] = df_final.select_dtypes(include=['float','int']).fillna(-1)

        df_final = df_final.loc[:, ~df_final.columns.str.contains('^Unnamed')]
        df_final.to_csv(output_file, index=False,sep="\t", encoding="utf-8-sig")
        print(f"✅ Archivo final de licitaciones guardado en: {output_file}")
        end_time = time.time()
        elapsed = end_time - start_time
        print(f"⏱ Tiempo total de ejecución: {elapsed:.2f} segundos")
    sys.stdout = original_stdout  # Restaura salida original
    print(f"✅ Ejecución finalizada. Log guardado en: {log_filename}")

import argparse
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraping o carga de licitaciones")
    parser.add_argument(
        "fecha_proceso",
        nargs="?",
        default=None,
        help="Fecha del proceso en formato YYYY-MM-DD (opcional, se usará la máxima disponible si no se indica)"
    )
    parser.add_argument(
        "--usar_scraping",
        action="store_true",
        help="Ejecutar scraping en lugar de leer archivos existentes"
    )

    args = parser.parse_args()

    main(fecha_proceso=args.fecha_proceso,
         usar_scraping=args.usar_scraping)

#python main_scraping.py                  No hace scraping, lee ficheros con fecha más actualizada
#python main_scraping.py 2024-06-01       No hace scraping, lee ficheros con fecha la que se le pasa
#python main_scraping.py --usar_scraping  Hace scraping

