# Changelog

## 0.3.0

- Added the HTTPS pull-worker adapter.
- Added worker registration, worker heartbeat, atomic claim handling, package
  download, attempt heartbeats, result upload and failure reporting.
- Added safe ZIP extraction, HRX checksum verification and package-size limits.
- Added durable spool records and restart recovery without rerunning completed
  attempts.
- Added configurable parallel capacity and continuous/one-shot worker modes.
- Added `histra-worker` CLI and configuration for
  `https://histra.bonatte.cloud`.
- Preserved the network-independent `JobRunner` public API and existing scour
  mutation names/behaviour.
- Added network and end-to-end mocked worker tests.

## 0.2.0

- Added per-analysis foundation-interface scour mutation on the client side.

## 0.1.0

- Initial local job-runner package.
