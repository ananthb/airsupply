// Wires the Elm ports to the add-on's HTTP API.
import { Api } from "./api.js";

const app = window.Elm.Main.init({ node: document.getElementById("app") });
const api = new Api((event) => app.ports.fromAddon.send(event));

app.ports.toAddon.subscribe((intent) => api.act(intent));

api.start();
