// Consumption-modes explorer, used by docs/how-to/index.md.
// Loaded site-wide via extra_javascript; it returns immediately on any page
// that does not contain the architecture canvas.
(function(){
  "use strict";

  var CHIPS = {
    harness: { cls:"harness", label:"Harness", group:null },
    mas:     { cls:"mas",     label:"MAS",     group:["agents-own","agents-3p"] },
    skills:  { cls:"skills",  label:"Protocol skills", group:["protocols"] },
    bridge:  { cls:"bridge",  label:"AgentBridge", group:["bridges"] },
    oxp:     { cls:"oxp",     label:"SCOPE",     group:["scope"] },
    pe:      { cls:"detect",  label:"Platform Engine", group:["pe","telemetry","trigger"] },
    lab:     { cls:"lab",     label:"MAS-Lab", group:["maslab"] }
  };

  // Mode 1 is a choice of placement, not an addition: the same Cognition Protocol
  // skills live either in the agents or in the AgentBridge. option.lit is therefore
  // the complete lit set for the alternative placement, replacing lit rather than
  // extending it.
  var MODES = [
    {
      n:1, lab:false,
      chips:["harness","mas","skills"],
      title:"Cognition Protocol skills",
      tag:"The ACCORD and CONCORD skills are added to the agents — a natural-language protocol definition supplied at session start, per agent, with no recompile and no harness change.",
      get:"Coordination protocol inside the agent. A MAS can be converted one agent at a time.",
      cost:"Edit each agent — its prompt or skill directory.",
      pro:"Lowest possible adoption cost. Nothing but the agent’s own instruction surface changes.",
      con:"No observability: nothing measures whether the protocol ran or helped. Invocation is an LLM decision, so determinism on protocol mechanics is weak.",
      gate:"none",
      lit:["agents-own","protocols","band"],
      reading:"Agents carrying the protocol skill. Nothing else in the fabric is touched.",
      placementLabel:"Where the skills live",
      placementA:"In the agents",
      placementB:"In the AgentBridge",
      option:{
        label:"In the AgentBridge",
        tag:"Agents that cannot be modified get the same skills through a declared A2A/SLIM endpoint, deployed per agent or per harness, which carries the protocol machinery on the wire instead of inside the agent.",
        get:"The same coordination on agents you do not own, including services behind the Agent Gateway.",
        cost:"A2A on the agent, plus one bridge per agent or per harness.",
        pro:"No modification to agent code, and the bridge owns the deterministic part — envelope, sequence, phase barriers, merge, commit — while the agent only generates payloads.",
        con:"A2A is required and the bridge is an extra deployed component.",
        gate:"A2A",
        lit:["agents-own","agents-3p","protocols","band","bridges","gateway"],
        reading:"Bridges on the A2A/SLIM mesh, reaching third-party agents through the gateway."
      }
    },
    {
      n:2, lab:false,
      chips:["harness","mas","skills","oxp","pe"],
      title:"Cognition Protocols plus observability, detection and trigger",
      tag:"OpenTelemetry instrumentation feeds SCOPE, which recomposes the per-agent spans into one multi-agent trajectory. The Platform Engine reads that trajectory, names the cognitive failure by function and locus, and fires the protocol response.",
      get:"Observability of the agentic system as a whole, the cognitive-failure detection that latency and error-rate monitoring structurally cannot see, and the closed loop — detect, attribute, trigger, re-align.",
      cost:"OTel instrumentation on the agents — any semantic-convention-compliant emitter, through a SCOPE schema adapter.",
      pro:"Turns a claim of benefit into a measurable before and after, attributed to the originating event, and acts on what it finds while the session is still running.",
      con:"Requires observability instrumentation, and the engine has to be deployed alongside SCOPE.",
      gate:"OTel instrumentation",
      lit:["agents-own","protocols","band","fabric","scope","pe","telemetry","trigger"],
      reading:"Telemetry rising into SCOPE and the Platform Engine, and the trigger returning to the protocol layer."
    },
    {
      n:3, lab:true,
      chips:["mas","lab","skills","oxp","pe"],
      title:"MAS-Lab",
      tag:"The question changes from how to run this in production to how to prove it. The MAS is declared in YAML and executed under mas-runtime, with contracts enforced at the boundaries and the existing Labs reproduced against it.",
      get:"Reproducible labs, benchmarks and automated coverage — quantitative before and after on your own system rather than a reference scenario.",
      cost:"The MAS runs under mas-runtime instead of its own harness.",
      pro:"Extensive automated coverage and testing; integration regressions caught rather than discovered.",
      con:"The MAS has to be expressible as a MAS-Lab specification. This is a lab posture, not a production one — note the harness drops out entirely.",
      gate:"OTel + a MAS-Lab specification",
      lit:["agents-own","protocols","band","agents-3p","bridges","gateway","fabric","scope","pe","telemetry","trigger","maslab"],
      reading:"The whole agent tier runs inside the MAS-Lab runtime; the harness is gone."
    }
  ];

  var CONTEXT = ["engines","peer"];

  var svg = document.getElementById("ioc-arch");
  if (!svg) return;   // every other page on the site
  var groups = {};
  Array.prototype.forEach.call(svg.querySelectorAll("g[data-group]"), function(g){
    groups[g.getAttribute("data-group")] = g;
  });

  var dimOn = true;
  var current = 1;
  var optionOn = false;   // mode 1: skills deployed in the AgentBridge

  function buildRail(){
    MODES.forEach(function(m){
      var li = document.createElement("li");
      var b = document.createElement("button");
      b.className = "ioc-mode";
      b.type = "button";
      b.setAttribute("aria-pressed", String(m.n === current));
      b.setAttribute("data-mode", String(m.n));
      b.setAttribute("aria-label", "Mode " + m.n + ": " + m.title);

      var num = document.createElement("span");
      num.className = "n";
      num.textContent = String(m.n);
      b.appendChild(num);

      var stack = document.createElement("span");
      stack.className = "ioc-stack";
      if (m.lab) {
        var sp = document.createElement("span");
        sp.className = "ioc-chip spacer";
        sp.setAttribute("aria-hidden", "true");
        stack.appendChild(sp);
      }
      m.chips.forEach(function(key){
        var c = CHIPS[key];
        var el = document.createElement("span");
        el.className = "ioc-chip " + c.cls;
        el.textContent = c.label;
        el.setAttribute("data-chip", key);
        stack.appendChild(el);
      });
      b.appendChild(stack);

      b.addEventListener("click", function(){ select(m.n); });
      li.appendChild(b);
      document.getElementById(m.lab ? "ioc-modes-lab" : "ioc-modes-prod").appendChild(li);

      if (m.option) {
        var optLi = document.createElement("li");
        optLi.className = "ioc-option";

        var cap = document.createElement("span");
        cap.className = "ioc-option-cap";
        cap.textContent = m.placementLabel;
        optLi.appendChild(cap);

        [[m.placementA, false], [m.placementB, true]].forEach(function(pair, i){
          var lblEl = document.createElement("label");
          var rb = document.createElement("input");
          rb.type = "radio";
          rb.name = "ioc-placement";
          rb.checked = (pair[1] === optionOn);
          if (i === 1) rb.id = "ioc-opt-bridge";
          rb.addEventListener("change", function(){
            optionOn = pair[1];
            if (current !== m.n) select(m.n); else paint();
          });
          lblEl.appendChild(rb);
          lblEl.appendChild(document.createTextNode(" " + pair[0]));
          optLi.appendChild(lblEl);
        });

        document.getElementById("ioc-modes-prod").appendChild(optLi);
      }
    });
  }

  // The selected mode, with its option folded in when the option applies and is on.
  function activeMode(){
    var m = MODES.filter(function(x){ return x.n === current; })[0];
    if (!m.option || !optionOn) return m;
    var o = m.option, merged = {};
    Object.keys(m).forEach(function(k){ merged[k] = m[k]; });
    merged.title = m.title + " — in the AgentBridge";
    ["tag","get","cost","pro","con","gate","reading"].forEach(function(k){
      if (o[k]) merged[k] = o[k];
    });
    merged.lit = o.lit;
    return merged;
  }

  function paint(){
    var m = activeMode();
    Object.keys(groups).forEach(function(name){
      var g = groups[name];
      g.classList.remove("off", "ctx", "lit");
      if (!dimOn) { g.classList.add("lit"); return; }
      if (CONTEXT.indexOf(name) > -1) { g.classList.add("ctx"); return; }
      g.classList.add(m.lit.indexOf(name) > -1 ? "lit" : "off");
    });

    document.getElementById("ioc-d-title").textContent = "Mode " + m.n + " — " + m.title;
    document.getElementById("ioc-d-tag").textContent = m.tag;
    document.getElementById("ioc-d-get").textContent = m.get;
    document.getElementById("ioc-d-cost").textContent = m.cost;
    document.getElementById("ioc-d-pro").textContent = m.pro;
    document.getElementById("ioc-d-con").textContent = m.con;
    document.getElementById("ioc-d-gate").textContent = m.gate;
    document.getElementById("ioc-inplay").textContent = m.reading;

    Array.prototype.forEach.call(document.querySelectorAll(".ioc-mode"), function(b){
      b.setAttribute("aria-pressed", String(Number(b.getAttribute("data-mode")) === current));
    });

    var optWrap = document.querySelector(".ioc-option");
    if (optWrap) {
      var owner = MODES.filter(function(x){ return !!x.option; })[0];
      optWrap.classList.toggle("muted", current !== owner.n);
    }
  }

  function select(n){ current = n; paint(); }

  function clearPulse(){
    Object.keys(groups).forEach(function(k){ groups[k].classList.remove("pulse"); });
  }

  document.addEventListener("mouseover", function(e){
    var chip = e.target.closest ? e.target.closest("[data-chip]") : null;
    if (!chip) return;
    clearPulse();
    var names = CHIPS[chip.getAttribute("data-chip")].group;
    if (!names) return;
    names.forEach(function(nm){ if (groups[nm]) groups[nm].classList.add("pulse"); });
  });
  document.addEventListener("mouseout", function(e){
    if (e.target.closest && e.target.closest("[data-chip]")) clearPulse();
  });

  document.getElementById("ioc-dim").addEventListener("click", function(){
    dimOn = !dimOn;
    this.setAttribute("aria-pressed", String(dimOn));
    this.textContent = dimOn ? "dim inactive" : "show all";
    paint();
  });

  document.addEventListener("keydown", function(e){
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      if (document.activeElement && document.activeElement.classList.contains("ioc-mode")) {
        e.preventDefault(); select(current === 5 ? 1 : current + 1);
        var nxt = document.querySelector('.ioc-mode[data-mode="' + current + '"]');
        if (nxt) nxt.focus();
      }
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      if (document.activeElement && document.activeElement.classList.contains("ioc-mode")) {
        e.preventDefault(); select(current === 1 ? 5 : current - 1);
        var prv = document.querySelector('.ioc-mode[data-mode="' + current + '"]');
        if (prv) prv.focus();
      }
    }
  });

  buildRail();
  paint();
})();
