Almacén de informes originales íntegros (PDF, TXT, JSON).

Convención: un archivo por `report_id` (p. ej. `{report_id}.pdf`). Servido de
forma segura por `GET /api/v1/reports/{id}/file` (SPEC-03 §5). Nunca se publica
directamente por Nginx.
