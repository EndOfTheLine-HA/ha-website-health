# Website Health for Home Assistant

**Is it down, or is it me?**

Website Health checks that a website really works, not just that it answers. On a
schedule it fetches the pages and files you list, confirms each one returns success
**and** contains the text (or JSON keys) you say it must, and turns the result into
Home Assistant entities.

When checks fail, it first asks two unrelated hosts whether your own internet works.
If they cannot be reached either, the status reads **My internet is down** instead of
**Down**, so an outage at home is never reported as your site being down.

## Features

- UI configuration; no YAML.
- One entry per website, with as many checks as you like.
- **Page checks:** success code plus required text in the HTML the server sends.
  Catches the page that loads while the part that matters is missing.
- **JSON checks:** success code, valid JSON, and required top-level keys.
- **Internet check** before any failure is blamed on the site.
- Retries within a sweep, plus an optional "failed sweeps before Down" threshold for
  sites with occasional blips.
- Optional **build info**: reads a JSON file such as `/build-info.json` and shows which
  build is live.
- Optional **heartbeat ping** to a dead-man's switch such as healthchecks.io, so you
  hear about it when Home Assistant itself stops checking.
- A notification blueprint for down, recovered, internet down and a daily summary.
- Read-only: GET requests only (plus the optional heartbeat POST), no actions, nothing
  that changes your site.

## Install with HACS

1. Open HACS.
2. Add `https://github.com/EndOfTheLine-HA/ha-website-health` as a custom repository
   of type **Integration**.
3. Install **Website Health**.
4. Restart Home Assistant.
5. Go to **Settings > Devices & services > Add integration** and search for
   **Website Health**.

Home Assistant **2026.10.2 or newer** is required. Inclusion in the default HACS
catalog is separate.

## Setting up a website

1. Enter the site's address without a path, for example `https://example.org`, and
   optionally a name. If the site is down today, tick **Add even if it does not answer
   now**.
2. Add the first check: a name, a path such as `/`, and its type. The next screen asks
   for the required text (for a page) or the required keys (for a JSON file).

After a site is added or Home Assistant starts, its entities stay unavailable for the
few seconds the first sweep takes. It runs in the background, so a slow or unreachable
site never holds up Home Assistant's startup.

Everything else is in the integration's **Configure** menu:

| Menu item | What it sets | Default |
|---|---|---|
| Add / Edit / Remove checks | Path, name, type (page or JSON file), required text or keys | |
| Timing | Interval, request timeout, retries, retry delay, failed sweeps before Down | 10 min, 10 s, 1, 5 s, 1 |
| Build info | Path to a JSON file and the keys to show | off |
| Internet check | Control addresses | `https://example.com/`, `https://connectivitycheck.gstatic.com/generate_204` |
| Advanced | User agent, TLS verification, heartbeat ping URL | built-in user agent, on, off |

### Choosing required text

Required text is plain, case-sensitive substring matching against the HTML the server
sends. Pick one structural phrase (a heading) and one that proves the important part
rendered (a button label or form field). Keep the list short: every phrase you add is
one more thing a copy edit can break.

**Pages built entirely in the browser** (single-page apps that render with JavaScript)
send only a small HTML shell. Website Health does not run JavaScript, so choose text
that is in the shell, or check a server-rendered page or a JSON endpoint instead.

### Choosing control addresses

The internet check only works if the control hosts share nothing with your site. Do
not use your site's host, its CDN, or its DNS provider: an outage there would then look
like a home-network fault and hide a real site outage. Avoid DNS-over-HTTPS resolvers
such as dns.google too: Pi-hole, AdGuard and many firewalls block them, and a blocked
control host makes every site failure look like an internet outage.

## Entities

For a website named "Example":

| Entity | State | Use it for |
|---|---|---|
| `sensor.example_status` | OK, Down, My internet is down | Dashboards. Attributes list the failing checks and the last internet check. |
| `binary_sensor.example_problem` | On when the site is Down | **Automations.** Unavailable while your internet is down, so it never fires for a home outage. |
| `binary_sensor.example_<check name>` | On when that check fails | Seeing which page broke. Attributes: path, HTTP status, failure kind, detail, size, response time, whether a retry was needed. |
| `sensor.example_response_time` | Milliseconds for the first check | History graphs. |
| `sensor.example_published_build` | First build-info key | Matching an outage to a deploy. Only when build info is set up. |
| `sensor.example_last_checked` | Time of the last sweep | Diagnostics. |

Entity IDs follow Home Assistant's own naming. If the device is in an area, checks
added later can include the area in their IDs (for example
`binary_sensor.office_example_home`); look them up on the device page.

To run a sweep immediately, call `homeassistant.update_entity` on any of these
entities. A broken build-info file never changes the status; it shows as a
`build_info_warning` attribute on the status sensor instead.

## How a sweep works

1. Every check is fetched in order, with retries for failures.
2. If all pass, the status is OK and nothing else is contacted.
3. If any fail, the control addresses are fetched. If at least one answers, the
   failures belong to the site and count as a failed sweep. If none answer, the site's
   homepage is tried once more: if it answers, the controls were wrong and the failures
   count; if not, the status is **My internet is down**.
4. After the configured number of failed sweeps in a row, the status becomes **Down**.

The internet check result is shared between websites for 60 seconds, so several sites
failing at once check the control hosts once.

## Notifications

Import the blueprint, then create an automation from it:

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FEndOfTheLine-HA%2Fha-website-health%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fwebsite_health_notify.yaml)

It notifies on Down (with the failing checks) and on recovery, and can also tell you
when your internet is down and send a daily summary. An
[example dashboard](examples/dashboard.yaml) is included; its entity IDs are
placeholders.

**When it does not notify.** Restarting Home Assistant, or saving any Website Health
option, reloads the entities: they are briefly unavailable and then take their new
state. The blueprint ignores changes that come from unavailable, so a restart never
sends a false "down" or "back" message. Two consequences:

- Editing a check so that it fails does not send a notification. To test the
  blueprint, set the status sensor's state by hand in **Developer tools > States**;
  the next sweep puts the real value back.
- If the site goes down at the same moment Home Assistant restarts, the first state
  after the restart is already Down, so no message is sent until it recovers and goes
  down again. The heartbeat ping (under **Advanced**) covers this from outside.

## Analytics and privacy

- Every check is a real page view, so it appears in most analytics tools. Each request
  sends the user agent from **Advanced** (by default
  `HomeAssistant-WebsiteHealth/<version>`); filter it out there.
- Response bodies are only searched. They are never stored, logged or put in
  attributes.
- The heartbeat URL is treated as a secret: it is not logged and is redacted from
  diagnostics. Diagnostics also remove the site's host name and check query strings.

## What it does not do

No JavaScript rendering, logins, custom headers, POST checks, regex matching, link
crawling, or checks from more than one location. It never restarts, purges or changes
anything. For certificate expiry, use the core Certificate Expiry integration; for the
health of the machine Home Assistant runs on, use System Monitor.

## Release status

Version 0.1.0 is the initial release. See [AUDIT.md](AUDIT.md) for what is verified
and what is not, and the [v0.1.0 release notes](docs/releases/v0.1.0.md).

## License

MIT
