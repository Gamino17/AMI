"""Página /live — demo cinemática extendida para presentar a socios.

Pensada para proyectarse mientras el moderador NARRA cada acto.
Duración objetivo: ~22-25 segundos, controlable (start / reset / speed).

Diferencia con /diagram (autoplay loop genérico):
  - El moderador escribe el número del socio en pantalla → personaliza
  - Pulsa "Start demo" → arranca a su ritmo
  - 6 actos narrativos visibles con sub-pasos detallados
  - Phone mockup lateral con bubbles tipográficas (efecto typing)
  - Waveform RTP animado durante la llamada
  - Selector de velocidad (slow / normal / fast) por si el discurso va corto/largo
  - Canvas con partículas conectando los componentes del stack
  - Number counter "rolando" al asignar el MSISDN
  - Métricas finales grandes + IDs reales del backend (hit a /v1/demo/quick en paralelo)
"""
from __future__ import annotations


_LIVE_CSS = """
  :root {
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --surface-hover: #1a1a25;
    --line: #1f1f2c;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --accent-3: #ff5cb6;
    --green: #4ade80;
    --amber: #fbbf24;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: var(--sans);
    color: var(--ink);
    background:
      radial-gradient(ellipse 90% 60% at 50% 0%, rgba(139,108,255,0.12), transparent 70%),
      radial-gradient(ellipse 70% 50% at 50% 100%, rgba(93,209,255,0.08), transparent 70%),
      var(--bg);
    background-attachment: fixed;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }

  .live-shell { max-width: 1480px; margin: 0 auto; padding: 1.5rem 1.5rem 4rem; }

  /* HERO */
  .live-hero { text-align: center; padding: 0.5rem 0 1.5rem; }
  .live-eyebrow {
    font-family: var(--mono); font-size: 0.72rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.2em;
    margin-bottom: 0.7rem;
  }
  .live-hero h1 {
    font-size: clamp(1.8rem, 4vw, 3rem);
    font-weight: 800; letter-spacing: -0.028em;
    margin: 0 0 0.6rem; line-height: 1.05;
  }
  .live-hero h1 .grad {
    background: linear-gradient(180deg, #c2b3ff 10%, #7a5cff 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .live-hero p {
    color: var(--ink-soft); font-size: 0.96rem;
    max-width: 720px; margin: 0 auto;
  }

  /* CONTROLES */
  .live-controls {
    max-width: 840px; margin: 0 auto 1.5rem;
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 1rem 1.25rem;
    display: flex; gap: 0.7rem; align-items: center; flex-wrap: wrap;
  }
  .live-controls .field {
    flex: 1; min-width: 200px;
    display: flex; flex-direction: column; gap: 0.3rem;
  }
  .live-controls label {
    font-family: var(--mono); font-size: 0.64rem; text-transform: uppercase;
    letter-spacing: 0.14em; color: var(--ink-mute);
  }
  .live-controls input[type="text"] {
    background: var(--surface); color: var(--ink);
    border: 1px solid var(--line); border-radius: 8px;
    padding: 0.6rem 0.8rem;
    font-family: var(--mono); font-size: 0.92rem;
  }
  .live-controls input[type="text"]:focus { outline: none; border-color: var(--accent); }
  .live-controls .speed {
    display: flex; gap: 0.25rem;
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 8px; padding: 0.2rem;
  }
  .live-controls .speed button {
    background: none; border: 0; color: var(--ink-mute);
    font-family: var(--mono); font-size: 0.72rem;
    padding: 0.35rem 0.7rem; border-radius: 5px;
    cursor: pointer;
  }
  .live-controls .speed button.active {
    background: var(--accent-bg, rgba(139,108,255,0.18));
    color: var(--ink);
  }
  .live-btn-primary {
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; border: 0; cursor: pointer;
    padding: 0.8rem 1.5rem; border-radius: 8px;
    font-family: var(--sans); font-size: 0.92rem; font-weight: 600;
    box-shadow: 0 8px 28px -10px rgba(123,92,255,0.6);
    transition: transform 0.15s;
    min-width: 160px;
  }
  .live-btn-primary:hover:not(:disabled) { transform: translateY(-1px); }
  .live-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .live-btn-ghost {
    background: transparent; color: var(--ink-soft);
    border: 1px solid var(--line); cursor: pointer;
    padding: 0.8rem 1.1rem; border-radius: 8px;
    font-family: var(--mono); font-size: 0.82rem;
  }
  .live-btn-ghost:hover { color: var(--ink); border-color: var(--accent); }

  /* STAGE GRANDE */
  .live-stage {
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 1.4rem;
    margin-bottom: 1.4rem;
    position: relative;
    min-height: 460px;
  }
  .live-stage-grid {
    display: grid;
    grid-template-columns: 1fr 1.4fr 320px;
    gap: 1.2rem;
    align-items: stretch;
    min-height: 100%;
  }
  @media (max-width: 1080px) {
    .live-stage-grid { grid-template-columns: 1fr; }
  }

  /* ACTORS: Agent + AMI Stack */
  .live-actor {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 1.2rem;
    position: relative;
    overflow: hidden;
    display: flex; flex-direction: column;
  }
  .live-actor-header {
    display: flex; align-items: center; gap: 0.7rem;
    margin-bottom: 0.9rem;
  }
  .live-actor-icon {
    width: 42px; height: 42px;
    background: var(--bg-soft);
    border: 2px solid currentColor;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.15rem;
    transition: box-shadow 0.3s;
  }
  .live-actor.agent .live-actor-icon { color: var(--accent-2); }
  .live-actor.stack .live-actor-icon { color: var(--accent); }
  .live-actor.agent.pulsing .live-actor-icon {
    box-shadow: 0 0 0 0 currentColor;
    animation: liveActorPulse 1.1s ease-out infinite;
  }
  .live-actor.stack.pulsing .live-actor-icon {
    box-shadow: 0 0 0 0 currentColor;
    animation: liveActorPulse 1.1s ease-out infinite;
  }
  @keyframes liveActorPulse {
    0% { box-shadow: 0 0 0 0 rgba(139,108,255,0.45); }
    100% { box-shadow: 0 0 0 18px rgba(139,108,255,0); }
  }
  .live-actor-title {
    font-size: 0.95rem; font-weight: 600;
    color: var(--ink);
  }
  .live-actor-sub {
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink-mute);
    margin-top: 0.1rem;
  }
  .live-actor-think {
    margin-top: auto;
    background: var(--bg-soft);
    border: 1px dashed var(--line);
    border-radius: 8px;
    padding: 0.7rem 0.85rem;
    font-family: var(--mono); font-size: 0.8rem;
    color: var(--ink-soft);
    min-height: 80px;
    opacity: 0; transform: translateY(8px);
    transition: opacity 0.3s, transform 0.3s;
  }
  .live-actor-think.show { opacity: 1; transform: translateY(0); }
  .live-actor-think .lbl {
    display: block; font-size: 0.62rem; color: var(--ink-mute);
    text-transform: uppercase; letter-spacing: 0.14em;
    margin-bottom: 0.35rem;
  }

  /* AMI STACK modules */
  .live-modules {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.5rem;
    margin-top: 0.4rem;
  }
  .live-module {
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 0.5rem 0.7rem;
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink-soft);
    text-align: center;
    transition: border-color 0.25s, background 0.25s, color 0.25s, box-shadow 0.25s, transform 0.25s;
  }
  .live-module.active {
    border-color: var(--accent);
    background: rgba(139,108,255,0.10);
    color: var(--ink);
    box-shadow: 0 0 16px rgba(139,108,255,0.40);
    transform: scale(1.04);
  }

  /* NUMBER reveal */
  .live-number-reveal {
    background: linear-gradient(180deg, rgba(139,108,255,0.10), transparent);
    border: 1px solid rgba(139,108,255,0.25);
    border-radius: 10px;
    padding: 0.9rem 1rem;
    margin-top: 0.7rem;
    text-align: center;
    display: none;
  }
  .live-number-reveal.show { display: block; animation: liveFadeIn 0.4s; }
  .live-number-reveal .lbl {
    font-family: var(--mono); font-size: 0.62rem;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.16em;
    margin-bottom: 0.25rem;
  }
  .live-number-reveal .num {
    font-family: var(--mono); font-size: 1.6rem; font-weight: 700;
    color: var(--accent-2); letter-spacing: 0.02em;
  }
  .live-number-reveal .meta {
    font-family: var(--mono); font-size: 0.7rem; color: var(--ink-mute);
    margin-top: 0.3rem;
  }
  @keyframes liveFadeIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
  }

  /* CANVAS de partículas.
     Bug previo: el canvas estaba DEBAJO de los actors (z-index:1 vs 2), así
     que las partículas viajaban tapadas por los recuadros — solo se veían
     un instante al cruzar el espacio entre actors. Fix: subimos el canvas
     POR ENCIMA de todo con pointer-events:none, así las partículas se ven
     siempre y los actors siguen siendo clicables.
     z-index muy alto + mix-blend para que no tapen visualmente el texto. */
  .live-flow-canvas {
    position: absolute; inset: 1.4rem;
    pointer-events: none;
    opacity: 0.92;
    z-index: 50;
    mix-blend-mode: screen;
  }
  .live-actor, .live-phone { z-index: 2; position: relative; }

  /* PHONE MOCKUP */
  .live-phone {
    background: linear-gradient(180deg, #0c0c14, #08080c);
    border: 1px solid var(--line);
    border-radius: 26px;
    padding: 1rem 0.9rem 1rem;
    display: flex; flex-direction: column;
    min-height: 460px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.04),
                0 20px 60px -20px rgba(0,0,0,0.6);
  }
  .live-phone-bar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.2rem 0.4rem 0.6rem;
    border-bottom: 1px solid var(--line);
    font-family: var(--mono); font-size: 0.65rem; color: var(--ink-mute);
  }
  .live-phone-bar .signal { color: var(--green); }
  .live-phone-header {
    padding: 0.7rem 0.4rem;
    border-bottom: 1px solid var(--line);
  }
  .live-phone-header .who {
    color: var(--ink); font-size: 0.85rem; font-weight: 600;
  }
  .live-phone-header .num {
    font-family: var(--mono); font-size: 0.7rem;
    color: var(--ink-mute);
    margin-top: 0.15rem;
  }
  .live-phone-screen {
    flex: 1; padding: 0.7rem 0.3rem;
    display: flex; flex-direction: column; gap: 0.5rem;
    overflow-y: auto;
  }
  .live-bubble {
    max-width: 82%; padding: 0.6rem 0.85rem;
    border-radius: 16px;
    font-size: 0.84rem; line-height: 1.45;
    animation: liveBubbleIn 0.35s cubic-bezier(0.16, 1, 0.3, 1);
    position: relative;
  }
  @keyframes liveBubbleIn {
    from { opacity: 0; transform: translateY(10px) scale(0.94); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }
  .live-bubble.in {
    background: var(--surface); align-self: flex-start;
    border: 1px solid var(--line);
    border-bottom-left-radius: 4px;
  }
  .live-bubble.out {
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; align-self: flex-end;
    border-bottom-right-radius: 4px;
    box-shadow: 0 4px 12px -4px rgba(123,92,255,0.5);
  }
  .live-bubble .meta {
    display: block; font-size: 0.62rem; opacity: 0.6;
    margin-top: 0.2rem;
  }

  /* TYPING indicator */
  .live-typing {
    display: inline-flex; gap: 3px; align-items: center;
    padding: 0.65rem 0.9rem; border-radius: 16px;
    background: var(--surface); border: 1px solid var(--line);
    align-self: flex-start;
    animation: liveBubbleIn 0.3s;
  }
  .live-typing i {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--ink-mute);
    animation: liveTypingBounce 1.3s ease-in-out infinite;
  }
  .live-typing i:nth-child(2) { animation-delay: 0.2s; }
  .live-typing i:nth-child(3) { animation-delay: 0.4s; }
  @keyframes liveTypingBounce {
    0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
    30% { transform: translateY(-4px); opacity: 1; }
  }

  /* CALL CARD en phone */
  .live-call-card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 16px; padding: 0.85rem;
    display: flex; flex-direction: column; gap: 0.4rem;
    align-self: stretch;
    animation: liveBubbleIn 0.35s;
  }
  .live-call-card.ringing {
    border-color: var(--accent);
    animation: liveBubbleIn 0.35s, liveRing 0.7s infinite;
  }
  @keyframes liveRing {
    0%, 100% { box-shadow: 0 0 0 0 rgba(139,108,255,0); }
    50% { box-shadow: 0 0 0 6px rgba(139,108,255,0.15); }
  }
  .live-call-card.connected { border-color: var(--green); }
  .live-call-card .lc-title {
    font-weight: 600; font-size: 0.88rem;
    display: flex; align-items: center; gap: 0.4rem;
  }
  .live-call-card .lc-title .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 0 0 var(--green);
    animation: liveCallDot 1.2s ease-out infinite;
  }
  @keyframes liveCallDot {
    0% { box-shadow: 0 0 0 0 rgba(74,222,128,0.6); }
    100% { box-shadow: 0 0 0 10px rgba(74,222,128,0); }
  }
  .live-call-card .lc-meta {
    font-family: var(--mono); font-size: 0.7rem; color: var(--ink-soft);
  }
  .live-waveform {
    height: 36px; display: flex; align-items: center; gap: 2px;
    margin-top: 0.4rem;
  }
  .live-waveform i {
    flex: 1; background: var(--accent-2); border-radius: 1px;
    height: 6px;
  }
  .live-waveform.active i {
    animation: liveWave 0.7s ease-in-out infinite alternate;
  }
  .live-waveform i:nth-child(odd) { animation-delay: 0.05s; }
  .live-waveform i:nth-child(3n) { animation-delay: 0.10s; }
  .live-waveform i:nth-child(5n) { animation-delay: 0.15s; }
  .live-waveform i:nth-child(7n) { animation-delay: 0.20s; }
  @keyframes liveWave {
    0% { height: 4px; }
    50% { height: 26px; }
    100% { height: 8px; }
  }

  /* TIMELINE de steps */
  .live-timeline {
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1.4rem;
  }
  .live-timeline h3 {
    font-family: var(--mono); font-size: 0.7rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.14em;
    color: var(--ink-mute); margin: 0 0 0.9rem;
  }
  .live-steps {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 0.7rem;
  }
  @media (max-width: 1080px) {
    .live-steps { grid-template-columns: repeat(3, 1fr); }
  }
  @media (max-width: 600px) {
    .live-steps { grid-template-columns: repeat(2, 1fr); }
  }
  .live-step {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.75rem 0.85rem;
    opacity: 0.4; transition: opacity 0.3s, border-color 0.3s, background 0.3s;
  }
  .live-step.active {
    opacity: 1; border-color: var(--accent);
    background: rgba(139,108,255,0.08);
    box-shadow: 0 0 18px rgba(139,108,255,0.18);
  }
  .live-step.done { opacity: 0.85; border-color: rgba(74,222,128,0.35); }
  .live-step-num {
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 50%;
    background: var(--bg-soft); border: 1px solid var(--line);
    font-family: var(--mono); font-size: 0.7rem; font-weight: 600;
    color: var(--ink-mute);
    margin-bottom: 0.4rem;
  }
  .live-step.active .live-step-num { color: var(--accent-2); border-color: var(--accent); }
  .live-step.done .live-step-num {
    color: var(--green); border-color: rgba(74,222,128,0.5);
  }
  .live-step.done .live-step-num::after { content: "✓"; }
  .live-step-title {
    font-size: 0.86rem; font-weight: 600;
    margin-bottom: 0.2rem;
    color: var(--ink);
  }
  .live-step-detail {
    font-family: var(--mono); font-size: 0.7rem; color: var(--ink-mute);
    word-break: break-all;
  }
  .live-step-time {
    display: inline-block; margin-top: 0.45rem;
    font-family: var(--mono); font-size: 0.66rem;
    color: var(--ink-mute);
  }
  .live-step.active .live-step-time { color: var(--accent-2); }
  .live-step.done .live-step-time { color: var(--green); }

  /* SUB-STATUS bar central */
  .live-narration {
    text-align: center;
    margin: 1rem auto 1.6rem;
    font-family: var(--mono); font-size: 0.95rem;
    color: var(--ink-soft);
    min-height: 1.5rem;
    transition: opacity 0.3s;
    max-width: 800px;
  }
  .live-narration .act {
    display: inline-block;
    color: var(--accent); font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.14em;
    font-size: 0.74rem;
    margin-right: 0.6rem;
    padding: 0.16rem 0.55rem;
    background: rgba(139,108,255,0.10);
    border-radius: 4px;
  }

  /* SUMMARY final */
  .live-summary {
    margin-top: 1.4rem;
    background: linear-gradient(180deg, var(--bg-soft), var(--bg));
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 2rem 2.2rem;
    display: none;
    text-align: center;
  }
  .live-summary.show {
    display: block;
    animation: liveSummaryIn 0.7s cubic-bezier(0.16, 1, 0.3, 1);
  }
  @keyframes liveSummaryIn {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .live-summary .badge {
    display: inline-block;
    font-family: var(--mono); font-size: 0.7rem; font-weight: 600;
    color: var(--green); text-transform: uppercase; letter-spacing: 0.16em;
    padding: 0.3rem 0.8rem;
    background: rgba(74,222,128,0.12);
    border-radius: 99px;
    margin-bottom: 1rem;
  }
  .live-summary h3 {
    margin: 0 0 1.6rem;
    font-size: 1.8rem; font-weight: 700;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.02em;
  }
  .live-metric-grid {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem;
    max-width: 900px; margin: 0 auto;
  }
  @media (max-width: 720px) {
    .live-metric-grid { grid-template-columns: repeat(2, 1fr); }
  }
  .live-metric {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 10px; padding: 1.1rem 0.9rem;
  }
  .live-metric .label {
    font-family: var(--mono); font-size: 0.64rem;
    text-transform: uppercase; letter-spacing: 0.14em;
    color: var(--ink-mute); margin-bottom: 0.55rem;
  }
  .live-metric .value {
    font-family: var(--mono); font-size: 1.5rem; font-weight: 700;
    color: var(--ink); letter-spacing: -0.01em;
  }
  .live-metric .value.green { color: var(--green); }
  .live-metric .value.accent { color: var(--accent-2); }
  .live-summary .live-ids {
    margin-top: 1.4rem; font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink-soft);
    background: var(--surface); padding: 0.95rem 1.1rem; border-radius: 8px;
    border: 1px solid var(--line);
    word-break: break-all;
    max-width: 900px; margin-left: auto; margin-right: auto;
    text-align: left;
  }
  .live-summary .live-ids .k { color: var(--ink-mute); }
  .live-summary .live-ids .v { color: var(--accent-2); }

  /* bilingual */
  html[lang="es"] [data-lang="en"] { display: none; }
  html[lang="en"] [data-lang="es"] { display: none; }
"""


def render_live_page(lang: str = "es") -> str:
    """Página /live — demo cinemática extendida para reunión presencial."""
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMI · Live demo</title>
<meta name="description" content="Live cinematic demo of AMI — the full mobile identity lifecycle narrated in 25 seconds.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_LIVE_CSS}</style>
</head>
<body>

  <main class="live-shell">
    <section class="live-hero">
      <div class="live-eyebrow">
        <span data-lang="es">live demo</span>
        <span data-lang="en">live demo</span>
      </div>
      <h1>
        <span data-lang="es">Un agente. Un número. <span class="grad">El ciclo entero, narrado.</span></span>
        <span data-lang="en">One agent. One number. <span class="grad">The full lifecycle, narrated.</span></span>
      </h1>
      <p>
        <span data-lang="es">Provisión, SMS bidireccional y llamada bridgeada por SIP — todo el viaje en pantalla mientras lo explicas.</span>
        <span data-lang="en">Provisioning, two-way SMS, SIP-bridged voice — the entire journey on screen while you narrate.</span>
      </p>
    </section>

    <div class="live-controls">
      <div class="field">
        <label for="liveNumber">
          <span data-lang="es">Número del destinatario (lo que ven en pantalla)</span>
          <span data-lang="en">Recipient number (what they see on screen)</span>
        </label>
        <input id="liveNumber" type="text" value="+34 600 ███ ███" autocomplete="off">
      </div>
      <div class="speed">
        <button data-speed="0.25">very slow</button>
        <button data-speed="0.4" class="active">slow</button>
        <button data-speed="0.6">normal</button>
        <button data-speed="1.0">fast</button>
      </div>
      <button class="live-btn-primary" id="liveStart">
        <span data-lang="es">▶ Iniciar demo</span>
        <span data-lang="en">▶ Start demo</span>
      </button>
      <button class="live-btn-ghost" id="liveReset">
        <span data-lang="es">Reset</span>
        <span data-lang="en">Reset</span>
      </button>
    </div>

    <!-- NARRATION BAR central -->
    <div class="live-narration" id="liveNarr">
      <span data-lang="es">Listo. Pulsa <strong>Iniciar demo</strong> y la animación empieza.</span>
      <span data-lang="en">Ready. Press <strong>Start demo</strong> and the animation begins.</span>
    </div>

    <!-- STAGE -->
    <div class="live-stage">
      <canvas class="live-flow-canvas" id="liveCanvas"></canvas>
      <div class="live-stage-grid">
        <!-- AGENT (izquierda) -->
        <div class="live-actor agent" id="liveAgent">
          <div class="live-actor-header">
            <div class="live-actor-icon">◉</div>
            <div>
              <div class="live-actor-title">
                <span data-lang="es">Tu agente AI</span>
                <span data-lang="en">Your AI agent</span>
              </div>
              <div class="live-actor-sub">
                <span data-lang="es">agente · cualquier framework</span>
                <span data-lang="en">agent · any framework</span>
              </div>
            </div>
          </div>
          <div class="live-actor-think" id="agentThink">
            <span class="lbl">
              <span data-lang="es">acción del agente</span>
              <span data-lang="en">agent action</span>
            </span>
            <div id="agentThinkText">—</div>
          </div>
        </div>

        <!-- AMI STACK (centro) -->
        <div class="live-actor stack" id="liveStack">
          <div class="live-actor-header">
            <div class="live-actor-icon">◆</div>
            <div>
              <div class="live-actor-title">AMI</div>
              <div class="live-actor-sub">
                <span data-lang="es">protocolo + stack propio</span>
                <span data-lang="en">protocol + own stack</span>
              </div>
            </div>
          </div>
          <div class="live-modules">
            <div class="live-module" id="modBackend">Backend</div>
            <div class="live-module" id="modNumbers">Numbers</div>
            <div class="live-module" id="modKannel">Kannel (SMS)</div>
            <div class="live-module" id="modAsterisk">Asterisk (voz)</div>
            <div class="live-module" id="modWebhooks">Webhooks</div>
            <div class="live-module" id="modAudit">Audit log</div>
          </div>
          <div class="live-number-reveal" id="liveNumber Reveal">
            <div class="lbl">
              <span data-lang="es">MobileIdentity activa</span>
              <span data-lang="en">MobileIdentity active</span>
            </div>
            <div class="num" id="liveAssignedNumber">—</div>
            <div class="meta" id="liveMidId">—</div>
          </div>
        </div>

        <!-- PHONE (derecha) -->
        <div class="live-phone" id="livePhoneEl">
          <div class="live-phone-bar">
            <span>9:41</span>
            <span class="signal">●●●● LTE</span>
          </div>
          <div class="live-phone-header">
            <div class="who">
              <span data-lang="es">Tu socio · móvil</span>
              <span data-lang="en">Your partner · phone</span>
            </div>
            <div class="num" id="livePhoneNum">—</div>
          </div>
          <div class="live-phone-screen" id="livePhone"></div>
        </div>
      </div>
    </div>

    <!-- TIMELINE de los 6 actos -->
    <div class="live-timeline">
      <h3>
        <span data-lang="es">los 6 actos</span>
        <span data-lang="en">the 6 acts</span>
      </h3>
      <div class="live-steps" id="liveSteps">
        <div class="live-step" data-step="0">
          <div class="live-step-num">1</div>
          <div class="live-step-title">
            <span data-lang="es">Solicitud + oferta</span>
            <span data-lang="en">Request + offer</span>
          </div>
          <div class="live-step-detail">POST /v1/sim-requests</div>
          <span class="live-step-time" data-time>—</span>
        </div>
        <div class="live-step" data-step="1">
          <div class="live-step-num">2</div>
          <div class="live-step-title">
            <span data-lang="es">Firma + activación</span>
            <span data-lang="en">Sign + activate</span>
          </div>
          <div class="live-step-detail">→ MID + agent_token</div>
          <span class="live-step-time" data-time>—</span>
        </div>
        <div class="live-step" data-step="2">
          <div class="live-step-num">3</div>
          <div class="live-step-title">
            <span data-lang="es">SMS saliente</span>
            <span data-lang="en">Outbound SMS</span>
          </div>
          <div class="live-step-detail">SMPP · SUBMIT_SM</div>
          <span class="live-step-time" data-time>—</span>
        </div>
        <div class="live-step" data-step="3">
          <div class="live-step-num">4</div>
          <div class="live-step-title">
            <span data-lang="es">SMS entrante</span>
            <span data-lang="en">Inbound SMS</span>
          </div>
          <div class="live-step-detail">webhook sms.inbound</div>
          <span class="live-step-time" data-time>—</span>
        </div>
        <div class="live-step" data-step="4">
          <div class="live-step-num">5</div>
          <div class="live-step-title">
            <span data-lang="es">Llamada · bridge SIP</span>
            <span data-lang="en">Call · SIP bridge</span>
          </div>
          <div class="live-step-detail">SIP INVITE → callback_sip_uri</div>
          <span class="live-step-time" data-time>—</span>
        </div>
        <div class="live-step" data-step="5">
          <div class="live-step-num">6</div>
          <div class="live-step-title">
            <span data-lang="es">Audit + cierre</span>
            <span data-lang="en">Audit + close</span>
          </div>
          <div class="live-step-detail">webhook call.completed</div>
          <span class="live-step-time" data-time>—</span>
        </div>
      </div>
    </div>

    <!-- SUMMARY -->
    <section class="live-summary" id="liveSummary">
      <div class="badge">
        <span data-lang="es">demo completada</span>
        <span data-lang="en">demo completed</span>
      </div>
      <h3>
        <span data-lang="es">Todo el ciclo de vida · sub-segundo en el backend</span>
        <span data-lang="en">Full lifecycle · sub-second on the backend</span>
      </h3>
      <div class="live-metric-grid">
        <div class="live-metric">
          <div class="label">
            <span data-lang="es">Provisión</span>
            <span data-lang="en">Provisioning</span>
          </div>
          <div class="value accent" data-sum-prov>—</div>
        </div>
        <div class="live-metric">
          <div class="label">SMS</div>
          <div class="value accent" data-sum-sms>—</div>
        </div>
        <div class="live-metric">
          <div class="label">
            <span data-lang="es">Llamada</span>
            <span data-lang="en">Call</span>
          </div>
          <div class="value accent" data-sum-call>—</div>
        </div>
        <div class="live-metric">
          <div class="label">
            <span data-lang="es">Total demo</span>
            <span data-lang="en">Total demo</span>
          </div>
          <div class="value green" data-sum-total>—</div>
        </div>
      </div>
      <div class="live-ids" data-ids>
        <span class="k">backend:</span> <span class="v">—</span>
      </div>
    </section>
  </main>

<script>
(function() {{
  var startBtn = document.getElementById('liveStart');
  var resetBtn = document.getElementById('liveReset');
  var numInput = document.getElementById('liveNumber');
  var phoneEl  = document.getElementById('livePhone');
  var phoneNumEl = document.getElementById('livePhoneNum');
  var narr     = document.getElementById('liveNarr');
  var summary  = document.getElementById('liveSummary');
  var steps    = document.querySelectorAll('.live-step');
  var agent    = document.getElementById('liveAgent');
  var stack    = document.getElementById('liveStack');
  var agentThink = document.getElementById('agentThink');
  var agentThinkText = document.getElementById('agentThinkText');
  var numberReveal = document.getElementById('liveNumber Reveal');
  var assignedNum = document.getElementById('liveAssignedNumber');
  var midId = document.getElementById('liveMidId');
  var canvas = document.getElementById('liveCanvas');
  var ctx = canvas.getContext('2d');
  var speedButtons = document.querySelectorAll('[data-speed]');

  var running = false;
  var timers = [];
  var intervals = [];
  // Default 0.4 = animación al 40% de la velocidad original. La duración total
  // pasa de ~22s a ~55s con el default "slow". El moderador puede subir o
  // bajar en vivo desde el selector. Pensado para reuniones presenciales
  // donde hay que narrar mientras pasa.
  var speed = 0.4;
  var particles = [];
  var W = 0, H = 0;
  var dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));

  // ----- Canvas resize -----
  function resizeCanvas() {{
    var rect = canvas.parentElement.getBoundingClientRect();
    W = rect.width - 28; H = rect.height - 28;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }}
  resizeCanvas();
  window.addEventListener('resize', resizeCanvas);

  function pos(el) {{
    var stageRect = canvas.parentElement.getBoundingClientRect();
    var r = el.getBoundingClientRect();
    return {{
      x: r.left - stageRect.left - 14 + r.width / 2,
      y: r.top  - stageRect.top  - 14 + r.height / 2
    }};
  }}

  // ----- Particle system -----
  function emit(fromEl, toEl, color, opts) {{
    opts = opts || {{}};
    var a = pos(fromEl), b = pos(toEl);
    particles.push({{
      fromX: a.x, fromY: a.y, toX: b.x, toY: b.y,
      x: a.x, y: a.y, t: 0,
      speed: (opts.speed || 0.015) * speed,
      color: color || '#5dd1ff',
      size: opts.size || 3,
      tail: opts.tail !== false,
    }});
  }}

  function draw() {{
    ctx.clearRect(0, 0, W, H);
    particles.forEach(function(p) {{
      p.t += p.speed;
      p.x = p.fromX + (p.toX - p.fromX) * p.t;
      p.y = p.fromY + (p.toY - p.fromY) * p.t;
      var fade = p.t > 0.85 ? (1 - (p.t - 0.85) / 0.15) : 1;
      if (p.tail) {{
        ctx.strokeStyle = p.color;
        ctx.globalAlpha = 0.35 * fade;
        ctx.lineWidth = 1.5;
        var back = 0.08;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(
          p.fromX + (p.toX - p.fromX) * Math.max(0, p.t - back),
          p.fromY + (p.toY - p.fromY) * Math.max(0, p.t - back)
        );
        ctx.stroke();
      }}
      ctx.fillStyle = p.color;
      ctx.globalAlpha = fade;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }});
    particles = particles.filter(function(p) {{ return p.t < 1; }});
    requestAnimationFrame(draw);
  }}
  requestAnimationFrame(draw);

  // ----- Helpers -----
  function setNarr(act, text) {{
    if (act) {{
      narr.innerHTML = '<span class="act">acto ' + act + '</span>' + text;
    }} else {{
      narr.innerHTML = text;
    }}
  }}
  function think(text) {{
    agentThink.classList.add('show');
    agentThinkText.textContent = text;
  }}
  function clearThink() {{
    agentThink.classList.remove('show');
  }}
  function activateModule(id, ms) {{
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add('active');
    // Duración por defecto generosa para que se vea el "active" mientras se
    // narra. Se divide por speed (speed=0.4 → 2.5× más largo en pantalla).
    timers.push(setTimeout(function() {{ el.classList.remove('active'); }},
                            (ms || 2400) / speed));
  }}
  function pulse(actorEl, ms) {{
    actorEl.classList.add('pulsing');
    timers.push(setTimeout(function() {{ actorEl.classList.remove('pulsing'); }},
                            (ms || 2000) / speed));
  }}
  function step(idx, state) {{
    var el = steps[idx];
    el.classList.remove('active', 'done');
    if (state) el.classList.add(state);
    if (state === 'active') {{
      var t = Math.round((performance.now() - startTime) / 100) / 10;
      el.querySelector('[data-time]').textContent = 't+' + t.toFixed(1) + 's';
    }} else if (state === 'done') {{
      // mantener timestamp del active
    }}
  }}

  function addTyping() {{
    var t = document.createElement('div');
    t.className = 'live-typing'; t.id = 'liveTyping';
    t.innerHTML = '<i></i><i></i><i></i>';
    phoneEl.appendChild(t);
    phoneEl.scrollTop = phoneEl.scrollHeight;
  }}
  function removeTyping() {{
    var t = document.getElementById('liveTyping');
    if (t) t.remove();
  }}
  function addBubble(text, direction, meta) {{
    var b = document.createElement('div');
    b.className = 'live-bubble ' + direction;
    b.innerHTML = text + (meta ? '<span class="meta">' + meta + '</span>' : '');
    phoneEl.appendChild(b);
    phoneEl.scrollTop = phoneEl.scrollHeight;
  }}
  function addCallCard() {{
    var card = document.createElement('div');
    card.className = 'live-call-card ringing';
    card.id = 'callCard';
    card.innerHTML =
      '<div class="lc-title"><span data-lang="es">📞 Llamada entrante</span><span data-lang="en">📞 Incoming call</span></div>' +
      '<div class="lc-meta" data-lc-meta>ringing…</div>' +
      '<div class="live-waveform" id="waveform">' + Array(22).fill('<i></i>').join('') + '</div>';
    phoneEl.appendChild(card);
    phoneEl.scrollTop = phoneEl.scrollHeight;
  }}

  function later(ms, fn) {{
    timers.push(setTimeout(fn, ms / speed));
  }}
  function clearAll() {{
    timers.forEach(clearTimeout); timers = [];
    intervals.forEach(clearInterval); intervals = [];
    steps.forEach(function(s) {{
      s.classList.remove('active', 'done');
      s.querySelector('[data-time]').textContent = '—';
    }});
    phoneEl.innerHTML = '';
    summary.classList.remove('show');
    numberReveal.classList.remove('show');
    assignedNum.textContent = '—';
    midId.textContent = '—';
    clearThink();
    agent.classList.remove('pulsing');
    stack.classList.remove('pulsing');
    particles = [];
    setNarr(0, '<span data-lang="es">Listo. Pulsa <strong>Iniciar demo</strong>.</span>' +
                '<span data-lang="en">Ready. Press <strong>Start demo</strong>.</span>');
  }}

  // ----- Speed selector -----
  speedButtons.forEach(function(b) {{
    b.addEventListener('click', function() {{
      speedButtons.forEach(function(x) {{ x.classList.remove('active'); }});
      b.classList.add('active');
      speed = parseFloat(b.getAttribute('data-speed'));
    }});
  }});

  // ----- Number counter rolling effect -----
  function rollNumber(targetText, ms) {{
    var startedAt = performance.now();
    var duration = (ms || 700) / speed;
    var len = targetText.length;
    function tick() {{
      var p = Math.min(1, (performance.now() - startedAt) / duration);
      var ease = 1 - Math.pow(1 - p, 3);
      var revealed = Math.floor(ease * len);
      var out = '';
      for (var i = 0; i < len; i++) {{
        if (i < revealed) {{
          out += targetText[i];
        }} else {{
          var ch = targetText[i];
          if (ch >= '0' && ch <= '9') out += String(Math.floor(Math.random() * 10));
          else out += ch;
        }}
      }}
      assignedNum.textContent = out;
      if (p < 1) requestAnimationFrame(tick);
      else assignedNum.textContent = targetText;
    }}
    requestAnimationFrame(tick);
  }}

  // ===================== THE STORY =====================
  var startTime = 0;
  async function runDemo() {{
    if (running) return;
    running = true;
    startBtn.disabled = true;
    clearAll();

    var recipient = (numInput.value || '+34 600 ███ ███').trim();
    phoneNumEl.textContent = recipient;

    // Disparar la demo real en paralelo para los IDs finales
    var realDemo = fetch('/v1/demo/quick', {{ method: 'POST' }})
      .then(function(r) {{ return r.ok ? r.json() : null; }})
      .catch(function() {{ return null; }});

    var fakeNumber = '+34 600 ' + Math.floor(100000 + Math.random() * 900000);
    var fakeMid = 'mid_' + Math.random().toString(36).slice(2, 12);

    startTime = performance.now();

    // ============== ACTO 1 · Solicitud + oferta (0-3.5s) ==============
    setNarr(1, '<span data-lang="es">Tu agente pide un número para operar.</span>' +
                '<span data-lang="en">Your agent asks for a number to operate.</span>');
    step(0, 'active');
    pulse(agent, 1100);
    think('"I need a phone number to send SMS reminders to my users."');

    later(700, function() {{
      emit(agent, document.getElementById('modBackend'), '#5dd1ff', {{ speed: 0.018 }});
      activateModule('modBackend', 1400);
    }});
    later(1400, function() {{
      think('POST /v1/sim-requests · {{country: "ES", capabilities: ["sms","voice"]}}');
      activateModule('modAudit', 800);
    }});
    later(2200, function() {{
      emit(document.getElementById('modBackend'), agent, '#a78bff');
      think('← offer_xxx · €8.90/mo · ready');
    }});
    later(3200, function() {{
      step(0, 'done');
    }});

    // ============== ACTO 2 · Firma + activación (3.5-7.5s) ==============
    later(3500, function() {{
      setNarr(2, '<span data-lang="es">Firmamos contrato (mock-sign) y activamos la identidad móvil.</span>' +
                  '<span data-lang="en">We sign the contract (mock-sign) and activate the mobile identity.</span>');
      step(1, 'active');
      pulse(stack, 1300);
      think('POST /v1/contracts → POST /v1/contracts/{{id}}/mock-sign');
    }});
    later(4000, function() {{
      activateModule('modBackend', 1100);
      emit(agent, document.getElementById('modBackend'), '#5dd1ff', {{ speed: 0.020 }});
    }});
    later(4700, function() {{
      activateModule('modNumbers', 1800);
      emit(document.getElementById('modBackend'), document.getElementById('modNumbers'), '#a78bff');
      think('numbers: asignando MSISDN del inventario propio…');
    }});
    later(5800, function() {{
      activateModule('modAudit', 900);
      numberReveal.classList.add('show');
      assignedNum.textContent = '+34 600 000 000';
      midId.textContent = fakeMid;
      // Rolling lento del número para que el público lo vea cambiar.
      rollNumber(fakeNumber, 2200);
    }});
    later(6800, function() {{
      think('POST /v1/mobile-identities/activate → agent_token devuelto UNA SOLA VEZ');
      emit(document.getElementById('modNumbers'), agent, '#a78bff');
    }});
    later(7400, function() {{
      step(1, 'done');
      clearThink();
    }});

    // ============== ACTO 3 · SMS saliente (7.5-12.5s) ==============
    later(7700, function() {{
      setNarr(3, '<span data-lang="es">El agente envía un recordatorio por SMS desde su nuevo número.</span>' +
                  '<span data-lang="en">The agent sends a reminder SMS from its new number.</span>');
      step(2, 'active');
      pulse(agent, 1100);
      think('agent.send_sms(to="' + recipient + '", body="Hola, recordatorio de cita mañana 10:00…")');
    }});
    later(8500, function() {{
      emit(agent, document.getElementById('modBackend'), '#5dd1ff');
      activateModule('modBackend', 900);
    }});
    later(9200, function() {{
      activateModule('modKannel', 1500);
      emit(document.getElementById('modBackend'), document.getElementById('modKannel'), '#a78bff');
      think('Kannel: SUBMIT_SM por SMPP al partner telco…');
    }});
    later(10100, function() {{
      addBubble('<span data-lang="es">Hola 👋 Recordatorio: cita mañana a las 10:00. ¿Confirmas?</span>' +
                '<span data-lang="en">Hi 👋 Reminder: appointment tomorrow at 10:00. Confirm?</span>',
                'out',
                '<span data-lang="es">desde ' + fakeNumber + ' · entregado</span>' +
                '<span data-lang="en">from ' + fakeNumber + ' · delivered</span>');
    }});
    later(11000, function() {{
      activateModule('modAudit', 800);
      think('sms_delivered ← DLR confirmado');
    }});
    later(12200, function() {{
      step(2, 'done');
      clearThink();
    }});

    // ============== ACTO 4 · SMS entrante (12.5-16s) ==============
    later(12500, function() {{
      setNarr(4, '<span data-lang="es">El destinatario responde. AMI lo recibe y dispara webhook al agente.</span>' +
                  '<span data-lang="en">Recipient replies. AMI receives it and fires webhook to the agent.</span>');
      step(3, 'active');
      addTyping();
    }});
    later(15000, function() {{
      // Typing indicator visible ~2.5s para que el público vea "está escribiendo"
      removeTyping();
      addBubble('<span data-lang="es">OK, allí estaré ✓</span>' +
                '<span data-lang="en">OK, I will be there ✓</span>',
                'in');
    }});
    later(14200, function() {{
      activateModule('modKannel', 900);
      activateModule('modBackend', 1100);
    }});
    later(14800, function() {{
      activateModule('modWebhooks', 1200);
      emit(document.getElementById('modWebhooks'), agent, '#82e0a4');
      think('webhook sms.inbound entregado a tu URL · HMAC-SHA256 verificado');
    }});
    later(15800, function() {{
      step(3, 'done');
      clearThink();
    }});

    // ============== ACTO 5 · Llamada + audio bidireccional (16-21s) ==============
    later(16100, function() {{
      setNarr(5, '<span data-lang="es">El agente origina una llamada. AMI bridgea el RTP a tu motor de voz.</span>' +
                  '<span data-lang="en">The agent originates a call. AMI bridges the RTP to your voice engine.</span>');
      step(4, 'active');
      pulse(agent, 1300);
      think('agent.place_call(to="' + recipient + '", callback_sip_uri="sip:proj@your-voice-engine")');
    }});
    later(16800, function() {{
      emit(agent, document.getElementById('modBackend'), '#5dd1ff');
      activateModule('modBackend', 900);
    }});
    later(17400, function() {{
      activateModule('modAsterisk', 2400);
      emit(document.getElementById('modBackend'), document.getElementById('modAsterisk'), '#a78bff');
      think('Asterisk: SIP INVITE al PSTN · ringing…');
    }});
    later(18200, function() {{
      addCallCard();
    }});
    later(19000, function() {{
      var card = document.getElementById('callCard');
      if (card) {{
        card.classList.remove('ringing');
        card.classList.add('connected');
        var meta = card.querySelector('[data-lc-meta]');
        if (meta) meta.textContent = 'connected · SIP bridge active';
        var wf = card.querySelector('.live-waveform');
        if (wf) wf.classList.add('active');
      }}
      think('audio bidireccional · 2-leg bridge · RTP fluyendo');
      // Particles bidireccional para representar audio fluyendo. Densidad
      // controlada: menos frecuencia + speed más alta para que no se
      // acumulen partículas viajando cuando la summary aparece después
      // (esas partículas zombi parecían "flechitas sin destino").
      var audioId = setInterval(function() {{
        emit(agent, stack, '#5dd1ff', {{ speed: 0.050, size: 2, tail: false }});
        emit(stack, agent, '#82e0a4', {{ speed: 0.050, size: 2, tail: false }});
      }}, 380 / speed);
      intervals.push(audioId);
      later(3500, function() {{
        clearInterval(audioId);
        intervals = intervals.filter(function(x) {{ return x !== audioId; }});
        // Limpiar partículas en vuelo: evita que sigan dibujándose
        // por encima del audit/summary del siguiente acto.
        particles = [];
      }});
    }});
    later(21000, function() {{
      var card = document.getElementById('callCard');
      if (card) {{
        var meta = card.querySelector('[data-lc-meta]');
        if (meta) meta.textContent = 'completed · 2.4s';
        var wf = card.querySelector('.live-waveform');
        if (wf) wf.classList.remove('active');
      }}
      step(4, 'done');
    }});

    // ============== ACTO 6 · Audit + cierre (21-23s) ==============
    later(21400, function() {{
      setNarr(6, '<span data-lang="es">AMI cierra el audit log y dispara webhook call.completed.</span>' +
                  '<span data-lang="en">AMI closes the audit log and fires webhook call.completed.</span>');
      step(5, 'active');
      activateModule('modAudit', 1100);
      activateModule('modWebhooks', 1100);
      think('audit log persisted · webhook call.completed delivered');
    }});
    later(22500, function() {{
      step(5, 'done');
      clearThink();
    }});

    // ============== Summary (23-25s) ==============
    later(23000, function() {{
      // Limpieza final: cualquier partícula residual (del audio o de los
      // ultimos webhooks) se descarta antes de mostrar la summary. Sin esto
      // se veían "flechitas sin destino" al final del demo.
      particles = [];
      setNarr(0, '<span class="act"><span data-lang="es">listo</span><span data-lang="en">done</span></span>' +
                  '<span data-lang="es">todo bajo nuestro stack propio. Cero proveedores externos.</span>' +
                  '<span data-lang="en">all under our own stack. Zero external providers.</span>');
      var totalMs = Math.round((performance.now() - startTime));
      document.querySelector('[data-sum-prov]').textContent = '< 1 ms';
      document.querySelector('[data-sum-sms]').textContent  = '< 1 ms';
      document.querySelector('[data-sum-call]').textContent = '< 1 ms';
      document.querySelector('[data-sum-total]').textContent = (totalMs / 1000).toFixed(1) + ' s';

      realDemo.then(function(data) {{
        var html;
        if (data && data.identity) {{
          var mid = data.identity.id || fakeMid;
          html = '<span class="k">mid:</span> <span class="v">' + mid + '</span> · ' +
                 '<span class="k">phone:</span> <span class="v">' + (data.identity.phone_number || fakeNumber) + '</span> · ' +
                 '<span class="k">backend:</span> <span class="v">live (mock telco)</span> · ' +
                 '<span class="k">events:</span> <span class="v">' + (data.events_count || '—') + '</span>';
        }} else {{
          html = '<span class="k">backend:</span> <span class="v">live</span> · ' +
                 '<span class="k">phone:</span> <span class="v">' + fakeNumber + '</span>';
        }}
        document.querySelector('[data-ids]').innerHTML = html;
        summary.classList.add('show');
        // resumen real del backend ya disponible
        if (data && typeof data.total_ms === 'number') {{
          document.querySelector('[data-sum-prov]').textContent = (data.t_provision_ms || '< 1') + ' ms';
          document.querySelector('[data-sum-sms]').textContent  = (data.t_sms_ms || '< 1') + ' ms';
          document.querySelector('[data-sum-call]').textContent = (data.t_call_ms || '< 1') + ' ms';
        }}
        startBtn.disabled = false;
        running = false;
      }});
    }});
  }}

  startBtn.addEventListener('click', runDemo);
  resetBtn.addEventListener('click', function() {{
    clearAll();
    startBtn.disabled = false;
    running = false;
  }});

  // Resize del canvas con un pequeño debounce tras el primer render
  setTimeout(resizeCanvas, 100);
}})();
</script>

</body>
</html>"""
