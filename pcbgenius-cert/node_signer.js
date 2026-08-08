#!/usr/bin/env node
/*
 * PCBGenius E1 — local cryptographic signer (node-crypto backend).
 * =================================================================
 * Called as a subprocess by pcbgenius-cert/sign.py and verify.py when a node
 * runtime is available. Implements the Ed25519 operations the certificate
 * needs. The Python side treats this script as an *external signer* (call
 * sites are marked `[E1 ext:node]`), exactly as the E1 spec allows
 * ("sign the record with a local key via node crypto/minisign").
 *
 * Protocol: read a single JSON object from stdin, emit a single JSON object
 * on stdout.
 *
 *   { "command": "keygen" } -> { "privatePem":..., "publicPem":... }
 *   { "command": "sign",
 *     "privatePem": "...",
 *     "message": "<utf8 hex of record_digest>" } -> { "signature": "base64..." }
 *   { "command": "verify",
 *     "publicPem": "...",
 *     "message": "<utf8 hex>",
 *     "signature": "base64..." } -> { "ok": bool }
 *
 * All keys are Ed25519 and stay local — nothing touches the network.
 */
"use strict";

const crypto = require("crypto");

function keygen() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  return {
    privatePem: privateKey.export({ type: "pkcs8", format: "pem" }),
    publicPem: publicKey.export({ type: "spki", format: "pem" }),
  };
}

function sign(payload) {
  const privateKey = crypto.createPrivateKey(payload.privatePem);
  const signature = crypto.sign(null, Buffer.from(payload.message, "hex"), privateKey);
  return { signature: signature.toString("base64") };
}

function verify(payload) {
  const publicKey = crypto.createPublicKey(payload.publicPem);
  const ok = crypto.verify(
    null,
    Buffer.from(payload.message, "hex"),
    publicKey,
    Buffer.from(payload.signature, "base64")
  );
  return { ok };
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", () => {
  let req;
  try {
    req = JSON.parse(input);
  } catch (e) {
    console.error(JSON.stringify({ error: "bad json: " + e.message }));
    process.exit(2);
  }
  try {
    let out;
    switch (req.command) {
      case "keygen": out = keygen(); break;
      case "sign":   out = sign(req); break;
      case "verify": out = verify(req); break;
      default:
        console.error(JSON.stringify({ error: "unknown command: " + req.command }));
        process.exit(2);
    }
    process.stdout.write(JSON.stringify(out));
  } catch (e) {
    console.error(JSON.stringify({ error: e && e.message ? e.message : String(e) }));
    process.exit(1);
  }
});