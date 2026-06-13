"""Entry point for aiblueprint-mcp: stdio MCP server."""

import asyncio

from aiblueprint_mcp.server import main as server_main


def main():
    """Run the MCP server over stdio."""
    asyncio.run(server_main())


if __name__ == "__main__":
    main()
