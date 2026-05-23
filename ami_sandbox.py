"""Página /sandbox: playground interactivo de la REST API de AMI.

Pensada para reuniones con socios: el técnico abre /sandbox en su navegador,
elige un snippet, le da Run, y ve la respuesta JSON real del backend en
tiempo real. Sube la credibilidad técnica enormemente porque demuestra
que el protocolo es completamente público y exerciable sin SDK, sin auth
en los endpoints públicos, sin setup.

Vanilla JS, sin CodeMirror/Monaco/Prism — el syntax highlighting básico se
hace con un <pre> overlay sincronizado al scroll del <textarea> y un
tokenizer minimal en CSS-classes (cubre keywords JS y strings); cuando se
edita, el highlight se regenera vía expresión regular. Es suficiente para
snippets de 10-20 líneas sin pagar la complejidad de una librería externa.

Stdlib pura, bilingüe inline ES/EN, reutiliza chrome (header + footer).
Brand-agnóstica: cero menciones a terceros en el copy.

NO toca ami_api.py — el route /sandbox lo cabea el dueño del API.
"""
from __future__ import annotations

from ami_chrome import chrome_css, chrome_js, footer_html, header_html


# ---------------------------------------------------------------------------
# Snippets predefinidos. Cada snippet declara método + path + body opcional
# y un trozo de JS que llama a runRequest(). Se renderizan como tabs y al
# clickar uno se carga su contenido en el <textarea>. El snippet "Custom"
# se queda vacío para que el usuario escriba lo que quiera.
# ---------------------------------------------------------------------------

_SNIPPETS = [
    {
        "id": "demo-quick",
        "label_es": "Demo quick",
        "label_en": "Demo quick",
        "method": "POST",
        "path": "/v1/demo/quick",
        "body": "",
        "code": (
            "// End-to-end demo: contrata identidad, manda SMS, recibe respuesta.\n"
            "// Endpoint publico, sin auth. Devuelve el flujo completo en un JSON.\n"
            "await runRequest('POST', '/v1/demo/quick');\n"
        ),
    },
    {
        "id": "health",
        "label_es": "Health check",
        "label_en": "Health check",
        "method": "GET",
        "path": "/v1/health",
        "body": "",
        "code": (
            "// Status del servicio. Util para verificar que el backend esta vivo.\n"
            "await runRequest('GET', '/v1/health');\n"
        ),
    },
    {
        "id": "events",
        "label_es": "List events",
        "label_en": "List events",
        "method": "GET",
        "path": "/v1/events",
        "body": "",
        "code": (
            "// Audit log: ultimos eventos firmados del backend.\n"
            "// Cada evento lleva timestamp, tipo, hash y payload.\n"
            "await runRequest('GET', '/v1/events');\n"
        ),
    },
    {
        "id": "openapi",
        "label_es": "Discovery",
        "label_en": "Discovery",
        "method": "GET",
        "path": "/openapi.json",
        "body": "",
        "code": (
            "// OpenAPI 3.1: 47 endpoints documentados con schema completo.\n"
            "// El backend se autodocumenta — generadores de SDK lo consumen tal cual.\n"
            "const res = await runRequest('GET', '/openapi.json');\n"
            "// Cuenta paths para una vista rapida:\n"
            "// console.log(Object.keys(JSON.parse(res.body).paths).length);\n"
        ),
    },
    {
        "id": "llms",
        "label_es": "llms.txt",
        "label_en": "llms.txt",
        "method": "GET",
        "path": "/llms.txt",
        "body": "",
        "code": (
            "// Indice para agentes AI: mapa textual de toda la superficie de AMI.\n"
            "await runRequest('GET', '/llms.txt');\n"
        ),
    },
    {
        "id": "custom",
        "label_es": "Custom",
        "label_en": "Custom",
        "method": "GET",
        "path": "/v1/health",
        "body": "",
        "code": (
            "// Escribe lo que quieras. Tienes fetch() disponible y la helper\n"
            "// runRequest(method, path, body?) que mide latencia y formatea\n"
            "// el output en el panel derecho.\n"
            "//\n"
            "// Ejemplo: hacer dos llamadas y comparar.\n"
            "const a = await runRequest('GET', '/v1/health');\n"
            "// const b = await runRequest('POST', '/v1/demo/quick');\n"
        ),
    },
]


# Lista de paths conocidos para el autocompletado del input URL. Solo los
# endpoints publicos o de discovery — los que requieren Bearer no tienen
# sentido en el sandbox sin pegar una API key.
_KNOWN_PATHS = [
    "/v1/health",
    "/v1/events",
    "/v1/demo/quick",
    "/openapi.json",
    "/llms.txt",
    "/install.sh",
    "/metrics",
    "/v1/admin/customers",
    "/v1/admin/waitlist",
]


# ---------------------------------------------------------------------------
# CSS embebido. Aislado bajo .sbx-* para no chocar con otras paginas.
# ---------------------------------------------------------------------------

_CSS = """
  :root {
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --surface-hover: #1a1a25;
    --line: #1f1f2c;
    --line-soft: #16161f;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --accent-bg: rgba(139,108,255,0.10);
    --green: #4ade80;
    --red: #f87171;
    --amber: #fbbf24;
    --code-bg: #0a0a12;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "JetBrains Mono", "SF Mono", "Menlo", monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    color: var(--ink);
    background: var(--bg);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: var(--accent); color: #fff; }

  /* HERO ------------------------------------------------------------- */
  .sbx-hero {
    position: relative;
    padding: 4rem 1.5rem 2rem;
    text-align: center;
    overflow: hidden;
  }
  .sbx-hero::before, .sbx-hero::after {
    content: "";
    position: absolute;
    border-radius: 50%;
    filter: blur(140px);
    pointer-events: none;
    z-index: 0;
  }
  .sbx-hero::before {
    width: 520px; height: 520px;
    background: radial-gradient(circle, #8b6cff 0%, transparent 70%);
    top: -40%; left: -10%;
    opacity: 0.32;
  }
  .sbx-hero::after {
    width: 440px; height: 440px;
    background: radial-gradient(circle, #5dd1ff 0%, transparent 70%);
    top: -20%; right: -10%;
    opacity: 0.25;
  }
  .sbx-hero .wrap {
    position: relative;
    z-index: 1;
    max-width: 860px;
    margin: 0 auto;
  }
  .sbx-eyebrow {
    display: inline-block;
    font-family: var(--mono);
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.18em;
    margin-bottom: 1rem;
  }
  .sbx-hero h1 {
    font-weight: 800;
    letter-spacing: -0.035em;
    line-height: 1.05;
    font-size: clamp(2rem, 5vw, 3.4rem);
    margin: 0 0 1rem 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .sbx-hero p {
    font-size: 1.05rem;
    color: var(--ink-soft);
    max-width: 620px;
    margin: 0 auto;
  }

  /* LAYOUT ----------------------------------------------------------- */
  .sbx-stage {
    max-width: 1400px;
    margin: 0 auto;
    padding: 1rem 1.5rem 4rem;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.2rem;
  }
  @media (max-width: 960px) {
    .sbx-stage { grid-template-columns: 1fr; }
  }

  .sbx-panel {
    background: linear-gradient(180deg, var(--surface) 0%, var(--bg-soft) 100%);
    border: 1px solid var(--line);
    border-radius: 14px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    min-height: 560px;
  }

  /* TABS / SNIPPETS -------------------------------------------------- */
  .sbx-tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 0;
    border-bottom: 1px solid var(--line);
    background: var(--bg-soft);
  }
  .sbx-tab {
    background: none;
    border: none;
    border-right: 1px solid var(--line);
    border-bottom: 2px solid transparent;
    color: var(--ink-soft);
    font-family: var(--mono);
    font-size: 0.72rem;
    padding: 0.7rem 0.95rem;
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
  }
  .sbx-tab:hover {
    color: var(--ink);
    background: rgba(139,108,255,0.05);
  }
  .sbx-tab.active {
    color: var(--ink);
    border-bottom-color: var(--accent);
    background: rgba(139,108,255,0.08);
  }

  /* REQUEST CONTROLS ------------------------------------------------- */
  .sbx-req-row {
    display: flex;
    gap: 0.5rem;
    padding: 0.7rem 0.85rem;
    border-bottom: 1px solid var(--line);
    background: var(--bg-soft);
    align-items: center;
  }
  .sbx-method {
    background: var(--code-bg);
    border: 1px solid var(--line);
    color: var(--accent);
    font-family: var(--mono);
    font-size: 0.78rem;
    font-weight: 600;
    padding: 0.4rem 0.6rem;
    border-radius: 6px;
    cursor: pointer;
  }
  .sbx-url {
    flex: 1;
    background: var(--code-bg);
    border: 1px solid var(--line);
    color: var(--ink);
    font-family: var(--mono);
    font-size: 0.78rem;
    padding: 0.4rem 0.65rem;
    border-radius: 6px;
    min-width: 0;
  }
  .sbx-url:focus, .sbx-method:focus {
    outline: none;
    border-color: var(--accent);
  }

  /* EDITOR ----------------------------------------------------------- */
  .sbx-editor {
    position: relative;
    flex: 1;
    min-height: 320px;
    background: var(--code-bg);
  }
  /* Overlay + textarea apilados para fake-syntax-highlight sin libs.
     El <pre> renderiza HTML con clases (kw, str, com); el <textarea>
     transparente recibe input y queda perfectamente alineado encima. */
  .sbx-editor pre, .sbx-editor textarea {
    margin: 0;
    padding: 1rem 1.1rem;
    font-family: var(--mono);
    font-size: 0.82rem;
    line-height: 1.55;
    border: 0;
    width: 100%;
    height: 100%;
    min-height: 320px;
    white-space: pre;
    overflow: auto;
    tab-size: 2;
  }
  .sbx-editor pre {
    position: absolute;
    inset: 0;
    pointer-events: none;
    color: var(--ink);
    background: transparent;
    z-index: 1;
  }
  .sbx-editor textarea {
    position: relative;
    color: transparent;
    caret-color: var(--accent);
    background: transparent;
    resize: none;
    z-index: 2;
    -webkit-text-fill-color: transparent;
  }
  .sbx-editor textarea::selection {
    background: rgba(139,108,255,0.30);
  }
  /* Tokens del fake highlight: lo justo para que el editor se sienta vivo. */
  .sbx-editor .tk-kw  { color: #c084fc; }
  .sbx-editor .tk-str { color: #86efac; }
  .sbx-editor .tk-num { color: #fbbf24; }
  .sbx-editor .tk-com { color: var(--ink-mute); font-style: italic; }
  .sbx-editor .tk-fn  { color: var(--accent-2); }

  /* RUN BAR ---------------------------------------------------------- */
  .sbx-runbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.7rem 0.85rem;
    background: var(--bg-soft);
    border-top: 1px solid var(--line);
  }
  .sbx-runhint {
    font-family: var(--mono);
    font-size: 0.7rem;
    color: var(--ink-mute);
  }
  .sbx-runhint kbd {
    font-family: var(--mono);
    font-size: 0.68rem;
    background: var(--code-bg);
    border: 1px solid var(--line);
    border-bottom-width: 2px;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
    color: var(--ink-soft);
  }
  .sbx-run {
    background: linear-gradient(135deg, var(--accent) 0%, #6b4cff 100%);
    border: 0;
    color: #fff;
    font-family: var(--mono);
    font-size: 0.82rem;
    font-weight: 600;
    padding: 0.55rem 1.4rem;
    border-radius: 8px;
    cursor: pointer;
    transition: transform 0.1s, box-shadow 0.2s;
    box-shadow: 0 6px 20px rgba(139,108,255,0.30);
  }
  .sbx-run:hover { transform: translateY(-1px); box-shadow: 0 10px 28px rgba(139,108,255,0.40); }
  .sbx-run:active { transform: translateY(0); }
  .sbx-run:disabled {
    background: var(--surface);
    color: var(--ink-mute);
    cursor: not-allowed;
    box-shadow: none;
  }

  /* OUTPUT PANEL ----------------------------------------------------- */
  .sbx-out-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.7rem 0.95rem;
    border-bottom: 1px solid var(--line);
    background: var(--bg-soft);
    gap: 0.6rem;
  }
  .sbx-status {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    font-family: var(--mono);
    font-size: 0.78rem;
  }
  .sbx-status .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--ink-mute);
    box-shadow: 0 0 8px currentColor;
  }
  .sbx-status[data-state="idle"] .dot { background: var(--ink-mute); }
  .sbx-status[data-state="pending"] .dot { background: var(--accent-2); animation: sbxPulse 1.2s ease-in-out infinite; }
  .sbx-status[data-state="ok"] .dot { background: var(--green); }
  .sbx-status[data-state="err"] .dot { background: var(--red); }
  .sbx-status[data-state="idle"] { color: var(--ink-mute); }
  .sbx-status[data-state="pending"] { color: var(--accent-2); }
  .sbx-status[data-state="ok"] { color: var(--green); }
  .sbx-status[data-state="err"] { color: var(--red); }
  @keyframes sbxPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }
  .sbx-timing {
    font-family: var(--mono);
    font-size: 0.72rem;
    color: var(--ink-mute);
  }

  .sbx-out-tabs {
    display: flex;
    border-bottom: 1px solid var(--line);
    background: var(--bg-soft);
  }
  .sbx-out-tab {
    background: none;
    border: 0;
    border-right: 1px solid var(--line);
    border-bottom: 2px solid transparent;
    color: var(--ink-soft);
    font-family: var(--mono);
    font-size: 0.7rem;
    padding: 0.55rem 0.9rem;
    cursor: pointer;
  }
  .sbx-out-tab:hover { color: var(--ink); }
  .sbx-out-tab.active {
    color: var(--ink);
    border-bottom-color: var(--accent-2);
  }

  .sbx-output {
    flex: 1;
    margin: 0;
    padding: 1rem 1.1rem;
    font-family: var(--mono);
    font-size: 0.78rem;
    line-height: 1.55;
    color: var(--ink);
    background: var(--code-bg);
    overflow: auto;
    white-space: pre;
    min-height: 320px;
  }
  .sbx-output[hidden] { display: none; }

  /* JSON pretty (clases generadas por el formateador) */
  .sbx-output .j-key { color: var(--accent-2); }
  .sbx-output .j-str { color: #86efac; }
  .sbx-output .j-num { color: #fbbf24; }
  .sbx-output .j-bool { color: #c084fc; }
  .sbx-output .j-null { color: var(--ink-mute); }

  /* FOOTNOTE --------------------------------------------------------- */
  .sbx-note {
    max-width: 1400px;
    margin: 0 auto;
    padding: 0 1.5rem 4rem;
    font-family: var(--mono);
    font-size: 0.78rem;
    color: var(--ink-mute);
    text-align: center;
  }
  .sbx-note a { color: var(--accent-2); text-decoration: none; }
  .sbx-note a:hover { color: var(--ink); }
"""


# ---------------------------------------------------------------------------
# JS de la pagina. Pegado tal cual dentro de <script>. Usa tres helpers:
#   - runRequest(method, path, body) — hace fetch, mide ms, popula la UI.
#   - highlightJS(text) — tokenizer regex minimal para keywords/strings.
#   - formatJSON(obj) — pretty-print con spans clasificados.
# ---------------------------------------------------------------------------

def _snippets_js() -> str:
    """Serializa los snippets como objeto JS literal."""
    lines = ["{"]
    for s in _SNIPPETS:
        body_js = s["body"].replace("\\", "\\\\").replace("`", "\\`")
        code_js = s["code"].replace("\\", "\\\\").replace("`", "\\`")
        lines.append(
            f'        "{s["id"]}": {{ '
            f'method: "{s["method"]}", '
            f'path: "{s["path"]}", '
            f'body: `{body_js}`, '
            f'code: `{code_js}` '
            f'}},'
        )
    lines.append("      }")
    return "\n".join(lines)


def _known_paths_js() -> str:
    """Lista de paths para el <datalist>."""
    return "[" + ", ".join(f'"{p}"' for p in _KNOWN_PATHS) + "]"


_JS = """
  (function() {
    const SNIPPETS = __SNIPPETS__;
    const PATHS = __PATHS__;

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => Array.from(document.querySelectorAll(sel));

    const editorTA = $("#sbx-editor-input");
    const editorPre = $("#sbx-editor-overlay");
    const methodSel = $("#sbx-method");
    const urlInp = $("#sbx-url");
    const runBtn = $("#sbx-run");
    const statusEl = $("#sbx-status");
    const statusText = $("#sbx-status-text");
    const timingEl = $("#sbx-timing");
    const outBody = $("#sbx-out-body");
    const outHeaders = $("#sbx-out-headers");
    const outCurl = $("#sbx-out-curl");

    // --- syntax highlight minimal -----------------------------------
    // Cubre keywords JS, strings (', ", `), numeros y comentarios //.
    // No es un parser real, pero para snippets <30 lineas es indistinguible.
    const KEYWORDS = /\\b(await|async|const|let|var|function|return|if|else|for|while|new|try|catch|throw|typeof|null|undefined|true|false)\\b/g;
    const FNCALL = /\\b(runRequest|fetch|console|JSON|Object|Array|Math|performance)\\b/g;
    function escapeHTML(s) {
      return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }
    function highlightJS(src) {
      // 1. Escapar.
      let s = escapeHTML(src);
      // 2. Comentarios linea completa (debe ir antes que strings para no
      //    matchear // dentro de "http://...").
      s = s.replace(/(^|[^:])(\\/\\/[^\\n]*)/g, function(_, p, c) {
        return p + '<span class="tk-com">' + c + '</span>';
      });
      // 3. Strings (', ", `). Greedy hasta el siguiente delimitador igual.
      s = s.replace(/(&quot;[^&\\n]*?&quot;|'[^'\\n]*?'|`[^`]*?`)/g, '<span class="tk-str">$1</span>');
      // 4. Numeros.
      s = s.replace(/\\b(\\d+(?:\\.\\d+)?)\\b/g, '<span class="tk-num">$1</span>');
      // 5. Keywords y funciones.
      s = s.replace(KEYWORDS, '<span class="tk-kw">$1</span>');
      s = s.replace(FNCALL, '<span class="tk-fn">$1</span>');
      // Asegurar newline final visible.
      if (!s.endsWith("\\n")) s += "\\n";
      return s;
    }
    function syncHighlight() {
      editorPre.innerHTML = highlightJS(editorTA.value);
      editorPre.scrollTop = editorTA.scrollTop;
      editorPre.scrollLeft = editorTA.scrollLeft;
    }
    editorTA.addEventListener("input", syncHighlight);
    editorTA.addEventListener("scroll", function() {
      editorPre.scrollTop = editorTA.scrollTop;
      editorPre.scrollLeft = editorTA.scrollLeft;
    });
    // Tab key inserta dos espacios.
    editorTA.addEventListener("keydown", function(e) {
      if (e.key === "Tab") {
        e.preventDefault();
        const s = editorTA.selectionStart;
        const v = editorTA.value;
        editorTA.value = v.slice(0, s) + "  " + v.slice(editorTA.selectionEnd);
        editorTA.selectionStart = editorTA.selectionEnd = s + 2;
        syncHighlight();
      }
      // Cmd/Ctrl+Enter ejecuta.
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        runBtn.click();
      }
    });

    // --- pretty JSON ------------------------------------------------
    function formatJSON(text) {
      try {
        const parsed = JSON.parse(text);
        const pretty = JSON.stringify(parsed, null, 2);
        // Tokenize for syntax colors.
        const esc = escapeHTML(pretty);
        return esc
          .replace(/(&quot;[^&]*?&quot;)(\\s*:)/g, '<span class="j-key">$1</span>$2')
          .replace(/:\\s*(&quot;[^&]*?&quot;)/g, ': <span class="j-str">$1</span>')
          .replace(/:\\s*(-?\\d+(?:\\.\\d+)?)/g, ': <span class="j-num">$1</span>')
          .replace(/:\\s*(true|false)/g, ': <span class="j-bool">$1</span>')
          .replace(/:\\s*(null)/g, ': <span class="j-null">$1</span>');
      } catch (e) {
        return escapeHTML(text);
      }
    }

    // --- curl equivalent --------------------------------------------
    function buildCurl(method, path, body) {
      const origin = window.location.origin;
      const parts = ["curl -X " + method + " \\\\\\n  '" + origin + path + "'"];
      parts.push("  -H 'Accept: application/json'");
      if (body && method !== "GET") {
        parts.push("  -H 'Content-Type: application/json'");
        const safeBody = body.replace(/'/g, "'\\\\''");
        parts.push("  -d '" + safeBody + "'");
      }
      return parts.join(" \\\\\\n");
    }

    // --- runRequest (expuesto al usuario en el editor) --------------
    let lastResult = null;
    async function runRequest(method, path, body) {
      const m = (method || "GET").toUpperCase();
      methodSel.value = m;
      urlInp.value = path;

      statusEl.dataset.state = "pending";
      statusText.textContent = "...";
      timingEl.textContent = "";

      const start = performance.now();
      const opts = { method: m, headers: { "Accept": "application/json" } };
      if (body && m !== "GET") {
        opts.headers["Content-Type"] = "application/json";
        opts.body = (typeof body === "string") ? body : JSON.stringify(body);
      }
      let resp, text, headers;
      try {
        resp = await fetch(path, opts);
        text = await resp.text();
        headers = {};
        resp.headers.forEach((v, k) => { headers[k] = v; });
      } catch (err) {
        statusEl.dataset.state = "err";
        statusText.textContent = "Network error";
        outBody.innerHTML = '<span class="tk-com">' + escapeHTML(String(err)) + '</span>';
        return null;
      }
      const elapsed = Math.round(performance.now() - start);

      // Update status.
      const ok = resp.status >= 200 && resp.status < 300;
      statusEl.dataset.state = ok ? "ok" : "err";
      statusText.textContent = resp.status + " " + resp.statusText;
      timingEl.textContent = elapsed + " ms";

      // Update outputs.
      const ct = (headers["content-type"] || "").toLowerCase();
      if (ct.includes("application/json")) {
        outBody.innerHTML = formatJSON(text);
      } else {
        outBody.innerHTML = escapeHTML(text);
      }
      const hdrLines = Object.keys(headers).sort().map(k =>
        '<span class="j-key">' + escapeHTML(k) + '</span>: <span class="j-str">' + escapeHTML(headers[k]) + '</span>'
      ).join("\\n");
      outHeaders.innerHTML = hdrLines;
      outCurl.textContent = buildCurl(m, path, opts.body || "");

      lastResult = { status: resp.status, headers, body: text, ms: elapsed };
      return lastResult;
    }
    // Exponer para que el codigo del editor lo use.
    window.runRequest = runRequest;

    // --- snippet tabs ----------------------------------------------
    function loadSnippet(id) {
      const s = SNIPPETS[id];
      if (!s) return;
      editorTA.value = s.code;
      methodSel.value = s.method;
      urlInp.value = s.path;
      syncHighlight();
      $$(".sbx-tab").forEach(t => t.classList.toggle("active", t.dataset.snippet === id));
    }
    $$(".sbx-tab").forEach(t => {
      t.addEventListener("click", () => loadSnippet(t.dataset.snippet));
    });
    loadSnippet("demo-quick");

    // --- output tabs -----------------------------------------------
    $$(".sbx-out-tab").forEach(t => {
      t.addEventListener("click", () => {
        $$(".sbx-out-tab").forEach(x => x.classList.toggle("active", x === t));
        const target = t.dataset.outtab;
        outBody.hidden = (target !== "body");
        outHeaders.hidden = (target !== "headers");
        outCurl.hidden = (target !== "curl");
      });
    });

    // --- run button -------------------------------------------------
    runBtn.addEventListener("click", async () => {
      const code = editorTA.value;
      // Si el usuario solo cambio el URL/method sin escribir codigo, dispara
      // un runRequest directo. Si hay codigo, lo evalua en una async IIFE
      // que captura runRequest del closure.
      runBtn.disabled = true;
      try {
        if (code.trim() === "" || code.trim().startsWith("//")) {
          await runRequest(methodSel.value, urlInp.value);
        } else {
          // Evaluacion: wrap en async function. El editor es sandbox local,
          // no recibimos codigo del backend — solo lo que el usuario tipea.
          const fn = new Function("runRequest", "fetch",
            '"use strict"; return (async () => { ' + code + ' })();');
          await fn(runRequest, window.fetch.bind(window));
        }
      } catch (err) {
        statusEl.dataset.state = "err";
        statusText.textContent = "Script error";
        outBody.innerHTML = '<span class="tk-com">' + escapeHTML(String(err)) + '</span>';
      } finally {
        runBtn.disabled = false;
      }
    });

    // --- datalist de paths ------------------------------------------
    const dl = $("#sbx-paths");
    PATHS.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p;
      dl.appendChild(opt);
    });

    // Estado inicial.
    syncHighlight();
    statusEl.dataset.state = "idle";
    statusText.textContent = "ready";
  })();
"""


# ---------------------------------------------------------------------------
# HTML render.
# ---------------------------------------------------------------------------

def _tabs_html() -> str:
    items = []
    for s in _SNIPPETS:
        items.append(
            f'<button type="button" class="sbx-tab" data-snippet="{s["id"]}">'
            f'<span data-chrome-lang="es">{s["label_es"]}</span>'
            f'<span data-chrome-lang="en">{s["label_en"]}</span>'
            f'</button>'
        )
    return "\n        ".join(items)


def render_sandbox_page(lang: str = "es") -> str:
    """Página /sandbox: editor de codigo + run + output JSON.

    Bilingüe inline ES/EN. El parametro `lang` solo decide el atributo
    `<html lang>` inicial; el toggle del chrome cambia entre los dos sin
    recargar (igual que el resto de paginas).
    """
    title = "AMI · Sandbox" if lang == "en" else "AMI · Sandbox"
    description = (
        "Interactive playground for the AMI API. Write JS, hit Run, see the "
        "real JSON response from the backend in real time."
        if lang == "en"
        else
        "Playground interactivo de la API de AMI. Escribe JS, dale Run, mira "
        "la respuesta JSON real del backend en tiempo real."
    )

    snippets_obj = _snippets_js().replace("{", "{{").replace("}", "}}")
    # _JS uses single-brace templating with str.replace, not f-string, to avoid
    # collisions with the literal braces of JS code.
    js = (_JS
          .replace("__SNIPPETS__", _snippets_js())
          .replace("__PATHS__", _known_paths_js()))

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{chrome_css()}
{_CSS}
</style>
</head>
<body>
{header_html(active="sandbox", lang=lang)}

<main>
  <section class="sbx-hero">
    <div class="wrap">
      <span class="sbx-eyebrow">
        <span data-chrome-lang="es">Sandbox</span>
        <span data-chrome-lang="en">Sandbox</span>
      </span>
      <h1>
        <span data-chrome-lang="es">Sandbox de AMI — prueba la API en tu navegador</span>
        <span data-chrome-lang="en">AMI Sandbox — try the API in your browser</span>
      </h1>
      <p>
        <span data-chrome-lang="es">Elige un snippet, dale Run, lee la respuesta JSON real del backend. Sin claves, sin setup, sin SDK.</span>
        <span data-chrome-lang="en">Pick a snippet, hit Run, read the real JSON response from the backend. No keys, no setup, no SDK.</span>
      </p>
    </div>
  </section>

  <section class="sbx-stage">
    <!-- LEFT: editor ------------------------------------------------ -->
    <div class="sbx-panel" id="sbx-left">
      <div class="sbx-tabs" role="tablist">
        {_tabs_html()}
      </div>
      <div class="sbx-req-row">
        <select class="sbx-method" id="sbx-method" aria-label="HTTP method">
          <option>GET</option>
          <option>POST</option>
          <option>PUT</option>
          <option>DELETE</option>
          <option>PATCH</option>
        </select>
        <input class="sbx-url" id="sbx-url" list="sbx-paths" placeholder="/v1/health" spellcheck="false" autocomplete="off">
        <datalist id="sbx-paths"></datalist>
      </div>
      <div class="sbx-editor">
        <pre id="sbx-editor-overlay" aria-hidden="true"></pre>
        <textarea id="sbx-editor-input" spellcheck="false" autocomplete="off" autocorrect="off" autocapitalize="off"></textarea>
      </div>
      <div class="sbx-runbar">
        <span class="sbx-runhint">
          <span data-chrome-lang="es"><kbd>Cmd</kbd>+<kbd>Enter</kbd> para ejecutar</span>
          <span data-chrome-lang="en"><kbd>Cmd</kbd>+<kbd>Enter</kbd> to run</span>
        </span>
        <button class="sbx-run" id="sbx-run" type="button">
          <span data-chrome-lang="es">▶ Run</span>
          <span data-chrome-lang="en">▶ Run</span>
        </button>
      </div>
    </div>

    <!-- RIGHT: output ---------------------------------------------- -->
    <div class="sbx-panel" id="sbx-right">
      <div class="sbx-out-header">
        <span class="sbx-status" id="sbx-status" data-state="idle">
          <span class="dot" aria-hidden="true"></span>
          <span id="sbx-status-text">ready</span>
        </span>
        <span class="sbx-timing" id="sbx-timing"></span>
      </div>
      <div class="sbx-out-tabs" role="tablist">
        <button type="button" class="sbx-out-tab active" data-outtab="body">
          <span data-chrome-lang="es">Response body</span>
          <span data-chrome-lang="en">Response body</span>
        </button>
        <button type="button" class="sbx-out-tab" data-outtab="headers">
          <span data-chrome-lang="es">Headers</span>
          <span data-chrome-lang="en">Headers</span>
        </button>
        <button type="button" class="sbx-out-tab" data-outtab="curl">
          <span data-chrome-lang="es">cURL</span>
          <span data-chrome-lang="en">cURL</span>
        </button>
      </div>
      <pre class="sbx-output" id="sbx-out-body"><span class="tk-com">// <span data-chrome-lang="es">El output aparecera aqui. Dale Run para empezar.</span><span data-chrome-lang="en">Output will appear here. Hit Run to start.</span></span></pre>
      <pre class="sbx-output" id="sbx-out-headers" hidden></pre>
      <pre class="sbx-output" id="sbx-out-curl" hidden></pre>
    </div>
  </section>

  <p class="sbx-note">
    <span data-chrome-lang="es">Todas las llamadas van contra este mismo servidor. Endpoints publicos no requieren auth; el resto usa Bearer. Esquema completo en <a href="/openapi.json">/openapi.json</a>.</span>
    <span data-chrome-lang="en">All calls hit this same server. Public endpoints need no auth; the rest use Bearer. Full schema at <a href="/openapi.json">/openapi.json</a>.</span>
  </p>
</main>

{footer_html(lang=lang)}

<script>
(function() {{
{chrome_js()}
}})();
</script>
<script>
{js}
</script>
</body>
</html>
"""
