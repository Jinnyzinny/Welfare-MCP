from mcp_container import mcp


if __name__ == "__main__":
    mcp.run(transport="streamable-http", 
            mount_path="/mcp", 
            host="localhost", 
            port=8000)