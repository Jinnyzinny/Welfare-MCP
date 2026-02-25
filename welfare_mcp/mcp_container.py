import contextlib
import logging
import importlib
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# MCP 서버 설정 Singleton Pattern으로 구현
mcp = FastMCP(
    name="Welfare MCP Server",
    stateless_http=True,
    json_response=True,
    host="welfare-mcpserver.shop",
)

# MCP Streamable HTTP App 생성
# MCP 서버를 배포하려면 이 기능을 사용해야 한다.
mcp_http_app = mcp.streamable_http_app()

@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    # [시작] MCP 세션 실행
    async with mcp.session_manager.run():
        yield
        # [종료] 서버가 꺼질 때 실행 (메모리 해제 핵심)
        try:
            # db_pool이 정의된 파일명을 여기에 적으세요 (예: tools.check_eligibility)
            # 파일 경로에 맞춰 수정이 필요할 수 있습니다.
            import tools.check_eligibility as db_mod

            if hasattr(db_mod, "db_pool") and db_mod.db_pool:
                logging.info("🔻 Closing DB Pool safely...")
                await db_mod.db_pool.close()
                logging.info("✅ DB Pool closed.")
        except Exception as e:
            logging.error(f"❌ Error during DB Pool shutdown: {e}")


base_app = Starlette(
    routes=[Mount("/", mcp_http_app)],
    lifespan=lifespan,
)

# CORS 미들웨어 설정
app = CORSMiddleware(
    base_app,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    expose_headers=["Mcp-Session-Id"],
)
