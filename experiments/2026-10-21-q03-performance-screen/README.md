# Active1 short-screen producer

Base: fixed normal qualification PR21 4d3f478e7ef8221cb45a3c19fd81bf17aaa14c02.
Composed parent: reviewed Stage 1b PR26
`d8cbf37978b77a621ea26b9c700a97922a603e27`, exact tree
`77ef9d7e5e7dd19f2e0f8bea71bf41e60cf2fea2`.
One intended change: the finite Stage 2 performance producer using the same
owned HTTP lifecycle and immutable PR8 toolbox. See
`docs/q03-performance-screen.md` for workload, identity, bounds and stop rules.

Only CPU source/args/receipt/lifecycle tests were run for this preparation.
Synthetic fixtures, including fake network counters and clocks, are not actual
tokenizer, model, GPU, thermal, fan, memory or service performance observations.
There are no field runs, image changes, device operations or measured speedup.
Keep all failed cells and partial evidence; never select a later green attempt.

Targeted validation:

    python -m unittest discover -s tests -p test_q03_performance.py -q
    python -m unittest discover -s tests -p test_q03_qualification.py -q
    python -m unittest discover -s tests -p test_q03_http_service.py -q
    python -m unittest discover -s tests -p test_q03_http_independent_faults.py -q
    git diff --check

Conclusion: source/CPU preparation only. Stage 1 admission is still required;
capacity/performance remain INCONCLUSIVE until actual complete applicable gates.

Cloud doctor remains blocked on the absent private `config/nodes-ring4.local.json`;
no site configuration was invented or copied. No build/runtime change was made,
so there is no affected upstream patch series to apply or native build to claim.
