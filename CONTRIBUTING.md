# Contributing

Contributions are welcome. Before opening a pull request:

1. Create a branch from `main`.
2. Install the application and test dependencies:

   ```bash
   pip install -e ".[test]"
   ```

3. Add or update tests for behavior changes.
4. Run the test suite:

   ```bash
   python -m unittest discover -s tests -v
   ```

5. Start the app with `uvicorn app:app --host 127.0.0.1 --port 4173` and
   verify the relevant browser flow.

When reporting a bug, include the browser, operating system, relevant search
filters, and server error output. Do not include private ticketing or account
information.
