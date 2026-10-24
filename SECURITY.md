# Security reports

Please do not post private site addresses, heartbeat ping URLs, or diagnostics that
have not been reviewed in a public issue. Diagnostics redact the site's host name and
the heartbeat URL, but check the file before attaching it.

For a vulnerability, contact the repository owner privately through GitHub before
public disclosure.

This integration is read-only by design: it sends GET requests to the sites you list
and, if configured, a heartbeat request to the URL you provide. Any other outbound
request, or any change made to a monitored site, should be treated as a critical
defect.
