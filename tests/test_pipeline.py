import os
import tempfile
import unittest
from datetime import date

import pandas as pd

from src.functions import (
    FINAL_OUTPUT_COLUMNS,
    asegurar_identificador_en_pdfs,
    construir_salida_final,
    normalizar_columnas_multilingues,
    parsear_fechas_inteligente,
)
from src.gemini_processor import LicitacionGeminiProcessor, extraer_paginas_clave_pdf
from src.analytics import guardar_metricas
from src.document_keywords import download_document, find_document_links, matches_document_text
from app import (
    aplicar_filtros, cargar_cache_analisis, construir_dossier, construir_ficha_html,
    formatear_importe_compacto, guardar_cache_analisis, normalizar_estado,
    seleccionar_registros_preferentes,
)


class PipelineTests(unittest.TestCase):
    def test_tabla_alternativa_de_pliegos_detecta_enlaces_sin_texto(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("""
            <table id="myTablaDetallePliegosPlatAgreVISUOE"><tr><td>
              <a title="Este documento se abrirá en una nueva ventana"
                 href="https://example.test/documento/123"></a>
            </td></tr></table>
        """, "html.parser")
        self.assertEqual(
            find_document_links(soup, "https://example.test/ficha"),
            ["https://example.test/documento/123"],
        )

    def test_descarga_documental_rechaza_html_y_no_crea_pdf(self):
        class Response:
            headers = {"Content-Type": "text/html"}
            def raise_for_status(self):
                return None
            def iter_content(self, _):
                yield b"<html><body>No es un PDF</body></html>"
        class Session:
            def get(self, *args, **kwargs):
                return Response()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "no devolvió un PDF real"):
                download_document(Session(), "https://example.test", directory, "falso.pdf")
            self.assertFalse(os.path.exists(os.path.join(directory, "falso.pdf")))

    def test_descarga_documental_acepta_firma_pdf_sin_content_type(self):
        class Response:
            headers = {}
            def raise_for_status(self):
                return None
            def iter_content(self, _):
                yield b"%PDF-1.7\ncontenido"
        class Session:
            def get(self, *args, **kwargs):
                return Response()
        with tempfile.TemporaryDirectory() as directory:
            result = download_document(
                Session(), "https://example.test", directory, "valido.pdf"
            )
            self.assertEqual(result, "valido.pdf")
            with open(os.path.join(directory, result), "rb") as document:
                self.assertTrue(document.read().startswith(b"%PDF"))

    def test_ficha_muestra_expediente_completo_sin_ellipsis(self):
        expediente = "EXPEDIENTE-MUY-LARGO-2026/000000000123456789"
        card = construir_ficha_html(pd.Series({
            "Nº Expediente": expediente, "Título": "Servicio tecnológico",
            "Estado": "En plazo", "Fecha Límite Presentación": "2026-09-30",
        }))
        self.assertIn(expediente, card)
        self.assertNotIn("text-overflow", card)
        self.assertNotIn("white-space:nowrap", card.replace(" ", ""))

    def test_selector_prefiere_duplicado_con_pdf_local(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_name = "pliego.pdf"
            with open(os.path.join(directory, pdf_name), "wb") as document:
                document.write(b"%PDF-1.4\n")
            source = pd.DataFrame([
                {"Nº Expediente": "EXP-1", "Fuente": "Andalucía", "PDF / Ruta": pdf_name},
                {"Nº Expediente": "EXP-1", "Fuente": "España", "PDF / Ruta": "No disponible"},
            ])
            selected = seleccionar_registros_preferentes(source, directory)
            self.assertEqual(len(selected), 1)
            self.assertEqual(next(iter(selected.values()))["Fuente"], "Andalucía")

    def test_clasificacion_local_no_inicializa_ni_invoca_gemini(self):
        source = pd.DataFrame([{"titulo": "Desarrollo de software de gestión"}])
        processor = LicitacionGeminiProcessor(source, usar_gemini=False)
        result = processor.procesar_completo()
        self.assertEqual(result.loc[0, "clasificacion"], "Software y desarrollo")
        self.assertEqual(processor.stats["gemini_api_solicitudes"], 0)
        self.assertFalse(processor.stats["gemini_api_disponible"])

    def test_smart_chunking_prioriza_paginas_clave_y_limita_tamano(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pliego.pdf")
            import fitz
            document = fitz.open()
            document.new_page().insert_text((72, 72), "PORTADA DEL EXPEDIENTE")
            document.new_page().insert_text(
                (72, 72), "Solvencia tecnica: se requieren proyectos similares."
            )
            document.new_page().insert_text((72, 72), "Anexo administrativo repetitivo")
            document.save(path)
            document.close()
            result = extraer_paginas_clave_pdf(path, max_caracteres=120)
            self.assertIn("Solvencia tecnica", result)
            self.assertNotIn("PORTADA", result)
            self.assertLessEqual(len(result), 120)

    def test_smart_chunking_incluye_paginas_vecinas_y_completa_contexto(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pliego_amplio.pdf")
            import fitz
            document = fitz.open()
            for text in (
                "Introducción general del servicio y antecedentes.",
                "Detalle previo necesario para entender la sección.",
                "Criterios de adjudicacion y valoración de ofertas.",
                "Continuación de las reglas y condiciones aplicables.",
                "Contenido adicional sobre ejecución y entregables.",
            ):
                document.new_page().insert_text((72, 72), text)
            document.save(path)
            document.close()
            result = extraer_paginas_clave_pdf(path, max_caracteres=2000)
            self.assertIn("Detalle previo", result)
            self.assertIn("Criterios de adjudicacion", result)
            self.assertIn("Continuación", result)
            self.assertIn("Contenido adicional", result)

    def test_cache_por_expediente_y_dossier_incluyen_chat(self):
        row = pd.Series({"Nº Expediente": "EXP/1", "Título": "Plataforma"})
        data = {
            "analisis": {"resumen": "Resumen guardado"},
            "chat": [{"pregunta": "¿Plazo?", "respuesta": "Doce meses"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            guardar_cache_analisis(row, data, directory)
            self.assertEqual(cargar_cache_analisis(row, directory), data)
        dossier = construir_dossier(row, data)
        self.assertIn("Resumen guardado", dossier)
        self.assertIn("¿Plazo?", dossier)
        self.assertIn("Doce meses", dossier)

    def test_esquema_final_tiene_exactamente_20_columnas(self):
        source = pd.DataFrame(
            [{
                "estado_licitacion": "Publicada",
                "fecha_limite_presentacion": "2026-09-01",
                "titulo": "Plataforma de datos",
                "numero_expediente": "EXP-1",
                "codigo_cpv": "45216111",
                "importe_con_iva": 1210,
                "enlace": "https://example.test/exp-1",
                "provincia_ejecucion": "Madrid",
                "comunidad_autonoma_ejecucion": "Comunidad de Madrid",
            }]
        )
        result = construir_salida_final(source)
        self.assertEqual(result.columns.tolist(), FINAL_OUTPUT_COLUMNS)
        self.assertEqual(result.shape[1], 20)
        self.assertEqual(result.columns[:2].tolist(), ["numero_expediente", "codigo_cpv"])
        self.assertEqual(result.loc[0, "enlace"], "https://example.test/exp-1")
        self.assertEqual(
            result.loc[0, "lugar_ejecucion"], "Madrid / Comunidad de Madrid"
        )

    def test_fallback_no_confunde_it_con_licitacion(self):
        source = pd.DataFrame(
            [{"titulo": "Asistencia técnica para rehabilitación de un edificio"}]
        )
        processor = LicitacionGeminiProcessor(source)
        processor.client = None
        result = processor.procesar_completo()
        self.assertEqual(result.loc[0, "clasificacion"], "No tecnológica")

    def test_cache_gemini_reutiliza_solo_respuestas_validas(self):
        source = pd.DataFrame([{"titulo": "Plataforma de datos"}])
        with tempfile.TemporaryDirectory() as directory:
            processor = LicitacionGeminiProcessor(source)
            processor.cache_dir = os.path.join(directory, "cache")
            # Path también acepta una ruta reasignada durante pruebas/configuración.
            from pathlib import Path
            processor.cache_dir = Path(processor.cache_dir)
            prompt = "contenido estable"
            response = {"es_tecnologica": True, "categoria": "Software y desarrollo"}
            processor._cache_set(prompt, "web", response)
            self.assertEqual(processor._cache_get(prompt, "web"), response)
            self.assertIsNone(processor._cache_get(prompt + " modificado", "web"))
            processor.client = None
            self.assertEqual(processor._request(prompt, "web"), response)
            self.assertEqual(processor.stats["gemini_cache_reutilizadas"], 1)

    def test_pdf_se_renombra_con_identificador(self):
        with tempfile.TemporaryDirectory() as directory:
            old_name = "pliego.pdf"
            with open(os.path.join(directory, old_name), "wb") as pdf:
                pdf.write(b"%PDF-1.4\n")
            source = pd.DataFrame(
                [{
                    "fuente": "España",
                    "numero_expediente": "EXP/2026-1",
                    "enlace": "https://example.test/1",
                    "pdf": old_name,
                }]
            )
            result = asegurar_identificador_en_pdfs(source, directory)
            new_name = result.loc[0, "pdf"]
            self.assertIn("espana-exp-2026-1", new_name)
            self.assertTrue(os.path.isfile(os.path.join(directory, new_name)))
            self.assertFalse(os.path.exists(os.path.join(directory, old_name)))
            repeated = asegurar_identificador_en_pdfs(source, directory)
            self.assertEqual(repeated.loc[0, "pdf"], new_name)

    def test_filtros_fecha_y_clasificacion_dual(self):
        source = pd.DataFrame([
            {"Nº Expediente": "A-1", "Fecha Límite Presentación": "2026-09-01",
             "Clasificación": "Software y desarrollo", "Importe (€)": 100,
             "Valor estimado contrato (€)": 150},
            {"Nº Expediente": "B-2", "Fecha Límite Presentación": "2026-08-01",
             "Clasificación": "No tecnológica", "Importe (€)": 200,
             "Valor estimado contrato (€)": 250},
        ])
        result = aplicar_filtros(
            source, fecha_desde=date(2026, 9, 1), grupo_clasificacion="Tecnológicas"
        )
        self.assertEqual(result["Nº Expediente"].tolist(), ["A-1"])

    def test_estado_prioriza_fase_administrativa_y_despues_fecha(self):
        self.assertEqual(normalizar_estado("Publicada", "2026-09-01", date(2026, 8, 19)), "En plazo")
        self.assertEqual(normalizar_estado("Publicada", "2026-08-01", date(2026, 8, 19)), "Fuera de plazo")
        self.assertEqual(normalizar_estado("Adjudicada", "2026-09-01", date(2026, 8, 19)), "Adjudicada")
        self.assertEqual(normalizar_estado("Abierta", None, date(2026, 8, 19)), "Abierta (plazo sin confirmar)")

    def test_filtro_por_estado_permite_seleccion_multiple(self):
        source = pd.DataFrame([
            {"Nº Expediente": "A", "Estado": "En plazo"},
            {"Nº Expediente": "B", "Estado": "Adjudicada"},
            {"Nº Expediente": "C", "Estado": "Fuera de plazo"},
        ])
        result = aplicar_filtros(source, estados=["En plazo", "Adjudicada"])
        self.assertEqual(result["Nº Expediente"].tolist(), ["A", "B"])

    def test_aliases_multilingues_recuperan_cpv_iva_y_expediente(self):
        source = pd.DataFrame([
            {"File number": "CON/45/25", "CPV code": "45216111",
             "Base budget with tax": "2157129.84", "Contracting Party": "Ayuntamiento"},
            {"numero_de_expediente": "ESP-1", "File number": "EN-IGNORADO",
             "organo_de_contratacion": "Órgano español", "Contracting Party": "English party"},
        ])
        result = normalizar_columnas_multilingues(source)
        self.assertEqual(result.loc[0, "numero_expediente"], "CON/45/25")
        self.assertEqual(result.loc[0, "codigo_cpv"], "45216111")
        self.assertEqual(result.loc[0, "importe_con_iva"], "2157129.84")
        self.assertEqual(result.loc[1, "numero_expediente"], "ESP-1")
        self.assertEqual(result.loc[1, "organo_contratacion"], "Órgano español")

    def test_field_map_ingles_completo(self):
        source = pd.DataFrame([{
            "contracting_party": "Council", "file_number": "FILE-1",
            "subject": "Technical services", "bidding_link": "https://example.test/tender",
            "tender_state": "Published", "eu_financing": "Yes",
            "base_budget_no_tax": "1000", "base_budget_with_tax": "1210",
            "estimated_value": "1500", "contract_type": "Services",
            "cpv_code": "72000000", "place_of_execution": "Murcia",
            "procurement_procedure": "Open", "processing_type": "Ordinary",
            "offer_submission_method": "Electronic", "submission_deadline": "08-Sep-2026",
        }])
        result = normalizar_columnas_multilingues(source)
        expected = {
            "organo_contratacion": "Council", "numero_expediente": "FILE-1",
            "titulo": "Technical services", "enlace": "https://example.test/tender",
            "estado_licitacion": "Published", "financiacion_ue": "Yes",
            "importe_licitacion": "1000", "importe_con_iva": "1210",
            "valor_estimado_contrato": "1500", "tipo_contrato": "Services",
            "codigo_cpv": "72000000", "lugar_ejecucion": "Murcia",
            "procedimiento_contratacion": "Open", "tramitacion": "Ordinary",
            "forma_presentacion": "Electronic", "fecha_limite_presentacion": "08-Sep-2026",
        }
        for column, value in expected.items():
            self.assertEqual(result.loc[0, column], value, column)

    def test_salida_cpv_elimina_descripcion(self):
        source = pd.DataFrame([{
            "numero_expediente": "EXP", "codigo_cpv": "45216111-Trabajos de construcción",
        }])
        result = construir_salida_final(source)
        self.assertEqual(result.loc[0, "codigo_cpv"], "45216111")

    def test_fechas_inglesas_y_catalanas(self):
        values = pd.Series(["08-Sep-2026", "September 8, 2026", "8 de setembre del 2026 23:59"])
        result = parsear_fechas_inteligente(values)
        self.assertTrue(all(value == date(2026, 9, 8) for value in result))

    def test_formato_compacto_de_importes(self):
        self.assertEqual(formatear_importe_compacto(1782751.93), "1,78 M €")
        self.assertEqual(formatear_importe_compacto(45000), "45,0 K €")
        self.assertEqual(formatear_importe_compacto(-1), "-")

    def test_documentos_se_detectan_en_cuatro_idiomas(self):
        for label in (
            "Pliego de prescripciones técnicas", "Tender specifications",
            "Veure documents - plec tècnic", "Baldintza teknikoak",
        ):
            self.assertTrue(matches_document_text(label), label)

    def test_metricas_se_guardan_en_json_e_historico(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics = {
                "ejecucion_id": "test", "total_licitaciones": 2,
                "pdf_descargados": 1, "gemini_requeridas": 2,
                "gemini_analizadas": 1,
            }
            json_path, history_path = guardar_metricas(metrics, directory)
            self.assertTrue(json_path.exists())
            history = pd.read_csv(history_path)
            self.assertEqual(history.loc[0, "total_licitaciones"], 2)


if __name__ == "__main__":
    unittest.main()
