"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "ui", "index.html"), "utf8");
const match = html.match(
  /\/\* WHEELHOUSE_UI_CONTRACTS_START[\s\S]*?\*\/([\s\S]*?)\/\* WHEELHOUSE_UI_CONTRACTS_END \*\//);
assert.ok(match, "UI contract block must remain extractable");
const context = {};
vm.createContext(context);
vm.runInContext(match[1] + ";globalThis.UIContracts=UIContracts;", context);
const contracts = context.UIContracts;

test("turn/start includes every selected override", () => {
  const payload = contracts.turnStart({
    threadId: "thread-1",
    text: "continue",
    model: "gpt-test",
    effort: "high",
    approvalPolicy: "on-request",
    serviceTier: "priority",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    threadId: "thread-1",
    input: [{type: "text", text: "continue"}],
    model: "gpt-test",
    effort: "high",
    approvalPolicy: "on-request",
    serviceTier: "priority",
  });
});

test("turn/start omits blank overrides instead of clearing sticky values", () => {
  const payload = contracts.turnStart({
    threadId: "thread-1", text: "continue", model: "", effort: "",
    approvalPolicy: "", serviceTier: "",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    threadId: "thread-1",
    input: [{type: "text", text: "continue"}],
  });
});

test("thread/start maps modal controls to the protocol shape", () => {
  const payload = contracts.threadStart({
    cwd: "  /work/project  ", defaultCwd: "/fallback",
    sandbox: "workspace-write", approvalPolicy: "untrusted",
    model: "gpt-test", effort: "xhigh",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    cwd: "/work/project",
    sandbox: "workspace-write",
    approvalPolicy: "untrusted",
    threadSource: "user",
    model: "gpt-test",
    config: {model_reasoning_effort: "xhigh"},
  });
});

test("effective metadata refresh is bounded and ignores a departed thread", () => {
  const scheduled = [];
  const loaded = [];
  let current = true;
  contracts.refreshMeta({
    threadId: "thread-1",
    isCurrent: () => current,
    load: id => loaded.push(id),
    schedule: (fn, delay) => scheduled.push({fn, delay}),
  });
  assert.deepEqual(
    scheduled.map(item => item.delay),
    Array.from(contracts.metaRefreshDelays),
  );
  scheduled[0].fn();
  current = false;
  scheduled[1].fn();
  scheduled[2].fn();
  assert.deepEqual(loaded, ["thread-1"]);
});
