"""Labs lane: an OpenCode-driven chat brain running beside the production one.

Nothing in this package is wired into `/chat`. It exists so the OpenCode runtime
can be measured against the production harness over the *same* capability
executor, with the same RBAC and policy gates.
"""
