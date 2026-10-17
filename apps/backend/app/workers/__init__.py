"""Workers asíncronos de OSINT Nexus (SPEC-02 §5).

Autorizado por: **SPEC-02 §5** — *«arquitectura FastAPI + Celery/Redis»* — y
**SPEC-02 §6** (*la subida no bloquea*).

El paquete expone el *worker* Celery (``celery_app``), la tarea de ingesta
(``tasks.ingest_report``) y un *enqueue* tolerante a fallos
(``tasks.enqueue_ingest``). Importar este paquete **nunca** requiere un broker:
todas las dependencias (Celery, Redis) son perezosas/protegidas.
"""

from __future__ import annotations

__all__: list[str] = []
