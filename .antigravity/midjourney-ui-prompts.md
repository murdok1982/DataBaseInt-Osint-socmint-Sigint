# Prompts Midjourney — Referencia Visual UI/UX (OSINT Nexus)

> Referencia visual para `specs/05-dashboard-ui-ux.spec.md`. No es código; son
> prompts de generación de imágenes. Estilo objetivo: Palantir Gotham, Maltego,
> Recorded Future.

## 1. Vista Principal — Grafo Interactivo de Inteligencia (Core Dashboard)

```
/imagine prompt: UI/UX design of an advanced cybersecurity OSINT intelligence web dashboard, dark mode interface, inspired by Palantir Gotham and Maltego. In the center, a large interactive constellation network graph with glowing nodes and connecting lines representing people, shell companies, and IP addresses. Color-coded nodes in neon cyan, amber, and crimson red. Left sidebar with tactical filter controls and entity types. Bottom timeline slider showing chronological intelligence events. Sleek glassmorphism panels, high contrast data visualization, ultra-detailed typography, professional enterprise software UI, Figma, Dribbble, 8k resolution --ar 16:9 --v 6.1 --style raw --stylize 250
```

Mapea a: `apps/frontend/src/components/graph/` · SPEC-05 §2.

## 2. Vista de Expediente y Lectura del Informe Original (Entity Dossier & Source Report)

```
/imagine prompt: High-tech intelligence analysis software UI, split-screen layout in dark mode. On the left side, an interactive relationship node graph focusing on a specific target profile with a photo placeholder, aliases, confidence score meter, and metadata tags. On the right side, an integrated document viewer displaying an unclassified OSINT intelligence PDF report with highlighted text extracts linked directly to the graph nodes. Dark slate background, subtle grid lines, emerald green and electric blue accents, clean modern tactical interface, UX wireframe masterpiece, high resolution --ar 16:9 --v 6.1 --style raw --stylize 200
```

Mapea a: `apps/frontend/src/components/dossier/` · SPEC-05 §3.

## 3. Centro de Ingesta IA y Buscador en Lenguaje Natural (AI Ingestion & GraphRAG)

```
/imagine prompt: Modern AI-powered data ingestion and GraphRAG search interface for an intelligence database, dark theme UI. Top floating command bar for natural language queries. Main screen showing a drag-and-drop OSINT report upload zone with real-time NLP processing progress bars, automatic entity extraction tags, and relationship mapping preview cards. Clean futuristic dashboard, subtle glowing violet and cyan accents, minimalist data tables, enterprise SaaS design, Behance UI trend, crisp details --ar 16:9 --v 6.1 --style raw --stylize 200
```

Mapea a: `apps/frontend/src/components/ingest/` + `components/search/` · SPEC-05 §4-5.

## 4. Portal de Acceso Seguro para Operadores (Tactical Login Screen)

```
/imagine prompt: Minimalist and secure login portal UI for a classified OSINT intelligence platform, dark mode. Centered frosted glass authentication card with username field, password input, two-factor authentication token prompt, and a subtle security clearance badge icon. In the dark background, a very faint, out-of-focus 3D global network node topology map. Professional cybersecurity aesthetic, pitch black and charcoal tones with subtle steel blue highlights, clean enterprise UI design --ar 16:9 --v 6.1 --style raw --stylize 150
```

Mapea a: `apps/frontend/src/context/` (auth) · SPEC-04 + SPEC-05 §5.

## Consejos de uso
- **Variaciones de diseño:** al encontrar una paleta/layout que guste, usa
  `Vary (Subtle)` o `--sref [URL_de_tu_imagen]` en los demás prompts para que las
  4 pantallas parezcan el mismo software.
- **Del diseño al código:** pasa la imagen elegida al Agente 4 (`frontend-ui`)
  para que programe React/Tailwind imitando esa distribución.
- Cuando se fije una referencia visual, se documenta aquí y se referencia desde
  SPEC-05 para que el diseño quede trazado a la spec.
