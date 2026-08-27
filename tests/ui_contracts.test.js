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

test("approval vocabularies are exact and do not swallow permission prompts", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(
    contracts.approvalVocab("execCommandApproval"))), {
    yes: "approved", always: "approved_for_session", no: "abort",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(
    contracts.approvalVocab("item/fileChange/requestApproval"))), {
    yes: "accept", always: "acceptForSession", no: "decline",
  });
  assert.equal(
    contracts.approvalVocab("item/permissions/requestApproval"), null);
});

test("permission decisions always satisfy the required permissions shape", () => {
  const requested = {permissions: {network: {enabled: true}}};
  assert.deepEqual(JSON.parse(JSON.stringify(
    contracts.permissionResponse(requested, true))), {
    permissions: {network: {enabled: true}},
    scope: "turn",
    strictAutoReview: false,
  });
  assert.deepEqual(JSON.parse(JSON.stringify(
    contracts.permissionResponse(requested, false))), {
    permissions: {},
    scope: "turn",
    strictAutoReview: true,
  });
});

test("user answers and MCP elicitation actions use protocol response shapes", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(contracts.userInputResponse([
    ["scope", ["UI only"]], ["risk", ["low"]],
  ]))), {
    answers: {
      scope: {answers: ["UI only"]},
      risk: {answers: ["low"]},
    },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(
    contracts.elicitationResponse("accept", {email: "a@example.com"}))), {
    action: "accept", content: {email: "a@example.com"},
  });
  assert.deepEqual(JSON.parse(JSON.stringify(
    contracts.elicitationResponse("decline"))), {action: "decline"});
});

test("automatic and unsupported server responses are deterministic", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(
    contracts.currentTimeResponse(1234567))), {currentTimeAt: 1234});
  assert.deepEqual(JSON.parse(JSON.stringify(
    contracts.unsupportedRequestError("item/tool/call"))), {
    code: -32601,
    message: "Wheelhouse does not implement item/tool/call",
  });
});

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

test("thread settings persist an explicit working directory", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(contracts.threadSettings({
    threadId: "thread-1", cwd: "  /work/other-repo  ",
  }))), {threadId: "thread-1", cwd: "/work/other-repo"});
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

test("an approval-paused turn leaves next-turn controls selectable", () => {
  const send = {textContent: "", disabled: false};
  const stop = {style: {display: "none"}};
  const input = {disabled: false};
  const model = {disabled: false, value: ""};
  const approval = {disabled: false, value: ""};

  contracts.applyBusyState({
    busy: true, hasThread: true, send, stop, input,
    turnControls: [model, approval],
  });
  assert.equal(send.textContent, "Steer");
  assert.equal(model.disabled, false);
  assert.equal(approval.disabled, false);

  model.value = "gpt-test";
  approval.value = "on-request";
  contracts.applyBusyState({
    busy: false, hasThread: true, send, stop, input,
    turnControls: [model, approval],
  });
  assert.equal(model.value, "gpt-test");
  assert.equal(approval.value, "on-request");
});

test("multi-bucket usage keeps model-specific limits", () => {
  const buckets = contracts.rateLimitBuckets({
    rateLimits: {limitId: "codex", primary: {usedPercent: 1}},
    rateLimitsByLimitId: {
      codex: {limitId: "codex", primary: {usedPercent: 1}},
      codex_bengalfox: {
        limitId: "codex_bengalfox", limitName: "GPT-5.3-Codex-Spark",
        primary: {usedPercent: 2}, secondary: {usedPercent: 3},
      },
    },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(buckets)), [
    {id: "codex", name: "Codex",
      snapshot: {limitId: "codex", primary: {usedPercent: 1}}},
    {id: "codex_bengalfox", name: "GPT-5.3-Codex-Spark", snapshot: {
      limitId: "codex_bengalfox", limitName: "GPT-5.3-Codex-Spark",
      primary: {usedPercent: 2}, secondary: {usedPercent: 3},
    }},
  ]);
});

test("single-bucket usage remains compatible with older servers", () => {
  const buckets = contracts.rateLimitBuckets({
    rateLimits: {limitId: "codex", primary: {usedPercent: 9}},
  });
  assert.equal(buckets.length, 1);
  assert.equal(buckets[0].name, "Codex");
  assert.equal(buckets[0].snapshot.primary.usedPercent, 9);
});
