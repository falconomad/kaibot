# AllInvestView logo-search answers (tests/test_fetch_symbol_meta.py)

One file per query, `GET https://www.allinvestview.com/api/logo-search/?q=<SYMBOL>`, in the API's shape
`{"query", "count", "results": [{"symbol", "name", "website", "exchange"}]}`, the same shape that
apps/web/src/apiLogoDomain.test.ts uses.

**These are built to that shape, not recorded live.** www.allinvestview.com was not reachable from the build sandbox
(the egress proxy refused the connection, 19 Nov 2026). The values follow the cases already in apiLogoDomain.test.ts.
To replace them with real answers, from a machine with network access:

    for s in AAPL SPY NBIS META DIS ZZZZ; do curl -s "https://www.allinvestview.com/api/logo-search/?q=$s" > $s.json; sleep 1.2; done

and check that the tests still pass.
