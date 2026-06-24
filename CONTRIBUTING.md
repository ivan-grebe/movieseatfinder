# Contributing

Contributions are welcome. Before opening a pull request:

1. Create a branch from `main`.
2. Keep the app dependency-free unless a dependency provides a clear benefit.
3. Add or update tests for behavior changes.
4. Run the full test suite:

   ```bash
   python -m unittest discover -s tests -v
   ```

5. Confirm the app still starts with `python run.py`.

When reporting a bug, include the browser, operating system, relevant search
filters, and any server error output. Do not include private ticketing or
account information.
