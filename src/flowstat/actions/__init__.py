"""B6 step action library. Layer 6.

Thin adapters around components: xlsx.read/write, csv, http.call, email.send, db.write,
stats.aggregate, statguard.validate. Each action implements engine.ports.Action and declares
its signature and whether it honours an idempotency key.

Built from I2. Third-party imports allowed here (openpyxl, pandas, httpx).
"""
