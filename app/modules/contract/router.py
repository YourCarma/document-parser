"""Ручки контракта: JSON для машин, Markdown для агентов и людей."""

from fastapi import APIRouter, Request, Response

from modules.contract.markdown import render_markdown
from modules.contract.service import build_contract

router = APIRouter(prefix="/api/v1")


@router.get(
    "/contract",
    name="Контракт задач (JSON)",
    summary="Правила публикации задач в очередь, машиночитаемо",
    description=(
        "Полный контракт интеграции: топология, JSON Schema конверта и "
        "payload каждого типа задач, формат ключа задачи, статусы, семантика "
        "повторов и лимиты.\n\n"
        "Собирается из моделей и настроек **этого** пода, поэтому показывает "
        "имена exchange и очереди, лимиты и таймауты того окружения, у "
        "которого запрошен."
    ),
    tags=["System"],
)
async def get_contract(request: Request) -> dict:
    return build_contract(version=request.app.version)


@router.get(
    "/contract.md",
    name="Контракт задач (Markdown)",
    summary="То же самое одним документом — для человека или ИИ-агента",
    description=(
        "Тот же контракт в Markdown: таблицы полей, примеры сообщений, "
        "правила и чек-лист продюсера. Пригодно для того, чтобы отдать "
        "документ команде-интегратору или её агенту:\n\n"
        "```bash\n"
        "curl -s http://document-parser:1338/api/v1/contract.md > contract.md\n"
        "```"
    ),
    tags=["System"],
    response_class=Response,
    responses={
        200: {
            "content": {"text/markdown": {}},
            "description": "Контракт в Markdown.",
        }
    },
)
async def get_contract_markdown(request: Request) -> Response:
    document = render_markdown(build_contract(version=request.app.version))
    return Response(content=document, media_type="text/markdown; charset=utf-8")
