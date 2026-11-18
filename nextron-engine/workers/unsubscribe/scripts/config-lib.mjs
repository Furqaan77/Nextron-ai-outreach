// The deploy guard's logic, shared by scripts/check-config.mjs and scripts/secret.mjs (thin
// CLIs that always run) and by the tests.
//
// checkConfig(config, env) is empty only when, for that env and the top level: every key is on
// the allow-list below; workers.dev, preview URLs, Logpush and Workers Logs are off; every route
// is a custom domain on the env's own host (u.<domain> for prod, u-dev.<domain> and
// u-test.<domain> otherwise) that is neither a placeholder ".invalid" host nor *.workers.dev;
// and the env has exactly one KV binding UNSUB whose id is 32 lowercase hex and differs from
// every other env's.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const ENVIRONMENTS = ["dev", "test", "prod"];
export const HOST_PREFIX = { dev: "u-dev.", test: "u-test.", prod: "u." };
export const CONFIG_PATH = fileURLToPath(new URL("../wrangler.jsonc", import.meta.url));
const KV_ID = /^[0-9a-f]{32}$/;
const HOST = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/;

// JSONC to JSON: drops // and /* */ comments and trailing commas, never inside a string.
export function stripJsonc(text) {
  let out = "";
  let inString = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      out += ch;
      if (ch === "\\") {
        out += text[i + 1] ?? "";
        i += 1;
      } else if (ch === '"') {
        inString = false;
      }
    } else if (ch === '"') {
      inString = true;
      out += ch;
    } else if (ch === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") {
        i += 1;
      }
      out += "\n";
    } else if (ch === "/" && text[i + 1] === "*") {
      const end = text.indexOf("*/", i + 2);
      if (end === -1) {
        throw new SyntaxError("unterminated /* comment in wrangler.jsonc");
      }
      out += " ";
      i = end + 1;
    } else {
      out += ch;
    }
  }
  // Trailing commas, again outside strings only.
  let result = "";
  inString = false;
  for (let i = 0; i < out.length; i += 1) {
    const ch = out[i];
    if (inString) {
      result += ch;
      if (ch === "\\") {
        result += out[i + 1] ?? "";
        i += 1;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
    } else if (ch === ",") {
      let j = i + 1;
      while (j < out.length && /\s/.test(out[j])) {
        j += 1;
      }
      if (out[j] === "}" || out[j] === "]") {
        continue;
      }
    }
    result += ch;
  }
  return result;
}

export function parseJsonc(text) {
  return JSON.parse(stripJsonc(text));
}

// Only the keys the committed config uses. Anything else (tail_consumers, logfwdr, vars, unsafe,
// bindings of any other kind, services, ...) could add a log sink, a secret in plain text or
// another store, so it is refused until it is reviewed and added here.
export const TOP_LEVEL_KEYS = [
  "$schema", "name", "main", "compatibility_date", "send_metrics",
  "workers_dev", "preview_urls", "logpush", "observability", "env",
];
export const ENV_KEYS = [
  "name", "workers_dev", "preview_urls", "logpush", "observability", "routes", "kv_namespaces",
];

function checkKeys(section, allowed, where, problems) {
  for (const key of Object.keys(section)) {
    if (!allowed.includes(key)) {
      problems.push(`${where}: key ${JSON.stringify(key)} is not allowed in this Worker's config`);
    }
  }
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function checkSwitchesOff(section, where, problems) {
  if (section.workers_dev !== false) {
    problems.push(`${where}: workers_dev must be false`);
  }
  if (section.preview_urls !== false) {
    problems.push(`${where}: preview_urls must be false`);
  }
  if (section.logpush !== false) {
    problems.push(`${where}: logpush must be false`);
  }
  const observability = section.observability;
  if (!isObject(observability) || observability.enabled !== false) {
    problems.push(`${where}: observability.enabled must be false (Workers Logs off)`);
    return;
  }
  for (const [name, value] of Object.entries(observability)) {
    if (isObject(value) && value.enabled !== false) {
      problems.push(`${where}: observability.${name}.enabled must be false`);
    }
    if (isObject(value) && value.invocation_logs === true) {
      problems.push(`${where}: observability.${name}.invocation_logs must not be true`);
    }
  }
}

function checkRoutes(section, envName, problems) {
  const where = `env.${envName}`;
  if ("route" in section) {
    problems.push(`${where}: use "routes" of custom domains, not "route"`);
  }
  const routes = section.routes;
  if (!Array.isArray(routes) || routes.length === 0) {
    problems.push(`${where}: needs at least one custom-domain route`);
    return;
  }
  for (const route of routes) {
    if (!isObject(route) || typeof route.pattern !== "string" || route.custom_domain !== true) {
      problems.push(`${where}: every route must be {"pattern": <host>, "custom_domain": true}`);
      continue;
    }
    const host = route.pattern.toLowerCase();
    if (host.includes("workers.dev")) {
      problems.push(`${where}: route ${route.pattern} is on workers.dev`);
    } else if (host.endsWith(".invalid")) {
      problems.push(`${where}: route ${route.pattern} is a placeholder host (Story 3.9)`);
    } else if (!HOST.test(host)) {
      problems.push(`${where}: route ${route.pattern} is not a plain host name`);
    } else if (!host.startsWith(HOST_PREFIX[envName])) {
      problems.push(`${where}: route ${route.pattern} must start with ${HOST_PREFIX[envName]}`);
    }
  }
}

function kvIds(section) {
  const namespaces = isObject(section) ? section.kv_namespaces : undefined;
  if (!Array.isArray(namespaces)) {
    return [];
  }
  return namespaces.filter(isObject).flatMap((ns) => [ns.id, ns.preview_id]).filter((id) => id !== undefined);
}

function checkKv(section, envName, envs, problems) {
  const where = `env.${envName}`;
  const namespaces = section.kv_namespaces;
  if (!Array.isArray(namespaces) || namespaces.length !== 1 || !isObject(namespaces[0])) {
    problems.push(`${where}: needs exactly one KV namespace, bound as UNSUB`);
    return;
  }
  const ns = namespaces[0];
  if (ns.binding !== "UNSUB") {
    problems.push(`${where}: the KV binding must be named UNSUB`);
  }
  for (const field of ["id", "preview_id"]) {
    if (field === "preview_id" && ns.preview_id === undefined) {
      continue;
    }
    const id = ns[field];
    if (typeof id !== "string" || /placeholder/i.test(id) || !KV_ID.test(id)) {
      problems.push(`${where}: KV ${field} ${JSON.stringify(id)} is a placeholder or not 32 lowercase hex`);
    }
  }
  for (const other of ENVIRONMENTS) {
    if (other !== envName && kvIds(envs[other]).includes(ns.id)) {
      problems.push(`${where}: KV id is shared with env.${other}; each env needs its own namespace`);
    }
  }
}

// Every reason the config can't be deployed to envName; empty when it can.
export function checkConfig(config, envName) {
  if (!ENVIRONMENTS.includes(envName)) {
    return [`unknown env ${JSON.stringify(envName)}; use one of ${ENVIRONMENTS.join(", ")}`];
  }
  if (!isObject(config)) {
    return ["wrangler.jsonc is not an object"];
  }
  const problems = [];
  checkKeys(config, TOP_LEVEL_KEYS, "top level", problems);
  checkSwitchesOff(config, "top level", problems);
  const envs = isObject(config.env) ? config.env : {};
  const section = envs[envName];
  if (!isObject(section)) {
    return [...problems, `env.${envName} is missing`];
  }
  const where = `env.${envName}`;
  checkKeys(section, ENV_KEYS, where, problems);
  checkSwitchesOff(section, where, problems);
  if (typeof section.name !== "string" || section.name === "" || section.name === config.name) {
    problems.push(`${where}: needs its own Worker name`);
  }
  for (const other of ENVIRONMENTS) {
    if (other !== envName && isObject(envs[other]) && envs[other].name === section.name) {
      problems.push(`${where}: Worker name is shared with env.${other}`);
    }
  }
  checkRoutes(section, envName, problems);
  checkKv(section, envName, envs, problems);
  return problems;
}

export function checkConfigFile(envName, path = CONFIG_PATH) {
  let config;
  try {
    config = parseJsonc(readFileSync(path, "utf8"));
  } catch (error) {
    return [`cannot read ${path}: ${error.message}`];
  }
  return checkConfig(config, envName);
}

const SECRET_NAME = /^(K_EDGE_([1-9][0-9]{0,2})|PENDING_BEARER|PENDING_BEARER_NEXT)$/;

// K_EDGE_<1-255>, PENDING_BEARER or PENDING_BEARER_NEXT.
export function secretNameOk(name) {
  const match = typeof name === "string" ? SECRET_NAME.exec(name) : null;
  if (match === null) {
    return false;
  }
  return match[2] === undefined || Number(match[2]) <= 255;
}

// What `secret.mjs <env> <NAME>` does: {refusals} (non-empty) or {args}, wrangler's argv.
// check(env) gives the config's problems (checkConfigFile by default).
export function secretPlan(argv, check = checkConfigFile) {
  const [envName, name, ...rest] = argv;
  const problems = check(envName);
  if (problems.length > 0) {
    return { refusals: problems.map((p) => `check-config: ${p}`) };
  }
  if (rest.length > 0 || !secretNameOk(name)) {
    return {
      refusals: ["secret: give exactly one name: K_EDGE_<1-255>, PENDING_BEARER or PENDING_BEARER_NEXT"],
    };
  }
  return { args: ["secret", "put", name, "--env", envName] };
}
