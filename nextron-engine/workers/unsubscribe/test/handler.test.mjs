// The Worker's handler, called directly against a fake KV (no node_modules, no port), and the
// deploy guard scripts. The engine's pytest gate runs this file too.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import worker from "../src/index.js";
import {
  checkConfig,
  checkConfigFile,
  parseJsonc,
  secretNameOk,
  secretPlan,
  stripJsonc,
} from "../scripts/config-lib.mjs";

// The engine's fixture, read in place (never a copy).
const VECTORS = JSON.parse(
  readFileSync(new URL("../../../tests/fixtures/unsub_token_vectors.json", import.meta.url), "utf8"),
);
const CONFIG_TEXT = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
const BEARER = "b".repeat(43);
const BEARER_NEXT = "n".repeat(43) + "-next";
const BASE = "https://u.example.test";
const RECEIVED_AT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const UNSUBSCRIBED = "You're unsubscribed. You won't get further emails from this sender.";

class FakeKV {
  constructor(pageSize = 1000) {
    this.store = new Map();
    this.calls = [];
    this.pageSize = pageSize;
  }

  async get(name) {
    this.calls.push(["get", name]);
    return this.store.has(name) ? this.store.get(name).value : null;
  }

  async put(name, value, options) {
    this.calls.push(["put", name, value, options]);
    this.store.set(name, { value, metadata: options?.metadata, expiration: options?.expiration });
  }

  async list(options) {
    this.calls.push(["list", options]);
    const names = [...this.store.keys()].filter((n) => n.startsWith(options.prefix)).sort();
    const start = options.cursor === undefined ? 0 : Number(options.cursor);
    const slice = names.slice(start, start + this.pageSize);
    const done = start + this.pageSize >= names.length;
    const keys = slice.map((name) => {
      const entry = this.store.get(name);
      const key = { name };
      if (entry.metadata !== undefined) key.metadata = entry.metadata;
      if (entry.expiration !== undefined) key.expiration = entry.expiration;
      return key;
    });
    return done ? { keys, list_complete: true } : { keys, list_complete: false, cursor: String(start + this.pageSize) };
  }

  async delete() {
    throw new Error("the Worker must never remove a KV key");
  }
}

function environment(overrides = {}) {
  return {
    K_EDGE_1: VECTORS.keys["1"].k_edge,
    K_EDGE_2: VECTORS.keys["2"].k_edge,
    PENDING_BEARER: BEARER,
    PENDING_BEARER_NEXT: BEARER_NEXT,
    UNSUB: new FakeKV(),
    ...overrides,
  };
}

function call(env, method, path, init = {}) {
  return worker.fetch(new Request(BASE + path, { method, ...init }), env);
}

function assertCommonHeaders(response) {
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
}

async function assertEmpty(response, status) {
  assert.equal(response.status, status);
  assertCommonHeaders(response);
  assert.equal(await response.text(), "");
}

function sha256(text) {
  return `sha256-${createHash("sha256").update(text, "utf8").digest("base64")}`;
}

// A request whose every property and header read is recorded.
function recordingRequest(method, path, authorization) {
  const real = new Request(BASE + path, { method, body: method === "POST" ? "List-Unsubscribe=One-Click" : undefined });
  const touched = [];
  const headersRead = [];
  const headers = {
    get(name) {
      headersRead.push(name);
      return name === "authorization" ? (authorization ?? null) : real.headers.get(name);
    },
  };
  const request = new Proxy(real, {
    get(target, prop) {
      touched.push(prop);
      if (prop === "headers") return headers;
      const value = target[prop];
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
  return { request, touched, headersRead, real };
}

const validVectors = VECTORS.valid;
const rejected = VECTORS.tampered.filter((t) => t.edge === "reject");
const accepted = VECTORS.tampered.filter((t) => t.edge === "accept");

describe("the fixture", () => {
  it("has valid tokens, both edge verdicts and kids 1 and 2", () => {
    assert.ok(validVectors.length >= 2);
    assert.ok(rejected.length > 0 && accepted.length > 0);
    assert.deepEqual(Object.keys(VECTORS.keys).sort(), ["1", "2"]);
  });
});

describe("POST /u/<token>", () => {
  for (const vector of validVectors) {
    it(`records valid token ${vector.name} once, with received_at and a 30-day TTL`, async () => {
      const env = environment();
      const response = await call(env, "POST", `/u/${vector.token}`, {
        body: "List-Unsubscribe=One-Click",
        headers: { "content-type": "application/x-www-form-urlencoded" },
      });
      await assertEmpty(response, 200);
      assert.deepEqual([...env.UNSUB.store.keys()], [`p:${vector.token}`]);
      const puts = env.UNSUB.calls.filter((c) => c[0] === "put");
      assert.equal(puts.length, 1);
      const [, , value, options] = puts[0];
      assert.equal(value, "");
      assert.equal(options.expirationTtl, 2592000);
      assert.deepEqual(Object.keys(options.metadata), ["received_at"]);
      assert.match(options.metadata.received_at, RECEIVED_AT);
    });
  }

  it("keeps the first received_at on a repeat, writing nothing", async (t) => {
    t.mock.timers.enable({ apis: ["Date"], now: Date.UTC(2026, 10, 18, 9, 30, 15, 789) });
    const env = environment();
    const path = `/u/${validVectors[0].token}`;
    await assertEmpty(await call(env, "POST", path), 200);
    t.mock.timers.setTime(Date.UTC(2026, 10, 19, 10, 0, 0));
    await assertEmpty(await call(env, "POST", path, { body: "anything at all" }), 200);
    assert.equal(env.UNSUB.store.size, 1);
    assert.equal(env.UNSUB.calls.filter((c) => c[0] === "put").length, 1);
    assert.deepEqual(env.UNSUB.store.get(`p:${validVectors[0].token}`).metadata, {
      received_at: "2026-11-18T09:30:15Z",
    });
  });

  for (const vector of rejected) {
    it(`refuses tampered ${vector.name} with 400 and no KV call`, async () => {
      const env = environment();
      await assertEmpty(await call(env, "POST", `/u/${encodeURIComponent(vector.token)}`), vector.token === "" ? 404 : 400);
      assert.deepEqual(env.UNSUB.calls, []);
    });
  }

  for (const vector of accepted) {
    it(`accepts tampered ${vector.name}, whose tag verifies (the engine rejects it)`, async () => {
      const env = environment();
      await assertEmpty(await call(env, "POST", `/u/${vector.token}`), 200);
      assert.deepEqual([...env.UNSUB.store.keys()], [`p:${vector.token}`]);
    });
  }

  it("refuses a kid whose secret is missing", async () => {
    const env = environment({ K_EDGE_2: undefined });
    const kid2 = validVectors.find((v) => v.kid === 2);
    await assertEmpty(await call(env, "POST", `/u/${kid2.token}`), 400);
    assert.deepEqual(env.UNSUB.calls, []);
  });

  const malformedKeys = {
    "standard base64": VECTORS.keys["1"].k_edge.replace(/-/g, "+").replace(/_/g, "/") + "+",
    "42 characters": VECTORS.keys["1"].k_edge.slice(0, 42),
    "45 characters": `${VECTORS.keys["1"].k_edge}==`,
    "non-zero unused bits": VECTORS.keys["1"].k_edge.slice(0, 42) + "B",
    "a number": 7,
    "an object": { k: 1 },
    "spaces around": ` ${VECTORS.keys["1"].k_edge} `,
  };
  for (const [label, key] of Object.entries(malformedKeys)) {
    it(`refuses when K_EDGE_1 is malformed (${label})`, async () => {
      const env = environment({ K_EDGE_1: key });
      const kid1 = validVectors.find((v) => v.kid === 1);
      await assertEmpty(await call(env, "POST", `/u/${kid1.token}`), 400);
      assert.deepEqual(env.UNSUB.calls, []);
    });
  }

  it("accepts K_EDGE_1 in its padded 44-character form", async () => {
    const env = environment({ K_EDGE_1: `${VECTORS.keys["1"].k_edge}=` });
    const kid1 = validVectors.find((v) => v.kid === 1);
    await assertEmpty(await call(env, "POST", `/u/${kid1.token}`), 200);
  });

  it("refuses garbage tokens", async () => {
    const env = environment();
    const good = validVectors[0].token;
    for (const token of ["x", good.slice(1), `${good}A`, `${good.slice(0, 107)}%`, "A".repeat(108), "%E2%82%AC".repeat(12)]) {
      await assertEmpty(await call(env, "POST", `/u/${token}`), 400);
    }
    assert.deepEqual(env.UNSUB.calls, []);
  });

  it("never reads the body or any header, and never touches request.cf", async () => {
    const env = environment();
    const { request, touched, headersRead, real } = recordingRequest("POST", `/u/${validVectors[0].token}`);
    await assertEmpty(await worker.fetch(request, env), 200);
    assert.deepEqual(headersRead, []);
    assert.deepEqual(touched.filter((p) => !["url", "method"].includes(p)), []);
    assert.equal(real.bodyUsed, false);
  });

  it("answers an empty 500 when KV fails", async () => {
    const kv = new FakeKV();
    kv.get = async () => {
      throw new Error("kv down");
    };
    await assertEmpty(await call(environment({ UNSUB: kv }), "POST", `/u/${validVectors[0].token}`), 500);
  });
});

describe("GET /u/<token>", () => {
  it("serves a self-contained page whose button POSTs, without touching KV", async () => {
    const env = environment();
    const response = await call(env, "GET", `/u/${validVectors[0].token}`);
    assert.equal(response.status, 200);
    assertCommonHeaders(response);
    assert.equal(response.headers.get("content-type"), "text/html; charset=utf-8");
    const html = await response.text();
    assert.match(html, /<form id="f" method="post"><button type="submit">Unsubscribe<\/button><\/form>/);
    assert.ok(html.includes(UNSUBSCRIBED));
    assert.match(html, /<p id="m" role="status">/);
    assert.match(html, /<noscript><p>Pressing Unsubscribe records your opt-out, even if the next page is blank\.<\/p><\/noscript>/);
    assert.doesNotMatch(html, /\b(src|href)=/);
    assert.doesNotMatch(html, /action=/);
    assert.ok(!html.includes(validVectors[0].token));
    const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    const styles = [...html.matchAll(/<style>([\s\S]*?)<\/style>/g)].map((m) => m[1]);
    assert.equal(scripts.length, 1);
    assert.equal(styles.length, 1);
    assert.match(scripts[0], /fetch\(location\.href,\{method:"POST"/);
    const csp = response.headers.get("content-security-policy");
    for (const directive of [
      "default-src 'none'",
      `script-src '${sha256(scripts[0])}'`,
      `style-src '${sha256(styles[0])}'`,
      "form-action 'self'",
      "connect-src 'self'",
      "frame-ancestors 'none'",
      "base-uri 'none'",
    ]) {
      assert.ok(csp.split("; ").includes(directive), `CSP lacks ${directive}: ${csp}`);
    }
    assert.deepEqual(env.UNSUB.calls, []);
  });

  for (const vector of [...rejected.filter((v) => v.token !== ""), { name: "garbage", token: "nope" }]) {
    it(`says ${vector.name} isn't valid, with 400 and no button`, async () => {
      const env = environment();
      const response = await call(env, "GET", `/u/${encodeURIComponent(vector.token)}`);
      assert.equal(response.status, 400);
      assertCommonHeaders(response);
      const html = await response.text();
      assert.ok(html.includes("This link isn't valid"));
      assert.doesNotMatch(html, /<button|<script|<form/);
      assert.ok(response.headers.get("content-security-policy").includes("default-src 'none'"));
      assert.deepEqual(env.UNSUB.calls, []);
    });
  }
});

describe("GET /pending", () => {
  async function seeded(pageSize) {
    const env = environment({ UNSUB: new FakeKV(pageSize) });
    for (const vector of validVectors) {
      await call(env, "POST", `/u/${vector.token}`);
    }
    // A key without metadata or expiration still lists, with nulls.
    env.UNSUB.store.set("p:bare", { value: "" });
    env.UNSUB.store.set("other:not-listed", { value: "" });
    for (const entry of env.UNSUB.store.values()) {
      if (entry.metadata !== undefined) entry.expiration = 1800000000;
    }
    env.UNSUB.calls = [];
    return env;
  }

  for (const bearer of [BEARER, BEARER_NEXT]) {
    it(`lists every p: key across list pages (bearer ${bearer === BEARER ? "current" : "next"})`, async () => {
      const env = await seeded(2);
      const response = await call(env, "GET", "/pending", { headers: { authorization: `Bearer ${bearer}` } });
      assert.equal(response.status, 200);
      assertCommonHeaders(response);
      assert.equal(response.headers.get("content-type"), "application/json");
      const body = JSON.parse(await response.text());
      assert.equal(body.format, "nextron-unsub-pending/1");
      const expected = [
        { token: "bare", received_at: null, expiration: null },
        ...validVectors.map((v) => ({ token: v.token, received_at: body.entries.find((e) => e.token === v.token)?.received_at, expiration: 1800000000 })),
      ].sort((a, b) => (a.token < b.token ? -1 : 1));
      assert.deepEqual(body.entries, expected);
      for (const entry of body.entries.filter((e) => e.token !== "bare")) {
        assert.match(entry.received_at, RECEIVED_AT);
      }
      const lists = env.UNSUB.calls.filter((c) => c[0] === "list");
      assert.ok(lists.length > 1, "more than one page");
      assert.deepEqual(lists[0][1], { prefix: "p:" });
      assert.deepEqual(lists[1][1], { prefix: "p:", cursor: "2" });
      assert.deepEqual(env.UNSUB.calls.filter((c) => c[0] !== "list"), []);
    });
  }

  it("returns an empty list when nothing is pending", async () => {
    const env = environment();
    const response = await call(env, "GET", "/pending", { headers: { authorization: `Bearer ${BEARER}` } });
    assert.deepEqual(JSON.parse(await response.text()), { format: "nextron-unsub-pending/1", entries: [] });
  });

  const refusals = {
    "no header": [{}, {}],
    "a wrong bearer": [{ authorization: `Bearer ${"w".repeat(43)}` }, {}],
    Basic: [{ authorization: `Basic ${BEARER}` }, {}],
    "Bearer with no value": [{ authorization: "Bearer " }, {}],
    "a bearer with a prefix of the secret": [{ authorization: `Bearer ${BEARER.slice(0, 42)}` }, {}],
    "both secrets unset": [{ authorization: "Bearer undefined" }, { PENDING_BEARER: undefined, PENDING_BEARER_NEXT: undefined }],
    "a matching but short secret": [{ authorization: `Bearer ${"s".repeat(42)}` }, { PENDING_BEARER: "s".repeat(42) }],
    "an empty secret": [{ authorization: "Bearer x" }, { PENDING_BEARER: "", PENDING_BEARER_NEXT: "" }],
  };
  for (const [label, [headers, overrides]] of Object.entries(refusals)) {
    it(`answers 401 with ${label}`, async () => {
      const env = environment(overrides);
      await assertEmpty(await call(env, "GET", "/pending", { headers }), 401);
      assert.deepEqual(env.UNSUB.calls, []);
    });
  }

  it("accepts the scheme in lowercase", async () => {
    const env = environment();
    const response = await call(env, "GET", "/pending", { headers: { authorization: `bearer ${BEARER}` } });
    assert.equal(response.status, 200);
  });

  it("still accepts the current bearer when the next one is unset", async () => {
    const env = environment({ PENDING_BEARER_NEXT: undefined });
    const response = await call(env, "GET", "/pending", { headers: { authorization: `Bearer ${BEARER}` } });
    assert.equal(response.status, 200);
  });

  it("reads only the authorization header", async () => {
    const env = environment();
    const { request, headersRead, touched } = recordingRequest("GET", "/pending", `Bearer ${BEARER}`);
    const response = await worker.fetch(request, env);
    assert.equal(response.status, 200);
    assert.deepEqual(headersRead, ["authorization"]);
    assert.deepEqual(touched.filter((p) => !["url", "method", "headers"].includes(p)), []);
  });

  it("answers an empty 500 when a page has no cursor", async () => {
    const kv = new FakeKV();
    kv.list = async () => ({ keys: [], list_complete: false });
    const response = await call(environment({ UNSUB: kv }), "GET", "/pending", {
      headers: { authorization: `Bearer ${BEARER}` },
    });
    await assertEmpty(response, 500);
  });
});

describe("other routes", () => {
  const token = validVectors[0].token;
  const cases = [
    ["GET", "/"],
    ["POST", "/"],
    ["GET", "/u/"],
    ["POST", "/u/"],
    ["GET", `/u/${token}/x`],
    ["POST", `/u/${token}/x`],
    ["PUT", `/u/${token}`],
    ["DELETE", `/u/${token}`],
    ["PATCH", `/u/${token}`],
    ["HEAD", `/u/${token}`],
    ["HEAD", "/pending"],
    ["POST", "/pending"],
    ["GET", "/pending/"],
    ["GET", "/U/" + token],
    ["GET", "/favicon.ico"],
  ];
  for (const [method, path] of cases) {
    it(`${method} ${path.slice(0, 24)} is 404, empty, with no KV call`, async () => {
      const env = environment();
      await assertEmpty(await call(env, method, path, { headers: { authorization: `Bearer ${BEARER}` } }), 404);
      assert.deepEqual(env.UNSUB.calls, []);
    });
  }
});

describe("check-config", () => {
  function ready() {
    const config = parseJsonc(CONFIG_TEXT);
    const ids = { dev: "a".repeat(32), test: "b".repeat(32), prod: "c".repeat(32) };
    for (const [name, prefix] of Object.entries({ dev: "u-dev.", test: "u-test.", prod: "u." })) {
      config.env[name].routes = [{ pattern: `${prefix}mail.example.com`, custom_domain: true }];
      config.env[name].kv_namespaces = [{ binding: "UNSUB", id: ids[name] }];
    }
    return config;
  }

  for (const env of ["dev", "test", "prod"]) {
    it(`refuses the committed placeholders for ${env}`, () => {
      const problems = checkConfigFile(env);
      assert.ok(problems.some((p) => p.includes(".invalid")), problems.join("\n"));
      assert.ok(problems.some((p) => p.includes("placeholder or not 32 lowercase hex")), problems.join("\n"));
    });

    it(`passes a ready config for ${env}`, () => {
      assert.deepEqual(checkConfig(ready(), env), []);
    });
  }

  it("keeps every switch off in the committed config", () => {
    const config = parseJsonc(CONFIG_TEXT);
    for (const section of [config, config.env.dev, config.env.test, config.env.prod]) {
      assert.equal(section.workers_dev, false);
      assert.equal(section.preview_urls, false);
      assert.equal(section.logpush, false);
      assert.equal(section.observability.enabled, false);
      assert.equal(section.observability.logs.enabled, false);
    }
  });

  const refusals = {
    "an unknown env": [(c) => c, "staging", /unknown env/],
    "no env": [(c) => c, undefined, /unknown env/],
    "a workers.dev route": [(c) => { c.env.prod.routes = [{ pattern: "u.nextron.workers.dev", custom_domain: true }]; }, "prod", /workers\.dev/],
    "an .invalid host": [(c) => { c.env.prod.routes[0].pattern = "u.thing.invalid"; }, "prod", /placeholder host/],
    "a non-custom-domain route": [(c) => { c.env.prod.routes = ["u.mail.example.com/*"]; }, "prod", /custom_domain/],
    "a route on another env's host": [(c) => { c.env.prod.routes[0].pattern = "u-dev.mail.example.com"; }, "prod", /must start with u\./],
    "a dev route on the prod host": [(c) => { c.env.dev.routes[0].pattern = "u.mail.example.com"; }, "dev", /must start with u-dev\./],
    "a placeholder KV id": [(c) => { c.env.test.kv_namespaces[0].id = "PLACEHOLDER0000000000000000000000"; }, "test", /placeholder/],
    "an uppercase KV id": [(c) => { c.env.test.kv_namespaces[0].id = "A".repeat(32); }, "test", /32 lowercase hex/],
    "a shared KV id": [(c) => { c.env.test.kv_namespaces[0].id = "a".repeat(32); }, "test", /shared with env\.dev/],
    "a second KV binding": [(c) => { c.env.dev.kv_namespaces.push({ binding: "X", id: "d".repeat(32) }); }, "dev", /exactly one/],
    "a wrong binding name": [(c) => { c.env.dev.kv_namespaces[0].binding = "KV"; }, "dev", /UNSUB/],
    "workers_dev on in an env": [(c) => { c.env.prod.workers_dev = true; }, "prod", /workers_dev/],
    "workers_dev missing at the top": [(c) => { delete c.workers_dev; }, "prod", /top level: workers_dev/],
    "preview URLs not off": [(c) => { delete c.env.prod.preview_urls; }, "prod", /preview_urls/],
    "logpush on": [(c) => { c.env.prod.logpush = true; }, "prod", /logpush/],
    "Workers Logs on": [(c) => { c.env.prod.observability.enabled = true; }, "prod", /observability/],
    "logs on inside observability": [(c) => { c.observability.logs.enabled = true; }, "prod", /observability\.logs/],
    "the env's name shared": [(c) => { c.env.prod.name = c.env.test.name; }, "prod", /shared with env\.test/],
    "a singular route": [(c) => { c.env.prod.route = "u.mail.example.com"; }, "prod", /not "route"/],
    "a non-plain custom-domain host": [(c) => { c.env.prod.routes[0].pattern = "u.mail.example.com/*"; }, "prod", /not a plain host name/],
    "invocation logs on": [(c) => { c.env.prod.observability.logs.invocation_logs = true; }, "prod", /invocation_logs/],
    "a placeholder preview_id": [(c) => { c.env.prod.kv_namespaces[0].preview_id = "PLACEHOLDER-PREVIEW"; }, "prod", /preview_id/],
    "a malformed preview_id": [(c) => { c.env.prod.kv_namespaces[0].preview_id = "e".repeat(31); }, "prod", /preview_id/],
    "the env's name equal to the top-level name": [(c) => { c.env.prod.name = c.name; }, "prod", /its own Worker name/],
    "a top-level tail_consumers": [(c) => { c.tail_consumers = [{ service: "x" }]; }, "prod", /"tail_consumers" is not allowed/],
  };
  for (const [label, [change, env, pattern]] of Object.entries(refusals)) {
    it(`refuses ${label}`, () => {
      const config = ready();
      change(config);
      const problems = checkConfig(config, env);
      assert.ok(problems.some((p) => pattern.test(p)), problems.join("\n") || "no problem reported");
    });
  }

  for (const key of ["tail_consumers", "logfwdr", "vars", "unsafe", "analytics_engine_datasets", "d1_databases", "r2_buckets", "queues", "services", "durable_objects"]) {
    it(`refuses ${key} at the top level and in an env`, () => {
      const top = ready();
      top[key] = {};
      assert.ok(checkConfig(top, "prod").some((p) => p.startsWith(`top level: key "${key}"`)));
      const env = ready();
      env.env.test[key] = {};
      assert.ok(checkConfig(env, "test").some((p) => p.startsWith(`env.test: key "${key}"`)));
    });
  }

  it("strips comments and trailing commas outside strings only", () => {
    const text = '{\n  // a comment\n  "a": "x//y", /* block */ "b": "/*z*/",\n  "c": [1, 2,],\n  "d": "q\\"//",\n}';
    assert.deepEqual(JSON.parse(stripJsonc(text)), { a: "x//y", b: "/*z*/", c: [1, 2], d: 'q"//' });
  });
});

describe("secretPlan", () => {
  function readyCheck() {
    const config = parseJsonc(CONFIG_TEXT);
    const ids = { dev: "a".repeat(32), test: "b".repeat(32), prod: "c".repeat(32) };
    for (const [name, prefix] of Object.entries({ dev: "u-dev.", test: "u-test.", prod: "u." })) {
      config.env[name].routes = [{ pattern: `${prefix}mail.example.com`, custom_domain: true }];
      config.env[name].kv_namespaces = [{ binding: "UNSUB", id: ids[name] }];
    }
    return (env) => checkConfig(config, env);
  }

  it("plans wrangler secret put for an allowed name on a ready config", () => {
    assert.deepEqual(secretPlan(["prod", "PENDING_BEARER"], readyCheck()), {
      args: ["secret", "put", "PENDING_BEARER", "--env", "prod"],
    });
    assert.deepEqual(secretPlan(["dev", "K_EDGE_7"], readyCheck()).args, ["secret", "put", "K_EDGE_7", "--env", "dev"]);
  });

  for (const [label, argv] of Object.entries({
    "K_TOK_1": ["prod", "K_TOK_1"],
    "an extra argument": ["prod", "PENDING_BEARER", "value"],
    "no name": ["prod"],
  })) {
    it(`refuses ${label}`, () => {
      const plan = secretPlan(argv, readyCheck());
      assert.equal(plan.args, undefined);
      assert.match(plan.refusals.join("\n"), /give exactly one name/);
    });
  }

  it("refuses an unknown env and the committed placeholders", () => {
    assert.match(secretPlan(["staging", "PENDING_BEARER"], readyCheck()).refusals.join("\n"), /unknown env/);
    assert.match(secretPlan(["prod", "PENDING_BEARER"]).refusals.join("\n"), /\.invalid/);
    assert.match(secretPlan([]).refusals.join("\n"), /unknown env/);
  });
});

describe("secret names", () => {
  it("accepts only K_EDGE_<1-255> and the two bearers", () => {
    for (const name of ["K_EDGE_1", "K_EDGE_42", "K_EDGE_255", "PENDING_BEARER", "PENDING_BEARER_NEXT"]) {
      assert.equal(secretNameOk(name), true, name);
    }
    for (const name of ["K_EDGE_0", "K_EDGE_256", "K_EDGE_01", "K_TOK_1", "K_EDGE_", "pending_bearer", "PENDING_BEARER_2", "", undefined]) {
      assert.equal(secretNameOk(name), false, String(name));
    }
  });
});

