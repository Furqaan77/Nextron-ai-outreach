// The story's acceptance test against `wrangler dev --local` (env test), offline: loopback
// only, free ports, a temporary persist dir, secrets via --var, telemetry and the cf.json
// fetch off. The whole process tree is killed afterwards and both ports are checked free.

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const WRANGLER = join(ROOT, "node_modules", "wrangler", "bin", "wrangler.js");
const VECTORS = JSON.parse(
  readFileSync(new URL("../../../tests/fixtures/unsub_token_vectors.json", import.meta.url), "utf8"),
);
const BEARER = "c".repeat(43);
const BEARER_NEXT = "d".repeat(43);
const START_TIMEOUT_MS = 90000;

function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function portIsFree(port) {
  return new Promise((resolve) => {
    const server = createServer();
    server.once("error", () => resolve(false));
    server.listen(port, "127.0.0.1", () => server.close(() => resolve(true)));
  });
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function killTree(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return;
  }
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
      child.kill("SIGKILL");
    }
  }
}

function childEnvironment(scratch) {
  const env = {};
  for (const name of ["PATH", "Path", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"]) {
    if (process.env[name] !== undefined) env[name] = process.env[name];
  }
  // A home of its own, so wrangler can never read a real `wrangler login` token or config.
  const home = {
    HOME: join(scratch, "home"),
    USERPROFILE: join(scratch, "home"),
    APPDATA: join(scratch, "home", "AppData", "Roaming"),
    LOCALAPPDATA: join(scratch, "home", "AppData", "Local"),
    XDG_CONFIG_HOME: join(scratch, "home", ".config"),
  };
  for (const dir of Object.values(home)) {
    mkdirSync(dir, { recursive: true });
  }
  return {
    ...env,
    ...home,
    CI: "1",
    DO_NOT_TRACK: "1",
    WRANGLER_SEND_METRICS: "false",
    WRANGLER_SEND_ERROR_REPORTS: "false",
    CLOUDFLARE_CF_FETCH_ENABLED: "false",
    CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV: "false",
    CLOUDFLARE_INCLUDE_PROCESS_ENV: "false",
    WRANGLER_HIDE_BANNER: "true",
    WRANGLER_LOG_PATH: join(scratch, "logs"),
    WRANGLER_REGISTRY_PATH: join(scratch, "registry"),
    WRANGLER_CACHE_DIR: join(scratch, "cache"),
    MINIFLARE_CACHE_DIR: join(scratch, "mf-cache"),
  };
}

describe("wrangler dev --local", { timeout: START_TIMEOUT_MS + 60000 }, () => {
  let child;
  let base;
  let ports = [];
  let scratch;
  let output = "";

  before(async () => {
    assert.ok(existsSync(WRANGLER), "run npm ci in workers/unsubscribe first");
    scratch = mkdtempSync(join(tmpdir(), "nextron-unsub-dev-"));
    const port = await freePort();
    const inspector = await freePort();
    ports = [port, inspector];
    base = `http://127.0.0.1:${port}`;
    const vars = {
      K_EDGE_1: VECTORS.keys["1"].k_edge,
      K_EDGE_2: VECTORS.keys["2"].k_edge,
      PENDING_BEARER: BEARER,
      PENDING_BEARER_NEXT: BEARER_NEXT,
    };
    const args = [
      WRANGLER, "dev", "--local", "--env", "test",
      "--ip", "127.0.0.1", "--port", String(port), "--inspector-port", String(inspector),
      "--persist-to", join(scratch, "state"),
      "--show-interactive-dev-session=false", "--live-reload=false",
      ...Object.entries(vars).flatMap(([name, value]) => ["--var", `${name}:${value}`]),
    ];
    child = spawn(process.execPath, args, {
      cwd: ROOT,
      env: childEnvironment(scratch),
      stdio: ["ignore", "pipe", "pipe"],
      detached: process.platform !== "win32",
      windowsHide: true,
    });
    child.stdout.on("data", (chunk) => { output += chunk; });
    child.stderr.on("data", (chunk) => { output += chunk; });
    const deadline = Date.now() + START_TIMEOUT_MS;
    for (;;) {
      if (child.exitCode !== null) {
        throw new Error(`wrangler dev exited early (${child.exitCode}):\n${output}`);
      }
      try {
        const response = await fetch(`${base}/`);
        if (response.status === 404) break;
      } catch {
        // not listening yet
      }
      if (Date.now() > deadline) {
        throw new Error(`wrangler dev did not start in time:\n${output}`);
      }
      await pause(250);
    }
  });

  after(async () => {
    if (child !== undefined) {
      const exited = child.exitCode !== null || child.signalCode !== null
        ? Promise.resolve()
        : new Promise((resolve) => child.once("exit", resolve));
      killTree(child);
      await exited;
    }
    for (const port of ports) {
      let free = false;
      for (let i = 0; i < 40 && !free; i += 1) {
        free = await portIsFree(port);
        if (!free) await pause(250);
      }
      assert.ok(free, `port ${port} is still in use after teardown`);
    }
    if (scratch !== undefined) {
      // workerd's files can stay locked for a moment after the tree is killed (Windows).
      for (let i = 0; ; i += 1) {
        try {
          rmSync(scratch, { recursive: true, force: true });
          break;
        } catch (error) {
          if (i >= 60) throw error;
          await pause(250);
        }
      }
    }
  });

  async function pendingEntries(bearer = BEARER) {
    const response = await fetch(`${base}/pending`, { headers: { authorization: `Bearer ${bearer}` } });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    const body = await response.json();
    assert.equal(body.format, "nextron-unsub-pending/1");
    return body.entries;
  }

  const valid = VECTORS.valid[0];
  const tampered = VECTORS.tampered.find((t) => t.name === "tag_flipped");

  it("starts with nothing pending", async () => {
    assert.deepEqual(await pendingEntries(), []);
  });

  it("a GET writes nothing, valid or not", async () => {
    const good = await fetch(`${base}/u/${valid.token}`);
    assert.equal(good.status, 200);
    assert.match(await good.text(), /<button type="submit">Unsubscribe<\/button>/);
    const bad = await fetch(`${base}/u/${tampered.token}`);
    assert.equal(bad.status, 400);
    assert.match(await bad.text(), /This link isn't valid/);
    assert.deepEqual(await pendingEntries(), []);
  });

  it("a tampered POST gives 400 and no key", async () => {
    const response = await fetch(`${base}/u/${tampered.token}`, { method: "POST", body: "List-Unsubscribe=One-Click" });
    assert.equal(response.status, 400);
    assert.equal(await response.text(), "");
    assert.deepEqual(await pendingEntries(), []);
  });

  let first;
  it("a valid POST gives 200 and one key", async () => {
    const response = await fetch(`${base}/u/${valid.token}`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: "List-Unsubscribe=One-Click",
    });
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "");
    const entries = await pendingEntries();
    assert.equal(entries.length, 1);
    assert.equal(entries[0].token, valid.token);
    assert.match(entries[0].received_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
    assert.equal(typeof entries[0].expiration, "number");
    first = entries[0];
  });

  it("a repeated POST still gives one key with the first received_at", async () => {
    await pause(1100);
    const response = await fetch(`${base}/u/${valid.token}`, { method: "POST", body: "List-Unsubscribe=One-Click" });
    assert.equal(response.status, 200);
    assert.deepEqual(await pendingEntries(), [first]);
  });

  it("/pending answers 401 without or with a wrong bearer, and 200 with the next one", async () => {
    const none = await fetch(`${base}/pending`);
    assert.equal(none.status, 401);
    assert.equal(await none.text(), "");
    const wrong = await fetch(`${base}/pending`, { headers: { authorization: `Bearer ${"e".repeat(43)}` } });
    assert.equal(wrong.status, 401);
    assert.equal(await wrong.text(), "");
    assert.deepEqual(await pendingEntries(BEARER_NEXT), [first]);
  });

  it("any other path or method is 404", async () => {
    for (const [method, path] of [["GET", "/"], ["PUT", `/u/${valid.token}`], ["POST", "/pending"], ["GET", "/u/"]]) {
      const response = await fetch(`${base}${path}`, { method });
      assert.equal(response.status, 404, `${method} ${path}`);
      assert.equal(await response.text(), "");
    }
  });
});
