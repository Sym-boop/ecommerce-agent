from fastmcp import Client
import asyncio

async def test_mcp():
    # 这行代码会自动启动你的 erp_connector.py，通过 stdio 通信，用完自动关闭
    async with Client("mcp_servers/erp_connector.py") as client:
        result = await client.call_tool("query_product", {"sku": "SKU-1002"})
        print(result)

asyncio.run(test_mcp())