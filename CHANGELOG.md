# Changelog

## 0.1.0

- Initial release.
- Page checks (success code plus required text) and JSON checks (valid JSON plus
  required keys), configured from the UI with add, edit and remove.
- Internet check before failures are blamed on the site, with a homepage override so
  two bad control hosts cannot hide a real outage.
- Status, Problem, per-check, response time, last checked and optional published build
  entities.
- Retries, failed-sweeps threshold, optional heartbeat ping, and a notification
  blueprint.
