"""抖音 · 巨量千川 MCP Server（stdio）。

启动（需先安装 mcp_servers/requirements.txt 依赖）：
    python mcp_servers/douyin/server.py
或在仓库根目录：
    python -m mcp_servers.douyin.server

工具分两类：
1) 目录工具：巨量千川常用接口（见 catalog.py），按接口名调用，入参为 params 字典；
2) 组合工具：账户概览 / 投递上下文 / 投递预检 / 提交投放，面向「广告投递 Agent」链路。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以脚本方式直接运行：python mcp_servers/douyin/server.py
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastmcp import FastMCP

from mcp_servers.douyin import tools
from mcp_servers.douyin.catalog import QIANCHUAN_TOOLS

mcp = FastMCP("Douyin Qianchuan")


def _catalog_tool(entry: dict):
    """把接口目录项包装成 MCP 工具（入参统一为 params 字典）。"""

    async def _tool(params: dict | None = None) -> dict:
        return await tools.qianchuan_api_call(tool=entry["name"], params=params)

    _tool.__name__ = entry["name"]
    _tool.__doc__ = f"{entry['description']}（接口 path：{entry['path']}）"
    return _tool


for _entry in QIANCHUAN_TOOLS:
    mcp.tool()(_catalog_tool(_entry))

# 已有完整签名的组合工具直接注册
mcp.tool()(tools.qianchuan_api_call)
mcp.tool()(tools.qianchuan_account_overview)
mcp.tool()(tools.qianchuan_delivery_context)
mcp.tool()(tools.qianchuan_delivery_preflight)
mcp.tool()(tools.qianchuan_submit_delivery)


if __name__ == "__main__":
    mcp.run(transport="stdio")