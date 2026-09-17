## What

- add a FastHTML web application for `/templates` and public `/u/{token}` track records
- preserve the existing visual system by serving the established stylesheet with browser-safe font imports
- render public pages without HTMX, Surreal, inline handlers, or any other JavaScript
- add Python web lint and test coverage to CI

## Verification

- `uv run --project apps/web ruff check apps/web/polytrade_web apps/web/tests_py`
- `uv run --project apps/web pytest -q apps/web/tests_py`
- browser sweep at 1440px and 390px: zero script elements and zero horizontal overflow

## Notes

- stacked on #45; merge #44, then #45, then this PR
- the existing React entrypoint remains available until the authenticated pages are migrated and traffic can switch atomically
