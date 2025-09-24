from modules.parser.v1.router import router as v1_parser_router
from modules.parser.v2.router import router as v2_parser_router

routers = [
    v1_parser_router,
    v2_parser_router
]