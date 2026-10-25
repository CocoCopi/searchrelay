## What this changes

<!-- One or two sentences. What is different after this pull request? -->

## Why

<!-- The problem this solves, or the issue it closes. -->

Closes #

## How it was verified

<!-- Paste the real output of the checks you ran. Do not claim something works
     that you did not run. -->

```text
```

## Checklist

- [ ] `.venv/bin/pytest` passes
- [ ] `.venv/bin/ruff check .` passes
- [ ] `.venv/bin/mypy` passes
- [ ] The documentation site builds: `mkdocs build --strict`
- [ ] `openapi.json` is regenerated and committed if the API contract changed
- [ ] `CHANGELOG.md` is updated under "Unreleased"
- [ ] The public contract is unchanged, or the change is called out as
      breaking with a `BREAKING CHANGE:` note
- [ ] Anything experimental, stubbed, or unverified is labeled as such in the
      code and the documentation
