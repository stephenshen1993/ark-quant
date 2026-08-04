---
name: run-tests
description: Run the full test suite and surface any failures. Use when verifying changes haven't broken anything.
---

Run the test suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Report back:
- Total tests run, passed, failed, errored
- Full traceback for any failures or errors
- A one-line summary: "All X tests passed" or "X failed, Y errored — see above"

If there are no test files yet in `tests/`, note that and suggest adding tests for any new behavior before relying on it.
