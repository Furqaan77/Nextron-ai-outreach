// Nextron's one-click unsubscribe Worker (Story 3.10; FR-70, AD-14, AD-15, RFC 8058).
//
// It is the engine's only off-PC store. It holds a token and the second it arrived, nothing
// else: no IP address, User-Agent, header or request body is stored, and nothing is logged.
// The only request header it reads is `authorization`, and it never reads a request body.
//
// A token is base64url (no padding) of 81 bytes: kid (1) || nonce (12) || ciphertext (56) ||
// tag (12), where tag = HMAC-SHA256(K_EDGE_<kid>, the first 69 bytes)[:12]. The Worker checks
// only the tag; it never holds k_tok and never decrypts. The engine (Story 3.11) decodes.
//
// Routes (anything else, HEAD included, is 404 with an empty body):
//   POST /u/<token>  invalid -> 400; valid -> records "p:<token>" once in KV -> 200. Empty body.
//   GET  /u/<token>  a self-contained page whose button POSTs to the same URL. Never touches KV.
//   GET  /pending    every "p:" key, for the engine's poll, behind a bearer secret.

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{108}$/;
const KEY_TEXT_PATTERN = /^[A-Za-z0-9_-]{43}=?$/;
const BEARER_PATTERN = /^Bearer ([\x21-\x7e]+)$/i;
const RAW_SIZE = 81;
const AUTHED_SIZE = 69;
const TAG_SIZE = 12;
const KEY_SIZE = 32;
const MIN_BEARER_LENGTH = 43; // AD-15: 256 bits; a shorter configured secret counts as unset
const PENDING_PREFIX = "p:";
const PENDING_TTL_SECONDS = 2592000; // 30 days
const PENDING_FORMAT = "nextron-unsub-pending/1";

const UNSUBSCRIBED = "You're unsubscribed. You won't get further emails from this sender.";
const NOT_VALID = "This link isn't valid";
const NOSCRIPT_NOTE =
  "Pressing Unsubscribe records your opt-out, even if the next page is blank.";

// The page's only script and style. Their CSP hashes below are checked by the tests.
const PAGE_SCRIPT =
  'var f=document.getElementById("f"),m=document.getElementById("m");' +
  'f.addEventListener("submit",function(e){e.preventDefault();f.hidden=true;m.textContent="Working...";' +
  'fetch(location.href,{method:"POST",credentials:"omit",cache:"no-store"}).then(function(r){' +
  "if(!r.ok){throw new Error(String(r.status))}" +
  'm.textContent="' + UNSUBSCRIBED + '"' +
  '}).catch(function(){f.hidden=false;m.textContent="That didn\'t work. Please try again."})});';
const PAGE_STYLE =
  "body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;" +
  "background:#f6f7f9;color:#1d2330}" +
  "main{max-width:32rem;margin:4rem auto;padding:2rem 1.5rem;background:#fff;" +
  "border:1px solid #d9dde4;border-radius:8px}" +
  "h1{font-size:1.4rem;margin:0 0 1rem}p{line-height:1.5}" +
  "button{font:inherit;padding:.6rem 1.2rem;border:0;border-radius:6px;" +
  "background:#1d4ed8;color:#fff;cursor:pointer}" +
  "@media (prefers-color-scheme:dark){body{background:#14171c;color:#e6e8ec}" +
  "main{background:#1d2128;border-color:#2e333c}}";
const PAGE_SCRIPT_HASH = "sha256-rkS7SAmwY/siEmdNBcliH5t6gwNEyVPr2/soJBTUv/E=";
const PAGE_STYLE_HASH = "sha256-FMluAeE8SveG2wg+MGiPCz2Wp5nD3I/AYXMug1lRm6E=";

const COMMON_HEADERS = {
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};
const PAGE_CSP = [
  "default-src 'none'",
  `script-src '${PAGE_SCRIPT_HASH}'`,
  `style-src '${PAGE_STYLE_HASH}'`,
  "form-action 'self'",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
].join("; ");

function respond(status, body = null, extra = {}) {
  return new Response(body, { status, headers: { ...COMMON_HEADERS, ...extra } });
}

function page(valid) {
  const content = valid
    ? '<h1>Unsubscribe</h1><p id="m" role="status">Stop further emails from this sender.</p>' +
      '<form id="f" method="post"><button type="submit">Unsubscribe</button></form>' +
      `<noscript><p>${NOSCRIPT_NOTE}</p></noscript>` +
      `<script>${PAGE_SCRIPT}</script>`
    : `<h1>Unsubscribe</h1><p>${NOT_VALID}</p>`;
  const html =
    '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<meta name="robots" content="noindex">' +
    `<title>Unsubscribe</title><style>${PAGE_STYLE}</style></head>` +
    `<body><main>${content}</main></body></html>`;
  return respond(valid ? 200 : 400, html, {
    "Content-Type": "text/html; charset=utf-8",
    "Content-Security-Policy": PAGE_CSP,
  });
}

function fromBase64url(text) {
  const standard = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(standard + "=".repeat((4 - (standard.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function toBase64url(bytes) {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// The 32 bytes of a canonical base64url key (43 characters, or 44 ending in one "="), or null.
function parseKey(text) {
  if (typeof text !== "string" || !KEY_TEXT_PATTERN.test(text)) {
    return null;
  }
  const body = text.endsWith("=") ? text.slice(0, -1) : text;
  const raw = fromBase64url(body);
  if (raw.length !== KEY_SIZE || toBase64url(raw) !== body) {
    return null;
  }
  return raw;
}

// Constant time for equal lengths; lengths are never secret here.
function sameBytes(a, b) {
  if (a.length !== b.length) {
    return false;
  }
  let difference = 0;
  for (let i = 0; i < a.length; i += 1) {
    difference |= a[i] ^ b[i];
  }
  return difference === 0;
}

// Whether the token's tag verifies under K_EDGE_<kid>. It never throws.
async function tokenValid(token, env) {
  try {
    if (typeof token !== "string" || !TOKEN_PATTERN.test(token)) {
      return false;
    }
    const raw = fromBase64url(token);
    if (raw.length !== RAW_SIZE) {
      return false;
    }
    const kid = raw[0];
    if (kid < 1 || kid > 255) {
      return false;
    }
    const keyBytes = parseKey(env[`K_EDGE_${kid}`]);
    if (keyBytes === null) {
      return false;
    }
    const key = await crypto.subtle.importKey(
      "raw",
      keyBytes,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, raw.slice(0, AUTHED_SIZE)));
    return sameBytes(mac.slice(0, TAG_SIZE), raw.slice(AUTHED_SIZE));
  } catch {
    return false;
  }
}

function receivedAt() {
  return `${new Date().toISOString().slice(0, 19)}Z`; // UTC, whole seconds
}

async function recordOptOut(token, env) {
  if (!(await tokenValid(token, env))) {
    return respond(400);
  }
  const name = PENDING_PREFIX + token;
  if ((await env.UNSUB.get(name)) === null) {
    await env.UNSUB.put(name, "", {
      metadata: { received_at: receivedAt() },
      expirationTtl: PENDING_TTL_SECONDS,
    });
  }
  return respond(200);
}

async function digest(text) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

async function authorised(request, env) {
  const match = BEARER_PATTERN.exec(request.headers.get("authorization") ?? "");
  if (match === null) {
    return false;
  }
  const presented = await digest(match[1]);
  let accepted = false;
  for (const name of ["PENDING_BEARER", "PENDING_BEARER_NEXT"]) {
    const secret = env[name];
    if (typeof secret === "string" && secret.length >= MIN_BEARER_LENGTH) {
      // Every configured secret is compared, so timing never says which one matched.
      accepted = sameBytes(presented, await digest(secret)) || accepted;
    }
  }
  return accepted;
}

async function pending(request, env) {
  if (!(await authorised(request, env))) {
    return respond(401);
  }
  const entries = [];
  let cursor;
  for (;;) {
    const options = cursor === undefined ? { prefix: PENDING_PREFIX } : { prefix: PENDING_PREFIX, cursor };
    const listing = await env.UNSUB.list(options);
    for (const key of listing.keys) {
      const metadata = key.metadata;
      const at = metadata !== null && typeof metadata === "object" ? metadata.received_at : undefined;
      entries.push({
        token: key.name.slice(PENDING_PREFIX.length),
        received_at: typeof at === "string" ? at : null,
        expiration: typeof key.expiration === "number" ? key.expiration : null,
      });
    }
    if (listing.list_complete) {
      break;
    }
    if (typeof listing.cursor !== "string" || listing.cursor === "" || listing.cursor === cursor) {
      throw new Error("KV list gave no cursor before list_complete");
    }
    cursor = listing.cursor;
  }
  return respond(200, JSON.stringify({ format: PENDING_FORMAT, entries }), {
    "Content-Type": "application/json",
  });
}

async function route(request, env) {
  const { pathname } = new URL(request.url);
  const method = request.method;
  if (pathname === "/pending") {
    return method === "GET" ? pending(request, env) : respond(404);
  }
  const match = /^\/u\/([^/]+)$/.exec(pathname);
  if (match === null) {
    return respond(404);
  }
  const token = match[1];
  if (method === "POST") {
    return recordOptOut(token, env);
  }
  if (method === "GET") {
    return page(await tokenValid(token, env));
  }
  return respond(404);
}

export default {
  async fetch(request, env) {
    try {
      return await route(request, env);
    } catch {
      // A KV failure: an empty 500 (nothing logged), so a mail client or the poller retries.
      return respond(500);
    }
  },
};
