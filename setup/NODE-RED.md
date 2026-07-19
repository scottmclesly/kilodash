# Wiring the Scottina Node-RED panel

The Scottina **Node-RED** screen is a thin front panel for a flow running on the
Pi. It shows **6 feedback fields** and **6 trigger buttons**. It talks to your
flow over two HTTP endpoints on `127.0.0.1:1880`:

| Direction | Endpoint | What it does |
|---|---|---|
| Scottina → reads | `GET /kilodash/state` | your flow returns up to 6 field labels+values and 6 button labels |
| Scottina → writes | `POST /kilodash/btn/1..6` | fired when you tap a panel button |

Scottina polls `/kilodash/state` about every 2 seconds and redraws.

## One-time setup

1. Open the Node-RED editor — on the panel, launch Node-RED and open the URL it
   shows (e.g. `http://<pi-ip>:1880`) from a laptop.
2. Menu (☰) → **Import** → paste [`nodered-kilodash-flow.json`](nodered-kilodash-flow.json)
   → **Import** → **Deploy**.
3. On Scottina, open the **Node-RED** tile. Within ~2s **Field 1** shows a live
   clock — that's the built-in demo proving the feedback path works. Delete the
   two demo nodes once you've seen it.

## How feedback fields work (the important part)

You do **not** wire a node into the panel directly. The imported **build state**
function answers `/kilodash/state` by reading **flow context**:

```
Field 1  ← flow.f1      label ← flow.f1_label  (defaults to "Field 1")
Field 2  ← flow.f2      label ← flow.f2_label
  …
Field 6  ← flow.f6      label ← flow.f6_label
```

(Flows imported before the 6-field panel only publish f1–f4 — that's fine,
fields 5–6 just show `—` until you write their context keys.)

So **to fill a field, write your value into that context key** from anywhere in
your flows. The panel picks it up on the next poll. Unset keys show `—`.

### Recipe: put a sensor/value into Field 2

```
[ your source node ] → [ change node ]
```

In the **change** node add two rules:

- **Set** `flow.f2_label`  to the string  `Temp`
- **Set** `flow.f2`        to  `msg.payload`   (type: *msg.*)

Deploy. Field 2 now reads "Temp" with your live value. Anything that produces a
`msg` works as the source: `inject`, `mqtt in`, `serial in`, a `function`, an
`exec` reading a script, etc.

### Recipe: live CPU temperature into Field 1

- `inject` (repeat every 5s) →
- `exec` node, command: `vcgencmd measure_temp` →
- `change` node:
  - Set `flow.f1_label` = `CPU °C`
  - Set `flow.f1` to a JSONata expr: `$substringBefore($substringAfter(payload,"="),"'")`

### Doing it from a function node instead

If you prefer code over a change node, write context directly:

```js
flow.set('f3_label', 'Battery');
flow.set('f3', msg.payload + ' V');
return null;   // nothing needs to reach the panel; it polls context
```

> Values are shown truncated to ~9 characters and labels to ~10, so keep them
> short (e.g. `21.4°C`, `OPEN`, `3/4`). Send strings or numbers, not objects.

## Wiring the buttons

Tapping a panel button sends `POST /kilodash/btn/N` (N = 1–6). The imported
**handle trigger** function receives it with `msg.req.params.n` = `"1"`…`"6"`.
Branch on it:

```js
const n = msg.req.params.n;
if (n === '1') return [{ payload: 'on' }, null, null, null];   // e.g. drive output 1
// ...
```

Give buttons custom names by setting `flow.b1_label` … `flow.b4_label` (same
pattern as fields). A button posts harmlessly (HTTP 404, panel toasts "no
handler") until its route exists — so the panel is safe to open before the flow
is finished.

## Test without the panel

From the Pi or any device on the LAN:

```sh
curl -s http://127.0.0.1:1880/kilodash/state | python3 -m json.tool
curl -s -X POST http://127.0.0.1:1880/kilodash/btn/1
```

The first should print your 6 fields + 6 button labels; the second triggers
button 1 (watch the Node-RED debug sidebar for the `node.warn`).

## Reference: the JSON contract

`GET /kilodash/state` must return:

```json
{
  "fields":  [ {"label": "Temp", "value": "21.4"}, ... up to 6 ],
  "buttons": [ {"label": "Fan"}, ... up to 6 ]
}
```

Fewer entries is fine — Scottina pads the rest with defaults.
