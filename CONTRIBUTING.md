# Contributing to SearchRelay

Thanks for helping out. SearchRelay is a small project with a large surface
area, so focused pull requests are much easier to review than large ones.

## Code of conduct

Everyone participating in this project is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- **Report a bug.** Open an issue with the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md)
  and include the exact request, the response you got, and the version or
  commit you ran.
- **Pick up a task.** The [good first issues](docs/good-first-issues.md) page
  lists ten scoped tasks written as issue text.
- **Add a provider.** A new `SearchProvider` adapter is a self-contained
  contribution. See [Providers](docs/providers.md) and
  `app/providers/adapters.py`.
- **Improve the docs.** The documentation site is MkDocs Material and builds in
  strict mode, so a broken link fails the build.
- **Improve the agent story.** SDKs, the MCP server, the LangChain tool, and
  the LlamaIndex reader all live under `clients/`.

## Development setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pip install -e clients/python
```

The test suite mocks upstream HTTP responses, so you do not need internet
access, a SearXNG instance, or provider credentials to run it.

## Required checks

Run all of these before opening a pull request:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python -m compileall -q app clients/python examples
.venv/bin/python scripts/export_openapi.py /tmp/openapi.json
diff <(cat /tmp/openapi.json) openapi.json   # must be empty
NO_MKDOCS_2_WARNING=1 .venv/bin/mkdocs build --strict --site-dir /tmp/site
(cd clients/typescript && npm test)
(cd clients/go && go test ./...)
```

CI runs the same set, plus a Docker build. If you changed the API contract,
regenerate `openapi.json` with `scripts/export_openapi.py` and commit it — the
repository fails CI when the committed document is stale.

## Coding conventions

- Python is typed. Public functions have annotations, and `mypy` runs over
  `app`, `scripts`, `examples`, `clients/python`, and `benchmarks`.
- Keep lines under 100 characters; `ruff` enforces the style rules.
- Comments should explain *why*, not restate the code.
- Never weaken an assertion or skip a test to make a check pass. If a test
  encodes the wrong behavior, change the behavior deliberately and explain it
  in the pull request.
- Keep the public contract stable. The DeepSeek-compatible field names, the
  error envelope, and the billing rules are compatibility promises; changing
  them is a major version, not a patch.
- Anything that is stubbed, experimental, or unverified must say so in the
  code and in the documentation. Do not publish a claim you have not measured.

## Commit messages

The project uses [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<optional scope>): <short summary>
```

Types in use: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`,
`chore`, `perf`. Use `!` and a `BREAKING CHANGE:` paragraph when a change
breaks compatibility, for example the environment-prefix rename in v0.1.0.

## Pull requests

1. Open an issue first for anything larger than a bug fix, so the approach can
   be discussed before you write the code.
2. Keep the pull request focused on one change.
3. Describe what you changed, why, and how you verified it. Paste the real
   command output when you claim something works.
4. Note any documentation that needs to follow the change — the README, the
   API reference, and the configuration tables tend to move together.
5. Update `CHANGELOG.md` under the "Unreleased" section.

## Versioning

SearchRelay uses [semantic versioning](https://semver.org/):

- **PATCH** (`0.1.1`) — bug fixes that keep the contract and the behavior.
- **MINOR** (`0.2.0`) — new features that are backwards compatible, including
  new providers, endpoints, and configuration options.
- **MAJOR** (`1.0.0`) — breaking changes to the HTTP contract, the error
  envelope, the billing rules, or the configuration interface.

While the project is `0.x`, breaking changes may appear in a minor release.
They are always called out in `CHANGELOG.md` and in the release notes, and
deprecated configuration keeps working for at least one minor release when a
replacement exists.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow
[SECURITY.md](SECURITY.md) instead.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
