# WHY:  makes tests/live a package so unittest discovery finds the gated live layer.
# WHAT: empty package marker; the tests inside skip unless BRAIN_LIVE_TESTS=1.
# WHO:  unittest discovery.
