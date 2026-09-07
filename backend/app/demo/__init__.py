"""Phase 7A demo workbench support package.

    bootstrap.py  create + seed the local demo SQLite database
    demo_seed.py  demo dataset (orders 1001+ / logistics / knowledge)
    store.py      process-local Chat run + approval projection store
    payloads.py   JSON projection builders (never fabricate business data)
    service.py    wires the real AgentWorkflow / Risk / MCP components
    routes.py     thin /api/v1/demo endpoints used by the frontend

None of these modules rewrite the core Agent / Risk / Tool / MCP layers.
"""
