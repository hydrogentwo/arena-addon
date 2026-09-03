"""arena-addon for jcode.

Bridges jcode to arena.ai's Agent Mode so jcode can dispatch sub-tasks to
Arena agents and fold their results back into its swarm/coordinator flow.

The addon exposes an MCP (Model Context Protocol) server over stdio, which
jcode loads from `~/.jcode/mcp.json`. jcode's agent calls the exposed tools
as a sub-agent: it spawns an Arena task, waits for completion, and optionally
pulls back the workspace files the Arena agent produced.

Because arena.ai's Agent Mode has no public API, the default `cdp` driver
drives your existing authenticated browser through Chrome DevTools Protocol
(CDP). A dependency-free `mock` driver is included so the MCP contract can be
exercised and tested end to end without a browser.
"""

__version__ = "0.1.0"