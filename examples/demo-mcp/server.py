"""Small, read-only MCP server with invented work-order and inventory data."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


mcp = FastMCP(
    "Nexus Demo Warehouse",
    instructions=(
        "A test MCP server containing fictional work orders and stock. "
        "Use it to demonstrate tool discovery and project-scoped MCP calls."
    ),
    host="0.0.0.0",
    port=8765,
    streamable_http_path="/mcp",
    stateless_http=True,
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@mcp.tool(
    description="Look up a fictional work order by its number, such as TEST-WO-1001.",
    annotations=READ_ONLY,
)
def lookup_work_order(work_order_number: str) -> dict:
    """Return the demo work order with this number, if it exists."""
    work_orders = {
        "TEST-WO-1001": {
            "work_order_number": "TEST-WO-1001",
            "summary": "Inspect cooling pump",
            "status": "IN_PROGRESS",
            "site": "North Yard",
            "assigned_team": "Demo Maintenance Team",
        },
        "TEST-WO-1002": {
            "work_order_number": "TEST-WO-1002",
            "summary": "Replace loading-bay sensor",
            "status": "WAITING_FOR_PARTS",
            "site": "East Depot",
            "assigned_team": "Demo Electrical Team",
        },
    }
    return work_orders.get(
        work_order_number.upper(),
        {"found": False, "work_order_number": work_order_number},
    )


@mcp.tool(
    description="Search fictional warehouse stock by part number or description.",
    annotations=READ_ONLY,
)
def search_stock(query: str) -> dict:
    """Search the small, fictional inventory included with this demo."""
    inventory = [
        {
            "part_number": "TEST-FILTER-01",
            "description": "Pump intake filter",
            "available_quantity": 12,
            "warehouse": "North Yard",
        },
        {
            "part_number": "TEST-SENSOR-02",
            "description": "Loading-bay proximity sensor",
            "available_quantity": 0,
            "warehouse": "East Depot",
        },
        {
            "part_number": "TEST-BOLT-03",
            "description": "Stainless mounting bolt",
            "available_quantity": 240,
            "warehouse": "North Yard",
        },
    ]
    needle = query.casefold()
    matches = [
        item
        for item in inventory
        if needle in item["part_number"].casefold()
        or needle in item["description"].casefold()
        or needle in item["warehouse"].casefold()
    ]
    return {"query": query, "matches": matches, "match_count": len(matches)}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
