let Defined;
globalThis.HTMLElement = class { constructor() { this.style = {}; } };
globalThis.customElements = { define: (_n, cls) => { Defined = cls; } };
globalThis.window = globalThis;
const quiet = console.info; console.info = () => {};
await import("./airsupply-card.js");
console.info = quiet;

let failures = 0;
const check = (what, got, want) => {
  const ok = got === want;
  if (!ok) { failures++; console.log("FAIL", what, "got", got, "want", want); } else console.log("ok  ", what);
};
const card = (config, owner, viewer) => {
  const c = Object.create(Defined.prototype);
  c._config = { entity: "sensor.x", nights: 14, goal: 4, only_owner: false, ...config };
  c._hass = {
    user: viewer ? { id: viewer } : undefined,
    states: { "sensor.x": { attributes: owner ? { person_user_id: owner } : {} } },
  };
  return c;
};

check("off by default", card({}, "alice", "bob")._hidden(), false);
check("hidden from someone else", card({ only_owner: true }, "alice", "bob")._hidden(), true);
check("shown to the owner", card({ only_owner: true }, "alice", "alice")._hidden(), false);
check("an unowned machine stays visible", card({ only_owner: true }, null, "bob")._hidden(), false);
check("no viewer, owned machine, stays hidden", card({ only_owner: true }, "alice", null)._hidden(), true);
process.exit(failures ? 1 : 0);
