"use strict";

/* Behaviour tests for the per-thread settings controls at the bottom of the
   window (#selModel / #selEffort / #selTier / #selAppr) and the resolved-value
   chips they mirror.

   These run the REAL functions out of ui/index.html -- open_(), loadMeta(),
   effortOptions() and tierOptions() are sliced verbatim out of the page and
   evaluated against a minimal <select>-faithful DOM. Nothing here re-implements
   the behaviour under test; if the page stops containing these functions the
   slice assertions fail loudly rather than testing a copy that has drifted. */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "ui", "index.html"), "utf8");

/* ---------- slice the real implementation out of the page ---------- */

function slice(from, to) {
  const start = html.indexOf(from);
  assert.notEqual(start, -1, `ui/index.html no longer contains ${from}`);
  const end = html.indexOf(to, start);
  assert.notEqual(end, -1, `ui/index.html no longer contains ${to}`);
  return html.slice(start, end + to.length);
}

const contractBlock = html.match(
  /\/\* WHEELHOUSE_UI_CONTRACTS_START[\s\S]*?\*\/([\s\S]*?)\/\* WHEELHOUSE_UI_CONTRACTS_END \*\//);
assert.ok(contractBlock, "UI contract block must remain extractable");
const CONTRACTS = contractBlock[1];

// modelById / effortOptions / tierOptions / defaultModel
const MODEL_CAPABILITIES = slice("function modelById(id){", 'let defaultModel="";');
// fmtSandbox / loadMeta / curMeta / renderedMetaFingerprint
const EFFECTIVE_SETTINGS = slice("function fmtSandbox(j){", 'let renderedMetaFingerprint="";');
// openGeneration / open_
const OPEN_THREAD = slice("let openGeneration=0;", "let renderedFromRollout=0;");

function selectMarkup(id) {
  const match = html.match(
    new RegExp(`<select[^>]*id="${id}"[^>]*>([\\s\\S]*?)</select>`));
  assert.ok(match, `ui/index.html has no <select id="${id}">`);
  return match[1];
}

const APPROVAL_OPTIONS = selectMarkup("selAppr");

/* ---------- a <select>-faithful DOM, small enough to read ---------- */

const OPTION = /<option value="([^"]*)"[^>]*>([\s\S]*?)<\/option>/g;

class FakeSelect {
  constructor(markup) {
    this._options = [];
    this._value = "";
    this.style = {};
    this.disabled = false;
    if (markup !== undefined) this.innerHTML = markup;
  }
  set innerHTML(markup) {
    this._options = [];
    for (const m of String(markup).matchAll(OPTION))
      this._options.push({value: m[1], textContent: m[2]});
    // A <select> selects its first option when the option list is replaced.
    this._value = this._options.length ? this._options[0].value : "";
  }
  get options() { return this._options; }
  get value() { return this._value; }
  set value(next) {
    // HTML select semantics: an unknown value deselects everything.
    this._value = this._options.some(o => o.value === next) ? next : "";
  }
  // What the user actually reads off the closed control.
  get displayed() {
    const chosen = this._options.find(o => o.value === this._value);
    return chosen ? chosen.textContent : "";
  }
}

class FakeChip {
  constructor() {
    this.style = {};
    this.textContent = "";
    this.title = "";
  }
}

const MODELS = [
  {id: "model-x", displayName: "Model X", defaultReasoningEffort: "high",
   supportedReasoningEfforts: [{reasoningEffort: "low"},
                               {reasoningEffort: "high"},
                               {reasoningEffort: "xhigh"}],
   serviceTiers: [{id: "priority", name: "priority"}]},
  {id: "model-y", displayName: "Model Y", defaultReasoningEffort: "low",
   supportedReasoningEfforts: [{reasoningEffort: "low"},
                               {reasoningEffort: "high"},
                               {reasoningEffort: "xhigh"}],
   serviceTiers: [{id: "priority", name: "priority"}]},
];

const CATALOG_OPTIONS = MODELS
  .map(m => `<option value="${m.id}">${m.displayName}</option>`).join("");

function sandboxFor(name) {
  return JSON.stringify({
    type: "workspaceWrite", network: name,
    file_system: {type: "restricted", entries: [{access: "write"}]},
  });
}

/* ---------- harness ---------- */

function harness(threadMeta) {
  const elements = {
    // The bottom bar: the per-thread settings controls under test. Their
    // option lists are built at boot exactly as ui/index.html builds them.
    "#selModel": new FakeSelect(
      `<option value="">model: default</option>` + CATALOG_OPTIONS),
    "#selEffort": new FakeSelect(`<option value="">effort: default</option>`),
    "#selTier": new FakeSelect(`<option value="">tier: default</option>`),
    "#selAppr": new FakeSelect(APPROVAL_OPTIONS),
    // The creation modal's controls, which loadMeta also relabels.
    "#mModel": new FakeSelect(
      `<option value="">default</option>` + CATALOG_OPTIONS),
    "#mEffort": new FakeSelect(`<option value="">default</option>`),
    "#mSbx": new FakeSelect(
      `<option value="workspace-write">workspace-write</option>` +
      `<option value="read-only">read-only</option>`),
  };
  const chipFor = id => (elements[id] ||= new FakeChip());

  const fetched = [];
  const sandbox = {
    console,
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    models: MODELS,
    cur: null, curInfo: {}, curTurn: null,
    streamEl: null, reasonEl: null, threadViewReady: false,
    reviewerCache: {}, nameCache: {}, cwdCache: {},
    logEl: {innerHTML: "", scrollTop: 0, scrollHeight: 0},
    $: sel => (sel.startsWith("#sel") || sel.startsWith("#m")
               ? elements[sel] : chipFor(sel)) || null,
    $$: sel => {
      assert.equal(sel, "#ctl select",
        `unstubbed $$ selector ${sel}`);
      return ["#selModel", "#selEffort", "#selTier", "#selAppr"]
        .map(id => elements[id]);
    },
    rememberThreadId() {},
    setBusy() {},
    renderList() {},
    loadAgents() {},
    reconcile() {},
    renderTranscript() {},
    showCwd() {},
    async rpc(method, params) {
      if (method === "thread/resume")
        return {result: {thread: {id: params.threadId, name: params.threadId,
                                  cwd: "/work", status: {type: "idle"}}}};
      if (method === "thread/read")
        return {result: {thread: {status: {type: "idle"}}}};
      return {result: {}};
    },
    async fetch(url) {
      fetched.push(url);
      if (url.startsWith("/transcript"))
        return {ok: true, json: async () => ({rows: []})};
      const id = decodeURIComponent(url.split("id=")[1]);
      const meta = threadMeta[id];
      if (meta === undefined) return {ok: false, status: 404};
      return {ok: true, json: async () => meta};
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(
    CONTRACTS + "\n" + MODEL_CAPABILITIES + "\n" + EFFECTIVE_SETTINGS + "\n" +
    OPEN_THREAD, sandbox);

  return {
    elements, fetched, sandbox,
    // open_ fires loadMeta without awaiting it; drain the microtask queue.
    async select(id) {
      await sandbox.open_(id);
      for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r));
    },
    reads(sel) { return elements[sel].displayed; },
  };
}

const THREADS = {
  "thread-a": {model: "model-x", reasoning_effort: "high",
               approval_mode: "never", sandbox_policy: sandboxFor("off"),
               cwd: "/work/a", tokens_used: 100},
  "thread-b": {model: "model-y", reasoning_effort: "low",
               approval_mode: "on-request", sandbox_policy: sandboxFor("on"),
               cwd: "/work/b", tokens_used: 200},
};

/* ---------- Scenario 1 ----------------------------------------------------
   Given thread A has model=X, effort=high, approvals=never
     And thread B has model=Y, effort=low,  approvals=on-request
    When the user selects thread A, then selects thread B
    Then the bottom controls read Y / low / on-request
   ------------------------------------------------------------------------ */

test("switching threads re-reads the newly selected thread's settings", async () => {
  const ui = harness(THREADS);

  await ui.select("thread-a");
  assert.match(ui.reads("#selModel"), /model-x/,
    "thread A's model must be shown while thread A is selected");
  assert.match(ui.reads("#selEffort"), /high/);
  assert.match(ui.reads("#selAppr"), /never/);

  await ui.select("thread-b");
  assert.match(ui.reads("#selModel"), /model-y/,
    "the model control must read thread B, not thread A");
  assert.match(ui.reads("#selEffort"), /low/,
    "the effort control must read thread B, not thread A");
  assert.match(ui.reads("#selAppr"), /on-request/,
    "the approvals control must read thread B, not thread A");
  assert.equal(ui.elements["#cSbx"].title, sandboxFor("on"),
    "the sandbox chip must read thread B, not thread A");

  assert.ok(ui.fetched.includes("/threadmeta?id=thread-b"),
    "selecting a thread must fetch that thread's effective settings");
});

test("switching threads reads the new thread even after a next-turn override",
  async () => {
    const ui = harness(THREADS);

    await ui.select("thread-a");
    // The user picks explicit values for thread A's next turn.
    ui.elements["#selModel"].value = "model-x";
    ui.elements["#selEffort"].value = "xhigh";
    ui.elements["#selAppr"].value = "never";
    ui.elements["#selTier"].value = "priority";
    assert.equal(ui.elements["#selEffort"].value, "xhigh",
      "the override must stick while thread A is selected");

    await ui.select("thread-b");
    assert.match(ui.reads("#selModel"), /model-y/,
      "thread A's model override must not carry into thread B");
    assert.match(ui.reads("#selEffort"), /low/,
      "thread A's effort override must not carry into thread B");
    assert.match(ui.reads("#selAppr"), /on-request/,
      "thread A's approvals override must not carry into thread B");
    assert.equal(ui.elements["#selTier"].value, "",
      "thread A's service tier override must not carry into thread B");
  });

/* ---------- Scenario 2 ----------------------------------------------------
   Given a thread whose settings were changed in the UI
    When the user switches away and back
    Then the controls read the CHANGED values, not the creation-time values
   ------------------------------------------------------------------------ */

test("returning to a thread reads its changed settings, not its creation-time ones",
  async () => {
    const meta = JSON.parse(JSON.stringify(THREADS));
    const ui = harness(meta);

    await ui.select("thread-a");
    assert.match(ui.reads("#selEffort"), /high/);
    assert.match(ui.reads("#selAppr"), /never/);

    // The thread's own settings change (an approval-routing change, or a turn
    // that ran with an override and persisted it).
    meta["thread-a"] = {...meta["thread-a"], reasoning_effort: "xhigh",
                        approval_mode: "untrusted"};

    await ui.select("thread-b");
    await ui.select("thread-a");
    assert.match(ui.reads("#selEffort"), /xhigh/,
      "the effort control must read the changed value");
    assert.match(ui.reads("#selAppr"), /untrusted/,
      "the approvals control must read the changed value");
  });

/* ---------- Scenario 3 ----------------------------------------------------
   Given a newly created thread
    When it becomes the active thread
    Then the controls reflect that thread's actual settings, not the modal
         defaults used to create it
   ------------------------------------------------------------------------ */

test("a newly created thread shows its own settings, not the creating modal's",
  async () => {
    const meta = {...THREADS,
      // Created from the modal with model-y / low, whatever the modal's
      // controls were left on.
      "thread-new": {model: "model-y", reasoning_effort: "low",
                     approval_mode: "on-request",
                     sandbox_policy: sandboxFor("off"), cwd: "/work/new"}};
    const ui = harness(meta);

    await ui.select("thread-a");
    // The creation modal is left on other values entirely.
    ui.elements["#mModel"].value = "model-x";
    ui.elements["#mSbx"].value = "read-only";

    await ui.select("thread-new");
    assert.match(ui.reads("#selModel"), /model-y/);
    assert.match(ui.reads("#selEffort"), /low/);
    assert.match(ui.reads("#selAppr"), /on-request/);
  });

test("a thread whose settings are not readable yet shows no other thread's values",
  async () => {
    // The state DB trails thread/start by milliseconds, so /threadmeta for a
    // brand-new thread legitimately comes back empty (bridge.py _threadmeta
    // returns {} when the row is not there yet).
    const ui = harness({...THREADS, "thread-new": {}});

    await ui.select("thread-a");
    assert.match(ui.reads("#selEffort"), /high/);

    await ui.select("thread-new");
    assert.doesNotMatch(ui.reads("#selModel"), /model-x/,
      "an unread thread must not inherit the previous thread's model");
    assert.doesNotMatch(ui.reads("#selEffort"), /high/,
      "an unread thread must not inherit the previous thread's effort");
    assert.doesNotMatch(ui.reads("#selAppr"), /never/,
      "an unread thread must not inherit the previous thread's approvals");
  });
