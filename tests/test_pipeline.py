import os
import tempfile
import unittest

import pandas as pd

from src.functions import (
    FINAL_OUTPUT_COLUMNS,
    asegurar_identificador_en_pdfs,
    construir_salida_final,
)
from src.gemini_processor import LicitacionGeminiProcessor


class PipelineTests(unittest.TestCase):
    def test_esquema_final_tiene_exactamente_17_columnas(self):
        source = pd.DataFrame(
            [{
                "estado_licitacion": "Publicada",
                "fecha_limite_presentacion": "2026-09-01",
                "titulo": "Plataforma de datos",
                "numero_expediente": "EXP-1",
                "provincia_ejecucion": "Madrid",
                "comunidad_autonoma_ejecucion": "Comunidad de Madrid",
            }]
        )
        result = construir_salida_final(source)
        self.assertEqual(result.columns.tolist(), FINAL_OUTPUT_COLUMNS)
        self.assertEqual(result.shape[1], 17)
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


if __name__ == "__main__":
    unittest.main()
