// Reverse proxy so the `agentcore dev` chat UI works behind `tailscale serve`.
//
// The UI server only accepts requests whose Origin is http://localhost:<uiPort>,
// so a browser on https://<host>.ts.net gets 403s. This proxy accepts requests
// from the tailnet origin only, rewrites Origin/Host to localhost, and streams
// responses (SSE) back unchanged.
//
// The agent can run shell commands on this host, so access is limited to the
// Tailscale logins given with --allow-user. `tailscale serve` sets the
// Tailscale-User-Login header on proxied requests from user devices (and strips
// any client-supplied value); requests without an allowed login are rejected.
// Listen on loopback only, so nothing can reach the proxy except tailscale serve.
//
// Usage: node scripts/tailnet-proxy.mjs --origin https://<host>.ts.net --allow-user you@example.com
//          [--allow-user other@example.com] [--listen 8090] [--target 8081]

import http from "node:http";
import { parseArgs } from "node:util";

const { values: args } = parseArgs({
  options: {
    origin: { type: "string" },
    listen: { type: "string", default: "8090" },
    target: { type: "string", default: "8081" },
    "allow-user": { type: "string", multiple: true },
  },
});
if (!args.origin || !args["allow-user"]?.length) {
  console.error("--origin https://<host>.ts.net and at least one --allow-user are required");
  process.exit(1);
}
const allowedUsers = new Set(args["allow-user"].map((u) => u.toLowerCase()));
const allowedOrigin = args.origin.replace(/\/$/, "");
const targetHost = `localhost:${args.target}`;

const server = http.createServer((req, res) => {
  const login = String(req.headers["tailscale-user-login"] ?? "").toLowerCase();
  if (!allowedUsers.has(login)) {
    res.writeHead(403, { "Content-Type": "text/plain" }).end("Forbidden user");
    return;
  }
  const origin = req.headers.origin;
  if (origin && origin !== allowedOrigin) {
    res.writeHead(403, { "Content-Type": "text/plain" }).end("Forbidden origin");
    return;
  }
  const headers = { ...req.headers, host: targetHost };
  if (origin) headers.origin = `http://${targetHost}`;

  const upstream = http.request(
    { host: "127.0.0.1", port: args.target, method: req.method, path: req.url, headers },
    (up) => {
      const resHeaders = { ...up.headers };
      if (resHeaders["access-control-allow-origin"]) resHeaders["access-control-allow-origin"] = allowedOrigin;
      res.writeHead(up.statusCode ?? 502, resHeaders);
      up.pipe(res);
    },
  );
  upstream.on("error", (err) => {
    if (!res.headersSent) res.writeHead(502, { "Content-Type": "text/plain" });
    res.end(`Upstream error: ${err.message}`);
  });
  res.on("close", () => upstream.destroy());
  req.pipe(upstream);
});

server.listen(Number(args.listen), "127.0.0.1", () => {
  console.log(
    `Proxying http://127.0.0.1:${args.listen} -> http://${targetHost} for ${allowedOrigin}, users: ${[...allowedUsers].join(", ")}`,
  );
});
