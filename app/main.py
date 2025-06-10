import uvicorn
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from settings import settings
from parser.router import router

# os.environ['PYTORCH_CUDA_ALLOC_CONF']='expandable_segments:True'
# os.environ['USE_PYTORCH_KERNEL_CACHE'] = '0'

app=FastAPI(
    title="Sova-Parser",
    description="""
**Предназначен для парсинга файлов различных типов, извлечение дополнительных элементов(картинок,таблиц) и конвертации с сохранением форматирования, перевода и озвучивания, выборки краткого содержания текстового содержимого.**

### Поддерживыемые форматы файлов:
```python
ALLOWED_MIME_TYPES: List[str] = [
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/doc",
    "application/msword",
    "application/pdf",
    "image/png",    
    "image/jpeg",
    "image/webp",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
]
```

### Поддерживаемые языки:
```python 
ALLOWED_LANGS: List[str] = ["ru","en","ar","fr","uk"]
```
 - `ru` - Русский
 - `en` - Английский (default)
 - `ar` - Арабский
 - `fr` - Французский
 - `uk` - Украинский
  
### Поддерживаемые форматы извлечения:
```python 
ALLOWED_CONVERTED_TYPES: List[str] = ["md","json","yaml","txt"]
```
 - `txt` - .txt
 - `json` - .json
 - `yaml` - .yaml
 - `markdown` - .md
 
### Дополнительные элементы
- 📊 Таблицы
- 🖼️ Картинки
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

app.mount("/scratch",StaticFiles(directory=Path("scratch")),name="scratch")

app.include_router(router)

if __name__ == "__main__":  
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )