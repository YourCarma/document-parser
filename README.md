# Sova Parser

## Overwiew
Сова-парсер - сервисы\, предназначенный для обработки различных типов файлов, извлечение их элементов и конвертации их в [markdown](https://en.wikipedia.org/wiki/Markdown) с сохранением форматирования, а так же `перевода` на основе библиотеки [docling](https://github.com/docling-project/docling) при помощи движка [EasyOCR](https://www.jaided.ai/easyocr/documentation/). Доступен перевод извлеченного текста.    

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


## Quick start

1.  Сборка образа определенной версии
    ```shell
    docker build -t parser:[version] .
    ```

2) Запуск контейнера  
    ```shell
    docker run -d \
        --name parser \
        -p 1338:1338 \
        --restart unless-stopped \
        parser:[version] 
    ```

3) Просмотр логов

    ```shell
    docker logs -f parser
    ```