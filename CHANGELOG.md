# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Argus is currently pre-1.0 (0.x releases).

## [Unreleased]

### Added
- **Quick Links** — a configurable panel on the Dashboard tab to save and open frequently-used sites (icon glyph + label + https URL), persisted in the backend SQLite store. URLs are sanitized to https-only.

### Fixed
- Dev-mode CSP now includes `'unsafe-eval'` **in development only**, so `next dev`'s React Refresh runtime can hydrate client components (previously all dev-mode client hydration silently failed). Production and packaged builds keep the strict, eval-free policy. This also unblocks the Playwright e2e suite.
