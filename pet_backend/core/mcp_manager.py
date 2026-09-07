import json
import os
import logging
from typing import Any
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent  # 👈 新增：用於型別判斷

logger = logging.getLogger("PetMiddleware")

class MCPManager:
    def __init__(self, config_path: str = "mcp_servers.json"):
        self.config_path = config_path
        self.exit_stack = AsyncExitStack()
        
        self.sessions: dict[str, ClientSession] = {}
        self.tool_to_session: dict[str, ClientSession] = {}
        self.tools_description_cache: str = "目前沒有可用的 MCP 工具。"

    async def initialize(self):
        if not os.path.exists(self.config_path):
            logger.warning(f"⚠️ 找不到 MCP 設定檔: {self.config_path}，跳過 MCP 初始化")
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"讀取 MCP 設定檔失敗: {e}")
            return

        servers = config.get("mcpServers", {})
        all_tools_info: list[str] = []

        for server_name, srv_config in servers.items():
            try:
                env = os.environ.copy()
                if "env" in srv_config:
                    env.update(srv_config["env"])

                params = StdioServerParameters(
                    command=srv_config["command"],
                    args=srv_config.get("args", []),
                    env=env
                )
                
                read, write = await self.exit_stack.enter_async_context(stdio_client(params))
                session = await self.exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                
                self.sessions[server_name] = session
                logger.info(f"✅ MCP 伺服器已連線: {server_name}")

                tools_result = await session.list_tools()
                for tool in tools_result.tools:
                    self.tool_to_session[tool.name] = session
                    
                    # 👈 修正：使用 getattr 安全取得 inputSchema 避免 Pylance 報錯
                    schema = getattr(tool, "inputSchema", {})
                    schema_str = json.dumps(schema, ensure_ascii=False)
                    
                    tool_desc = (
                        f"工具名稱 (mcp_tool_name): {tool.name}\n"
                        f"功能描述: {tool.description}\n"
                        f"所需參數 (mcp_tool_args) 格式: {schema_str}\n"
                        "-" * 30
                    )
                    all_tools_info.append(tool_desc)
                    
            except Exception as e:
                logger.error(f"❌ 初始化 MCP 伺服器 [{server_name}] 失敗: {e}")

        if all_tools_info:
            self.tools_description_cache = "\n".join(all_tools_info)
        logger.info(f"🛠️ MCP 總共載入 {len(self.tool_to_session)} 個外部工具")

    def get_tools_description(self) -> str:
        return self.tools_description_cache

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        session = self.tool_to_session.get(tool_name)
        if not session:
            return f"執行失敗：找不到名為 '{tool_name}' 的工具。"

        try:
            logger.info(f"執行 MCP 工具: {tool_name}，參數: {arguments}")
            result = await session.call_tool(tool_name, arguments)
            
            # 👈 修正：明確判斷物件型別是否為 TextContent
            content_texts: list[str] = [
                c.text for c in result.content if isinstance(c, TextContent)
            ]
            
            return "\n".join(content_texts) if content_texts else "工具執行成功，但無回傳文字內容。"
        except Exception as e:
            logger.error(f"執行 MCP 工具 {tool_name} 發生錯誤: {e}")
            return f"工具執行發生錯誤: {e}"

    async def shutdown(self):
        await self.exit_stack.aclose()
        logger.info("🛑 所有 MCP 子行程連線已關閉")

mcp_manager = MCPManager()