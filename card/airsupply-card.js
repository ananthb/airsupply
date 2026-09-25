/*
 * airsupply-card -- a night's sleep, fourteen of them at a time.
 *
 * The interesting number about a CPAP is not what it is doing now; it is how
 * many hours it ran last night, and the night before. Home Assistant already
 * has that: therapy hours is a total_increasing sensor, so the recorder keeps
 * the increase per hour, and a night is the increase between one midday and
 * the next.
 *
 * Midday to midday rather than midnight to midnight, because a night spans
 * midnight and calendar days would cut every one of them in half.
 *
 * No build step and no dependencies: one ES module, which is what Home
 * Assistant loads. Colours are Home Assistant's own custom properties -- a
 * card that ships its own palette fights whatever theme it lands in.
 */

const GOAL_HOURS = 4;
const NIGHTS = 14;
const NOON = 12;

// Four hours a night is the number everything clinical is measured against.
const DAY = 86400000;

class AirsupplyCard extends HTMLElement {
  static getStubConfig(hass) {
    const guess = Object.keys(hass?.states || {}).find(
      (id) => id.startsWith("sensor.airsupply_") && id.endsWith("_therapy_hours"),
    );
    return { entity: guess || "sensor.airsupply_therapy_hours" };
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("airsupply-card needs an entity: the therapy hours sensor.");
    }
    this._config = {
      nights: NIGHTS,
      goal: GOAL_HOURS,
      only_owner: false,
      ...config,
    };
    this._nights = null;
    this._fetchedFor = null;
    this._problem = null;
  }

  getCardSize() {
    return 4;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._hidden()) {
      this.style.display = "none";
      return;
    }
    this.style.display = "";
    this._load();
    this._render();
  }

  /* Hidden unless the viewer is the machine's owner.
   *
   * The owner's user id rides on every entity, so the card follows the
   * assignment rather than hard-coded ids. Unassigned stays visible.
   */
  _hidden() {
    if (!this._config?.only_owner) return false;
    const owner = this._hass?.states?.[this._config.entity]?.attributes?.person_user_id;
    // A machine nobody owns is not somebody else's, so it stays visible.
    if (!owner) return false;
    return this._hass?.user?.id !== owner;
  }

  /* --- the nights ------------------------------------------------------- */

  async _load() {
    const key = `${this._config.entity}|${this._config.nights}|${this._bucketOfNow()}`;
    if (this._fetchedFor === key) return;
    this._fetchedFor = key;

    const start = new Date(this._nightStart(new Date()));
    start.setTime(start.getTime() - (this._config.nights - 1) * DAY);

    try {
      const answer = await this._hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: start.toISOString(),
        statistic_ids: [this._config.entity],
        period: "hour",
        types: ["change", "sum"],
      });
      this._nights = this._fold(answer?.[this._config.entity] || []);
      this._problem = null;
    } catch (err) {
      this._problem = err?.message || "No history for that sensor.";
      this._nights = null;
    }
    this._render();
  }

  /* Statistics are refetched once an hour, not on every state change. */
  _bucketOfNow() {
    return Math.floor(Date.now() / 3600000);
  }

  /* The midday a night belongs to: a sleep at 01:00 belongs to yesterday. */
  _nightStart(when) {
    const noon = new Date(when);
    noon.setHours(NOON, 0, 0, 0);
    if (when.getTime() < noon.getTime()) noon.setTime(noon.getTime() - DAY);
    return noon.getTime();
  }

  _fold(rows) {
    const buckets = new Map();
    let previous = null;

    for (const row of rows) {
      const at = new Date(typeof row.start === "number" ? row.start : Date.parse(row.start));
      // `change` is what we want and what modern Home Assistant sends. Older
      // recorders answer with a running `sum` only, so difference it.
      let hours = typeof row.change === "number" ? row.change : null;
      if (hours === null && typeof row.sum === "number") {
        hours = previous === null ? 0 : row.sum - previous;
        previous = row.sum;
      }
      if (hours === null || hours < 0) continue;

      const night = this._nightStart(at);
      buckets.set(night, (buckets.get(night) || 0) + hours);
    }

    const latest = this._nightStart(new Date());
    const out = [];
    for (let i = this._config.nights - 1; i >= 0; i -= 1) {
      const at = latest - i * DAY;
      out.push({ at, hours: buckets.get(at) || 0 });
    }
    return out;
  }

  /* --- the machine's other entities ------------------------------------- */

  _sibling(suffix) {
    const id = this._config.entity;
    const stem = id.slice(id.indexOf(".") + 1).replace(/_therapy_hours$/, "");
    for (const domain of ["sensor", "binary_sensor"]) {
      const found = this._hass.states[`${domain}.${stem}_${suffix}`];
      if (found) return found;
    }
    return null;
  }

  _title() {
    if (this._config.title) return this._config.title;
    const own = this._hass.states[this._config.entity];
    const name = own?.attributes?.friendly_name || "";
    return name.replace(/\s*therapy hours$/i, "") || "Therapy";
  }

  /* --- drawing ---------------------------------------------------------- */

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
      this._root.innerHTML = `<ha-card><div class="wrap"></div></ha-card><style>${STYLE}</style>`;
    }
    const wrap = this._root.querySelector(".wrap");
    const missing = !this._hass.states[this._config.entity];

    if (missing) {
      wrap.innerHTML = `<p class="problem">${escape(this._config.entity)} does not exist.</p>`;
      return;
    }

    wrap.innerHTML = [
      this._head(),
      this._problem ? `<p class="problem">${escape(this._problem)}</p>` : this._chart(),
      this._foot(),
    ].join("");
  }

  _head() {
    const status = this._sibling("status");
    const therapy = this._sibling("in_therapy");
    const connected = this._sibling("connected");

    const running = therapy?.state === "on";
    const live = connected ? connected.state === "on" : true;
    const lamp = running ? "running" : live ? "idle" : "gone";
    const words = running
      ? "In therapy"
      : status
        ? status.state
        : live
          ? "Idle"
          : "Disconnected";

    return `<header>
      <h2>${escape(this._title())}</h2>
      <span class="state"><i class="lamp ${lamp}"></i>${escape(words)}</span>
    </header>`;
  }

  _chart() {
    const nights = this._nights;
    if (!nights) return `<p class="problem">Reading the last fortnight&hellip;</p>`;

    const goal = this._config.goal;
    const top = Math.max(goal + 1, Math.ceil(Math.max(...nights.map((n) => n.hours))));
    const W = 100;
    const H = 46;
    const step = W / nights.length;
    // A 2px gap between bars, in the same units as everything else.
    const gap = Math.min(step * 0.25, 1.6);
    const width = step - gap;

    const y = (hours) => H - (Math.min(hours, top) / top) * H;
    const bars = nights
      .map((night, i) => {
        const x = i * step + gap / 2;
        const height = H - y(night.hours);
        const short = night.hours + 0.0001 < goal;
        const label = `${dayName(night.at)} ${clock(night.hours)}`;
        if (height <= 0) {
          return `<line class="empty" x1="${x + width / 2}" y1="${H}" x2="${x + width / 2}" y2="${H - 0.6}"><title>${label}</title></line>`;
        }
        return `<path class="bar ${short ? "short" : ""}" d="${capped(x, y(night.hours), width, height)}"><title>${label}</title></path>`;
      })
      .join("");

    const line = y(goal);
    return `<div class="chart">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
           aria-label="Hours of therapy on each of the last ${nights.length} nights">
        <line class="goal" x1="0" y1="${line}" x2="${W}" y2="${line}"></line>
        ${bars}
      </svg>
      <span class="goal-label" style="bottom: calc(${(1 - line / H) * 100}% - 0.55em)">${goal} h</span>
    </div>
    <div class="scale">${nights.map((n, i) => tick(n, i, nights.length)).join("")}</div>`;
  }

  _foot() {
    const nights = this._nights;
    if (!nights) return "";
    const goal = this._config.goal;
    const met = nights.filter((n) => n.hours + 0.0001 >= goal).length;
    const last = nights[nights.length - 1];
    const used = this._sibling("last_used");

    return `<footer>
      <span class="big">${clock(last.hours)}</span>
      <span class="since">${last.hours > 0 ? "last night" : "nothing last night"}</span>
      <span class="tally">${met} of ${nights.length} nights over ${goal} h</span>
      ${used && used.state !== "unknown" ? `<span class="tally">last used ${escape(shortDate(used.state))}</span>` : ""}
    </footer>`;
  }
}

/* A bar with its data end rounded and its base square on the axis. */
function capped(x, y, w, h) {
  const r = Math.min(1.2, w / 2, h);
  return [
    `M${x} ${y + h}`,
    `V${y + r}`,
    `A${r} ${r} 0 0 1 ${x + r} ${y}`,
    `H${x + w - r}`,
    `A${r} ${r} 0 0 1 ${x + w} ${y + r}`,
    `V${y + h}`,
    "Z",
  ].join(" ");
}

function tick(night, i, total) {
  // A label under every bar is a smear at this width; the ends and the middle
  // are enough to read the axis by.
  const show = i === 0 || i === total - 1 || i === Math.floor(total / 2);
  return `<span>${show ? dayName(night.at) : ""}</span>`;
}

function dayName(at) {
  return new Date(at).toLocaleDateString(undefined, { weekday: "short" });
}

function shortDate(iso) {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function clock(hours) {
  if (!hours) return "0 h";
  const whole = Math.floor(hours);
  const minutes = Math.round((hours - whole) * 60);
  if (minutes === 60) return `${whole + 1} h`;
  return minutes ? `${whole} h ${minutes} m` : `${whole} h`;
}

function escape(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

const STYLE = `
  ha-card { padding: 16px; }
  .wrap { display: flex; flex-direction: column; gap: 10px; }
  header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
  h2 { margin: 0; font-size: 16px; font-weight: 500; color: var(--primary-text-color);
       overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .state { display: inline-flex; align-items: center; gap: 6px; flex: none;
           font-size: 13px; color: var(--secondary-text-color); }
  .lamp { width: 7px; height: 7px; border-radius: 50%; background: var(--disabled-text-color, #999); }
  .lamp.running { background: var(--success-color, var(--primary-color)); }
  .lamp.idle { background: var(--primary-color); opacity: .5; }

  .chart { position: relative; }
  svg { display: block; width: 100%; height: 108px; overflow: visible; }
  .bar { fill: var(--primary-color); }
  .bar.short { fill: var(--warning-color, var(--primary-color)); opacity: .55; }
  .empty { stroke: var(--divider-color, #ccc); stroke-width: .6; stroke-linecap: round; }
  .goal { stroke: var(--secondary-text-color); stroke-width: .4; stroke-dasharray: 1.5 1.5; opacity: .6; }
  .goal-label { position: absolute; right: 0; font-size: 11px; color: var(--secondary-text-color);
                background: var(--card-background-color, var(--ha-card-background)); padding: 0 3px; }

  .scale { display: flex; margin-top: 2px; }
  .scale span { flex: 1; text-align: center; font-size: 11px; color: var(--secondary-text-color); }

  footer { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; }
  .big { font-size: 24px; font-weight: 500; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .since, .tally { font-size: 12.5px; color: var(--secondary-text-color); }
  .tally { margin-left: auto; }
  .problem { margin: 0; font-size: 13px; color: var(--error-color, var(--secondary-text-color)); }
`;

customElements.define("airsupply-card", AirsupplyCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "airsupply-card",
  name: "airsupply",
  description: "Hours of CPAP therapy each night, against the four-hour mark.",
  preview: true,
  documentationURL: "https://github.com/ananthb/airsupply",
});

console.info("%c airsupply-card ", "background:#0d7f74;color:#fff;border-radius:3px", "loaded");
