// Exercise the card's arithmetic with no browser: the night bucketing is the
// part that goes wrong silently, because a night spans midnight.
let Defined;
globalThis.HTMLElement = class {};
globalThis.customElements = { define: (_name, cls) => { Defined = cls; } };
globalThis.window = globalThis;
const quiet = console.info;
console.info = () => {};

await import("./airsupply-card.js");
console.info = quiet;

const card = Object.create(Defined.prototype);
card._config = { entity: "sensor.x", nights: 14, goal: 4 };

let failures = 0;
const check = (what, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { failures++; console.log("FAIL", what, "\n  got ", JSON.stringify(got), "\n  want", JSON.stringify(want)); }
  else console.log("ok  ", what);
};
const noonOn = (y, m, d) => new Date(y, m, d, 12, 0, 0, 0).getTime();

check("01:00 belongs to yesterday's night", card._nightStart(new Date(2026, 8, 26, 1, 0)), noonOn(2026, 8, 25));
check("23:00 belongs to tonight", card._nightStart(new Date(2026, 8, 25, 23, 0)), noonOn(2026, 8, 25));
check("noon exactly starts a new night", card._nightStart(new Date(2026, 8, 25, 12, 0)), noonOn(2026, 8, 25));
check("11:59 is still the night before", card._nightStart(new Date(2026, 8, 25, 11, 59)), noonOn(2026, 8, 24));

const hour = (y, m, d, h, extra) => ({ start: new Date(y, m, d, h).getTime(), ...extra });
const sleep = [
  hour(2026, 8, 25, 23, { change: 1 }),
  hour(2026, 8, 26, 0, { change: 1 }),
  hour(2026, 8, 26, 1, { change: 1 }),
  hour(2026, 8, 26, 2, { change: 1 }),
  hour(2026, 8, 26, 3, { change: 2.5 }),
];
const folded = card._fold(sleep);
const night = folded.find((n) => n.at === noonOn(2026, 8, 25));
check("a sleep across midnight is one night", night ? Math.round(night.hours * 10) / 10 : null, 6.5);
check("fourteen nights come back", folded.length, 14);
check("nights are in order", folded.every((n, i) => i === 0 || n.at > folded[i - 1].at), true);
check("a night with nothing is zero, not missing", folded.some((n) => n.hours === 0), true);

const old = card._fold([
  hour(2026, 8, 25, 23, { sum: 100 }),
  hour(2026, 8, 26, 0, { sum: 101 }),
  hour(2026, 8, 26, 1, { sum: 102.5 }),
]);
const oldNight = old.find((n) => n.at === noonOn(2026, 8, 25));
check("a running sum is differenced", oldNight ? Math.round(oldNight.hours * 10) / 10 : null, 2.5);

const reset = card._fold([
  hour(2026, 8, 25, 23, { change: 2 }),
  hour(2026, 8, 26, 1, { change: -900 }),
]);
const resetNight = reset.find((n) => n.at === noonOn(2026, 8, 25));
check("a meter reset does not make a negative night", resetNight ? resetNight.hours : null, 2);

process.exit(failures ? 1 : 0);
