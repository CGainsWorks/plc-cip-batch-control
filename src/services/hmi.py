"""A small operator page served straight from the PLC image.

This is a demonstration HMI, not a SCADA product: one page, no build step, no
dependencies, polling a JSON endpoint that is rendered from the same Modbus
bridge a real client would read. Commands are written into the bridge's coil
queue, so pressing Start here goes through exactly the path a DCS write would.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from ..harness.cell import (
    ALARM_TAGS,
    COMMAND_TAGS,
    FAIL_NAMES,
    PHASE_NAMES,
    STEP_NAMES,
    UNIT_STATE_NAMES,
)
from .modbus_tcp import ModbusBridge

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CIP-01 skid</title>
<style>
 :root{color-scheme:dark;--bg:#11151c;--panel:#1a212b;--line:#2b3543;--ink:#e6edf5;
       --muted:#8b98a9;--ok:#3fa86a;--warn:#d8a13a;--bad:#d1503f;--live:#3f8fd1;}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);
      font:14px/1.45 ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif}
 header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;
        align-items:center;gap:18px;flex-wrap:wrap}
 h1{font-size:16px;margin:0;letter-spacing:.02em}
 .state{font-weight:600;padding:3px 10px;border-radius:4px;background:var(--panel);
        border:1px solid var(--line)}
 .wrap{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;padding:16px}
 .panel{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:14px}
 .panel h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
           margin:0 0 10px}
 table{width:100%;border-collapse:collapse}
 td{padding:3px 0;vertical-align:top}
 td.k{color:var(--muted)}
 td.v{text-align:right;font-variant-numeric:tabular-nums}
 .bar{height:8px;background:#0d1116;border-radius:4px;overflow:hidden;margin-top:4px}
 .bar span{display:block;height:100%;background:var(--live)}
 .valves{display:grid;grid-template-columns:repeat(2,1fr);gap:4px 12px}
 .valve{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--muted)}
 .led{width:9px;height:9px;border-radius:50%;background:#33404f;flex:none}
 .led.on{background:var(--ok);box-shadow:0 0 6px rgba(63,168,106,.7)}
 .alarm{display:flex;justify-content:space-between;gap:10px;padding:4px 0;
        border-bottom:1px solid var(--line)}
 .p1{color:var(--bad)}.p2{color:var(--warn)}.p3{color:var(--muted)}
 .none{color:var(--muted);font-style:italic}
 button{background:#243040;color:var(--ink);border:1px solid var(--line);border-radius:4px;
        padding:7px 13px;font:inherit;cursor:pointer}
 button:hover{background:#2c3a4d}
 .cmds{display:flex;gap:8px;flex-wrap:wrap}
 .verdict{font-weight:600}
 .released{color:var(--ok)}.held{color:var(--warn)}
</style></head><body>
<header>
  <h1>CIP-01 &middot; tank circuit A</h1>
  <span class="state" id="unit">--</span>
  <span class="state" id="phase">--</span>
  <span class="state" id="step">--</span>
  <span class="state" id="clock">--</span>
</header>
<div class="wrap">
  <div class="panel"><h2>Process</h2><table id="process"></table></div>
  <div class="panel"><h2>Phase</h2><table id="phase-detail"></table></div>
  <div class="panel"><h2>Valve matrix</h2><div class="valves" id="valves"></div></div>
  <div class="panel"><h2>Consumption this batch</h2><table id="kpi"></table></div>
  <div class="panel"><h2>Alarms</h2><div id="alarms"></div></div>
  <div class="panel"><h2>Commands</h2><div class="cmds" id="cmds"></div>
    <p style="color:var(--muted);font-size:12px;margin:12px 0 0">
      Each button writes one coil in the memory area. The controller clears it
      once it has taken the edge.</p>
  </div>
</div>
<script>
const COMMANDS = ["start","hold","restart","abort","reset","ack"];
const el = id => document.getElementById(id);
function row(k, v){ return `<tr><td class="k">${k}</td><td class="v">${v}</td></tr>`; }
function bar(k, v, pct){
  return row(k, v) + `<tr><td colspan="2"><div class="bar"><span style="width:${
    Math.max(0,Math.min(100,pct))}%"></span></div></td></tr>`;
}
async function poll(){
  let s;
  try { s = await (await fetch("api/state")).json(); }
  catch(e){ el("unit").textContent = "disconnected"; return; }
  el("unit").textContent = s.unit_state;
  el("phase").textContent = s.phase;
  el("step").textContent = s.step;
  el("clock").textContent = (s.cycle_time_s/60).toFixed(1) + " min";
  el("process").innerHTML =
      bar("Balance tank", s.level_pct.toFixed(1)+" %", s.level_pct)
    + row("Supply flow", s.flow_m3h.toFixed(1)+" m&sup3;/h")
    + row("Supply temperature", s.temp_supply_c.toFixed(1)+" &deg;C")
    + row("Return temperature", s.temp_return_c.toFixed(1)+" &deg;C")
    + row("Return conductivity", s.conductivity_return.toFixed(2)+" mS/cm")
    + row("Return minus supply", s.conductivity_delta.toFixed(3)+" mS/cm")
    + row("Strength", s.concentration_pct.toFixed(2)+" % w/w")
    + bar("Pump speed", s.pump_speed_pct.toFixed(0)+" %", s.pump_speed_pct)
    + bar("Steam valve", s.steam_valve_pct.toFixed(0)+" %", s.steam_valve_pct);
  el("phase-detail").innerHTML =
      row("Batch", s.batch_id)
    + row("Recipe", s.recipe)
    + row("Phase elapsed", s.phase_elapsed_s.toFixed(0)+" s")
    + row("Verified hold", s.hold_achieved_s.toFixed(0)+" s")
    + row("Fail code", s.fail_code)
    + row("Release", `<span class="verdict ${s.verified_clean?"released":"held"}">${
        s.verified_clean?"RELEASED":"HELD"}</span>`);
  el("valves").innerHTML = s.valves.map(v =>
    `<div class="valve"><span class="led ${v.on?"on":""}"></span>${v.tag}</div>`).join("");
  el("kpi").innerHTML =
      row("Fresh water", s.fresh_water_l.toFixed(0)+" L")
    + row("Recovered water", s.recovered_water_l.toFixed(0)+" L")
    + row("To drain", s.effluent_l.toFixed(0)+" L")
    + row("Caustic concentrate", s.caustic_l.toFixed(1)+" L")
    + row("Acid concentrate", s.acid_l.toFixed(1)+" L")
    + row("Steam energy", s.energy_kwh.toFixed(1)+" kWh");
  el("alarms").innerHTML = s.alarms.length
    ? s.alarms.map(a => `<div class="alarm p${a.priority}"><span>${a.tag}</span>
        <span>P${a.priority} ${a.acked?"ack":"UNACK"}</span></div>`).join("")
    : '<div class="none">no active alarms</div>';
}
el("cmds").innerHTML = COMMANDS.map(c =>
  `<button onclick="send('${c}')">${c[0].toUpperCase()+c.slice(1)}</button>`).join("");
async function send(name){
  await fetch("api/command", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify({command:name})});
  poll();
}
poll(); setInterval(poll, 500);
</script></body></html>
"""


class HmiState:
    """A snapshot the socket threads may read while the scan loop runs."""

    def __init__(self, bridge: ModbusBridge, recipe_name: str = ""):
        self.bridge = bridge
        self.recipe_name = recipe_name
        self.lock = threading.Lock()
        self.snapshot: Dict[str, Any] = {}
        self.refresh()

    def refresh(self) -> None:
        rt = self.bridge.runtime
        valves = [
            ("V01 fresh water", "XV01FRESHWATER"),
            ("V02 recovered water", "XV02RECOVEREDWATER"),
            ("V03 caustic dose", "XV03CAUSTICDOSE"),
            ("V04 acid dose", "XV04ACIDDOSE"),
            ("V05 supply to circuit", "XV05SUPPLYTOCIRCUIT"),
            ("V06 return to drain", "XV06RETURNTODRAIN"),
            ("V07 return to balance", "XV07RETURNTOBALANCE"),
            ("V08 return to caustic", "XV08RETURNTOCAUSTIC"),
            ("V09 return to recovery", "XV09RETURNTORECOVERY"),
            ("V10 return to acid", "XV10RETURNTOACID"),
            ("V11 caustic supply", "XV11CAUSTICSUPPLY"),
            ("V12 acid supply", "XV12ACIDSUPPLY"),
            ("Supply pump", "XSUPPLYPUMPRUN"),
            ("Return pump", "XRETURNPUMPRUN"),
            ("Steam isolation", "XSTEAMISOLATION"),
        ]
        alarms = []
        for index in range(1, len(ALARM_TAGS) + 1):
            base = "gAlarms[{0}].".format(index)
            if not bool(rt.get_path(base + "xLatched")):
                continue
            alarms.append(
                {
                    "tag": ALARM_TAGS[index],
                    "priority": int(rt.get_path(base + "iPriority")),
                    "acked": bool(rt.get_path(base + "xAcked")),
                }
            )
        snapshot = {
            "unit_state": UNIT_STATE_NAMES[int(rt.get_path("iHmiUnitState"))],
            "phase": PHASE_NAMES[int(rt.get_path("iHmiPhase"))],
            "step": STEP_NAMES[int(rt.get_path("iHmiPhaseStep"))],
            "fail_code": FAIL_NAMES[int(rt.get_path("iHmiFailCode"))],
            "batch_id": int(rt.get_path("iHmiBatchId")),
            "recipe": self.recipe_name,
            "level_pct": float(rt.get_path("rLevelBalanceTankPct")),
            "flow_m3h": float(rt.get_path("rFlowSupplyM3h")),
            "temp_supply_c": float(rt.get_path("rTempSupplyC")),
            "temp_return_c": float(rt.get_path("rTempReturnC")),
            "conductivity_return": float(rt.get_path("rConductivityReturn")),
            "conductivity_delta": float(rt.get_path("grDeltaConductivity")),
            "concentration_pct": float(rt.get_path("rHmiConcentrationPct")),
            "pump_speed_pct": float(rt.get_path("rSupplyPumpSpeedPct")),
            "steam_valve_pct": float(rt.get_path("rSteamValvePct")),
            "cycle_time_s": float(rt.get_path("rHmiCycleTimeS")),
            "phase_elapsed_s": float(rt.get_path("rHmiPhaseElapsedS")),
            "hold_achieved_s": float(rt.get_path("rHmiHoldAchievedS")),
            "fresh_water_l": float(rt.get_path("rHmiFreshWaterL")),
            "recovered_water_l": float(rt.get_path("rHmiRecoveredWaterL")),
            "effluent_l": float(rt.get_path("rHmiEffluentL")),
            "caustic_l": float(rt.get_path("rHmiCausticL")),
            "acid_l": float(rt.get_path("rHmiAcidL")),
            "energy_kwh": float(rt.get_path("rHmiEnergyKWh")),
            "verified_clean": bool(rt.get_path("xHmiVerifiedClean")),
            "valves": [{"tag": label, "on": bool(rt.get_path(tag))} for label, tag in valves],
            "alarms": alarms,
        }
        with self.lock:
            self.snapshot = snapshot

    def read(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.snapshot)

    def command(self, name: str) -> bool:
        tag = COMMAND_TAGS.get(name)
        if tag is None:
            return False
        point = self.bridge.io_map.find(tag)
        return self.bridge.queue_write(point.table, point.offset, 1)


class _Handler(BaseHTTPRequestHandler):
    server_version = "cip-hmi/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console clean
        return

    @property
    def state(self) -> HmiState:
        return self.server.state  # type: ignore[attr-defined]

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            body = json.dumps(self.state.read()).encode("utf-8")
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0].rstrip("/") != "/api/command":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            self._send(400, b'{"error":"bad json"}', "application/json")
            return
        accepted = self.state.command(str(payload.get("command", "")))
        self._send(
            200 if accepted else 400,
            json.dumps({"accepted": accepted}).encode("utf-8"),
            "application/json",
        )


class HmiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, state: HmiState, host: str = "127.0.0.1", port: int = 8080):
        self.state = state
        super().__init__((host, port), _Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def serve_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="cip-hmi", daemon=True)
        thread.start()
        return thread
