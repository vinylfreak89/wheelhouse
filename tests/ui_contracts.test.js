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
    cwd: "  /work/current  ",
    model: "gpt-test",
    effort: "high",
    approvalPolicy: "on-request",
    serviceTier: "priority",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    threadId: "thread-1",
    input: [{type: "text", text: "continue"}],
    cwd: "/work/current",
    model: "gpt-test",
    effort: "high",
    approvalPolicy: "on-request",
    approvalsReviewer: "user",
    serviceTier: "priority",
  });
});

test("turn/start omits blank overrides instead of clearing sticky values", () => {
  const payload = contracts.turnStart({
    threadId: "thread-1", text: "continue", cwd: "", model: "", effort: "",
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
    approvalsReviewer: "user",
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

test("auto-review keeps on-request policy while delegating its decisions", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(contracts.autonomySettings({
    threadId: "thread-1", mode: "auto-review",
  }))), {
    threadId: "thread-1",
    approvalPolicy: "on-request",
    approvalsReviewer: "auto_review",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(contracts.autonomySettings({
    threadId: "thread-1", mode: "never",
  }))), {
    threadId: "thread-1",
    approvalPolicy: "never",
    approvalsReviewer: "user",
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

test("tail following tolerates normal near-bottom displacement", () => {
  assert.equal(contracts.nearTail({
    scrollHeight: 1000, scrollTop: 700, clientHeight: 200,
  }), true);
  assert.equal(contracts.nearTail({
    scrollHeight: 1000, scrollTop: 500, clientHeight: 200,
  }), false);
});

test("large item updates follow the tail without snapping a reader who scrolled up", () => {
  const following = {scrollHeight: 1000, scrollTop: 740, clientHeight: 200};
  contracts.mutatePreservingTail(following, () => {
    following.scrollHeight = 1600;
  });
  assert.equal(following.scrollTop, 1600);

  const reading = {scrollHeight: 1000, scrollTop: 420, clientHeight: 200};
  contracts.mutatePreservingTail(reading, () => {
    reading.scrollHeight = 1600;
  });
  assert.equal(reading.scrollTop, 420);
});

test("API errors retain their message and machine-readable classification", () => {
  assert.equal(contracts.errorText({
    message: "Selected model is at capacity. Please try a different model.",
    codexErrorInfo: {type: "serverOverloaded"},
  }), "Selected model is at capacity. Please try a different model.\ntype: serverOverloaded");
  assert.equal(contracts.errorText({
    message: "request failed", additionalDetails: "upstream returned 503",
    codex_error_info: "server_overloaded",
  }), "request failed\nupstream returned 503\ntype: server_overloaded");
  assert.equal(contracts.errorText("connection closed"), "connection closed");
});

test("long transcripts mount a bounded sliding window", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(contracts.virtualRange({
    total: 10000, direction: "tail",
  }))), {start: 9760, end: 10000});
  assert.deepEqual(JSON.parse(JSON.stringify(contracts.virtualRange({
    total: 10000, start: 9760, end: 10000, direction: "earlier",
  }))), {start: 9680, end: 9920});
  assert.deepEqual(JSON.parse(JSON.stringify(contracts.virtualRange({
    total: 10000, start: 9680, end: 9920, direction: "later",
  }))), {start: 9760, end: 10000});
  assert.deepEqual(JSON.parse(JSON.stringify(contracts.virtualRange({
    total: 30, direction: "tail",
  }))), {start: 0, end: 30});
});

test("a visible tall row can anchor one extra chunk without unbounded growth", () => {
  const range = contracts.virtualRange({
    total: 10000, start: 500, end: 740, direction: "later", anchor: 520,
  });
  assert.deepEqual(JSON.parse(JSON.stringify(range)), {start: 520, end: 820});
  assert.ok(range.end - range.start <= 320);

  const next = contracts.virtualRange({
    total: 10000, ...range, direction: "later", anchor: 520,
  });
  assert.deepEqual(JSON.parse(JSON.stringify(next)), {start: 580, end: 900});
  assert.ok(next.end - next.start <= 320);
});

test("repeated virtual shifts reach both ends without growing the DOM window", () => {
  const total = 10000;
  let range = contracts.virtualRange({total, direction: "tail"});
  while (range.start > 0) {
    range = contracts.virtualRange({total, ...range, direction: "earlier"});
    assert.ok(range.end - range.start <= 240);
  }
  assert.equal(range.start, 0);

  while (range.end < total) {
    range = contracts.virtualRange({total, ...range, direction: "later"});
    assert.ok(range.end - range.start <= 240);
  }
  assert.equal(range.end, total);
});

test("thread events cannot render before the selected transcript is attached", () => {
  assert.equal(contracts.threadEventRoute({
    eventThreadId: "thread-1", currentThreadId: null, viewReady: false,
  }), "not-ready");
  assert.equal(contracts.threadEventRoute({
    eventThreadId: "thread-1", currentThreadId: "thread-1", viewReady: false,
  }), "not-ready");
  assert.equal(contracts.threadEventRoute({
    eventThreadId: "thread-2", currentThreadId: "thread-1", viewReady: true,
  }), "other");
  assert.equal(contracts.threadEventRoute({
    eventThreadId: "thread-1", currentThreadId: "thread-1", viewReady: true,
  }), "current");
});

test("only a matching recent local user message is treated as an echo", () => {
  const pending = [
    {threadId: "thread-1", text: "local prompt", at: 1000},
    {threadId: "thread-2", text: "same words", at: 1500},
  ];
  assert.equal(contracts.optimisticEchoIndex(pending, {
    threadId: "thread-1", text: "local prompt", nowMs: 2000,
  }), 0);
  assert.equal(contracts.optimisticEchoIndex(pending, {
    threadId: "thread-1", text: "injected by Claude", nowMs: 2000,
  }), -1);
  assert.equal(contracts.optimisticEchoIndex(pending, {
    threadId: "thread-2", text: "same words", nowMs: 200000,
  }), -1);
});

test("navigation preserves only optimistic user messages absent from the rollout", () => {
  const entries = [
    {threadId: "thread-1", text: "saved", who: "you", at: 1000},
    {threadId: "thread-1", text: "duplicate", who: "you (steer)", at: 1001},
    {threadId: "thread-1", text: "duplicate", who: "you (steer)", at: 1002},
    {threadId: "thread-2", text: "other thread", who: "you", at: 1003},
  ];
  const missing = contracts.optimisticEchoesMissingFromTranscript(entries, {
    threadId: "thread-1",
    rows: [
      {cls: "user", text: "saved"},
      {cls: "user", text: "duplicate"},
      {cls: "agent", text: "not a user echo"},
    ],
  });
  assert.deepEqual(JSON.parse(JSON.stringify(missing)), [
    {threadId: "thread-1", text: "duplicate", who: "you (steer)", at: 1002},
  ]);
});

test("reset countdown carries rounded minutes into hours", () => {
  assert.equal(contracts.resetCountdown(17999, 0), "5h");
  assert.equal(contracts.resetCountdown(3599, 0), "1h");
  assert.equal(contracts.resetCountdown(90061, 0), "1d 1h");
});

test("poll refreshes keep existing thread order and prepend only new threads", () => {
  const previous = [{id: "a", status: "old-a"}, {id: "b", status: "old-b"}];
  const refreshed = [{id: "c"}, {id: "b", status: "new-b"},
                     {id: "a", status: "new-a"}];
  const stable = contracts.stableById(previous, refreshed);
  assert.deepEqual(stable.map(item => item.id), ["c", "a", "b"]);
  assert.equal(stable[1].status, "new-a");
  assert.equal(stable[2].status, "new-b");
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

test("model-specific usage buckets have deterministic order", () => {
  const buckets = contracts.rateLimitBuckets({
    rateLimitsByLimitId: {
      codex_zebra: {limitName: "Zebra"},
      codex_bengalfox: {limitName: "Bengalfox"},
      codex: {limitName: "Codex"},
    },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(buckets.map(bucket => bucket.id))), [
    "codex", "codex_bengalfox", "codex_zebra",
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
