"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "ui", "index.html"), "utf8");

function slice(from, to) {
  const start = html.indexOf(from);
  assert.notEqual(start, -1, `ui/index.html no longer contains ${from}`);
  const end = html.indexOf(to, start);
  assert.notEqual(end, -1, `ui/index.html no longer contains ${to}`);
  return html.slice(start, end);
}

class FakeClassList {
  constructor(node) { this.node = node; }
  values() { return new Set(this.node.className.split(/\s+/).filter(Boolean)); }
  write(values) { this.node.className = [...values].join(" "); }
  add(...tokens) { const values = this.values(); tokens.forEach(t => values.add(t)); this.write(values); }
  remove(...tokens) { const values = this.values(); tokens.forEach(t => values.delete(t)); this.write(values); }
  toggle(token, force) {
    const values = this.values();
    const enabled = force === undefined ? !values.has(token) : Boolean(force);
    if (enabled) values.add(token); else values.delete(token);
    this.write(values); return enabled;
  }
}

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.className = "";
    this.children = [];
    this.dataset = {};
    this.textContent = "";
    this.innerHTML = "";
    this.parentElement = null;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 100;
    this.classList = new FakeClassList(this);
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parentElement = this;
      this.children.push(node);
    }
    this.scrollHeight = this.children.length * 100;
  }
  addEventListener() {}
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return {top: 0, bottom: 0}; }
  querySelector(selector) {
    if (selector === ".body") return this.children.find(n => n.classList.values().has("body"));
    if (selector === ".who span") {
      const who = this.children.find(n => n.classList.values().has("who"));
      return who && who.children[0];
    }
    return null;
  }
}

function harness() {
  const logEl = new FakeElement("div");
  const historyEl = new FakeElement("div");
  const context = {
    console,
    document: {createElement: tag => new FakeElement(tag)},
    logEl,
    liveEl: logEl,
    historyEl,
    logTarget: logEl,
    itemEls: {},
    historyRows: [], historyRange: {start: 0, end: 0}, historyWarning: "",
    historyShiftFrame: 0, historyFindQuery: "", historyFindIndex: -1,
    HISTORY_WINDOW: 240, HISTORY_CHUNK: 80,
    requestAnimationFrame: () => 1,
    fmtTs: () => "",
    renderMd(el) { el.classList.add("md"); },
    UIContracts: {
      nearTail: () => false,
      mutatePreservingTail(_pane, mutate) { return mutate(); },
      virtualRange: () => ({start: 0, end: 0}),
    },
    $: () => ({disabled: false}),
    $$: () => [],
    cur: "thread-a",
  };
  vm.createContext(context);
  vm.runInContext(slice("function add(cls,who,text,ts){",
                        "/* ---------- protocol pane ---------- */"), context);
  return context;
}

function bodyFor(context, itemId) {
  return context.itemEls[itemId].querySelector(".body");
}

test("interleaved item deltas retain protocol start order and identity", () => {
  const ui = harness();
  ui.renderItem({id: "agent-a", type: "agentMessage"}, null, {reserve: true});
  ui.renderItem({id: "command-b", type: "commandExecution", command: "echo ok"},
                null, {reserve: true});
  ui.appendItemDelta("agent-a", {delta: "A1"});
  ui.renderItem({id: "command-b", type: "commandExecution", command: "echo ok",
                 aggregatedOutput: "ok", status: "completed"});
  ui.appendItemDelta("agent-a", {delta: "A2"});
  ui.renderItem({id: "agent-a", type: "agentMessage", content: []},
                null, {preserve: true});

  assert.deepEqual(ui.logEl.children.map(node => node.dataset.itemId),
                   ["agent-a", "command-b"]);
  assert.equal(bodyFor(ui, "agent-a").textContent, "A1A2");
  assert.match(bodyFor(ui, "command-b").textContent, /ok/);
});

test("two simultaneous streams never merge into one message", () => {
  const ui = harness();
  ui.renderItem({id: "a", type: "agentMessage"}, null, {reserve: true});
  ui.renderItem({id: "b", type: "agentMessage"}, null, {reserve: true});
  ui.appendItemDelta("a", {delta: "alpha"});
  ui.appendItemDelta("b", {delta: "beta"});
  ui.renderItem({id: "a", type: "agentMessage", content: []},
                null, {preserve: true});
  ui.renderItem({id: "b", type: "agentMessage", content: []},
                null, {preserve: true});

  assert.equal(bodyFor(ui, "a").textContent, "alpha");
  assert.equal(bodyFor(ui, "b").textContent, "beta");
});

test("non-empty completion is authoritative over streamed draft text", () => {
  const ui = harness();
  ui.renderItem({id: "a", type: "agentMessage"}, null, {reserve: true});
  ui.appendItemDelta("a", {delta: "partial"});
  ui.renderItem({id: "a", type: "agentMessage", text: "complete"},
                null, {preserve: true});
  assert.equal(bodyFor(ui, "a").textContent, "complete");
});

test("an empty completion without any deltas stays invisible", () => {
  const ui = harness();
  ui.renderItem({id: "a", type: "agentMessage"}, null, {reserve: true});
  ui.renderItem({id: "a", type: "agentMessage", content: []},
                null, {preserve: true});
  assert.equal(ui.itemEls.a.classList.values().has("pending"), true);
});
