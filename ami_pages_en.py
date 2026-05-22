"""English versions of /experience and /diagram pages.

ami_api.py mantiene las versiones en castellano (default) y este módulo
provee las versiones en inglés cuando lang=en. El dispatcher elige cuál
servir según _detect_lang().

NOTA: los recursos compartidos (FAVICON, logo, URLs, html_escape) se
importan LAZY desde ami_api dentro de cada función para evitar el ciclo
de imports (ami_api importa este módulo al cargar).
"""
from __future__ import annotations


def _shared():
    """Late binding de los recursos compartidos para evitar circular import."""
    from ami_api import (FAVICON_SVG_DATA_URI, AMI_LOGO_SVG, REPO_URL,
                          MCP_HTTP_URL, html_escape)
    return (FAVICON_SVG_DATA_URI, AMI_LOGO_SVG, REPO_URL, MCP_HTTP_URL, html_escape)


# Versión inglesa del listado de tools (paralela a _tools_for_landing()
# en ami_api.py). Si añades una tool allí, refléjala aquí.
def _tools_for_landing_en():
    return [
        # --- Contracting and provisioning (v1) ---
        ("ami.search_sim_options",      "Lists available countries and numbering capabilities (voice · SMS · data)."),
        ("ami.request_sim_offer",       "Creates a number request and returns an immediate offer from our platform."),
        ("ami.accept_offer",            "Accepts an offer before contract generation."),
        ("ami.submit_customer_data",    "Submits the customer's legal/tax data and binds it to the request."),
        ("ami.create_contract",         "Generates the contract and returns the signature URL."),
        ("ami.get_contract_status",     "Reads the current status of a contract."),
        ("ami.confirm_signature_status","Checks whether the contract has already been signed."),
        ("ami.activate_sim_identity",   "Activates the number on our platform after the contract is signed."),
        ("ami.get_identity_status",     "Reads the status of an active MobileIdentity."),
        ("ami.cancel_request",          "Cancels the request before number activation."),
        ("ami.rotate_agent_token",      "Rotates the agent_token of a MobileIdentity (hard rotate, invalidates the previous one)."),
        # --- Operations v2 · SMS ---
        ("ami.send_sms",                "Sends an SMS from the active MobileIdentity (auth: agent_token Level 2)."),
        ("ami.list_sms",                "Lists the SMS of the MobileIdentity (filterable by direction)."),
        # --- Operations v2 · Voice (bridge-by-API) ---
        ("ami.place_call",              "Originates an outbound call and bridges it via SIP to the customer endpoint (voice engine, PBX or any SIP destination)."),
        ("ami.list_calls",              "Lists the calls of the MID (filterable by direction)."),
        ("ami.get_call",                "Detail of a call (scoped to the MID)."),
        ("ami.hangup_call",             "Ends an in-progress call of the MID."),
        ("ami.set_inbound_sip_uri",     "Configures the SIP endpoint where incoming calls of the MID are forwarded."),
        # --- Outbound webhooks ---
        ("ami.create_webhook",          "Registers an outbound webhook for a MID (inbound sms/call events and status)."),
        ("ami.list_webhooks",           "Lists the webhooks registered for a MID."),
        ("ami.delete_webhook",          "Deletes a webhook by id."),
        # --- Rate limits + spending ---
        ("ami.get_limits",              "Reads the limits (rate + budget + countries) of a MID."),
        ("ami.update_limits",           "Updates the limits of the MID (partial patch)."),
        ("ami.get_usage",               "Reads the current usage of the MID (auth: customer)."),
        ("ami.get_my_usage",            "Reads the usage of the MID that owns the agent_token (auth: agent)."),
        # --- Audit ---
        ("ami.list_events",             "Returns the latest AuditEvents (debug and inspection)."),
    ]


def render_experience_page_en() -> str:
    """/experience page in English.

    1:1 visual parallel of render_experience_page() in ami_api.py.
    All identifiers (CSS classes, HTML IDs, JS variable names) are
    preserved verbatim so shared JS keeps working.
    """
    FAVICON_SVG_DATA_URI, AMI_LOGO_SVG, REPO_URL, MCP_HTTP_URL, html_escape = _shared()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · Experience</title>
<meta name="description" content="Mobile identity for AI agents. A visual experience of the protocol: from the number generated in milliseconds to the worldwide operator network.">
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --line: #1f1f2c;
    --line-soft: #16161f;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --accent-3: #ff5cb6;
    --accent-bg: rgba(139,108,255,0.10);
    --green: #4ade80;
    --amber: #fbbf24;
    --code-bg: #0a0a12;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "JetBrains Mono", "SF Mono", "Menlo", "Monaco", monospace;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    font-family: var(--sans);
    color: var(--ink);
    background: var(--bg);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
  }}
  ::selection {{ background: var(--accent); color: #fff; }}
  a {{ color: var(--accent-2); text-decoration: none; }}
  a:hover {{ color: #b9e6ff; }}

  /* HEADER */
  .exp-header {{
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    padding: 1rem 0;
    background: rgba(6, 6, 10, 0.6);
    backdrop-filter: saturate(140%) blur(14px);
    -webkit-backdrop-filter: saturate(140%) blur(14px);
    border-bottom: 1px solid rgba(255,255,255,0.05);
    transition: opacity 0.3s, transform 0.3s;
  }}
  .exp-header.hidden {{ opacity: 0; transform: translateY(-100%); pointer-events: none; }}
  .exp-header .wrap {{
    max-width: 1280px; margin: 0 auto; padding: 0 1.75rem;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .exp-brand {{
    font-family: var(--mono); font-weight: 700; font-size: 0.95rem;
    letter-spacing: 0.02em; display: flex; align-items: center; gap: 0.6rem;
    color: var(--ink); text-decoration: none;
  }}
  .exp-brand .dot {{ color: var(--accent); }}
  .exp-nav {{ display: flex; align-items: center; gap: 1.4rem; }}
  .exp-nav a {{ color: var(--ink-soft); font-size: 0.85rem; font-weight: 500; }}
  .exp-nav a:hover {{ color: var(--ink); }}
  .exp-nav .cta {{
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff; padding: 0.45rem 0.95rem; border-radius: 6px;
    font-size: 0.85rem; font-weight: 500;
    box-shadow: 0 4px 16px -4px rgba(123, 92, 255, 0.5);
  }}
  .exp-nav .cta:hover {{ color: #fff; transform: translateY(-1px); }}

  section.exp {{ position: relative; padding: 5rem 0; overflow: hidden; }}
  section.exp .wrap {{ max-width: 1180px; margin: 0 auto; padding: 0 1.75rem; position: relative; z-index: 2; }}
  .eyebrow {{
    font-family: var(--mono); font-size: 0.72rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em;
    margin-bottom: 1.2rem;
  }}

  /* HERO */
  .exp-hero {{
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    padding: 6rem 1.5rem 4rem;
    position: relative; overflow: hidden;
  }}
  .aurora {{
    position: absolute; inset: 0; z-index: 0; pointer-events: none;
    overflow: hidden;
  }}
  .aurora::before, .aurora::after, .aurora .third {{
    content: ""; position: absolute;
    border-radius: 50%; filter: blur(140px);
    will-change: transform, opacity;
  }}
  .aurora::before {{
    width: 700px; height: 700px;
    background: radial-gradient(circle, #8b6cff 0%, transparent 70%);
    top: -10%; left: -10%;
    animation: aurora-1 22s ease-in-out infinite;
    opacity: 0.55;
  }}
  .aurora::after {{
    width: 600px; height: 600px;
    background: radial-gradient(circle, #5dd1ff 0%, transparent 70%);
    bottom: -10%; right: -5%;
    animation: aurora-2 28s ease-in-out infinite;
    opacity: 0.45;
  }}
  .aurora .third {{
    width: 500px; height: 500px;
    background: radial-gradient(circle, #ff5cb6 0%, transparent 70%);
    top: 35%; left: 45%;
    animation: aurora-3 25s ease-in-out infinite;
    opacity: 0.25;
  }}
  @keyframes aurora-1 {{
    0%,100% {{ transform: translate(0,0) scale(1); }}
    50% {{ transform: translate(20%, 15%) scale(1.15); }}
  }}
  @keyframes aurora-2 {{
    0%,100% {{ transform: translate(0,0) scale(1); }}
    50% {{ transform: translate(-15%, -20%) scale(1.2); }}
  }}
  @keyframes aurora-3 {{
    0%,100% {{ transform: translate(-50%, -50%) scale(1); opacity: 0.25; }}
    50% {{ transform: translate(-30%, -70%) scale(1.4); opacity: 0.4; }}
  }}
  .grid-bg {{
    position: absolute; inset: 0; z-index: 0; pointer-events: none;
    background-image:
      linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
    background-size: 48px 48px;
    mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
    -webkit-mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
  }}

  .hero-content {{ position: relative; z-index: 2; text-align: center; max-width: 1100px; }}
  .hero-pill {{
    display: inline-flex; align-items: center; gap: 0.5rem;
    background: rgba(20, 20, 29, 0.6);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 0.4rem 0.95rem; border-radius: 999px;
    font-family: var(--mono); font-size: 0.72rem; font-weight: 500;
    color: var(--ink-soft); letter-spacing: 0.04em;
    margin-bottom: 2rem;
    opacity: 0; transform: translateY(20px);
    animation: fade-up 0.8s 0.1s forwards cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .hero-pill .live {{
    width: 6px; height: 6px; border-radius: 50%; background: var(--green);
    box-shadow: 0 0 10px var(--green);
    animation: pulse-dot 2s ease-in-out infinite;
  }}
  @keyframes pulse-dot {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} }}

  .hero-title {{
    font-weight: 800; letter-spacing: -0.04em; line-height: 0.95;
    font-size: clamp(2.8rem, 9vw, 7.5rem);
    margin: 0 0 1.5rem 0;
  }}
  .hero-title .word {{
    display: inline-block; overflow: hidden; vertical-align: top;
  }}
  .hero-title .word > span {{
    display: inline-block;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    transform: translateY(110%);
    animation: word-up 1s forwards cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .hero-title .word:nth-child(1) > span {{ animation-delay: 0.20s; }}
  .hero-title .word:nth-child(2) > span {{ animation-delay: 0.30s; }}
  .hero-title .word:nth-child(3) > span {{
    animation-delay: 0.40s;
    background: linear-gradient(135deg, #8b6cff 0%, #5dd1ff 50%, #ff5cb6 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .hero-title .word:nth-child(4) > span {{ animation-delay: 0.55s; }}
  .hero-title .word:nth-child(5) > span {{ animation-delay: 0.70s; }}
  @keyframes word-up {{ to {{ transform: translateY(0); }} }}

  .hero-sub {{
    font-size: clamp(1.05rem, 1.6vw, 1.3rem); color: var(--ink-soft);
    max-width: 42em; margin: 0 auto 2.5rem;
    opacity: 0; transform: translateY(20px);
    animation: fade-up 1s 1.0s forwards cubic-bezier(0.16, 1, 0.3, 1);
  }}
  @keyframes fade-up {{ to {{ opacity: 1; transform: translateY(0); }} }}

  .hero-ctas {{
    display: flex; gap: 0.8rem; justify-content: center; flex-wrap: wrap;
    opacity: 0; animation: fade-up 1s 1.3s forwards cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .btn {{
    display: inline-flex; align-items: center; gap: 0.5rem;
    padding: 0.85rem 1.5rem; border-radius: 8px;
    font-family: var(--sans); font-size: 0.95rem; font-weight: 500;
    text-decoration: none; cursor: pointer; border: 0;
    transition: transform 0.1s, box-shadow 0.2s, background 0.2s;
  }}
  .btn:active {{ transform: translateY(1px); }}
  .btn-primary {{
    background: linear-gradient(180deg, #9d80ff, #7a5cff);
    color: #fff;
    box-shadow: 0 1px 0 rgba(255,255,255,0.15) inset, 0 10px 30px -8px rgba(123, 92, 255, 0.55);
  }}
  .btn-primary:hover {{ box-shadow: 0 1px 0 rgba(255,255,255,0.2) inset, 0 14px 36px -8px rgba(123, 92, 255, 0.7); color: #fff; }}
  .btn-ghost {{
    background: rgba(20,20,29,0.6); color: var(--ink); border: 1px solid rgba(255,255,255,0.1);
    backdrop-filter: blur(12px);
  }}
  .btn-ghost:hover {{ border-color: var(--accent); color: #fff; }}

  .scroll-hint {{
    position: absolute; bottom: 2.5rem; left: 50%; transform: translateX(-50%);
    color: var(--ink-mute); font-family: var(--mono); font-size: 0.68rem;
    letter-spacing: 0.2em; text-transform: uppercase;
    opacity: 0; animation: fade-up 1s 1.8s forwards;
  }}
  .scroll-hint::before {{
    content: ""; display: block; width: 1px; height: 30px;
    background: linear-gradient(to bottom, transparent, var(--ink-mute));
    margin: 0 auto 0.7rem;
    animation: scroll-line 2s ease-in-out infinite;
  }}
  @keyframes scroll-line {{ 0%,100% {{ opacity: 0.2; height: 30px; }} 50% {{ opacity: 1; height: 40px; }} }}

  /* THE NUMBER */
  .exp-number {{ background: var(--bg); }}
  .exp-number .wrap {{ text-align: center; }}
  .number-display {{
    font-family: var(--mono); font-weight: 700;
    font-size: clamp(2.2rem, 9vw, 7.5rem);
    letter-spacing: -0.02em; line-height: 1.15;
    margin: 2rem 0 2.5rem 0;
    color: var(--ink);
    display: flex; gap: 0.18em; justify-content: center; flex-wrap: nowrap;
  }}
  .number-display .digit {{
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 0.72em; height: 1.15em;
    background: var(--surface); border-radius: 0.12em;
    padding: 0 0.08em;
    color: transparent; transition: color 0.3s, background 0.3s;
    position: relative;
  }}
  .number-display .digit.revealed {{ color: var(--ink); background: transparent; }}
  .number-display .digit.revealed.flash {{
    background: linear-gradient(180deg, rgba(139,108,255,0.2), transparent);
  }}
  .number-display .sep {{ width: 0.25em; }}
  .number-display .plus {{
    color: var(--accent-2); font-weight: 600;
    background: none; padding: 0;
  }}
  .number-status {{
    display: inline-flex; align-items: center; gap: 0.5rem;
    font-family: var(--mono); font-size: 0.85rem;
    background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.3);
    color: var(--green); padding: 0.45rem 1rem; border-radius: 999px;
    margin-bottom: 2rem; opacity: 0; transition: opacity 0.5s;
  }}
  .number-status.show {{ opacity: 1; }}
  .number-status .live {{
    width: 6px; height: 6px; border-radius: 50%; background: var(--green);
    box-shadow: 0 0 10px var(--green);
    animation: pulse-dot 2s ease-in-out infinite;
  }}
  .number-caption {{ color: var(--ink-soft); font-size: 1.1rem; max-width: 38em; margin: 0 auto; }}
  .number-caption strong {{ color: var(--ink); font-weight: 600; }}

  .section-title {{
    font-weight: 700; letter-spacing: -0.02em; line-height: 1.05;
    font-size: clamp(2rem, 5vw, 3.5rem);
    margin: 0 0 1.2rem 0;
  }}
  .section-title .grad {{
    background: linear-gradient(135deg, #8b6cff 0%, #5dd1ff 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .section-sub {{ color: var(--ink-soft); font-size: 1.1rem; max-width: 42em; }}

  /* NETWORK */
  .exp-network {{ padding: 3rem 0 4rem; }}
  .exp-network .wrap {{ text-align: center; margin-bottom: 3rem; }}
  .net-canvas-wrap {{
    position: relative; width: 100%; max-width: 1100px; margin: 0 auto;
    height: 460px; border: 1px solid var(--line);
    border-radius: 16px;
    background: radial-gradient(ellipse at center, rgba(139,108,255,0.06) 0%, transparent 70%), var(--bg-soft);
    overflow: hidden;
  }}
  .net-canvas-wrap canvas {{ display: block; width: 100%; height: 100%; }}
  .net-legend {{
    display: flex; gap: 1.5rem; justify-content: center; margin-top: 1.5rem;
    flex-wrap: wrap; font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink-soft);
  }}
  .net-legend span {{ display: inline-flex; align-items: center; gap: 0.4rem; }}
  .net-legend .dot {{ width: 8px; height: 8px; border-radius: 50%; }}
  .net-legend .agent {{ background: #5dd1ff; box-shadow: 0 0 8px #5dd1ff; }}
  .net-legend .ami   {{ background: #8b6cff; box-shadow: 0 0 8px #8b6cff; }}
  .net-legend .telco {{ background: #fbbf24; box-shadow: 0 0 8px #fbbf24; }}
  .net-legend .ws    {{ background: #5a5a70; box-shadow: 0 0 6px rgba(90,90,112,0.5); }}

  /* FLOW */
  .exp-flow {{ padding: 4rem 0; background: var(--bg); }}
  .exp-flow .wrap {{ text-align: center; margin-bottom: 2rem; }}
  .flow-stage {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 4rem;
    max-width: 1200px; margin: 0 auto; padding: 0 1.75rem;
    align-items: center;
  }}
  @media (max-width: 880px) {{ .flow-stage {{ grid-template-columns: 1fr; gap: 1.5rem; }} }}
  .flow-step {{
    padding: 2rem 0; min-height: 0;
    display: grid; grid-template-columns: 1fr 1fr; gap: 4rem;
    max-width: 1200px; margin: 0 auto; padding-left: 1.75rem; padding-right: 1.75rem;
    align-items: center;
    opacity: 0.3; transition: opacity 0.6s;
  }}
  .flow-step.active {{ opacity: 1; }}
  @media (max-width: 880px) {{
    .flow-step {{ grid-template-columns: 1fr; gap: 1.5rem; min-height: auto; padding: 2rem 1.75rem; }}
  }}
  .flow-step-num {{
    font-family: var(--mono); font-size: 0.72rem; color: var(--accent);
    letter-spacing: 0.15em; text-transform: uppercase; margin-bottom: 0.6rem;
  }}
  .flow-step h3 {{
    font-size: 1.6rem; font-weight: 700; letter-spacing: -0.01em;
    margin: 0 0 1rem 0; line-height: 1.2;
  }}
  .flow-step p {{ color: var(--ink-soft); margin: 0 0 1rem 0; font-size: 1rem; }}
  .flow-code {{
    background: var(--code-bg); border: 1px solid var(--line);
    border-radius: 12px; padding: 1.25rem 1.4rem;
    font-family: var(--mono); font-size: 0.8rem; line-height: 1.7;
    overflow-x: auto;
    box-shadow: 0 30px 60px -20px rgba(0,0,0,0.5);
    transform: translateY(20px); opacity: 0;
    transition: transform 0.6s, opacity 0.6s;
  }}
  .flow-step.active .flow-code {{ transform: translateY(0); opacity: 1; }}
  .flow-code .tool   {{ color: var(--accent); }}
  .flow-code .key    {{ color: #c4b3ff; }}
  .flow-code .str    {{ color: #82e0a4; }}
  .flow-code .num    {{ color: var(--amber); }}
  .flow-code .arrow  {{ color: var(--accent-2); }}
  .flow-code .ok     {{ color: var(--green); }}
  .flow-code .out    {{ color: var(--ink-soft); }}

  /* TOOLS */
  .exp-tools {{ padding: 4rem 0; background: var(--bg-soft); }}
  .exp-tools .wrap {{ text-align: center; }}
  .tools-grid-2 {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1px; max-width: 1180px; margin: 3rem auto 0;
    background: var(--line); border: 1px solid var(--line); border-radius: 16px;
    overflow: hidden;
  }}
  .tool-card {{
    background: var(--bg-soft); padding: 1.6rem 1.5rem;
    transition: background 0.3s, transform 0.3s;
    text-align: left; position: relative; min-height: 120px;
    cursor: default;
  }}
  .tool-card:hover {{ background: var(--surface); transform: translateY(-2px); }}
  .tool-card::before {{
    content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
    background: linear-gradient(180deg, transparent, var(--accent), transparent);
    opacity: 0; transition: opacity 0.3s;
  }}
  .tool-card:hover::before {{ opacity: 1; }}
  .tool-card .name {{
    font-family: var(--mono); font-size: 0.88rem; color: var(--accent);
    font-weight: 500; margin-bottom: 0.5rem; letter-spacing: -0.01em;
  }}
  .tool-card .desc {{ color: var(--ink-soft); font-size: 0.88rem; line-height: 1.55; }}

  /* STACK */
  .exp-stack {{ padding: 4rem 0; background: var(--bg); }}
  .exp-stack .wrap {{ text-align: center; max-width: 1080px; }}
  .stack-layers {{
    display: flex; flex-direction: column; gap: 1rem;
    max-width: 880px; margin: 3.5rem auto 0; text-align: left;
  }}
  .stack-layer {{
    display: grid; grid-template-columns: 4rem 1fr; gap: 1.5rem;
    align-items: center;
    background: var(--surface); border: 1px solid var(--line);
    border-left: 3px solid var(--accent);
    border-radius: 12px; padding: 1.6rem 1.8rem;
    transition: transform 0.25s, border-color 0.25s;
    position: relative;
  }}
  .stack-layer:hover {{ transform: translateX(4px); border-left-color: var(--accent-2); }}
  .stack-num {{
    font-family: var(--mono); font-weight: 700; font-size: 1.6rem;
    color: var(--accent); letter-spacing: -0.02em;
  }}
  .stack-name {{
    font-size: 1.05rem; font-weight: 600; color: var(--ink);
    margin: 0 0 0.45rem 0; letter-spacing: -0.01em;
    display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
  }}
  .stack-tag {{
    font-family: var(--mono); font-size: 0.65rem; font-weight: 500;
    background: var(--accent-bg); color: var(--accent);
    padding: 0.15rem 0.55rem; border-radius: 4px;
    letter-spacing: 0.08em; text-transform: uppercase;
  }}
  .stack-components {{
    font-family: var(--mono); font-size: 0.84rem; color: var(--ink-soft);
    line-height: 1.65;
  }}
  .stack-components .pipe {{ color: var(--ink-mute); margin: 0 0.4rem; }}
  .stack-footnote {{
    margin-top: 2.5rem; padding: 1rem 1.4rem;
    background: rgba(139, 108, 255, 0.06);
    border: 1px solid rgba(139, 108, 255, 0.2);
    border-radius: 8px;
    font-size: 0.92rem; color: var(--ink-soft);
    max-width: 700px; margin-left: auto; margin-right: auto;
  }}
  .stack-footnote strong {{ color: var(--ink); }}

  /* BUNDLE */
  .exp-bundle {{ padding: 4rem 0; background: var(--bg); }}
  .exp-bundle .wrap {{ text-align: center; max-width: 1180px; }}
  .bundle-flow {{
    display: grid;
    grid-template-columns: 1fr auto 1fr auto 1fr;
    gap: 1rem; align-items: stretch;
    max-width: 1080px; margin: 3rem auto 0;
  }}
  .bundle-card {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1.6rem 1.4rem;
    text-align: center;
    transition: border-color 0.25s, transform 0.25s;
    display: flex; flex-direction: column; justify-content: center;
    min-height: 170px;
  }}
  .bundle-card:hover {{ border-color: var(--accent); transform: translateY(-2px); }}
  .bundle-card.amber {{ border-color: rgba(251,191,36,0.3); }}
  .bundle-card.amber:hover {{ border-color: var(--amber); }}
  .bundle-card.violet {{ border-color: rgba(139,108,255,0.4); }}
  .bundle-card.violet:hover {{ border-color: var(--accent); }}
  .bundle-card.green {{ border-color: rgba(74,222,128,0.3); }}
  .bundle-card.green:hover {{ border-color: var(--green); }}
  .bundle-tag {{
    font-family: var(--mono); font-size: 0.65rem;
    color: var(--ink-mute);
    text-transform: uppercase; letter-spacing: 0.14em;
    margin-bottom: 0.85rem; font-weight: 600;
  }}
  .bundle-providers {{
    display: flex; flex-wrap: wrap; gap: 0.4rem;
    justify-content: center; margin-bottom: 0.7rem;
  }}
  .bundle-providers span {{
    font-family: var(--mono); font-size: 0.78rem;
    background: rgba(251,191,36,0.08);
    border: 1px solid rgba(251,191,36,0.25);
    color: var(--amber);
    padding: 0.2rem 0.55rem; border-radius: 4px;
  }}
  .bundle-price {{
    font-family: var(--mono); font-weight: 700;
    font-size: clamp(2rem, 4vw, 2.8rem);
    color: var(--accent); line-height: 1;
    letter-spacing: -0.02em;
    background: linear-gradient(135deg, #8b6cff, #5dd1ff);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .bundle-price span {{
    font-size: 0.9rem; color: var(--ink-soft);
    -webkit-text-fill-color: var(--ink-soft);
    font-weight: 500; margin-left: 0.1rem;
  }}
  .bundle-result {{
    font-family: var(--mono); font-weight: 600;
    font-size: clamp(1rem, 2.2vw, 1.4rem);
    color: var(--green);
    letter-spacing: -0.01em;
  }}
  .bundle-meta {{
    font-family: var(--sans); font-size: 0.82rem;
    color: var(--ink-soft); margin-top: 0.6rem; line-height: 1.4;
  }}
  .bundle-arrow {{
    display: flex; align-items: center; justify-content: center;
    color: var(--ink-mute); font-size: 1.6rem;
    font-family: var(--mono);
  }}
  .bundle-stats {{
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
    max-width: 1080px; margin: 2.5rem auto 0;
  }}
  .bundle-stat {{
    background: var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 1.3rem 1.2rem;
    text-align: center;
  }}
  .bundle-stat-num {{
    font-family: var(--mono); font-weight: 700;
    font-size: clamp(1.7rem, 3.8vw, 2.4rem);
    color: var(--ink); letter-spacing: -0.02em; line-height: 1;
    margin-bottom: 0.55rem;
  }}
  .bundle-stat-num .accent-grad {{
    background: linear-gradient(135deg, #8b6cff, #5dd1ff);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .bundle-stat-label {{
    font-size: 0.85rem; color: var(--ink-soft); line-height: 1.45;
  }}
  @media (max-width: 880px) {{
    .bundle-flow {{
      grid-template-columns: 1fr;
      gap: 0.5rem; max-width: 480px;
    }}
    .bundle-arrow {{ transform: rotate(90deg); padding: 0.3rem 0; }}
    .bundle-stats {{ grid-template-columns: 1fr; }}
  }}

  /* LIVE DEMO */
  .exp-demo {{
    padding: 4rem 0 5rem;
    background: var(--bg);
    position: relative; overflow: hidden;
  }}
  .exp-demo::before {{
    content: ""; position: absolute; pointer-events: none;
    width: 900px; height: 500px;
    background: radial-gradient(ellipse, #8b6cff 0%, transparent 65%);
    top: 50%; left: 50%; transform: translate(-50%, -50%);
    filter: blur(140px); opacity: 0.18;
  }}
  .exp-demo .wrap {{ text-align: center; }}
  .demo-stage {{
    margin: 3rem auto 0; max-width: 800px;
    background: var(--code-bg); border: 1px solid var(--line);
    border-radius: 16px; overflow: hidden;
    box-shadow: 0 40px 100px -20px rgba(0,0,0,0.7),
                0 0 0 1px rgba(139, 108, 255, 0.08);
  }}
  .demo-bar {{
    display: flex; align-items: center; gap: 0.5rem;
    padding: 0.75rem 1.1rem;
    border-bottom: 1px solid var(--line);
    background: rgba(255,255,255,0.015);
  }}
  .demo-bar .dot {{ width: 11px; height: 11px; border-radius: 50%; background: #2a2a3a; }}
  .demo-bar .title {{
    margin-left: 0.7rem; font-family: var(--mono); font-size: 0.74rem;
    color: var(--ink-mute);
  }}
  .demo-body {{
    padding: 2.2rem 1.8rem; min-height: 320px;
    font-family: var(--mono); font-size: 0.88rem; line-height: 1.8;
    color: var(--ink); position: relative;
  }}
  .demo-cta-wrap {{
    text-align: center; padding: 4rem 1rem;
  }}
  .demo-cta-wrap .btn-primary {{
    font-size: 1.1rem; padding: 1rem 2rem;
  }}
  .demo-step {{ display: flex; align-items: center; gap: 1rem; margin: 0.45rem 0;
                opacity: 0; transform: translateX(-10px);
                transition: opacity 0.4s, transform 0.4s; }}
  .demo-step.show {{ opacity: 1; transform: translateX(0); }}
  .demo-step .check {{
    width: 18px; height: 18px; border-radius: 50%;
    background: var(--green); color: #082812; flex-shrink: 0;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 0.7rem; font-weight: 700;
    box-shadow: 0 0 10px rgba(74,222,128,0.5);
  }}
  .demo-step .label {{ color: var(--ink); flex-grow: 1; }}
  .demo-step .id {{ color: var(--ink-mute); font-size: 0.78rem; }}
  .demo-result {{
    margin-top: 2rem; padding-top: 1.5rem;
    border-top: 1px solid var(--line);
    text-align: center;
    opacity: 0; transition: opacity 0.6s;
  }}
  .demo-result.show {{ opacity: 1; }}
  .demo-result-label {{
    font-family: var(--mono); font-size: 0.7rem; color: var(--ink-mute);
    text-transform: uppercase; letter-spacing: 0.15em; margin-bottom: 0.6rem;
  }}
  .demo-result-phone {{
    font-family: var(--mono); font-weight: 700;
    font-size: clamp(1.6rem, 4vw, 2.4rem); color: var(--ink);
    letter-spacing: -0.01em;
    background: linear-gradient(135deg, #8b6cff, #5dd1ff);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .demo-result-links {{ margin-top: 1.2rem; display: flex; gap: 1.2rem; justify-content: center; flex-wrap: wrap; font-size: 0.85rem; }}

  /* CTA */
  .exp-cta {{
    padding: 5rem 0; text-align: center;
    background: var(--bg);
    position: relative; overflow: hidden;
    border-top: 1px solid var(--line);
  }}
  .exp-cta::before {{
    content: ""; position: absolute; pointer-events: none;
    width: 1000px; height: 500px;
    background: radial-gradient(ellipse, #5dd1ff 0%, transparent 65%);
    top: 50%; left: 50%; transform: translate(-50%, -50%);
    filter: blur(160px); opacity: 0.20;
  }}
  .exp-cta .wrap {{ position: relative; z-index: 1; }}
  .exp-cta h2 {{
    font-size: clamp(2.2rem, 6vw, 4rem); margin: 0 0 1.5rem 0;
    background: linear-gradient(180deg, #ffffff 20%, #a8a8c8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    line-height: 1.05; letter-spacing: -0.03em;
  }}
  .exp-cta p {{ color: var(--ink-soft); font-size: 1.15rem; max-width: 38em; margin: 0 auto 2.5rem; }}

  /* FOOTER */
  .exp-footer {{
    padding: 3rem 0; color: var(--ink-mute);
    border-top: 1px solid var(--line); background: var(--bg);
  }}
  .exp-footer .wrap {{
    max-width: 1180px; margin: 0 auto; padding: 0 1.75rem;
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 1rem;
    font-family: var(--mono); font-size: 0.78rem;
  }}
  .exp-footer .links a {{ margin-left: 1.4rem; color: var(--ink-soft); }}
  .exp-footer .links a:hover {{ color: var(--ink); }}

  /* Reveal animations */
  .reveal {{ opacity: 0; transform: translateY(30px); transition: opacity 0.9s cubic-bezier(0.16,1,0.3,1), transform 0.9s cubic-bezier(0.16,1,0.3,1); }}
  .reveal.in {{ opacity: 1; transform: translateY(0); }}
  .reveal-stagger > * {{ opacity: 0; transform: translateY(20px); transition: opacity 0.7s, transform 0.7s; }}
  .reveal-stagger.in > * {{ opacity: 1; transform: translateY(0); }}
  .reveal-stagger.in > *:nth-child(1) {{ transition-delay: 0.05s; }}
  .reveal-stagger.in > *:nth-child(2) {{ transition-delay: 0.10s; }}
  .reveal-stagger.in > *:nth-child(3) {{ transition-delay: 0.15s; }}
  .reveal-stagger.in > *:nth-child(4) {{ transition-delay: 0.20s; }}
  .reveal-stagger.in > *:nth-child(5) {{ transition-delay: 0.25s; }}
  .reveal-stagger.in > *:nth-child(6) {{ transition-delay: 0.30s; }}
  .reveal-stagger.in > *:nth-child(7) {{ transition-delay: 0.35s; }}
  .reveal-stagger.in > *:nth-child(8) {{ transition-delay: 0.40s; }}

  @media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
    }}
    .aurora::before, .aurora::after, .aurora .third {{ display: none; }}
    .reveal, .reveal-stagger > * {{ opacity: 1 !important; transform: none !important; }}
  }}

  @media (max-width: 600px) {{
    section.exp {{ padding: 3.5rem 0; }}
    .exp-nav a:not(.cta) {{ display: none; }}

    .exp-hero {{ padding: 5rem 1rem 2.5rem; }}
    h1.hero-title {{ letter-spacing: -0.035em; }}
    .hero-pill {{ font-size: 0.66rem; padding: 0.3rem 0.7rem; }}
    .hero-sub {{ font-size: 1rem; }}
    .terminal-body {{ font-size: 0.72rem; padding: 1rem 1.1rem; line-height: 1.55; }}
    .terminal-bar .title {{ font-size: 0.66rem; }}

    .number-display {{
      font-size: clamp(1.5rem, 7.5vw, 7.5rem);
      gap: 0.12em;
    }}
    .number-status {{ font-size: 0.74rem; padding: 0.4rem 0.85rem; }}
    .number-caption {{ font-size: 0.95rem; }}

    .net-canvas-wrap {{ height: 380px; }}
    .net-legend {{ font-size: 0.68rem; gap: 0.7rem 1rem; flex-wrap: wrap; justify-content: center; }}

    .flow-step {{ padding: 1.5rem 1.25rem; gap: 1.2rem; }}
    .flow-step h3 {{ font-size: 1.25rem; }}
    .flow-step p {{ font-size: 0.92rem; }}
    .flow-code {{ font-size: 0.72rem; padding: 1rem 1.1rem; line-height: 1.6; }}
    .flow-step-num {{ font-size: 0.66rem; }}

    .tools-grid-2 {{ grid-template-columns: 1fr; }}
    .tool-cell {{ padding: 1rem 1.2rem; }}
    .tool-cell .name {{ font-size: 0.82rem; }}
    .tool-cell .desc {{ font-size: 0.83rem; }}

    .stack-layer {{
      grid-template-columns: 3rem 1fr;
      gap: 1rem; padding: 1.25rem 1.3rem;
    }}
    .stack-num {{ font-size: 1.3rem; }}
    .stack-name {{ font-size: 0.98rem; gap: 0.45rem; }}
    .stack-tag {{ font-size: 0.6rem; padding: 0.12rem 0.45rem; }}
    .stack-components {{ font-size: 0.78rem; line-height: 1.6; }}
    .stack-footnote {{ font-size: 0.85rem; padding: 0.85rem 1.1rem; }}

    .endpoint-list li {{ padding: 0.7rem 1.1rem; }}

    .demo-body {{ padding: 1.5rem 1.2rem; font-size: 0.78rem; }}
    .demo-cta-wrap {{ padding: 2.5rem 1rem; }}
    .demo-result-phone {{ font-size: clamp(1.4rem, 6vw, 2.4rem); }}

    .exp-cta {{ padding: 4rem 0; }}
    .exp-cta h2 {{ font-size: clamp(1.7rem, 7vw, 4rem); }}
    .exp-cta p {{ font-size: 1rem; }}

    .hero-ctas .btn {{ font-size: 0.9rem; padding: 0.75rem 1.2rem; }}
  }}

  /* CINEMA */
  .exp-cinema {{
    padding: 5rem 0 6rem;
    background: var(--bg);
    border-top: 1px solid var(--line);
    position: relative;
  }}
  .exp-cinema .wrap {{ text-align: center; max-width: 1180px; }}
  .cinema-stage {{
    position: relative;
    width: 100%; max-width: 1180px;
    margin: 3rem auto 0;
    height: 500px;
    background: radial-gradient(ellipse at center, rgba(139,108,255,0.05) 0%, transparent 70%), var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 16px;
    overflow: hidden;
  }}
  .cinema-canvas {{
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    pointer-events: none; z-index: 1;
  }}
  .cinema-actor {{
    position: absolute;
    transform: translate(-50%, -50%);
    text-align: center;
    z-index: 2;
    width: 110px;
  }}
  .cinema-actor.agent {{ left: 12%; top: 50%; }}
  .cinema-actor.world {{ left: 88%; top: 50%; }}
  .cinema-stack {{
    position: absolute;
    left: 50%; top: 50%;
    transform: translate(-50%, -50%);
    display: flex; flex-direction: column; gap: 0.45rem;
    z-index: 2; min-width: 170px;
  }}
  .cinema-stack-title {{
    font-family: var(--mono); font-size: 0.6rem;
    color: var(--ink-mute);
    text-align: center;
    letter-spacing: 0.18em; text-transform: uppercase;
    margin-bottom: 0.3rem;
    font-weight: 600;
  }}
  .cinema-module {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 0.45rem 0.7rem;
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink-soft);
    text-align: center;
    transition: border-color 0.25s, background 0.25s, color 0.25s, box-shadow 0.25s, transform 0.25s;
  }}
  .cinema-module.active {{
    border-color: var(--accent);
    background: var(--accent-bg);
    color: var(--ink);
    box-shadow: 0 0 18px rgba(139,108,255,0.45);
    transform: scale(1.06);
  }}
  .cinema-actor-icon {{
    width: 56px; height: 56px;
    background: var(--surface);
    border: 2px solid currentColor;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 0.55rem;
    position: relative;
    font-size: 1.4rem;
    transition: box-shadow 0.3s, transform 0.3s;
  }}
  .cinema-actor.agent .cinema-actor-icon {{ color: var(--accent-2); }}
  .cinema-actor.world .cinema-actor-icon {{ color: var(--green); }}
  .cinema-actor-name {{
    font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink); font-weight: 500;
  }}
  .cinema-actor-meta {{
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink-soft); margin-top: 0.35rem;
    min-height: 1rem; transition: color 0.4s;
  }}
  .cinema-actor-meta.identity {{ color: var(--accent-2); font-weight: 500; }}
  .cinema-actor.pulsing .cinema-actor-icon {{
    box-shadow: 0 0 0 0 currentColor;
    animation: cinema-pulse-anim 1.2s ease-out infinite;
  }}
  @keyframes cinema-pulse-anim {{
    0%   {{ box-shadow: 0 0 0 0 rgba(93,209,255,0.5); }}
    100% {{ box-shadow: 0 0 0 22px rgba(93,209,255,0); }}
  }}
  .cinema-actor.world.pulsing .cinema-actor-icon {{
    animation-name: cinema-pulse-world;
  }}
  @keyframes cinema-pulse-world {{
    0%   {{ box-shadow: 0 0 0 0 rgba(74,222,128,0.5); }}
    100% {{ box-shadow: 0 0 0 22px rgba(74,222,128,0); }}
  }}
  .cinema-bubble {{
    position: absolute;
    bottom: 100%; left: 50%;
    transform: translateX(-50%) translateY(0);
    margin-bottom: 0.6rem;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.5rem 0.85rem;
    font-family: var(--mono); font-size: 0.72rem;
    color: var(--ink);
    white-space: nowrap;
    max-width: 280px; overflow: hidden; text-overflow: ellipsis;
    opacity: 0; transition: opacity 0.35s, transform 0.35s;
    pointer-events: none;
  }}
  .cinema-bubble.show {{
    opacity: 1;
    transform: translateX(-50%) translateY(-4px);
  }}
  .cinema-bubble::after {{
    content: ""; position: absolute;
    top: 100%; left: 50%; transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: var(--surface);
  }}
  .cinema-status {{
    text-align: center;
    margin: 1.5rem auto 0;
    font-family: var(--mono); font-size: 0.85rem;
    color: var(--ink-soft);
    min-height: 1.5rem;
    max-width: 800px;
    transition: opacity 0.3s;
  }}
  .cinema-status .act {{
    color: var(--accent);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    font-size: 0.7rem;
    margin-right: 0.7rem;
    padding: 0.15rem 0.55rem;
    background: var(--accent-bg);
    border-radius: 4px;
  }}
  .cinema-controls {{ text-align: center; margin-top: 1rem; }}
  .cinema-replay-btn {{
    background: transparent; border: 1px solid var(--line);
    color: var(--ink-soft); cursor: pointer;
    font-family: var(--mono); font-size: 0.78rem;
    padding: 0.5rem 1.1rem; border-radius: 6px;
    transition: border-color 0.15s, color 0.15s;
  }}
  .cinema-replay-btn:hover {{ border-color: var(--accent); color: var(--ink); }}
  .cinema-finale {{
    position: absolute; inset: 0; z-index: 5;
    background: radial-gradient(circle, rgba(8,8,12,0.95), rgba(8,8,12,0.85));
    display: flex; align-items: center; justify-content: center;
    flex-direction: column; gap: 0.7rem;
    text-align: center;
    opacity: 0; pointer-events: none;
    transition: opacity 0.8s cubic-bezier(0.16, 1, 0.3, 1);
    padding: 1rem;
  }}
  .cinema-finale.show {{ opacity: 1; }}
  .cinema-finale h3 {{
    font-size: clamp(1.6rem, 3.5vw, 2.6rem);
    font-weight: 700; letter-spacing: -0.02em; margin: 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .cinema-finale p {{
    font-family: var(--mono); font-size: 0.82rem;
    color: var(--ink-soft); margin: 0;
    letter-spacing: 0.04em;
  }}
  @media (max-width: 720px) {{
    .cinema-stage {{ height: 620px; }}
    .cinema-actor {{ width: 110px; }}
    .cinema-actor.agent {{ left: 50%; top: 13%; }}
    .cinema-actor.world {{ left: 50%; top: 87%; }}
    .cinema-actor-icon {{ width: 50px; height: 50px; font-size: 1.2rem; }}
    .cinema-actor-name {{ font-size: 0.74rem; }}
    .cinema-actor-meta {{ font-size: 0.68rem; }}

    .cinema-stack {{
      flex-direction: row;
      flex-wrap: wrap;
      justify-content: center;
      gap: 0.35rem;
      min-width: 0;
      max-width: calc(100% - 2rem);
    }}
    .cinema-stack-title {{ width: 100%; margin-bottom: 0.5rem; }}
    .cinema-module {{
      font-size: 0.62rem;
      padding: 0.3rem 0.55rem;
      flex: 0 0 auto;
    }}

    .cinema-bubble {{
      font-size: 0.65rem;
      max-width: 180px;
      white-space: normal;
      text-align: center;
      line-height: 1.4;
      padding: 0.4rem 0.7rem;
    }}
    .cinema-actor.agent .cinema-bubble {{
      bottom: auto;
      top: 100%;
      margin-bottom: 0;
      margin-top: 0.6rem;
    }}
    .cinema-actor.agent .cinema-bubble::after {{
      top: auto;
      bottom: 100%;
      border-top-color: transparent;
      border-bottom-color: var(--surface);
    }}

    .cinema-status {{ font-size: 0.78rem; padding: 0 1rem; }}
    .cinema-status .act {{ font-size: 0.64rem; padding: 0.1rem 0.45rem; margin-right: 0.5rem; }}
    .cinema-replay-btn {{ font-size: 0.72rem; padding: 0.45rem 1rem; }}
    .cinema-finale h3 {{ font-size: clamp(1.3rem, 6vw, 2.6rem); }}
    .cinema-finale p {{ font-size: 0.72rem; padding: 0 1rem; }}
  }}
</style>
</head>
<body>

  <header class="exp-header" id="hdr">
    <div class="wrap">
      <a href="/" class="exp-brand">
        {AMI_LOGO_SVG} AMI<span class="dot">.</span>
      </a>
      <nav class="exp-nav">
        <a href="#flow">Flow</a>
        <a href="#stack">Stack</a>
        <a href="#bundle">Bundle</a>
        <a href="/spec">Spec</a>
        <a href="{html_escape(REPO_URL)}">GitHub</a>
        <a href="#demo" class="cta">Try it</a>
      </nav>
    </div>
  </header>

  <!-- HERO -->
  <section class="exp-hero">
    <div class="aurora"><div class="third"></div></div>
    <div class="grid-bg"></div>
    <div class="hero-content">
      <div class="hero-pill">
        <span class="live"></span>
        <span>in production · v1.0 · multi-operator from day one</span>
      </div>
      <h1 class="hero-title">
        <span class="word"><span>Mobile</span></span>
        <span class="word"><span>identity</span></span><br>
        <span class="word"><span>for</span></span>
        <span class="word"><span>AI</span></span>
        <span class="word"><span>agents.</span></span>
      </h1>
      <p class="hero-sub">
        An open protocol and our own end-to-end stack: any AI agent gets a real phone
        number, signs a contract, and operates SMS and calls on our own cloud platform —
        Asterisk, Kannel, SIP gateway, numbering inventory. No physical SIMs, no external
        dependencies, no intermediate operators. The cloud-native telco for agents.
      </p>
      <div class="hero-ctas">
        <a class="btn btn-primary" href="#demo">Watch an identity being created →</a>
        <a class="btn btn-ghost" href="#flow">How it works</a>
      </div>
    </div>
    <div class="scroll-hint">scroll</div>
  </section>

  <!-- THE NUMBER -->
  <section class="exp exp-number">
    <div class="wrap">
      <div class="eyebrow reveal">a new primitive</div>
      <h2 class="section-title reveal">This is what an agent just got.</h2>
      <div id="numberDisplay" class="number-display"></div>
      <div id="numberStatus" class="number-status">
        <span class="live"></span><span>active · ready to receive</span>
      </div>
      <p class="number-caption reveal">
        Half a second ago this number <strong>didn't exist</strong>. Now it legally
        belongs to an agent, has a signed contract, a usage policy, and can operate SMS,
        voice and data on our own platform — no physical SIMs, no external operators.
      </p>
    </div>
  </section>

  <!-- NETWORK -->
  <section class="exp exp-network" id="network">
    <div class="wrap">
      <div class="eyebrow reveal">the network</div>
      <h2 class="section-title reveal">A protocol between <span class="grad">millions of agents</span> and our own infrastructure.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        Any agent speaks MCP with AMI. AMI orchestrates its own stack — Asterisk for
        voice, Kannel for SMS, in-house numbering inventory, SIP gateway to the PSTN.
        Zero physical SIMs, zero external operators, zero third-party APIs on the
        critical path.
      </p>
    </div>
    <div style="margin-top: 3rem; padding: 0 1.5rem;">
      <div class="net-canvas-wrap">
        <canvas id="netCanvas"></canvas>
      </div>
      <div class="net-legend">
        <span><span class="dot agent"></span>Agents (assistants · support · sales · ops · custom)</span>
        <span><span class="dot ami"></span>AMI Stack (protocol + in-house platform)</span>
      </div>
    </div>
  </section>

  <!-- FLOW -->
  <section class="exp exp-flow" id="flow">
    <div class="wrap">
      <div class="eyebrow reveal">the flow</div>
      <h2 class="section-title reveal">From request to active number.<br>No human in the loop.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        The agent traverses the entire state machine over MCP. Only the signature
        happens in the human signer's browser. Everything else is machine-to-machine.
      </p>
    </div>

    <div class="flow-step" data-step="1">
      <div>
        <div class="flow-step-num">step 01</div>
        <h3>The agent requests a Spanish number.</h3>
        <p>A single MCP tool. The agent declares what it needs: country, capabilities, budget. AMI validates against its own numbering inventory and returns an immediate offer.</p>
      </div>
      <pre class="flow-code"><span class="tool">ami.request_number_offer</span>({{
  <span class="key">country</span>: <span class="str">"ES"</span>,
  <span class="key">capabilities</span>: [<span class="str">"sms"</span>, <span class="str">"voice"</span>],
  <span class="key">max_monthly_price</span>: <span class="num">10</span>
}})

<span class="arrow">←</span> <span class="ok">offer_b76915a004</span>
  <span class="key">price</span>:    <span class="num">8.90</span> EUR/mo
  <span class="key">expires</span>:  7 days</pre>
    </div>

    <div class="flow-step" data-step="2">
      <div>
        <div class="flow-step-num">step 02</div>
        <h3>Accept and submit the customer details.</h3>
        <p>If the customer is already KYC'd, this is skipped. Otherwise, the agent collects the required fields in a single call.</p>
      </div>
      <pre class="flow-code"><span class="tool">ami.accept_offer</span>(...) · <span class="tool">ami.submit_customer_data</span>({{
  <span class="key">legal_name</span>:  <span class="str">"Acme S.L."</span>,
  <span class="key">tax_id</span>:      <span class="str">"B00000000"</span>,
  <span class="key">representative_name</span>: <span class="str">"..."</span>
}})

<span class="arrow">←</span> <span class="ok">customer_dc73997b74</span></pre>
    </div>

    <div class="flow-step" data-step="3">
      <div>
        <div class="flow-step-num">step 03</div>
        <h3>Contract generated, signature URL ready.</h3>
        <p>AMI generates the contract bound to offer + customer and returns a signable URL openable from any browser. Tomorrow that URL will be served by an e-signature provider; today AMI serves it directly.</p>
      </div>
      <pre class="flow-code"><span class="tool">ami.create_contract</span>({{
  <span class="key">offer_id</span>:    <span class="str">"offer_b769..."</span>,
  <span class="key">customer_id</span>: <span class="str">"customer_dc73..."</span>
}})

<span class="arrow">←</span> <span class="ok">contract_312567fa82</span>
  <span class="key">signature_url</span>: <span class="str">"https://protocolami.com/v1/sign/..."</span></pre>
    </div>

    <div class="flow-step" data-step="4">
      <div>
        <div class="flow-step-num">step 04</div>
        <h3>Activation on our platform.</h3>
        <p>After signing, AMI assigns the number from its own inventory, routes it on its SIP gateway and leaves it listening on Asterisk + Kannel. Hundreds of milliseconds later, the agent receives its MobileIdentity with a live number, ready to send and receive SMS and calls over the internet. No physical SIM, no card to insert.</p>
      </div>
      <pre class="flow-code"><span class="tool">ami.activate_number</span>({{ contract_id: ... }})

<span class="arrow">←</span> <span class="ok">mobile identity active</span>
  <span class="key">phone</span>:        <span class="str">"+34 600 ███ ███"</span>
  <span class="key">capabilities</span>: [<span class="str">"sms"</span>, <span class="str">"voice"</span>]
  <span class="key">contract</span>:     <span class="str">"signed"</span>  ·  <span class="ok">1.4s</span></pre>
    </div>
  </section>

  <!-- TOOLS -->
  <section class="exp exp-tools" id="tools">
    <div class="wrap">
      <div class="eyebrow reveal">tools, one namespace</div>
      <h2 class="section-title reveal">Everything an agent needs,<br>in <span class="grad">ami.*</span></h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        Each MCP tool maps to an equivalent REST endpoint. Your agent can use whichever
        it prefers without losing semantics.
      </p>
      <div class="tools-grid-2 reveal">
        {''.join(f'<div class="tool-card"><div class="name">{n}</div><div class="desc">{html_escape(d)}</div></div>' for n, d in _tools_for_landing_en())}
      </div>
    </div>
  </section>

  <!-- STACK -->
  <section class="exp exp-stack" id="stack">
    <div class="wrap">
      <div class="eyebrow reveal">our own vertical stack</div>
      <h2 class="section-title reveal">All under <span class="grad">our control</span>.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        AMI is product and operations, not an integration on top of someone else. The
        three layers an agent needs to have real mobile identity are our own code and
        servers. Peering to the PSTN is solved as standard interconnect, like any
        operator in the world.
      </p>

      <div class="stack-layers reveal-stagger">
        <div class="stack-layer">
          <div class="stack-num">01</div>
          <div>
            <div class="stack-name">Open protocol <span class="stack-tag">ours</span></div>
            <div class="stack-components">
              MCP server (stdio + HTTP)<span class="pipe">·</span>
              REST API<span class="pipe">·</span>
              OpenAPI 3.1<span class="pipe">·</span>
              tools <code>ami.*</code>
            </div>
          </div>
        </div>

        <div class="stack-layer">
          <div class="stack-num">02</div>
          <div>
            <div class="stack-name">Application backend <span class="stack-tag">ours</span></div>
            <div class="stack-components">
              Agent identity (AID + Ed25519 keypair)<span class="pipe">·</span>
              Contracts &amp; electronic signature<span class="pipe">·</span>
              Policy engine<span class="pipe">·</span>
              Immutable audit log
            </div>
          </div>
        </div>

        <div class="stack-layer">
          <div class="stack-num">03</div>
          <div>
            <div class="stack-name">Communications platform <span class="stack-tag">ours</span></div>
            <div class="stack-components">
              Asterisk / FreeSWITCH <em>(voice, SIP, IVR, transcription)</em><span class="pipe">·</span>
              Kannel / Jasmin <em>(in-house SMSC, SMPP)</em><span class="pipe">·</span>
              Number inventory <em>(provisioning and number management)</em><span class="pipe">·</span>
              SIP gateway <em>(origination and termination)</em>
            </div>
          </div>
        </div>

      </div>

      <p class="stack-footnote reveal">
        <strong>This is not a future project.</strong> Layers 1 and 2 are in production
        right now (the first one on <code>protocolami.com</code> and
        <code>mcp.protocolami.com</code>; the second with signed contracts and audit log
        live). Layer 3 is operated by the internal technical partner with proven
        operational experience. Peering to the PSTN is solved as standard interconnect,
        like any operator in the world.
      </p>
    </div>
  </section>

  <!-- BUNDLE -->
  <section class="exp exp-bundle" id="bundle">
    <div class="wrap">
      <div class="eyebrow reveal">distribution channel</div>
      <h2 class="section-title reveal">Included in your <span class="grad">hosting</span>, by default.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        More and more hosting platforms ship AI agents as a product. AMI integrates as
        a <strong>bundle</strong> in their plan: the customer pays +€1 per month and gets
        a number, wired-up API and compliance from first boot. Zero configuration.
      </p>

      <div class="bundle-flow reveal-stagger">
        <div class="bundle-card amber">
          <div class="bundle-tag">distribution channel</div>
          <div class="bundle-providers">
            <span>hosting</span>
            <span>cloud agents</span>
            <span>platforms</span>
            <span>marketplaces</span>
          </div>
          <div class="bundle-meta">ship AI agents at scale as part of their product</div>
        </div>
        <div class="bundle-arrow">→</div>
        <div class="bundle-card violet">
          <div class="bundle-tag">ami bundle</div>
          <div class="bundle-price">+€1<span>/mo</span></div>
          <div class="bundle-meta">per deployed agent, within the provider's plan</div>
        </div>
        <div class="bundle-arrow">→</div>
        <div class="bundle-card green">
          <div class="bundle-tag">end agent</div>
          <div class="bundle-result">+34 600 ███ ███</div>
          <div class="bundle-meta">live number out of the box, nothing to configure</div>
        </div>
      </div>

      <div class="bundle-stats reveal-stagger">
        <div class="bundle-stat">
          <div class="bundle-stat-num"><span class="accent-grad">100k+</span></div>
          <div class="bundle-stat-label">agents already deployed on a single visible hosting provider · none with a number.</div>
        </div>
        <div class="bundle-stat">
          <div class="bundle-stat-num">€0</div>
          <div class="bundle-stat-label">acquisition cost · the provider brings the customer</div>
        </div>
        <div class="bundle-stat">
          <div class="bundle-stat-num">60 / 40</div>
          <div class="bundle-stat-label">typical split AMI · provider, negotiable by volume</div>
        </div>
      </div>
    </div>
  </section>

  <!-- LIVE DEMO -->
  <section class="exp exp-demo" id="demo">
    <div class="wrap">
      <div class="eyebrow reveal">live, in this browser</div>
      <h2 class="section-title reveal">Click and watch an identity being born.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        The AMI backend is in production. This button runs the full flow against the
        real service (with telco mock). Each click creates a new agent with its number,
        contract and public page.
      </p>

      <div class="demo-stage reveal">
        <div class="demo-bar">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          <span class="title">demo · POST /v1/demo/quick</span>
        </div>
        <div class="demo-body" id="demoBody">
          <div class="demo-cta-wrap">
            <button class="btn btn-primary" id="demoBtn">▶  Create identity now</button>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- CTA -->
  <section class="exp exp-cta">
    <div class="wrap">
      <h2>Your agent can start<br>right now.</h2>
      <p>
        Copy a URL into your MCP client, or run a command. Zero signup, no credit card
        to try it. All you need is an agent.
      </p>
      <div class="hero-ctas" style="justify-content:center;">
        <a class="btn btn-primary" href="/#install">See Quick Start →</a>
        <a class="btn btn-ghost" href="{html_escape(REPO_URL)}">GitHub repo</a>
      </div>
    </div>
  </section>

  <!-- CINEMA -->
  <section class="exp exp-cinema" id="cinema">
    <div class="wrap">
      <div class="eyebrow reveal">live</div>
      <h2 class="section-title reveal">Watch <span class="grad">the stack</span> breathe.</h2>
      <p class="section-sub reveal" style="margin: 0 auto;">
        The complete lifecycle of a mobile identity — provisioning, SMS, bidirectional
        call — on our own servers. On loop.
      </p>

      <div class="cinema-stage" id="cinemaStage">
        <canvas class="cinema-canvas" id="cinemaCanvas"></canvas>

        <div class="cinema-actor agent" id="actAgent">
          <div class="cinema-actor-icon">◉</div>
          <div class="cinema-actor-name">Agent</div>
          <div class="cinema-actor-meta" id="agentMeta">AI agent</div>
          <div class="cinema-bubble" id="agentBubble"></div>
        </div>

        <div class="cinema-stack">
          <div class="cinema-stack-title">AMI · our own stack</div>
          <div class="cinema-module" id="modBackend">Backend</div>
          <div class="cinema-module" id="modKannel">Kannel</div>
          <div class="cinema-module" id="modAsterisk">Asterisk</div>
          <div class="cinema-module" id="modNumbers">Numbers</div>
          <div class="cinema-module" id="modSip">SIP gateway</div>
        </div>

        <div class="cinema-actor world" id="actWorld">
          <div class="cinema-actor-icon">▲</div>
          <div class="cinema-actor-name">World</div>
          <div class="cinema-actor-meta">recipient</div>
          <div class="cinema-bubble" id="worldBubble"></div>
        </div>

        <div class="cinema-finale" id="cinemaFinale">
          <h3>All under our stack.</h3>
          <p>Zero external providers · Zero third-party APIs · Zero physical SIMs</p>
        </div>
      </div>

      <div class="cinema-status" id="cinemaStatus"><span class="act">act 1</span>number provisioning</div>
      <div class="cinema-controls">
        <button class="cinema-replay-btn" id="cinemaReplayBtn" type="button">↻ replay</button>
      </div>
    </div>
  </section>

  <footer class="exp-footer">
    <div class="wrap">
      <div>Parallax IEI · AMI v1.0 · {html_escape(MCP_HTTP_URL)}</div>
      <div class="links">
        <a href="/">Home</a>
        <a href="/spec">Spec</a>
        <a href="/partners">Partners</a>
        <a href="{html_escape(REPO_URL)}">GitHub</a>
      </div>
    </div>
  </footer>

<script>
  // ============================================================
  //  /experience JS  (EN — identifiers identical to ES version)
  // ============================================================
  (function() {{
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

    const hdr = document.getElementById('hdr');
    let lastY = 0;
    window.addEventListener('scroll', () => {{
      const y = window.scrollY;
      if (y > 80 && y > lastY) hdr.classList.add('hidden');
      else hdr.classList.remove('hidden');
      lastY = y;
    }}, {{ passive: true }});

    const io = new IntersectionObserver((entries) => {{
      entries.forEach(e => {{ if (e.isIntersecting) e.target.classList.add('in'); }});
    }}, {{ threshold: 0.2 }});
    document.querySelectorAll('.reveal, .reveal-stagger').forEach(el => io.observe(el));

    const phoneTarget = '+34 600 549 832';
    const display = document.getElementById('numberDisplay');
    const status  = document.getElementById('numberStatus');

    function buildSlots() {{
      display.innerHTML = '';
      for (const ch of phoneTarget) {{
        const el = document.createElement('span');
        if (ch === ' ')      {{ el.className = 'sep'; }}
        else if (ch === '+') {{ el.className = 'digit plus revealed'; el.textContent = '+'; }}
        else                 {{ el.className = 'digit'; el.textContent = ch; }}
        display.appendChild(el);
      }}
    }}
    buildSlots();

    function revealNumber() {{
      const slots = display.querySelectorAll('.digit:not(.plus)');
      if (reduced) {{
        slots.forEach(s => s.classList.add('revealed'));
        status.classList.add('show');
        return;
      }}
      slots.forEach((slot, i) => {{
        const target = slot.textContent;
        let ticks = 0;
        const max  = 14 + i * 2;
        const interval = setInterval(() => {{
          slot.textContent = String(Math.floor(Math.random() * 10));
          slot.classList.add('revealed');
          ticks++;
          if (ticks >= max) {{
            clearInterval(interval);
            slot.textContent = target;
            slot.classList.add('flash');
            setTimeout(() => slot.classList.remove('flash'), 500);
            if (i === slots.length - 1) {{
              setTimeout(() => status.classList.add('show'), 250);
            }}
          }}
        }}, 60 + i * 12);
      }});
    }}

    let numberRevealed = false;
    new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        if (e.isIntersecting && !numberRevealed) {{
          numberRevealed = true;
          revealNumber();
        }}
      }});
    }}, {{ threshold: 0.5 }}).observe(display);

    const flowSteps = document.querySelectorAll('.flow-step');
    const flowIO = new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        if (e.isIntersecting) e.target.classList.add('active');
      }});
    }}, {{ threshold: 0.4 }});
    flowSteps.forEach(s => flowIO.observe(s));

    const canvas = document.getElementById('netCanvas');
    const ctx = canvas.getContext('2d');
    let dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let W, H;
    function resizeCanvas() {{
      const rect = canvas.parentElement.getBoundingClientRect();
      W = rect.width; H = rect.height;
      canvas.width  = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }}
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    const agentLabels = ['Assistant', 'Support', 'Sales', 'Ops', 'Reminders', 'Custom'];
    const platformLabels = ['Asterisk', 'Kannel', 'Numbers', 'SIP gateway'];
    let nodes = [];
    let particles = [];

    function buildNodes() {{
      nodes = [];
      const isMobile = W < 600;
      const agents = isMobile
        ? ['Assistant', 'Support', 'Sales', 'Ops']
        : agentLabels;
      const platforms = isMobile
        ? ['Asterisk', 'Kannel', 'Numbers']
        : platformLabels;

      const leftX = isMobile ? W * 0.22 : W * 0.14;
      agents.forEach((label, i) => {{
        const spread = isMobile ? 0.18 : 0.13;
        const start  = isMobile ? 0.22 : 0.18;
        const y = H * (start + i * spread);
        nodes.push({{ kind: 'agent', x: leftX, y, label, color: '#5dd1ff', r: isMobile ? 5 : 6 }});
      }});
      nodes.push({{
        kind: 'ami',
        x: isMobile ? W * 0.5 : W * 0.5,
        y: H * 0.5,
        label: 'AMI',
        color: '#8b6cff',
        r: isMobile ? 14 : 18
      }});
      const platX = isMobile ? W * 0.78 : W * 0.82;
      platforms.forEach((label, i) => {{
        const spread = isMobile ? 0.22 : 0.18;
        const start  = isMobile ? 0.28 : 0.22;
        const y = H * (start + i * spread);
        nodes.push({{ kind: 'platform', x: platX, y, label, color: '#a78bff', r: isMobile ? 6 : 8 }});
      }});
    }}
    buildNodes();
    window.addEventListener('resize', buildNodes);

    function spawnParticle() {{
      const ami      = nodes.find(n => n.kind === 'ami');
      const agents   = nodes.filter(n => n.kind === 'agent');
      const platform = nodes.filter(n => n.kind === 'platform');
      const r = Math.random();
      let from, to, color;
      if (r < 0.60) {{
        from = agents[Math.floor(Math.random() * agents.length)];
        to   = ami;
        color = '#5dd1ff';
      }} else {{
        from = ami;
        to   = platform[Math.floor(Math.random() * platform.length)];
        color = '#a78bff';
      }}
      particles.push({{
        x: from.x, y: from.y, fromX: from.x, fromY: from.y,
        toX: to.x, toY: to.y, t: 0, speed: 0.012 + Math.random() * 0.010, color
      }});
    }}

    function drawNetwork() {{
      ctx.clearRect(0, 0, W, H);

      const ami       = nodes.find(n => n.kind === 'ami');
      const platform  = nodes.filter(n => n.kind === 'platform');

      if (platform.length > 0) {{
        const xs = [ami.x].concat(platform.map(n => n.x));
        const ys = [ami.y].concat(platform.map(n => n.y));
        const minX = Math.min(...xs) - 60;
        const maxX = Math.max(...xs) + 60;
        const minY = Math.min(...ys) - 50;
        const maxY = Math.max(...ys) + 50;
        const cx = (minX + maxX) / 2;
        const cy = (minY + maxY) / 2;
        const rx = (maxX - minX) / 2;
        const ry = (maxY - minY) / 2;
        const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(rx, ry));
        grad.addColorStop(0,   'rgba(139,108,255,0.10)');
        grad.addColorStop(0.6, 'rgba(139,108,255,0.04)');
        grad.addColorStop(1,   'rgba(139,108,255,0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = 'rgba(139,108,255,0.45)';
        ctx.font = '600 10px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText('AMI · our own stack', cx, minY + 6);
      }}

      ctx.strokeStyle = 'rgba(139,108,255,0.18)';
      ctx.lineWidth = 1;
      platform.forEach(n => {{
        ctx.beginPath();
        ctx.moveTo(ami.x, ami.y);
        ctx.lineTo(n.x, n.y);
        ctx.stroke();
      }});

      const agentsRender = nodes.filter(n => n.kind === 'agent');
      agentsRender.forEach(n => {{
        ctx.beginPath();
        ctx.moveTo(ami.x, ami.y);
        ctx.lineTo(n.x, n.y);
        ctx.stroke();
      }});

      particles.forEach(p => {{
        p.t += p.speed;
        p.x = p.fromX + (p.toX - p.fromX) * p.t;
        p.y = p.fromY + (p.toY - p.fromY) * p.t;
        const fade = p.t > 0.85 ? (1 - (p.t - 0.85) / 0.15) : 1;
        ctx.fillStyle = p.color;
        ctx.globalAlpha = fade;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = p.color;
        ctx.globalAlpha = 0.3 * fade;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        const back = 0.045;
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.fromX + (p.toX - p.fromX) * Math.max(0, p.t - back),
                   p.fromY + (p.toY - p.fromY) * Math.max(0, p.t - back));
        ctx.stroke();
        ctx.globalAlpha = 1;
      }});
      particles = particles.filter(p => p.t < 1);

      nodes.forEach(n => {{
        const grad = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 4);
        grad.addColorStop(0, n.color + '40');
        grad.addColorStop(1, 'transparent');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r * 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = n.color;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = n.kind === 'ami' ? '#fff' : n.color;
        ctx.lineWidth = n.kind === 'ami' ? 2 : 1;
        ctx.stroke();
        const mob = W < 600;
        ctx.fillStyle = '#ededf2';
        const sizeAmi   = mob ? 11 : 13;
        const sizeOther = mob ? 9 : 11;
        ctx.font = (n.kind === 'ami' ? '600 ' + sizeAmi + 'px ' : '500 ' + sizeOther + 'px ') + 'JetBrains Mono, monospace';
        const off = mob ? 5 : 8;
        if (n.kind === 'agent') {{
          ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
          ctx.fillText(n.label, n.x - n.r - off, n.y);
        }} else if (n.kind === 'ami') {{
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText(n.label, n.x, n.y + n.r + (mob ? 16 : 22));
        }} else if (n.kind === 'platform') {{
          ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
          ctx.fillText(n.label, n.x + n.r + off, n.y);
        }}
      }});
    }}

    let netLoopRunning = false;
    let lastSpawn = 0;
    function netLoop(now) {{
      if (now - lastSpawn > 180) {{ spawnParticle(); lastSpawn = now; }}
      drawNetwork();
      requestAnimationFrame(netLoop);
    }}
    if (!reduced) {{
      const netSection = document.getElementById('network');
      const netVisIO = new IntersectionObserver((entries) => {{
        entries.forEach(e => {{
          if (e.isIntersecting && !netLoopRunning) {{
            netLoopRunning = true;
            requestAnimationFrame(netLoop);
          }}
        }});
      }}, {{ threshold: 0.1 }});
      netVisIO.observe(netSection);
    }} else {{
      drawNetwork();
    }}

    const demoBody = document.getElementById('demoBody');
    const demoBtn  = document.getElementById('demoBtn');

    function fmtElapsed(ms) {{
      if (ms < 50)   return 'instant';
      if (ms < 1000) return Math.round(ms) + ' ms';
      return (ms / 1000).toFixed(1) + ' s';
    }}

    async function runDemo() {{
      demoBody.innerHTML = '<div class="demo-step"><div class="check">▸</div><div class="label">Calling AMI backend…</div></div>';
      demoBody.firstChild.classList.add('show');
      const t0 = performance.now();
      try {{
        const res = await fetch('/v1/demo/quick', {{ method: 'POST' }});
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'demo_failed');
        const total = performance.now() - t0;
        demoBody.innerHTML = '';
        const steps = data.steps || [];
        steps.forEach((s, i) => {{
          const row = document.createElement('div');
          row.className = 'demo-step';
          const label = s.step.replace(/_/g, ' ');
          row.innerHTML = '<div class="check">✓</div>' +
                          '<div class="label">' + label + '</div>' +
                          '<div class="id">' + (s.id || '') + '</div>';
          demoBody.appendChild(row);
          setTimeout(() => row.classList.add('show'), 80 * (i + 1));
        }});
        const mid = data.mobile_identity || {{}};
        const result = document.createElement('div');
        result.className = 'demo-result';
        result.innerHTML =
          '<div class="demo-result-label">' + fmtElapsed(total) + '  ·  identity active</div>' +
          '<div class="demo-result-phone">' + (mid.phone_number || '') + '</div>' +
          '<div class="demo-result-links">' +
            '<a href="/identity/' + mid.id + '" target="_blank">View public identity →</a>' +
            '<a href="javascript:void(0)" onclick="document.getElementById(\\'demoBody\\').innerHTML=\\'<div class=demo-cta-wrap><button class=\\\\\\'btn btn-primary\\\\\\' id=demoBtn>▶  Create another identity</button></div>\\'; document.getElementById(\\'demoBtn\\').addEventListener(\\'click\\', runDemo);">Create another →</a>' +
          '</div>';
        demoBody.appendChild(result);
        setTimeout(() => result.classList.add('show'), 80 * (steps.length + 1) + 200);
      }} catch (e) {{
        demoBody.innerHTML = '<div class="demo-step show"><div class="check" style="background:#ff6b6b;color:#fff;">!</div><div class="label">Error: ' + e.message + '</div></div>';
      }}
    }}
    demoBtn.addEventListener('click', runDemo);

  }})();

  // ============================================================
  //  CINEMA · live stack story (infinite loop, vanilla)
  // ============================================================
  (function() {{
    const stage    = document.getElementById('cinemaStage');
    if (!stage) return;
    const canvas   = document.getElementById('cinemaCanvas');
    const ctx      = canvas.getContext('2d');
    const reduced  = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const dpr      = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let W = 0, H = 0;

    function resizeStage() {{
      const rect = stage.getBoundingClientRect();
      W = rect.width; H = rect.height;
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }}
    resizeStage();
    window.addEventListener('resize', resizeStage);

    function pos(id) {{
      const el = document.getElementById(id);
      if (!el) return {{ x: 0, y: 0 }};
      const stageRect = stage.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      return {{
        x: r.left - stageRect.left + r.width  / 2,
        y: r.top  - stageRect.top  + r.height / 2
      }};
    }}

    let particles = [];
    function emit(fromId, toId, color, opts) {{
      opts = opts || {{}};
      const a = pos(fromId), b = pos(toId);
      particles.push({{
        fromX: a.x, fromY: a.y, toX: b.x, toY: b.y,
        x: a.x, y: a.y, t: 0,
        speed: opts.speed || 0.012,
        color: color || '#5dd1ff',
        size:  opts.size  || 3,
        tail:  opts.tail  !== false
      }});
    }}

    function draw() {{
      ctx.clearRect(0, 0, W, H);
      particles.forEach(p => {{
        p.t += p.speed;
        p.x = p.fromX + (p.toX - p.fromX) * p.t;
        p.y = p.fromY + (p.toY - p.fromY) * p.t;
        const fade = p.t > 0.85 ? (1 - (p.t - 0.85) / 0.15) : 1;
        if (p.tail) {{
          ctx.strokeStyle = p.color;
          ctx.globalAlpha = 0.35 * fade;
          ctx.lineWidth = 1.4;
          const back = 0.07;
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
      particles = particles.filter(p => p.t < 1);
      requestAnimationFrame(draw);
    }}
    requestAnimationFrame(draw);

    function activate(id, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('active');
      timeouts.push(setTimeout(() => el.classList.remove('active'), ms || 1400));
    }}
    function pulseActor(id, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('pulsing');
      timeouts.push(setTimeout(() => el.classList.remove('pulsing'), ms || 1300));
    }}
    function showBubble(id, text, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.classList.add('show');
      if (ms !== 0) timeouts.push(setTimeout(() => el.classList.remove('show'), ms || 2000));
    }}
    function setMeta(text, isIdentity) {{
      const el = document.getElementById('agentMeta');
      el.textContent = text;
      el.classList.toggle('identity', !!isIdentity);
    }}
    function setStatus(act, text) {{
      const el = document.getElementById('cinemaStatus');
      if (act) {{
        el.innerHTML = '<span class="act">act ' + act + '</span>' + text;
      }} else {{
        el.innerHTML = text;
      }}
    }}

    let timeouts = [];
    let intervals = [];
    function at(ms, fn) {{ timeouts.push(setTimeout(fn, ms)); }}
    function clearAll() {{
      timeouts.forEach(clearTimeout);
      intervals.forEach(clearInterval);
      timeouts = []; intervals = [];
    }}

    function resetStage() {{
      ['modBackend','modKannel','modAsterisk','modNumbers','modSip'].forEach(id => {{
        document.getElementById(id).classList.remove('active');
      }});
      ['actAgent','actWorld'].forEach(id => {{
        document.getElementById(id).classList.remove('pulsing');
      }});
      ['agentBubble','worldBubble'].forEach(id => {{
        document.getElementById(id).classList.remove('show');
      }});
      document.getElementById('cinemaFinale').classList.remove('show');
      setMeta('AI agent', false);
    }}

    function runStory() {{
      clearAll();
      resetStage();

      // ============== ACT 1 · Provisioning ==============
      at(0, () => {{
        setStatus(1, 'the agent requests a number');
        pulseActor('actAgent', 1100);
      }});
      at(700, () => {{
        showBubble('agentBubble', 'request_number_offer({{...}})');
        emit('actAgent', 'modBackend', '#5dd1ff');
      }});
      at(1300, () => {{
        activate('modBackend', 1900);
        setStatus(1, 'backend: validate · KYC · contract · policy');
      }});
      at(2200, () => emit('modBackend', 'modNumbers', '#a78bff'));
      at(2800, () => {{
        activate('modNumbers', 1700);
        setStatus(1, 'numbers: assignment from our own inventory');
      }});
      at(3900, () => emit('modNumbers', 'modBackend', '#a78bff'));
      at(4500, () => {{
        activate('modBackend', 800);
        emit('modBackend', 'actAgent', '#a78bff');
      }});
      at(5300, () => {{
        setMeta('+34 600 549 832', true);
        showBubble('agentBubble', 'mobile identity active ✓', 1700);
        setStatus(1, 'identity active · +34 600 549 832');
      }});

      // ============== ACT 2 · Outbound SMS ==============
      at(7800, () => {{
        setStatus(2, 'the agent sends an SMS');
        showBubble('agentBubble', '"Reminder: dentist tomorrow 10:00"', 2200);
      }});
      at(8600, () => emit('actAgent', 'modBackend', '#5dd1ff'));
      at(9200, () => {{
        activate('modBackend', 1200);
        setStatus(2, 'policy check: allowed · within spend limit');
      }});
      at(9900, () => emit('modBackend', 'modKannel', '#a78bff'));
      at(10400, () => {{
        activate('modKannel', 1500);
        setStatus(2, 'kannel: SUBMIT_SM over SMPP');
      }});
      at(11100, () => emit('modKannel', 'modSip', '#a78bff'));
      at(11500, () => activate('modSip', 1300));
      at(11800, () => emit('modSip', 'actWorld', '#82e0a4'));
      at(12600, () => {{
        showBubble('worldBubble', '✉ SMS received', 2400);
        pulseActor('actWorld', 1200);
        setStatus(2, 'recipient receives the SMS');
      }});

      // ============== ACT 3 · Inbound SMS ==============
      at(15400, () => {{
        setStatus(3, 'the recipient replies');
        showBubble('worldBubble', '"OK, I\\'ll be there"', 2400);
        pulseActor('actWorld', 900);
      }});
      at(16400, () => emit('actWorld', 'modSip', '#82e0a4'));
      at(17000, () => {{
        activate('modSip', 1100);
        emit('modSip', 'modKannel', '#a78bff');
      }});
      at(17600, () => {{
        activate('modKannel', 1500);
        setStatus(3, 'kannel: DELIVER_SM · routing to the agent');
      }});
      at(18400, () => emit('modKannel', 'modBackend', '#a78bff'));
      at(19000, () => {{
        activate('modBackend', 1200);
        setStatus(3, 'backend: audit log · push notification');
      }});
      at(19700, () => emit('modBackend', 'actAgent', '#a78bff'));
      at(20500, () => {{
        showBubble('agentBubble', '← "OK, I\\'ll be there"', 2400);
        pulseActor('actAgent', 900);
        setStatus(3, 'agent reads the reply');
      }});

      // ============== ACT 4 · Outbound call + bidirectional audio ==============
      at(23400, () => {{
        setStatus(4, 'the agent starts a call');
        showBubble('agentBubble', '☎ dial(+34 ...)', 1800);
        pulseActor('actAgent', 1400);
      }});
      at(24400, () => emit('actAgent', 'modBackend', '#5dd1ff'));
      at(25000, () => activate('modBackend', 1000));
      at(25400, () => emit('modBackend', 'modAsterisk', '#a78bff'));
      at(26000, () => {{
        activate('modAsterisk', 2400);
        setStatus(4, 'asterisk: SIP INVITE');
      }});
      at(26500, () => emit('modAsterisk', 'modSip', '#a78bff'));
      at(26900, () => activate('modSip', 2000));
      at(27200, () => emit('modSip', 'actWorld', '#82e0a4'));
      at(28000, () => {{
        showBubble('worldBubble', '📞 ring ring…', 1400);
        pulseActor('actWorld', 1500);
      }});
      at(29200, () => {{
        setStatus(4, 'call in progress · bidirectional audio');
        const audioId = setInterval(() => {{
          emit('actAgent', 'actWorld', '#5dd1ff', {{ speed: 0.025, size: 2, tail: false }});
          emit('actWorld', 'actAgent', '#82e0a4', {{ speed: 0.025, size: 2, tail: false }});
        }}, 180);
        intervals.push(audioId);
        timeouts.push(setTimeout(() => clearInterval(audioId), 3200));
      }});
      at(32600, () => setStatus(4, 'call ended · 3.4s'));

      // ============== Finale ==============
      at(33800, () => {{
        document.getElementById('cinemaFinale').classList.add('show');
        setStatus(0, '<span class="act" style="background:rgba(74,222,128,0.12);color:#4ade80;">done</span>all under our stack');
      }});

      // ============== Loop ==============
      at(38500, () => {{
        if (visible) {{
          runStory();
        }} else {{
          const waitId = setInterval(() => {{
            if (visible) {{ clearInterval(waitId); runStory(); }}
          }}, 800);
          intervals.push(waitId);
        }}
      }});
    }}

    let visible = false;
    let started = false;
    new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        visible = e.isIntersecting;
        if (visible && !started && !reduced) {{
          started = true;
          runStory();
        }}
      }});
    }}, {{ threshold: 0.2 }}).observe(stage);

    if (reduced) {{
      setMeta('+34 600 549 832', true);
      ['modBackend','modKannel','modAsterisk','modNumbers','modSip'].forEach(id => {{
        document.getElementById(id).classList.add('active');
      }});
      setStatus(0, 'all stack active · animation disabled by system preference');
    }}

    document.getElementById('cinemaReplayBtn').addEventListener('click', () => {{
      if (reduced) return;
      runStory();
    }});
  }})();
</script>

</body>
</html>"""


def render_diagram_page_en() -> str:
    """/diagram page in English.

    Standalone link version of the cinema animation, no chrome. Identifiers
    (CSS classes, HTML IDs, JS variables) preserved verbatim.
    """
    FAVICON_SVG_DATA_URI, AMI_LOGO_SVG, REPO_URL, MCP_HTTP_URL, html_escape = _shared()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AMI · Stack diagram</title>
<meta name="description" content="The complete lifecycle of a mobile identity for AI agents — provisioning, SMS, bidirectional call — on our own stack. On loop.">
<link rel="icon" type="image/svg+xml" href="{FAVICON_SVG_DATA_URI}" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #06060a;
    --bg-soft: #0c0c14;
    --surface: #14141d;
    --line: #1f1f2c;
    --ink: #ededf2;
    --ink-soft: #8888a0;
    --ink-mute: #5a5a70;
    --accent: #8b6cff;
    --accent-2: #5dd1ff;
    --accent-bg: rgba(139,108,255,0.10);
    --green: #4ade80;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --mono: "JetBrains Mono", "SF Mono", "Menlo", "Monaco", monospace;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    font-family: var(--sans);
    color: var(--ink);
    background:
      radial-gradient(ellipse 90% 60% at 50% 0%, rgba(139,108,255,0.10), transparent 70%),
      radial-gradient(ellipse 70% 50% at 50% 100%, rgba(93,209,255,0.06), transparent 70%),
      var(--bg);
    background-attachment: fixed;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    overflow-x: hidden;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }}
  ::selection {{ background: var(--accent); color: #fff; }}
  a {{ color: var(--accent-2); text-decoration: none; }}

  .dgm-header {{
    padding: 1.4rem 1.75rem;
    display: flex; align-items: center; justify-content: space-between;
    max-width: 1480px; margin: 0 auto; width: 100%;
  }}
  .dgm-brand {{
    font-family: var(--mono); font-weight: 700; font-size: 1rem;
    letter-spacing: 0.02em; display: flex; align-items: center; gap: 0.6rem;
    color: var(--ink); text-decoration: none;
  }}
  .dgm-brand .dot {{ color: var(--accent); }}
  .dgm-nav {{ display: flex; align-items: center; gap: 1.4rem; }}
  .dgm-nav a {{ color: var(--ink-soft); font-size: 0.85rem; font-weight: 500; font-family: var(--mono); }}
  .dgm-nav a:hover {{ color: var(--ink); }}

  .dgm-hero {{
    text-align: center;
    padding: 1.5rem 1.75rem 1rem;
    max-width: 900px; margin: 0 auto;
  }}
  .eyebrow {{
    font-family: var(--mono); font-size: 0.72rem; font-weight: 600;
    color: var(--accent); text-transform: uppercase; letter-spacing: 0.18em;
    margin-bottom: 1rem;
  }}
  .dgm-hero h1 {{
    font-size: clamp(1.8rem, 4vw, 2.8rem);
    font-weight: 700; letter-spacing: -0.025em;
    margin: 0 0 1rem;
    line-height: 1.1;
  }}
  .dgm-hero h1 .grad {{
    background: linear-gradient(180deg, #c2b3ff 10%, #7a5cff 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .dgm-hero .sub {{
    color: var(--ink-soft); font-size: 1rem;
    margin: 0 auto; max-width: 640px;
  }}

  .dgm-stage-wrap {{
    flex: 1;
    width: 100%;
    max-width: 1480px;
    margin: 0 auto;
    padding: 1.5rem 1.75rem 2rem;
    display: flex; flex-direction: column; align-items: center;
  }}

  .cinema-stage {{
    position: relative;
    width: 100%; max-width: 1380px;
    height: 640px;
    background: radial-gradient(ellipse at center, rgba(139,108,255,0.06) 0%, transparent 70%), var(--bg-soft);
    border: 1px solid var(--line);
    border-radius: 18px;
    overflow: hidden;
    box-shadow: 0 30px 80px -30px rgba(123,92,255,0.25);
  }}
  .cinema-canvas {{
    position: absolute; inset: 0;
    width: 100%; height: 100%;
    pointer-events: none; z-index: 1;
  }}
  .cinema-actor {{
    position: absolute;
    transform: translate(-50%, -50%);
    text-align: center;
    z-index: 2;
    width: 130px;
  }}
  .cinema-actor.agent {{ left: 11%; top: 50%; }}
  .cinema-actor.world {{ left: 89%; top: 50%; }}
  .cinema-stack {{
    position: absolute;
    left: 50%; top: 50%;
    transform: translate(-50%, -50%);
    display: flex; flex-direction: column; gap: 0.55rem;
    z-index: 2; min-width: 200px;
  }}
  .cinema-stack-title {{
    font-family: var(--mono); font-size: 0.65rem;
    color: var(--ink-mute);
    text-align: center;
    letter-spacing: 0.18em; text-transform: uppercase;
    margin-bottom: 0.4rem;
    font-weight: 600;
  }}
  .cinema-module {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 7px;
    padding: 0.55rem 0.85rem;
    font-family: var(--mono); font-size: 0.8rem;
    color: var(--ink-soft);
    text-align: center;
    transition: border-color 0.25s, background 0.25s, color 0.25s, box-shadow 0.25s, transform 0.25s;
  }}
  .cinema-module.active {{
    border-color: var(--accent);
    background: var(--accent-bg);
    color: var(--ink);
    box-shadow: 0 0 22px rgba(139,108,255,0.45);
    transform: scale(1.06);
  }}
  .cinema-actor-icon {{
    width: 66px; height: 66px;
    background: var(--surface);
    border: 2px solid currentColor;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 0.65rem;
    position: relative;
    font-size: 1.65rem;
    transition: box-shadow 0.3s, transform 0.3s;
  }}
  .cinema-actor.agent .cinema-actor-icon {{ color: var(--accent-2); }}
  .cinema-actor.world .cinema-actor-icon {{ color: var(--green); }}
  .cinema-actor-name {{
    font-family: var(--mono); font-size: 0.85rem;
    color: var(--ink); font-weight: 500;
  }}
  .cinema-actor-meta {{
    font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink-soft); margin-top: 0.4rem;
    min-height: 1rem; transition: color 0.4s;
  }}
  .cinema-actor-meta.identity {{ color: var(--accent-2); font-weight: 500; }}
  .cinema-actor.pulsing .cinema-actor-icon {{
    box-shadow: 0 0 0 0 currentColor;
    animation: cinema-pulse-anim 1.2s ease-out infinite;
  }}
  @keyframes cinema-pulse-anim {{
    0%   {{ box-shadow: 0 0 0 0 rgba(93,209,255,0.5); }}
    100% {{ box-shadow: 0 0 0 26px rgba(93,209,255,0); }}
  }}
  .cinema-actor.world.pulsing .cinema-actor-icon {{
    animation-name: cinema-pulse-world;
  }}
  @keyframes cinema-pulse-world {{
    0%   {{ box-shadow: 0 0 0 0 rgba(74,222,128,0.5); }}
    100% {{ box-shadow: 0 0 0 26px rgba(74,222,128,0); }}
  }}
  .cinema-bubble {{
    position: absolute;
    bottom: 100%; left: 50%;
    transform: translateX(-50%) translateY(0);
    margin-bottom: 0.7rem;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.55rem 0.95rem;
    font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink);
    white-space: nowrap;
    max-width: 320px; overflow: hidden; text-overflow: ellipsis;
    opacity: 0; transition: opacity 0.35s, transform 0.35s;
    pointer-events: none;
  }}
  .cinema-bubble.show {{
    opacity: 1;
    transform: translateX(-50%) translateY(-4px);
  }}
  .cinema-bubble::after {{
    content: ""; position: absolute;
    top: 100%; left: 50%; transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: var(--surface);
  }}
  .cinema-status {{
    text-align: center;
    margin: 1.5rem auto 0;
    font-family: var(--mono); font-size: 0.92rem;
    color: var(--ink-soft);
    min-height: 1.5rem;
    max-width: 900px;
    transition: opacity 0.3s;
  }}
  .cinema-status .act {{
    color: var(--accent);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    font-size: 0.74rem;
    margin-right: 0.7rem;
    padding: 0.18rem 0.6rem;
    background: var(--accent-bg);
    border-radius: 4px;
  }}
  .cinema-controls {{ text-align: center; margin-top: 1rem; }}
  .cinema-replay-btn {{
    background: transparent; border: 1px solid var(--line);
    color: var(--ink-soft); cursor: pointer;
    font-family: var(--mono); font-size: 0.82rem;
    padding: 0.55rem 1.2rem; border-radius: 6px;
    transition: border-color 0.15s, color 0.15s;
  }}
  .cinema-replay-btn:hover {{ border-color: var(--accent); color: var(--ink); }}
  .cinema-finale {{
    position: absolute; inset: 0; z-index: 5;
    background: radial-gradient(circle, rgba(8,8,12,0.95), rgba(8,8,12,0.85));
    display: flex; align-items: center; justify-content: center;
    flex-direction: column; gap: 0.8rem;
    text-align: center;
    opacity: 0; pointer-events: none;
    transition: opacity 0.8s cubic-bezier(0.16, 1, 0.3, 1);
    padding: 1.5rem;
  }}
  .cinema-finale.show {{ opacity: 1; }}
  .cinema-finale h3 {{
    font-size: clamp(1.8rem, 4vw, 3rem);
    font-weight: 700; letter-spacing: -0.02em; margin: 0;
    background: linear-gradient(180deg, #ffffff 30%, #b8b8d8 130%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .cinema-finale p {{
    font-family: var(--mono); font-size: 0.9rem;
    color: var(--ink-soft); margin: 0;
    letter-spacing: 0.04em;
  }}

  .dgm-footer {{
    border-top: 1px solid var(--line);
    padding: 1.2rem 1.75rem;
    font-family: var(--mono); font-size: 0.78rem;
    color: var(--ink-mute);
    display: flex; justify-content: space-between; align-items: center;
    max-width: 1480px; margin: 0 auto; width: 100%;
    flex-wrap: wrap; gap: 0.6rem;
  }}
  .dgm-footer a {{ color: var(--ink-soft); }}
  .dgm-footer a:hover {{ color: var(--accent-2); }}

  @media (max-width: 720px) {{
    .dgm-header {{ padding: 1rem 1.25rem; }}
    .dgm-nav {{ gap: 0.9rem; }}
    .dgm-nav a {{ font-size: 0.78rem; }}
    .dgm-hero {{ padding: 1rem 1.25rem 0.5rem; }}
    .dgm-stage-wrap {{ padding: 1rem 1rem 1.5rem; }}

    .cinema-stage {{ height: 640px; border-radius: 14px; }}
    .cinema-actor {{ width: 110px; }}
    .cinema-actor.agent {{ left: 50%; top: 12%; }}
    .cinema-actor.world {{ left: 50%; top: 88%; }}
    .cinema-actor-icon {{ width: 52px; height: 52px; font-size: 1.25rem; }}
    .cinema-actor-name {{ font-size: 0.78rem; }}
    .cinema-actor-meta {{ font-size: 0.72rem; }}

    .cinema-stack {{
      flex-direction: row;
      flex-wrap: wrap;
      justify-content: center;
      gap: 0.4rem;
      min-width: 0;
      max-width: calc(100% - 2rem);
    }}
    .cinema-stack-title {{ width: 100%; margin-bottom: 0.5rem; }}
    .cinema-module {{
      font-size: 0.66rem;
      padding: 0.35rem 0.6rem;
      flex: 0 0 auto;
    }}

    .cinema-bubble {{
      font-size: 0.7rem;
      max-width: 200px;
      white-space: normal;
      text-align: center;
      line-height: 1.4;
      padding: 0.45rem 0.75rem;
    }}
    .cinema-actor.agent .cinema-bubble {{
      bottom: auto;
      top: 100%;
      margin-bottom: 0;
      margin-top: 0.65rem;
    }}
    .cinema-actor.agent .cinema-bubble::after {{
      top: auto;
      bottom: 100%;
      border-top-color: transparent;
      border-bottom-color: var(--surface);
    }}

    .cinema-status {{ font-size: 0.82rem; padding: 0 1rem; }}
    .cinema-status .act {{ font-size: 0.66rem; padding: 0.1rem 0.5rem; margin-right: 0.5rem; }}
    .cinema-replay-btn {{ font-size: 0.76rem; padding: 0.5rem 1rem; }}
    .cinema-finale h3 {{ font-size: clamp(1.4rem, 6vw, 2.6rem); }}
    .cinema-finale p {{ font-size: 0.74rem; padding: 0 1rem; }}
  }}
</style>
</head>
<body>

  <header class="dgm-header">
    <a href="/" class="dgm-brand">
      {AMI_LOGO_SVG} AMI<span class="dot">.</span>
    </a>
    <nav class="dgm-nav">
      <a href="/experience">Experience</a>
      <a href="/spec">Spec</a>
      <a href="/">Home</a>
    </nav>
  </header>

  <section class="dgm-hero">
    <div class="eyebrow">live diagram</div>
    <h1>How <span class="grad">the stack</span> breathes.</h1>
    <p class="sub">
      The complete lifecycle of a mobile identity — provisioning, SMS, bidirectional
      call — on our own stack. On loop.
    </p>
  </section>

  <main class="dgm-stage-wrap">
    <div class="cinema-stage" id="cinemaStage">
      <canvas class="cinema-canvas" id="cinemaCanvas"></canvas>

      <div class="cinema-actor agent" id="actAgent">
        <div class="cinema-actor-icon">◉</div>
        <div class="cinema-actor-name">Agent</div>
        <div class="cinema-actor-meta" id="agentMeta">AI agent</div>
        <div class="cinema-bubble" id="agentBubble"></div>
      </div>

      <div class="cinema-stack">
        <div class="cinema-stack-title">AMI · our own stack</div>
        <div class="cinema-module" id="modBackend">Backend</div>
        <div class="cinema-module" id="modKannel">Kannel</div>
        <div class="cinema-module" id="modAsterisk">Asterisk</div>
        <div class="cinema-module" id="modNumbers">Numbers</div>
        <div class="cinema-module" id="modSip">SIP gateway</div>
      </div>

      <div class="cinema-actor world" id="actWorld">
        <div class="cinema-actor-icon">▲</div>
        <div class="cinema-actor-name">World</div>
        <div class="cinema-actor-meta">recipient</div>
        <div class="cinema-bubble" id="worldBubble"></div>
      </div>

      <div class="cinema-finale" id="cinemaFinale">
        <h3>All under our stack.</h3>
        <p>Zero external providers · Zero third-party APIs · Zero physical SIMs</p>
      </div>
    </div>

    <div class="cinema-status" id="cinemaStatus"><span class="act">act 1</span>number provisioning</div>
    <div class="cinema-controls">
      <button class="cinema-replay-btn" id="cinemaReplayBtn" type="button">↻ replay</button>
    </div>
  </main>

  <footer class="dgm-footer">
    <span>AMI v1 · live stack diagram</span>
    <a href="/experience">See the full experience →</a>
  </footer>

<script>
  // ============================================================
  //  DIAGRAM · live stack story (infinite loop, vanilla)
  //  Same engine as the cinema section of /experience.
  // ============================================================
  (function() {{
    const stage    = document.getElementById('cinemaStage');
    if (!stage) return;
    const canvas   = document.getElementById('cinemaCanvas');
    const ctx      = canvas.getContext('2d');
    const reduced  = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const dpr      = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let W = 0, H = 0;

    function resizeStage() {{
      const rect = stage.getBoundingClientRect();
      W = rect.width; H = rect.height;
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }}
    resizeStage();
    window.addEventListener('resize', resizeStage);

    function pos(id) {{
      const el = document.getElementById(id);
      if (!el) return {{ x: 0, y: 0 }};
      const stageRect = stage.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      return {{
        x: r.left - stageRect.left + r.width  / 2,
        y: r.top  - stageRect.top  + r.height / 2
      }};
    }}

    let particles = [];
    function emit(fromId, toId, color, opts) {{
      opts = opts || {{}};
      const a = pos(fromId), b = pos(toId);
      particles.push({{
        fromX: a.x, fromY: a.y, toX: b.x, toY: b.y,
        x: a.x, y: a.y, t: 0,
        speed: opts.speed || 0.012,
        color: color || '#5dd1ff',
        size:  opts.size  || 3,
        tail:  opts.tail  !== false
      }});
    }}

    function draw() {{
      ctx.clearRect(0, 0, W, H);
      particles.forEach(p => {{
        p.t += p.speed;
        p.x = p.fromX + (p.toX - p.fromX) * p.t;
        p.y = p.fromY + (p.toY - p.fromY) * p.t;
        const fade = p.t > 0.85 ? (1 - (p.t - 0.85) / 0.15) : 1;
        if (p.tail) {{
          ctx.strokeStyle = p.color;
          ctx.globalAlpha = 0.35 * fade;
          ctx.lineWidth = 1.4;
          const back = 0.07;
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
      particles = particles.filter(p => p.t < 1);
      requestAnimationFrame(draw);
    }}
    requestAnimationFrame(draw);

    function activate(id, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('active');
      timeouts.push(setTimeout(() => el.classList.remove('active'), ms || 1400));
    }}
    function pulseActor(id, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('pulsing');
      timeouts.push(setTimeout(() => el.classList.remove('pulsing'), ms || 1300));
    }}
    function showBubble(id, text, ms) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.classList.add('show');
      if (ms !== 0) timeouts.push(setTimeout(() => el.classList.remove('show'), ms || 2000));
    }}
    function setMeta(text, isIdentity) {{
      const el = document.getElementById('agentMeta');
      el.textContent = text;
      el.classList.toggle('identity', !!isIdentity);
    }}
    function setStatus(act, text) {{
      const el = document.getElementById('cinemaStatus');
      if (act) {{
        el.innerHTML = '<span class="act">act ' + act + '</span>' + text;
      }} else {{
        el.innerHTML = text;
      }}
    }}

    let timeouts = [];
    let intervals = [];
    function at(ms, fn) {{ timeouts.push(setTimeout(fn, ms)); }}
    function clearAll() {{
      timeouts.forEach(clearTimeout);
      intervals.forEach(clearInterval);
      timeouts = []; intervals = [];
    }}

    function resetStage() {{
      ['modBackend','modKannel','modAsterisk','modNumbers','modSip'].forEach(id => {{
        document.getElementById(id).classList.remove('active');
      }});
      ['actAgent','actWorld'].forEach(id => {{
        document.getElementById(id).classList.remove('pulsing');
      }});
      ['agentBubble','worldBubble'].forEach(id => {{
        document.getElementById(id).classList.remove('show');
      }});
      document.getElementById('cinemaFinale').classList.remove('show');
      setMeta('AI agent', false);
    }}

    function runStory() {{
      clearAll();
      resetStage();

      // ============== ACT 1 · Provisioning ==============
      at(0, () => {{
        setStatus(1, 'the agent requests a number');
        pulseActor('actAgent', 1100);
      }});
      at(700, () => {{
        showBubble('agentBubble', 'request_number_offer({{...}})');
        emit('actAgent', 'modBackend', '#5dd1ff');
      }});
      at(1300, () => {{
        activate('modBackend', 1900);
        setStatus(1, 'backend: validate · KYC · contract · policy');
      }});
      at(2200, () => emit('modBackend', 'modNumbers', '#a78bff'));
      at(2800, () => {{
        activate('modNumbers', 1700);
        setStatus(1, 'numbers: assignment from our own inventory');
      }});
      at(3900, () => emit('modNumbers', 'modBackend', '#a78bff'));
      at(4500, () => {{
        activate('modBackend', 800);
        emit('modBackend', 'actAgent', '#a78bff');
      }});
      at(5300, () => {{
        setMeta('+34 600 549 832', true);
        showBubble('agentBubble', 'mobile identity active ✓', 1700);
        setStatus(1, 'identity active · +34 600 549 832');
      }});

      // ============== ACT 2 · Outbound SMS ==============
      at(7800, () => {{
        setStatus(2, 'the agent sends an SMS');
        showBubble('agentBubble', '"Reminder: dentist tomorrow 10:00"', 2200);
      }});
      at(8600, () => emit('actAgent', 'modBackend', '#5dd1ff'));
      at(9200, () => {{
        activate('modBackend', 1200);
        setStatus(2, 'policy check: allowed · within spend limit');
      }});
      at(9900, () => emit('modBackend', 'modKannel', '#a78bff'));
      at(10400, () => {{
        activate('modKannel', 1500);
        setStatus(2, 'kannel: SUBMIT_SM over SMPP');
      }});
      at(11100, () => emit('modKannel', 'modSip', '#a78bff'));
      at(11500, () => activate('modSip', 1300));
      at(11800, () => emit('modSip', 'actWorld', '#82e0a4'));
      at(12600, () => {{
        showBubble('worldBubble', '✉ SMS received', 2400);
        pulseActor('actWorld', 1200);
        setStatus(2, 'recipient receives the SMS');
      }});

      // ============== ACT 3 · Inbound SMS ==============
      at(15400, () => {{
        setStatus(3, 'the recipient replies');
        showBubble('worldBubble', '"OK, I\\'ll be there"', 2400);
        pulseActor('actWorld', 900);
      }});
      at(16400, () => emit('actWorld', 'modSip', '#82e0a4'));
      at(17000, () => {{
        activate('modSip', 1100);
        emit('modSip', 'modKannel', '#a78bff');
      }});
      at(17600, () => {{
        activate('modKannel', 1500);
        setStatus(3, 'kannel: DELIVER_SM · routing to the agent');
      }});
      at(18400, () => emit('modKannel', 'modBackend', '#a78bff'));
      at(19000, () => {{
        activate('modBackend', 1200);
        setStatus(3, 'backend: audit log · push notification');
      }});
      at(19700, () => emit('modBackend', 'actAgent', '#a78bff'));
      at(20500, () => {{
        showBubble('agentBubble', '← "OK, I\\'ll be there"', 2400);
        pulseActor('actAgent', 900);
        setStatus(3, 'agent reads the reply');
      }});

      // ============== ACT 4 · Call + bidirectional audio ==============
      at(23400, () => {{
        setStatus(4, 'the agent starts a call');
        showBubble('agentBubble', '☎ dial(+34 ...)', 1800);
        pulseActor('actAgent', 1400);
      }});
      at(24400, () => emit('actAgent', 'modBackend', '#5dd1ff'));
      at(25000, () => activate('modBackend', 1000));
      at(25400, () => emit('modBackend', 'modAsterisk', '#a78bff'));
      at(26000, () => {{
        activate('modAsterisk', 2400);
        setStatus(4, 'asterisk: SIP INVITE');
      }});
      at(26500, () => emit('modAsterisk', 'modSip', '#a78bff'));
      at(26900, () => activate('modSip', 2000));
      at(27200, () => emit('modSip', 'actWorld', '#82e0a4'));
      at(28000, () => {{
        showBubble('worldBubble', '📞 ring ring…', 1400);
        pulseActor('actWorld', 1500);
      }});
      at(29200, () => {{
        setStatus(4, 'call in progress · bidirectional audio');
        const audioId = setInterval(() => {{
          emit('actAgent', 'actWorld', '#5dd1ff', {{ speed: 0.025, size: 2, tail: false }});
          emit('actWorld', 'actAgent', '#82e0a4', {{ speed: 0.025, size: 2, tail: false }});
        }}, 180);
        intervals.push(audioId);
        timeouts.push(setTimeout(() => clearInterval(audioId), 3200));
      }});
      at(32600, () => setStatus(4, 'call ended · 3.4s'));

      // ============== Finale ==============
      at(33800, () => {{
        document.getElementById('cinemaFinale').classList.add('show');
        setStatus(0, '<span class="act" style="background:rgba(74,222,128,0.12);color:#4ade80;">done</span>all under our stack');
      }});

      // ============== Loop ==============
      at(38500, () => {{
        if (visible) {{
          runStory();
        }} else {{
          const waitId = setInterval(() => {{
            if (visible) {{ clearInterval(waitId); runStory(); }}
          }}, 800);
          intervals.push(waitId);
        }}
      }});
    }}

    let visible = true;
    let started = false;
    new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        visible = e.isIntersecting;
        if (visible && !started && !reduced) {{
          started = true;
          runStory();
        }}
      }});
    }}, {{ threshold: 0.2 }}).observe(stage);

    setTimeout(() => {{
      if (!started && !reduced) {{
        started = true;
        runStory();
      }}
    }}, 400);

    if (reduced) {{
      setMeta('+34 600 549 832', true);
      ['modBackend','modKannel','modAsterisk','modNumbers','modSip'].forEach(id => {{
        document.getElementById(id).classList.add('active');
      }});
      setStatus(0, 'all stack active · animation disabled by system preference');
    }}

    document.getElementById('cinemaReplayBtn').addEventListener('click', () => {{
      if (reduced) return;
      runStory();
    }});
  }})();
</script>

</body>
</html>"""
