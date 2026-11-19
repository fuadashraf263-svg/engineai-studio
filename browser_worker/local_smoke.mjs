#!/usr/bin/env node
// Exercises the running worker against EngineAI's own local Project Hub only.
const base = process.env.ENGINEAI_BROWSER_URL || "http://127.0.0.1:8105";
async function call(path, body) {
  const response = await fetch(base + path, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || JSON.stringify(data));
  return data;
}
let id;
try {
  const session = await call("/api/sessions", { url: "http://127.0.0.1:8103/", confirm_external_send: true });
  id = session.session_id;
  if (session.title !== "EngineAI Project Hub") throw new Error(`unexpected title: ${session.title}`);
  const shot = await call(`/api/sessions/${id}/action`, { action: "screenshot", confirm_external_send: true });
  if (!shot.artifact) throw new Error("screenshot artifact was not returned");
  console.log(`browser worker local smoke: PASSED (${session.title}; ${shot.artifact})`);
} catch (error) {
  console.error(`browser worker local smoke: FAILED: ${error.name}: ${error.message}`);
  process.exitCode = 1;
} finally {
  if (id) await call(`/api/sessions/${id}/close`, {}).catch(() => {});
}
