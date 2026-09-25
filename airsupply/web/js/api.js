// The add-on's HTTP API, and the polling that keeps the page current.
//
// Every path here is relative. The page is served through Home Assistant's
// ingress, which proxies a per-session prefix down to "/" in the add-on, so a
// leading slash would escape that prefix and 404.
//
// Both a poll and an action answer with the whole state, so there is one shape
// to decode and the page never has to merge a partial update into what it
// already has.

const IDLE_MS = 4000;
const BUSY_MS = 1000;

export class Api {
  constructor(onState) {
    this.onState = onState;
    this.timer = null;
    this.inFlight = false;
    this.busy = false;
  }

  start() {
    this.poll();
  }

  // Poll at a pace that matches what is happening: once every four seconds
  // while the machine is idle, once a second while something is running, so a
  // scan or a pairing question appears without a wait worth noticing.
  schedule() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.poll(), this.busy ? BUSY_MS : IDLE_MS);
  }

  async poll() {
    if (this.inFlight) return;
    this.inFlight = true;
    try {
      const res = await fetch("api/state", { headers: { Accept: "application/json" } });
      this.deliver(await res.json());
    } catch (err) {
      this.onState({ kind: "unreachable", message: String(err && err.message ? err.message : err) });
    } finally {
      this.inFlight = false;
      this.schedule();
    }
  }

  async act(intent) {
    const { kind, ...body } = intent;
    clearTimeout(this.timer);
    try {
      const res = await fetch("api/" + kind, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      this.deliver(await res.json());
    } catch (err) {
      this.onState({ kind: "unreachable", message: String(err && err.message ? err.message : err) });
    } finally {
      this.schedule();
    }
  }

  deliver(state) {
    this.busy = Boolean(state.busy || state.scanning || state.question);
    this.onState({ kind: "state", state });
  }
}
