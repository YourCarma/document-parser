import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from settings import settings
from parser.router import router

app=FastAPI(
    title="Sova-Parser",
    description="""
## Overwiew
**Сова-парсер - сервис, предназначенный для обработки различных типов файлов, извлечение их элементов и конвертации их в [markdown](https://en.wikipedia.org/wiki/Markdown) с сохранением форматирования,
а так же `перевода` на основе библиотеки [docling](https://github.com/docling-project/docling) при помощи движка [EasyOCR](https://www.jaided.ai/easyocr/documentation/). 
Доступен перевод извлеченного текста**    

## Features 
### Поддерживыемые форматы файлов:
```python
ALLOWED_MIME_TYPES: List[str] = [
    "image/jpeg",
    "image/webp",
    "image/png",
    "image/tiff",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
    "application/pdf",
    "text/html",
]
```
 - `jpeg`,`jpg`,`webp` - Изображения
 - `doc`,`docx`,`odt` - Текстовые документы
 - `pdf`,`html` - Другие форматы


### Поддерживаемые языки:
```python 
ALLOWED_LANGS: List[str] = ["ru","en","ar","fr","uk"] # Согласно стандарту iso-639
```
 - `ru` - Русский
 - `en` - Английский (default)
 - `ar` - Арабский
 - `fr` - Французский
 - `uk` - Украинский
  
### Дополнительные элементы
- 📊 Таблицы
- 🖼️ Картинки

### Дополнительные опции
- Перевод

### Remark
 - В директории `/documents` лежат файлы-пробники для наглядной работы сервиса.
""",
    version="0.0.1"
)

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

if __name__ == "__main__":  
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
