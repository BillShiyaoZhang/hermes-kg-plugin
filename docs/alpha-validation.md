# Alpha implementation verification

Verified on 2026-10-23 using Linux aarch64 containers on Docker Desktop. All
profile and graph fixtures were synthetic and isolated from the owner's Hermes
profile. The development workbench runs at `http://127.0.0.1:8765`.

| Check | Result |
|---|---|
| Python regression suite, Neo4j and prepared real E5 cache enabled | 235 passed, no skips |
| Frontend unit tests | 14 passed |
| TypeScript and Vite production build | Passed |
| Browser workflow | Authentication, correction, archive exclusion, restore, related memories, stale-version rejection and mobile layout passed; no page errors |
| Copied profile installation | Installer, private setup, doctor, bundled UI and legacy read-only cron passed from an unrelated working directory |
| Standalone packaging | Wheel, editable installation and sdist-to-wheel passed; packaged HTML/API and runner worked outside the source checkout |
| Selected-profile model routing | Real Hermes auxiliary client and OpenAI SDK called one temporary loopback mock endpoint using the synthetic selected profile's model and dotenv credential |
| Restore and protected corrections | Transaction, interruption/replay, source withdrawal, namespace isolation and revision regression cases passed |

The dependency lock is in `requirements-console.lock`. The supported Hermes core
revision is `cdceca42e107f0e51ab9ff50e1cc881ad76b577e`. The model is pinned by full
revision in `kg.embedding`; search does not download it implicitly.

## Reproduce

Build the development environment and prepare its local model explicitly first:

```sh
docker compose up --build -d
```

Then run the suite with the read-only core checkout and prepared model volume:

```sh
HERMES_AGENT_HOME=/path/to/hermes-agent \
docker compose -f compose.yaml -f compose.test.yaml --profile tools run --rm \
  -e KG_SEARCH_MODEL_CACHE=/data/models \
  -v hermes-kg-dev_console-data:/data:ro tests
```

Without the model environment variable the two real-model checks are explicitly
skipped; no test downloads the model. PowerShell users should set
`$env:HERMES_AGENT_HOME` before the Compose command. Frontend checks are
`npm test` and `npm run build` inside `console/`.

The selected-profile SDK integration is `test_hermes_route.py`. It uses a clean
subprocess, a temporary profile and a local HTTP fixture; it blocks external
network access and real user-profile reads. Browser acceptance is reproducible
with `node scripts/browser_smoke.cjs` when Playwright is available. It refuses
to mutate a workbench that is not explicitly marked as the synthetic demo.

## Retrieval evidence and limits

The authored 50-question fixture has 48 positive questions and two exclusion
checks. Keyword Top-5 hit rate was 28/48; real local hybrid retrieval was 47/48,
with MRR 0.9375. Neither run returned a forbidden archived/disabled card. The
hybrid miss was question q21 about why product investment should not expand
immediately. This small synthetic fixture does not establish real-user accuracy
or reliable interpretation of negation and intent.

The separate [10,000-memory performance run](search-performance-alpha.md) measured
warm-search P95 at 505.211 ms with a combined 4 CPU / 8 GiB container ceiling. Its
reused passage vectors and empty relationship graph make it a performance check,
not a semantic-quality or graph-fan-out benchmark.

Native Windows and macOS release execution, live external-model behavior,
personal-profile migration and the planned user pilot remain release gates.
CI definitions exist; no hosted CI result or completed pilot is claimed here.
